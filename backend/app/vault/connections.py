"""Connections: storing a credential, and lending it out for one call.

Two entry points, and the asymmetry between them is the point:

    add()   takes a plaintext secret, and returns nothing readable
    use()   returns a plaintext secret, for one call, to callers that drop it
            immediately: the tool wrapper (app/runtime/guarded_tool.py), the
            registry re-check, and the health sweep

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
    user_id: str,
    server_name: str,
    secret: str,
    added_by: str = "",
) -> Connection:
    """Encrypt immediately. `secret` is not stored, logged or returned.

    The row is this PERSON's: row-level security scopes the lookup below to the
    signed-in user, and the ciphertext is bound to tenant + user + server.
    """
    sealed = envelope.seal(secret, tenant=tenant, user_id=user_id, server_name=server_name)

    conn = await session.scalar(
        select(Connection).where(Connection.server_name == server_name)
    )
    if conn is None:
        conn = Connection(server_name=server_name)
        session.add(conn)

    conn.encrypted_secret = sealed.encrypted_secret
    conn.secret_nonce = sealed.secret_nonce
    conn.encrypted_data_key = sealed.encrypted_data_key
    conn.data_key_nonce = sealed.data_key_nonce
    conn.master_key_version = sealed.master_key_version
    conn.status = "active"
    if added_by:
        conn.added_by = added_by
    await session.flush()
    return conn


async def revoke(session: AsyncSession, server_name: str) -> Connection:
    """Revoking must leave dependent agents DEGRADED, not crashed.

    The row is kept, marked revoked, and its encrypted_secret wiped. An agent that
    needs it now gets "not connected" back as a tool result and carries on with
    whatever else it can do.
    """
    conn = await session.scalar(
        select(Connection).where(Connection.server_name == server_name)
    )
    if conn is None:
        raise KeyError(server_name)

    conn.status = "revoked"
    conn.encrypted_secret = b""
    conn.encrypted_data_key = b""
    await session.flush()
    return conn


async def use(
    session: AsyncSession, *, tenant: str, user_id: str, server_name: str
) -> str | None:
    """Decrypt for ONE tool call. Returns None if this workspace has no key.

    None is not an error - it is the "degraded" path. The caller turns it into a
    sentence the model can read, never an exception.
    """
    conn = await session.scalar(
        select(Connection).where(
            Connection.server_name == server_name, Connection.owner_id == user_id
        )
    )
    if conn is None or conn.status != "active" or not conn.encrypted_secret:
        return None

    sealed = envelope.SealedSecret(
        encrypted_secret=conn.encrypted_secret,
        secret_nonce=conn.secret_nonce,
        encrypted_data_key=conn.encrypted_data_key,
        data_key_nonce=conn.data_key_nonce,
        master_key_version=conn.master_key_version,
    )
    try:
        secret = envelope.open_(sealed, tenant=tenant, user_id=user_id, server_name=server_name)
    except envelope.VaultError:
        # Wrong master key, or the row does not belong here. Degrade, do not crash.
        return None

    conn.last_used_at = datetime.now(timezone.utc)
    return secret
