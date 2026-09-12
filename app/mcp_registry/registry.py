"""The MCP Registry.

Step 1 of the brief's flow:

    "I open MCP Registry, paste in an MCP server address, and save it. Your
     platform connects to that server and ASKS IT WHAT TOOLS IT HAS. It stores
     the answer. I do not type tool names in by hand."

So `register()` takes an ADDRESS - a URL, or a stdio command line - connects to
it over whichever transport it speaks, and refuses to save anything it could not
reach. There is no code path that writes an mcp_tools row from anything but a
live tools/list response.

TWO REGISTRIES, ONE VIEW
------------------------
    t_<tenant>.mcp_servers   private to one company
    platform.mcp_servers     shared with every company (admin-only to create)

`list_servers()` returns the union. A private server shadows a shared one with
the same name, so a company can override a shared entry with its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_ import SharedServer, SharedTool
from app.models.tenant import Connection, McpServer, McpTool
from app.mcp_registry.mcp_client import AuthRequired, DiscoveredTool, Endpoint, list_tools

log = logging.getLogger(__name__)


class ServerUnreachable(Exception):
    """The server did not answer, so nothing was saved."""


@dataclass
class ServerView:
    """One row of the registry as the screen sees it, whichever table it came from."""

    name: str
    transport: str
    endpoint: str
    auth_type: str
    description: str
    status: str
    scope: str  # private | shared
    shared_by: str
    last_checked_at: datetime | None
    tools: list  # McpTool | SharedTool


# --------------------------------------------------------------------- read


async def list_servers(session: AsyncSession) -> list[ServerView]:
    """This company's servers, plus everything shared. Private wins on a clash.

    No tenant filter on the private query: the gate already pointed us at one
    company's schema. The shared query names platform explicitly, which is the
    one schema that is allowed.
    """
    private = list(await session.scalars(select(McpServer).order_by(McpServer.name)))
    shared = list(await session.scalars(select(SharedServer).order_by(SharedServer.name)))
    shared_tools = list(await session.scalars(select(SharedTool)))

    views: dict[str, ServerView] = {}
    for s in shared:
        views[s.name] = ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.status,
            "shared", s.shared_by, s.last_checked_at,
            [t for t in shared_tools if t.server_id == s.id],
        )
    for s in private:
        views[s.name] = ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.status,
            "private", "", s.last_checked_at, list(s.tools),
        )
    return sorted(views.values(), key=lambda v: v.name)


async def connected_servers(session: AsyncSession) -> set[str]:
    rows = await session.scalars(select(Connection).where(Connection.status == "active"))
    return {c.server_name for c in rows}


async def endpoint_for(session: AsyncSession, name: str) -> Endpoint | None:
    """How the runtime reaches a server this company can see."""
    row = await session.scalar(select(McpServer).where(McpServer.name == name))
    if row is None:
        row = await session.scalar(select(SharedServer).where(SharedServer.name == name))
    if row is None:
        return None
    return Endpoint.parse(row.transport, row.endpoint, row.token_env, row.auth_type)


# -------------------------------------------------------------------- write


async def discover(ep: Endpoint, token: str | None) -> list[DiscoveredTool]:
    """Connect and ask. Raises rather than returning nothing.

    AuthRequired passes straight through: the server is alive, it just wants a
    credential first. Everything else collapses to ServerUnreachable.
    """
    try:
        found = await list_tools(ep, token=token)
    except AuthRequired:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure means "do not save"
        raise ServerUnreachable(f"could not reach {ep.display}: {type(exc).__name__}") from exc
    if not found:
        raise ServerUnreachable(f"{ep.display} answered but reported no tools")
    return found


async def register(
    session: AsyncSession,
    *,
    name: str,
    transport: str,
    endpoint: str,
    auth_type: str = "none",
    token_env: str | None = None,
    description: str = "",
    scope: str = "private",
    shared_by: str = "",
    token: str | None = None,
) -> ServerView:
    """Connect, ask what tools it has, store the answer. In that order.

    `scope="shared"` writes to platform.mcp_servers instead of this company's
    schema. The router only allows that for admins.
    """
    ep = Endpoint.parse(transport, endpoint, token_env, auth_type)
    discovered = await discover(ep, token)
    now = datetime.now(timezone.utc)

    if scope == "shared":
        server = await session.scalar(select(SharedServer).where(SharedServer.name == name))
        if server is None:
            server = SharedServer(name=name)
            session.add(server)
        server.shared_by = shared_by
        tool_cls, fk = SharedTool, SharedTool.server_id
    else:
        server = await session.scalar(select(McpServer).where(McpServer.name == name))
        if server is None:
            server = McpServer(name=name)
            session.add(server)
        server.scope = "private"
        tool_cls, fk = McpTool, McpTool.server_id

    server.transport = ep.transport
    server.endpoint = ep.display
    server.auth_type = auth_type
    server.token_env = token_env
    server.description = description
    server.status = "ok"
    server.last_checked_at = now
    await session.flush()

    await _replace_tools(session, server.id, discovered, tool_cls, fk)
    await session.flush()

    tools = list(await session.scalars(select(tool_cls).where(fk == server.id)))
    return ServerView(
        server.name, server.transport, server.endpoint, server.auth_type, server.description,
        server.status, scope, shared_by, now, tools,
    )


async def refresh(
    session: AsyncSession, name: str, token: str | None = None, *, shared: bool = False
) -> str:
    """Re-ask a known server what it has; mark it down if it has gone away.

    Returns the new status. Called by the Re-check button AND by the scheduled
    health job - same function, so a dead server shows as down either way.

    `shared=True` skips the private table: the health sweep walks shared servers
    from a session that has no tenant schema in its search_path at all.
    """
    server = None
    tool_cls, fk = McpTool, McpTool.server_id
    if not shared:
        server = await session.scalar(select(McpServer).where(McpServer.name == name))
    if server is None:
        server = await session.scalar(select(SharedServer).where(SharedServer.name == name))
        tool_cls, fk = SharedTool, SharedTool.server_id
    if server is None:
        raise KeyError(name)

    server.last_checked_at = datetime.now(timezone.utc)
    ep = Endpoint.parse(server.transport, server.endpoint, server.token_env, server.auth_type)
    try:
        discovered = await discover(ep, token)
    except AuthRequired:
        # It answered. A 401 from a server we hold no token for is "alive",
        # which is what this check is for; the tool list stays as last seen.
        server.status = "ok"
        return "ok"
    except ServerUnreachable:
        server.status = "down"
        return "down"

    server.status = "ok"
    await _replace_tools(session, server.id, discovered, tool_cls, fk)
    return "ok"


async def _replace_tools(session, server_id, discovered, tool_cls, fk) -> None:
    """The server's answer is the truth. Tools it no longer reports are dropped.

    Re-deriving risk on every refresh matters: a tool that gains a destructive
    verb tightens automatically, and cannot be loosened by editing a config.
    """
    existing = {t.name: t for t in await session.scalars(select(tool_cls).where(fk == server_id))}

    seen: set[str] = set()
    for tool in discovered:
        seen.add(tool.name)
        row = existing.get(tool.name) or tool_cls(server_id=server_id, name=tool.name)
        row.description = tool.description
        row.input_schema = tool.input_schema
        row.risk = str(tool.risk)
        session.add(row)

    for name, row in existing.items():
        if name not in seen:
            await session.delete(row)
