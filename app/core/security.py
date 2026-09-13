"""Passwords and tokens.

Signing in decides three things that travel with you for the rest of the
session: which company you belong to, which person you are, and your role.
Everything else in the platform follows from those, so they live in the JWT and
the gate reads them straight out of it - no database round trip before we can
even pick a schema.

    platform_admin  exactly one, seeded from .env, belongs to no company.
                    Shares servers with everyone and reviews the marketplace.
    admin           created their company. Can also share a server with the
                    whole company.
    user            everyone else. Everything they make is theirs alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, Exception):  # noqa: B014 - any failure is a no
        return False


@dataclass(frozen=True)
class Claims:
    """Who is asking. This is what the tenant gate turns into a search_path."""

    user_id: str
    tenant_id: str | None  # None for the platform admin
    tenant_key: str | None
    email: str
    name: str
    role: str  # platform_admin | admin | user

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"

    @property
    def is_company_admin(self) -> bool:
        return self.role == "admin"


def issue_token(claims: Claims) -> str:
    payload = {
        "sub": claims.user_id,
        "tid": claims.tenant_id,
        "tenant_key": claims.tenant_key,
        "email": claims.email,
        "name": claims.name,
        "role": claims.role,
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def read_token(token: str) -> Claims:
    """Raises jwt.PyJWTError if the token is missing, expired or tampered with."""
    data = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    return Claims(
        user_id=data["sub"],
        tenant_id=data["tid"],
        tenant_key=data["tenant_key"],
        email=data["email"],
        name=data["name"],
        role=data["role"],
    )
