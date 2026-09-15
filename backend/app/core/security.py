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


# ------------------------------------------------------------------ API tokens

API_TOKEN_PREFIX = "forge_"


def new_api_token() -> tuple[str, str, str]:
    """(plaintext, sha256 hex, display prefix). The plaintext is shown once."""
    import hashlib
    import secrets

    plain = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hashlib.sha256(plain.encode()).hexdigest(), plain[:10]


def hash_api_token(plain: str) -> str:
    import hashlib

    return hashlib.sha256(plain.encode()).hexdigest()


async def claims_for_api_token(plain: str) -> Claims | None:
    """Resolve a `forge_...` token to the person it belongs to, or None."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.core.db import platform_session
    from app.models.platform_ import ApiToken, Tenant, User

    async with platform_session() as s:
        row = await s.scalar(select(ApiToken).where(
            ApiToken.token_hash == hash_api_token(plain), ApiToken.revoked_at.is_(None)))
        if row is None:
            return None
        user = await s.get(User, row.user_id)
        if user is None:
            return None
        tenant = await s.get(Tenant, user.tenant_id) if user.tenant_id else None
        row.last_used_at = datetime.now(timezone.utc)
        return Claims(
            user_id=str(user.id),
            tenant_id=str(tenant.id) if tenant else None,
            tenant_key=tenant.schema_key if tenant else None,
            email=user.email, name=user.name, role=user.role,
        )
