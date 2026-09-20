"""Wiring the vault into a run.

The runtime knows nothing about databases. It is handed a callable that turns a
server name into the signed-in PERSON's token, and this is the only
implementation of it that touches the vault.

One session per call, deliberately: the token exists between `use()` returning
and `del token` in the tool wrapper, and not one moment longer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.db import tenant_session
from app.mcp_registry import registry
from app.mcp_registry.mcp_client import Endpoint
from app.vault import connections


def vault_resolver(tenant_key: str, user_id: str) -> Callable[[str], Awaitable[str | None]]:
    """Build the `resolve_token` a RunContext needs, for one person in one company."""

    async def resolve(server_name: str) -> str | None:
        async with tenant_session(tenant_key, user_id) as session:
            return await connections.use(
                session, tenant=tenant_key, user_id=user_id, server_name=server_name
            )

    return resolve


def registry_endpoints(tenant_key: str, user_id: str) -> Callable[[str], Awaitable[Endpoint | None]]:
    """Server name -> Endpoint, from what this person can see (mine > company > everyone)."""

    async def resolve(server_name: str) -> Endpoint | None:
        async with tenant_session(tenant_key, user_id) as session:
            return await registry.endpoint_for(session, server_name)

    return resolve


def registry_disabled(tenant_key: str, user_id: str) -> Callable[[], Awaitable[set[str]]]:
    """The tool refs the admin switched off, as this person sees the registry."""

    async def resolve() -> set[str]:
        async with tenant_session(tenant_key, user_id) as session:
            return await registry.disabled_refs(session)

    return resolve
