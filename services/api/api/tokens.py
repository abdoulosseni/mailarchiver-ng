"""Jetons de session JWT (HS256) signés avec API_SECRET_KEY."""

from __future__ import annotations

import datetime as dt
import os

import jwt

from .auth import AuthenticatedUser

_SECRET = os.environ.get("API_SECRET_KEY", "change-me-session-jwt")
_ALGO = "HS256"
_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "8"))


def create_token(user: AuthenticatedUser) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": user.username,
        "name": user.display_name,
        "role": user.role,
        "admin": user.is_admin,
        "email": user.email,
        "audited_emails": user.audited_emails or [],
        "iat": now,
        "exp": now + dt.timedelta(hours=_TTL_HOURS),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def decode_token(token: str) -> AuthenticatedUser | None:
    try:
        claims = jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except jwt.InvalidTokenError:
        return None
    return AuthenticatedUser(
        username=claims.get("sub", ""),
        display_name=claims.get("name", ""),
        role=claims.get("role", "user"),
        is_admin=bool(claims.get("admin", False)),
        email=claims.get("email"),
        audited_emails=claims.get("audited_emails") or [],
    )
