"""Request dependencies: who is calling, and are they allowed to?

API clients authenticate with `Authorization: Bearer <token>`.
The browser UI uses an httpOnly session cookie; state-changing requests made with the cookie
must also send the CSRF token (double-submit), so other sites cannot act as the user.
"""

from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Role, User
from app.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_ok,
    decode_session_token,
    token_matches_user,
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class LoginRequired(Exception):
    """Raised by page routes; main.py turns it into a redirect to /login."""

    def __init__(self, next_url: str):
        self.next_url = next_url


def _resolve_user(request: Request, db: Session) -> tuple[User | None, bool]:
    auth = request.headers.get("Authorization", "")
    via_cookie = False
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.cookies.get(SESSION_COOKIE)
        via_cookie = True
    if not token:
        return None, via_cookie

    payload = decode_session_token(token)
    if payload is None:
        return None, via_cookie
    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if user is None or not user.active or not token_matches_user(payload, user):
        return None, via_cookie
    return user, via_cookie


def _bind(request: Request, db: Session, user: User) -> User:
    db.info["user"] = user.username
    request.state.user = user.username
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, via_cookie = _resolve_user(request, db)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not signed in or session expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if via_cookie and request.method not in SAFE_METHODS:
        if not csrf_ok(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return _bind(request, db, user)


def require_roles(*roles: Role):
    """Allow the listed roles (ADMIN is always allowed)."""

    def dependency(user: User = Depends(current_user)) -> User:
        if not user.has_role(*roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role: {', '.join(r.value for r in roles) or 'ADMIN'}",
            )
        return user

    return dependency


def page_user(request: Request, db: Session = Depends(get_db)) -> User:
    user, _ = _resolve_user(request, db)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise LoginRequired(quote(target, safe="/?=&"))
    return _bind(request, db, user)


def page_roles(*roles: Role):
    def dependency(user: User = Depends(page_user)) -> User:
        if not user.has_role(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this page")
        return user

    return dependency


# Shorthands used by routers
Operator = require_roles(Role.OPERATOR, Role.SUPERVISOR)
Supervisor = require_roles(Role.SUPERVISOR)
Quality = require_roles(Role.QUALITY)
QualityOrSupervisor = require_roles(Role.QUALITY, Role.SUPERVISOR)
Admin = require_roles()
