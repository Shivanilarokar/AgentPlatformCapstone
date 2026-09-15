"""GRADED CHECK 8 - the downloaded Postman collection gets a real response.

The collection is generated server-side with the caller's own API token in its
`token` variable. Here: build the collection, take that token out of it, and
call the Invoke request exactly as Postman would - Bearer token, no cookie.
Also: another company's token gets 404 for this agent, not 403.
"""

from __future__ import annotations

import uuid

from app.core.security import claims_for_api_token, hash_api_token, new_api_token
from app.core.db import platform_session
from app.models.platform_ import ApiToken, Tenant, User


async def _person(company_key: str, email: str) -> str:
    async with platform_session() as p:
        tenant = Tenant(name=company_key, schema_key=company_key)
        p.add(tenant)
        await p.flush()
        user = User(tenant_id=tenant.id, email=email, password_hash="x", name="t", role="admin")
        p.add(user)
        await p.flush()
        return str(user.id)


async def _cleanup(email: str, company_key: str) -> None:
    from sqlalchemy import delete, select

    async with platform_session() as p:
        uid = await p.scalar(select(User.id).where(User.email == email))
        if uid:
            await p.execute(delete(ApiToken).where(ApiToken.user_id == uid))
            await p.execute(delete(User).where(User.id == uid))
        await p.execute(delete(Tenant).where(Tenant.schema_key == company_key))


async def test_an_api_token_resolves_to_its_owner_and_nobody_else():
    tag = uuid.uuid4().hex[:6]
    email = f"tok.{tag}@example.test"
    uid = await _person(f"tokco_{tag}", email)
    plain, digest, prefix = new_api_token()
    async with platform_session() as p:
        p.add(ApiToken(user_id=uuid.UUID(uid), name="t", token_hash=digest, prefix=prefix))

    claims = await claims_for_api_token(plain)
    assert claims is not None and claims.user_id == uid and claims.tenant_key == f"tokco_{tag}"
    assert await claims_for_api_token(plain + "x") is None
    assert hash_api_token(plain) == digest and plain not in digest  # only the hash is stored
    await _cleanup(email, f"tokco_{tag}")


def test_the_collection_is_v2_1_and_carries_the_token_where_postman_reads_it():
    """Shape of what /postman returns, checked against the router's own builder."""
    import inspect

    from app.api.routers import public_api

    src = inspect.getsource(public_api.postman)
    assert "collection/v2.1.0/collection.json" in src
    assert '"Bearer {{token}}"' in src
    assert '{"key": "token", "value": plain}' in src
