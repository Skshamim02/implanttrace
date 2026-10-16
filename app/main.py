import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.deps import LoginRequired
from app.errors import AuthenticationFailed, Conflict, NotFound, PermissionDenied, ValidationFailed
from app.logging_config import request_id_var, setup_logging
from app.routers import admin, auth, pages, production, quality

setup_logging()
log = logging.getLogger("implanttrace")
access_log = logging.getLogger("implanttrace.access")

VERSION = "1.0.0"
API = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.check()
    log.info("ImplantTrace %s starting (%s)", VERSION, settings.environment)
    yield


app = FastAPI(
    title="ImplantTrace",
    description="Shop-floor traceability for a precision implant machining plant.",
    version=VERSION,
    lifespan=lifespan,
    docs_url=f"{API}/docs",
    redoc_url=None,
    openapi_url=f"{API}/openapi.json",
)
for module in (auth, admin, production, quality):
    app.include_router(module.router, prefix=API)
app.include_router(pages.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)
# Swagger UI loads assets from a CDN, so its pages get a looser policy.
DOCS_CSP = (
    "default-src 'self'; img-src 'self' data: https://fastapi.tiangolo.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        response = JSONResponse(
            {"detail": "Internal error", "request_id": request_id}, status_code=500
        )
    finally:
        request_id_var.reset(token)
    duration = round((time.perf_counter() - start) * 1000, 1)

    response.headers["X-Request-ID"] = request_id
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    is_docs = request.url.path.startswith(f"{API}/docs")
    response.headers.setdefault("Content-Security-Policy", DOCS_CSP if is_docs else CSP)
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(API):
        response.headers.setdefault("Cache-Control", "no-store")

    if not request.url.path.startswith("/static"):
        access_log.info(
            "%s %s %s %sms",
            request.method,
            request.url.path,
            response.status_code,
            duration,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
                "user": getattr(request.state, "user", None),
            },
        )
    return response


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": message, **extra})


@app.exception_handler(NotFound)
async def not_found(request: Request, exc: NotFound):
    if not request.url.path.startswith(API):
        return pages.render(
            request, "error.html", None, status_code=404, status=404, message=str(exc)
        )
    return _error(404, str(exc))


@app.exception_handler(Conflict)
async def conflict(request: Request, exc: Conflict):
    return _error(409, str(exc))


@app.exception_handler(PermissionDenied)
async def permission_denied(request: Request, exc: PermissionDenied):
    return _error(403, str(exc))


@app.exception_handler(AuthenticationFailed)
async def auth_failed(request: Request, exc: AuthenticationFailed):
    return _error(401, str(exc))


@app.exception_handler(ValidationFailed)
async def validation_failed(request: Request, exc: ValidationFailed):
    return _error(422, str(exc), errors=exc.details)


@app.exception_handler(RequestValidationError)
async def request_invalid(request: Request, exc: RequestValidationError):
    errors = [
        f"{'.'.join(str(p) for p in e['loc'] if p not in ('body', 'query', 'path'))}: {e['msg']}"
        for e in exc.errors()
    ]
    return _error(422, "Please correct the highlighted fields", errors=errors)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={exc.next_url}", status_code=303)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if not request.url.path.startswith(API) and exc.status_code in (403, 404):
        return pages.render(
            request,
            "error.html",
            None,
            status_code=exc.status_code,
            status=exc.status_code,
            message=exc.detail,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/health", include_in_schema=False)
def health():
    """Liveness: the process is up."""
    return {"status": "ok", "version": VERSION}


@app.get("/ready", include_in_schema=False)
def ready():
    """Readiness: the database answers."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness check failed")
        return JSONResponse({"status": "database unavailable"}, status_code=503)
    return {"status": "ready"}
