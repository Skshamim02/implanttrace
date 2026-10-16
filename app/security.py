"""Password hashing, session tokens and CSRF tokens."""

import hmac
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import settings
from app.models import User, utcnow

SESSION_COOKIE = "it_session"
CSRF_COOKIE = "it_csrf"
CSRF_HEADER = "X-CSRF-Token"
ALGORITHM = "HS256"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def check_password_policy(password: str) -> list[str]:
    problems = []
    if len(password) < settings.password_min_length:
        problems.append(f"at least {settings.password_min_length} characters")
    if password.lower() == password or password.upper() == password:
        problems.append("both upper- and lower-case letters")
    if not any(c.isdigit() for c in password):
        problems.append("at least one digit")
    return problems


def create_session_token(user: User) -> str:
    now = utcnow()
    payload = {
        "sub": user.username,
        "role": user.role.value,
        "sv": user.session_version,
        "iat": now,
        "exp": now + timedelta(hours=settings.session_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def token_matches_user(payload: dict, user: User) -> bool:
    return payload.get("sv") == user.session_version


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, header_value: str | None) -> bool:
    return bool(cookie_value and header_value and hmac.compare_digest(cookie_value, header_value))
