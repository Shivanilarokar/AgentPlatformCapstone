"""Wiring the vault into a run.

The runtime knows nothing about databases. It is handed a callable that turns a
server name into that tenant's token, and this is the only implementation of it
that touches the vault.

One session per call, deliberately: the token exists between `use()` returning
and `del token` in the tool wrapper, and not one moment longer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.db import tenant_session
from app.mcp_registry import registry
from app.mcp_registry.catalogue import CATALOGUE
from app.mcp_registry.mcp_client import Endpoint
from app.vault import connections


def vault_resolver(tenant_slug: str) -> Callable[[str], Awaitable[str | None]]:
    """Build the `resolve_token` a RunContext needs, for one company."""

    async def resolve(server_name: str) -> str | None:
        async with tenant_session(tenant_slug) as session:
            return await connections.use(session, tenant=tenant_slug, server_name=server_name)

    return resolve


def static_resolver(tokens: dict[str, str]) -> Callable[[str], Awaitable[str | None]]:
    """For scripts and tests that have no database. Never used by the app."""

    async def resolve(server_name: str) -> str | None:
        return tokens.get(server_name)

    return resolve


def registry_endpoints(tenant_slug: str) -> Callable[[str], Awaitable[Endpoint | None]]:
    """Server name -> Endpoint, from this company's registry (private first, then shared)."""

    async def resolve(server_name: str) -> Endpoint | None:
        async with tenant_session(tenant_slug) as session:
            return await registry.endpoint_for(session, server_name)

    return resolve


def catalogue_endpoints() -> Callable[[str], Awaitable[Endpoint | None]]:
    """For scripts with no registry: the servers the platform can launch itself."""

    async def resolve(server_name: str) -> Endpoint | None:
        spec = CATALOGUE.get(server_name)
        return spec.to_endpoint() if spec else None

    return resolve
