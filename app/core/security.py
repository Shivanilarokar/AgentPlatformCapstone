"""Passwords and tokens.

Signing in decides two things that travel with you for the rest of the session:
which company you belong to, and whether you are an admin. Everything else in
the platform follows from those two, so they live in the JWT and the tenant gate
reads them straight out of it - no database round trip before we can even pick a
schema.
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
    tenant_id: str
    tenant_slug: str
    email: str
    name: str
    role: str

    @property
    def is_admin(self) -> bool:
        """PLATFORM admin: can share a server with everyone, and reviews the
        marketplace. Only the first person to sign up gets this."""
        return self.role == "platform_admin"

    @property
    def is_workspace_admin(self) -> bool:
        """Runs one company. The first user of each company."""
        return self.role in ("admin", "platform_admin")


def issue_token(claims: Claims) -> str:
    payload = {
        "sub": claims.user_id,
        "tid": claims.tenant_id,
        "slug": claims.tenant_slug,
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
        tenant_slug=data["slug"],
        email=data["email"],
        name=data["name"],
        role=data["role"],
    )
