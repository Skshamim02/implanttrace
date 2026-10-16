import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.errors import AuthenticationFailed
from app.models import User
from app.schemas import LoginIn, LoginOut, PasswordChange, UserOut
from app.security import CSRF_COOKIE, SESSION_COOKIE, create_session_token, new_csrf_token
from app.services import users

router = APIRouter(prefix="/auth", tags=["auth"])

# Per-IP throttle on top of per-account lockout. In-process only: with several workers or
# servers, put the limit in the reverse proxy instead.
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
IP_LIMIT, IP_WINDOW_S = 20, 300


def _throttle(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = _ATTEMPTS[ip]
    while attempts and now - attempts[0] > IP_WINDOW_S:
        attempts.popleft()
    if len(attempts) >= IP_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts; wait")
    attempts.append(now)


def _set_session(response: Response, token: str) -> None:
    max_age = settings.session_hours * 3600
    common = {"max_age": max_age, "secure": settings.cookie_secure, "samesite": "strict"}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), httponly=False, **common)


@router.post("/login", response_model=LoginOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _throttle(request)
    try:
        user = users.authenticate(db, data.username.strip(), data.password)
    except AuthenticationFailed:
        db.commit()  # keep the failed-attempt count
        raise
    db.commit()
    token = create_session_token(user)
    _set_session(response, token)
    return {"access_token": token, "user": user}


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/change-password", status_code=204)
def change_password(
    data: PasswordChange,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    users.change_password(db, user, data.current_password, data.new_password)
    db.commit()
    _set_session(response, create_session_token(user))  # old sessions are now invalid
