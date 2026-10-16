"""User accounts, login with lockout, and password management."""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit_reason
from app.config import settings
from app.errors import (
    AuthenticationFailed,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.models import Role, User, aware, utcnow
from app.schemas import UserCreate, UserUpdate
from app.security import check_password_policy, hash_password, verify_password

log = logging.getLogger(__name__)

# Verified against when the username does not exist, so response time does not reveal it.
_DUMMY_HASH = hash_password("dummy-password-for-timing")
GENERIC_FAILURE = "Invalid username or password"


def get_user(db: Session, username: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise NotFound(f"User {username} not found")
    return user


def _validate_password(password: str) -> None:
    problems = check_password_policy(password)
    if problems:
        raise ValidationFailed("Password must contain " + ", ".join(problems))


def authenticate(db: Session, username: str, password: str) -> User:
    """Check credentials. Failed attempts are counted; too many lock the account.

    The caller must commit even when this raises, so failed attempts are recorded.
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        log.warning("Login failed for unknown user %r", username)
        raise AuthenticationFailed(GENERIC_FAILURE)

    db.info["user"] = user.username
    now = utcnow()
    if not user.active:
        raise AuthenticationFailed(GENERIC_FAILURE)
    if user.locked_until and aware(user.locked_until) > now:
        raise AuthenticationFailed("Account temporarily locked. Try later or ask an administrator.")

    if not verify_password(user.password_hash, password):
        with audit_reason(db, "Failed login attempt"):
            user.failed_logins += 1
            if user.failed_logins >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                log.warning("Account %s locked after %d failures", username, user.failed_logins)
            db.flush()
        raise AuthenticationFailed(GENERIC_FAILURE)

    if user.failed_logins or user.locked_until:
        with audit_reason(db, "Successful login resets failure count"):
            user.failed_logins = 0
            user.locked_until = None
            db.flush()
    log.info("User %s signed in", username)
    return user


def create_user(db: Session, data: UserCreate) -> User:
    if db.scalar(select(User).where(User.username == data.username)):
        raise Conflict(f"Username {data.username} is taken")
    _validate_password(data.password)
    user = User(
        username=data.username,
        full_name=data.full_name,
        role=data.role,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    db.flush()
    return user


def update_user(db: Session, actor: User, username: str, data: UserUpdate) -> User:
    user = get_user(db, username)
    changes = data.model_dump(exclude_none=True)
    if user.id == actor.id and (
        changes.get("active") is False or changes.get("role", Role.ADMIN) != Role.ADMIN
    ):
        raise Conflict("You cannot deactivate or demote your own account")
    with audit_reason(db, f"User updated by {actor.username}"):
        signs_out = changes.get("active") is False or changes.get("role", user.role) != user.role
        for key, value in changes.items():
            setattr(user, key, value)
        if signs_out:
            user.session_version += 1
        if changes.get("active"):
            user.failed_logins = 0
            user.locked_until = None
        db.flush()
    return user


def reset_password(db: Session, actor: User, username: str, new_password: str) -> User:
    user = get_user(db, username)
    _validate_password(new_password)
    with audit_reason(db, f"Password reset by {actor.username}"):
        user.password_hash = hash_password(new_password)
        user.password_changed_at = utcnow()
        user.session_version += 1
        user.failed_logins = 0
        user.locked_until = None
        db.flush()
    return user


def change_password(db: Session, user: User, current: str, new: str) -> User:
    if not verify_password(user.password_hash, current):
        raise PermissionDenied("Current password is incorrect")
    if current == new:
        raise ValidationFailed("New password must be different")
    _validate_password(new)
    with audit_reason(db, "User changed own password"):
        user.password_hash = hash_password(new)
        user.password_changed_at = utcnow()
        user.session_version += 1
        db.flush()
    return user
