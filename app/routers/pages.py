"""Server-rendered screens. Reads go straight to the service layer; every change is sent by
static/app.js to the JSON API, so business rules and permissions live in one place."""

from pathlib import Path
from urllib.parse import urlencode

import segno
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import _resolve_user, page_roles, page_user
from app.models import (
    INSPECTION_STEP,
    AuditLog,
    Decision,
    OrderStatus,
    Role,
    StepStatus,
    User,
    utcnow,
)
from app.routers.quality import audit_query
from app.services import dashboard, masterdata, production, quality, trace
from app.services.common import paginate

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
PAGE_SIZE = 25


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.1f}%"


def _dt(value) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else ""


templates.env.filters["pct"] = _pct
templates.env.filters["dt"] = _dt
templates.env.globals.update(Role=Role, OrderStatus=OrderStatus, StepStatus=StepStatus)

NAV = [
    ("/", "Dashboard", None),
    ("/station", "Operator station", (Role.OPERATOR, Role.SUPERVISOR)),
    ("/orders", "Work orders", None),
    ("/quality", "Quality", None),
    ("/trace", "Traceability", None),
    ("/materials", "Materials", None),
    ("/parts", "Parts", None),
    ("/machines", "Machines", None),
    ("/audit", "Audit trail", (Role.QUALITY,)),
    ("/users", "Users", ()),
]


def render(
    request: Request, name: str, user: User | None, status_code: int = 200, **ctx
) -> HTMLResponse:
    nav = [
        (href, label)
        for href, label, roles in NAV
        if user and (roles is None or user.has_role(*roles))
    ]
    return templates.TemplateResponse(
        request,
        name,
        {"user": user, "nav": nav, "path": request.url.path, **ctx},
        status_code=status_code,
    )


def _pager(request: Request, total: int, offset: int) -> dict:
    params = dict(request.query_params)

    def link(new_offset: int) -> str:
        return "?" + urlencode({**params, "offset": new_offset})

    return {
        "total": total,
        "start": offset + 1 if total else 0,
        "end": min(offset + PAGE_SIZE, total),
        "prev": link(max(offset - PAGE_SIZE, 0)) if offset > 0 else None,
        "next": link(offset + PAGE_SIZE) if offset + PAGE_SIZE < total else None,
    }


def _safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/"


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if _resolve_user(request, db)[0]:
        return RedirectResponse(_safe_next(next), status_code=303)
    return render(request, "login.html", None, next=_safe_next(next))


@router.get("/")
def dashboard_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)
):
    return render(request, "dashboard.html", user, d=dashboard.summary(db))


@router.get("/orders")
def orders_page(
    request: Request,
    status: OrderStatus | None = None,
    search: str = "",
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(page_user),
):
    page = production.list_orders(db, status, search.strip(), PAGE_SIZE, offset)
    return render(
        request,
        "orders.html",
        user,
        orders=page["items"],
        pager=_pager(request, page["total"], offset),
        status=status,
        search=search,
        parts=[p for p in masterdata.list_parts(db) if p.active],
        materials=production.list_materials(db, limit=200)["items"],
    )


@router.get("/orders/{order_no}")
def order_page(
    order_no: str, request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)
):
    order = production.get_order(db, order_no)
    step_ids = [s.id for s in order.steps]
    history = db.scalars(
        select(AuditLog)
        .where(
            ((AuditLog.table_name == "work_order") & (AuditLog.record_id == order.id))
            | ((AuditLog.table_name == "route_step") & AuditLog.record_id.in_(step_ids))
        )
        .order_by(AuditLog.id.desc())
        .limit(50)
    ).all()
    results, failures = production.open_inspection_failures(order)
    return render(
        request,
        "order_detail.html",
        user,
        order=order,
        history=history,
        open_results=results,
        open_failures=failures,
        decisions=list(Decision),
    )


@router.get("/orders/{order_no}/traveller")
def traveller_page(
    order_no: str, request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)
):
    order = production.get_order(db, order_no)
    # Standard QR (not Micro QR, which many handheld scanners cannot read) with a 4-module
    # quiet zone, as the QR spec requires for reliable scanning.
    qr = segno.make_qr(order.order_no, error="m").svg_inline(
        scale=5, border=4, title=order.order_no
    )
    return templates.TemplateResponse(
        request, "traveller.html", {"order": order, "qr": qr, "user": user, "printed_at": utcnow()}
    )


@router.get("/station")
def station_page(
    request: Request,
    order: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(page_roles(Role.OPERATOR, Role.SUPERVISOR)),
):
    ctx = {"order_no": order.strip(), "order": None, "error": None, "qty_in": {}}
    if ctx["order_no"]:
        try:
            wo = production.get_order(db, ctx["order_no"])
            ctx["order"] = wo
            ctx["qty_in"] = {s.seq: production.qty_into_step(wo, s) for s in wo.steps}
            ctx["open_log"] = {
                s.seq: next((log for log in s.logs if log.finished_at is None), None)
                for s in wo.steps
            }
        except Exception as exc:  # NotFound -> show inline, keep the scan box focused
            ctx["error"] = str(exc)
    ctx["machines"] = masterdata.list_machines(db, active_only=True)
    ctx["inspection_step"] = INSPECTION_STEP
    return render(request, "station.html", user, **ctx)


@router.get("/quality")
def quality_page(request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)):
    holds = quality.hold_queue(db)
    return render(
        request,
        "quality.html",
        user,
        holds=holds,
        decisions=list(Decision),
        can_sign=user.has_role(Role.QUALITY),
    )


@router.get("/trace")
def trace_page(
    request: Request,
    heat: str = "",
    serial: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(page_user),
):
    ctx = {"heat": heat.strip(), "serial": serial.strip(), "fwd": None, "bwd": None, "error": None}
    try:
        if ctx["heat"]:
            ctx["fwd"] = trace.forward(db, ctx["heat"])
        elif ctx["serial"]:
            ctx["bwd"] = trace.backward(db, ctx["serial"])
    except Exception as exc:
        ctx["error"] = str(exc)
    return render(request, "trace.html", user, **ctx)


@router.get("/materials")
def materials_page(
    request: Request,
    search: str = "",
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(page_user),
):
    page = production.list_materials(db, search.strip(), PAGE_SIZE, offset)
    return render(
        request,
        "materials.html",
        user,
        materials=page["items"],
        pager=_pager(request, page["total"], offset),
        search=search,
    )


@router.get("/parts")
def parts_page(request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)):
    return render(request, "parts.html", user, parts=masterdata.list_parts(db))


@router.get("/machines")
def machines_page(request: Request, db: Session = Depends(get_db), user: User = Depends(page_user)):
    return render(request, "machines.html", user, machines=masterdata.list_machines(db))


@router.get("/audit")
def audit_page(
    request: Request,
    q=Depends(audit_query),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(page_roles(Role.QUALITY)),
):
    page = paginate(db, q, PAGE_SIZE, offset)
    return render(
        request,
        "audit.html",
        user,
        entries=page["items"],
        pager=_pager(request, page["total"], offset),
        filters=dict(request.query_params),
        export_qs=urlencode({k: v for k, v in request.query_params.items() if k != "offset"}),
    )


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), user: User = Depends(page_roles())):
    return render(
        request,
        "users.html",
        user,
        users=db.scalars(select(User).order_by(User.username)).all(),
        roles=list(Role),
    )


@router.get("/account")
def account_page(request: Request, user: User = Depends(page_user)):
    return render(request, "account.html", user)
