"""Connections: storing a credential, and lending it out for one call.

Two entry points, and the asymmetry between them is the point:

    add()   takes a plaintext secret, and returns nothing readable
    use()   returns a plaintext secret, and is called from exactly one place
            (app/runtime/guarded_tool.py), inside a try/finally that drops it

There is deliberately no `get_secret()`. No endpoint returns one. If you find
yourself wanting one, the answer is that the tool call should move to where the
secret already is, not the other way round.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Connection
from app.vault import envelope


async def list_connections(session: AsyncSession) -> list[Connection]:
    """No tenant filter: the gate already chose one company's schema."""
    rows = await session.scalars(select(Connection).order_by(Connection.server_name))
    return list(rows)


async def add(
    session: AsyncSession,
    *,
    tenant: str,
    server_name: str,
    secret: str,
    added_by: str = "",
) -> Connection:
    """Encrypt immediately. `secret` is not stored, logged or returned."""
    sealed = envelope.seal(secret, tenant=tenant, server_name=server_name)

    conn = await session.scalar(
        select(Connection).where(Connection.server_name == server_name)
    )
    if conn is None:
        conn = Connection(server_name=server_name)
        session.add(conn)

    conn.ciphertext = sealed.ciphertext
    conn.nonce = sealed.nonce
    conn.wrapped_dek = sealed.wrapped_dek
    conn.dek_nonce = sealed.dek_nonce
    conn.key_version = sealed.key_version
    conn.status = "active"
    conn.added_by = added_by
    await session.flush()
    return conn


async def revoke(session: AsyncSession, server_name: str) -> Connection:
    """Revoking must leave dependent agents DEGRADED, not crashed.

    The row is kept, marked revoked, and its ciphertext wiped. An agent that
    needs it now gets "not connected" back as a tool result and carries on with
    whatever else it can do.
    """
    conn = await session.scalar(
        select(Connection).where(Connection.server_name == server_name)
    )
    if conn is None:
        raise KeyError(server_name)

    conn.status = "revoked"
    conn.ciphertext = b""
    conn.wrapped_dek = b""
    await session.flush()
    return conn


async def use(session: AsyncSession, *, tenant: str, server_name: str) -> str | None:
    """Decrypt for ONE tool call. Returns None if this workspace has no key.

    None is not an error - it is the "degraded" path. The caller turns it into a
    sentence the model can read, never an exception.
    """
    conn = await session.scalar(
        select(Connection).where(Connection.server_name == server_name)
    )
    if conn is None or conn.status != "active" or not conn.ciphertext:
        return None

    sealed = envelope.SealedSecret(
        ciphertext=conn.ciphertext,
        nonce=conn.nonce,
        wrapped_dek=conn.wrapped_dek,
        dek_nonce=conn.dek_nonce,
        key_version=conn.key_version,
    )
    try:
        secret = envelope.open_(sealed, tenant=tenant, server_name=server_name)
    except envelope.VaultError:
        # Wrong master key, or the row does not belong here. Degrade, do not crash.
        return None

    conn.last_used_at = datetime.now(timezone.utc)
    return secret
