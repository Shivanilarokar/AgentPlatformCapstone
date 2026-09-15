"""The MCP Registry.

Step 1 of the brief's flow:

    "I open MCP Registry, paste in an MCP server address, and save it. Your
     platform connects to that server and ASKS IT WHAT TOOLS IT HAS. It stores
     the answer. I do not type tool names in by hand."

So `register()` takes an ADDRESS - a URL, or a stdio command line - connects to
it over whichever transport it speaks, and refuses to save anything it could not
reach. There is no code path that writes an mcp_tools row from anything but a
live tools/list response.

TWO TABLES, ONE VIEW
--------------------
    t_<company>.mcp_servers  registered by a person: theirs alone (`private`),
                             or opened to the whole company by its admin (`company`)
    platform.mcp_servers     shared with every company - the platform admin only

`list_servers()` returns what the signed-in person can use, nearest first: a
server they registered shadows a company one of the same name, which shadows a
shared one. Row-level security does the per-person part; nothing here filters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_ import SharedServer, SharedTool
from app.models.tenant import Connection, McpServer, McpTool
from app.mcp_registry.mcp_client import AuthRequired, DiscoveredTool, Endpoint, list_tools

log = logging.getLogger(__name__)


class ServerUnreachable(Exception):
    """The server did not answer, so nothing was saved."""


def _why(exc: BaseException) -> str:
    """The innermost message. MCP wraps failures in nested ExceptionGroups."""
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]  # type: ignore[attr-defined]
    return f"{type(exc).__name__}: {str(exc)[:160]}"


@dataclass
class ServerView:
    """One row of the registry as the screen sees it, whichever table it came from."""

    name: str
    transport: str
    endpoint: str
    auth_type: str
    description: str
    health: str  # ok | down
    visibility: str  # private (mine) | company | everyone
    shared_by: str
    last_checked_at: datetime | None
    tools: list  # McpTool | SharedTool


# --------------------------------------------------------------------- read


async def list_shared(session: AsyncSession) -> list[ServerView]:
    """What the platform admin shared with every company. Works from any session:
    platform is always on the search_path."""
    shared = list(await session.scalars(select(SharedServer).order_by(SharedServer.name)))
    shared_tools = list(await session.scalars(select(SharedTool)))
    return [
        ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.health,
            "everyone", s.shared_by, s.last_checked_at,
            [t for t in shared_tools if t.server_id == s.id],
        )
        for s in shared
    ]


async def list_servers(session: AsyncSession) -> list[ServerView]:
    """What THIS person can use: shared with everyone, shared in the company,
    and their own. Nearer wins on a name clash (mine > company > everyone).

    No tenant filter and no owner filter on the private query: the gate pointed
    us at one company's schema, and row-level security hides every row that is
    neither mine nor marked "company".
    """
    mine_or_company = list(await session.scalars(select(McpServer).order_by(McpServer.name)))
    me = await session.scalar(text("SELECT NULLIF(current_setting('app.user_id', true), '')::uuid"))

    views: dict[str, ServerView] = {v.name: v for v in await list_shared(session)}
    for s in sorted(mine_or_company, key=lambda s: s.visibility == "company"):  # company first, mine overrides
        views[s.name] = ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.health,
            "private" if s.owner_id == me and s.visibility == "private" else "company",
            "", s.last_checked_at, list(s.tools),
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
    return Endpoint.parse(row.transport, row.endpoint, row.credential_env_var, row.auth_type)


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
        raise ServerUnreachable(f"could not reach {ep.display}: {_why(exc)}") from exc
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
    credential_env_var: str | None = None,
    description: str = "",
    visibility: str = "private",
    shared_by: str = "",
    token: str | None = None,
) -> ServerView:
    """Connect, ask what tools it has, store the answer. In that order.

        private   mine - the row's owner is whoever the gate signed in
        company   mine, but visible to everyone in my company (admins only)
        everyone  platform.mcp_servers - the platform admin only

    The router enforces who may pick which; row-level security enforces that
    the row is written as the signed-in person's.
    """
    ep = Endpoint.parse(transport, endpoint, credential_env_var, auth_type)
    discovered = await discover(ep, token)
    now = datetime.now(timezone.utc)

    if visibility == "everyone":
        server = await session.scalar(select(SharedServer).where(SharedServer.name == name))
        if server is None:
            server = SharedServer(name=name)
            session.add(server)
        server.shared_by = shared_by
        tool_cls, fk = SharedTool, SharedTool.server_id
    else:
        # RLS: this can only find MY row of that name (a colleague's private
        # row is invisible), so two people may each register "github".
        me = await session.scalar(text("SELECT NULLIF(current_setting('app.user_id', true), '')::uuid"))
        server = await session.scalar(
            select(McpServer).where(McpServer.name == name, McpServer.owner_id == me)
        )
        if server is None:
            server = McpServer(name=name)
            session.add(server)
        server.visibility = visibility
        tool_cls, fk = McpTool, McpTool.server_id

    server.transport = ep.transport
    server.endpoint = ep.display
    server.auth_type = auth_type
    server.credential_env_var = credential_env_var
    server.description = description
    server.health = "ok"
    server.last_checked_at = now
    await session.flush()

    await _replace_tools(session, server.id, discovered, tool_cls, fk)
    await session.flush()

    tools = list(await session.scalars(select(tool_cls).where(fk == server.id)))
    return ServerView(
        server.name, server.transport, server.endpoint, server.auth_type, server.description,
        server.health, visibility, shared_by, now, tools,
    )


async def refresh(
    session: AsyncSession, name: str, token: str | None = None, *, shared: bool = False,
    private_only: bool = False, server_id=None,
) -> str:
    """Re-ask a known server what it has; mark it down if it has gone away.

    Returns the new health. Called by the Re-check button AND by the scheduled
    health job - same function, so a dead server shows as down either way.

    `shared=True` skips the private table: the health sweep walks shared servers
    from a session that has no tenant schema in its search_path at all.
    """
    server = None
    tool_cls, fk = McpTool, McpTool.server_id
    if not shared:
        q = select(McpServer).where(McpServer.name == name)
        if server_id is not None:  # the sweep names the exact row; a person sees only theirs anyway
            q = q.where(McpServer.id == server_id)
        server = await session.scalar(q)
    if server is None and not private_only:
        server = await session.scalar(select(SharedServer).where(SharedServer.name == name))
        tool_cls, fk = SharedTool, SharedTool.server_id
    if server is None:
        raise KeyError(name)

    server.last_checked_at = datetime.now(timezone.utc)
    ep = Endpoint.parse(server.transport, server.endpoint, server.credential_env_var, server.auth_type)
    try:
        discovered = await discover(ep, token)
    except AuthRequired:
        # It answered. A 401 from a server we hold no token for is "alive",
        # which is what this check is for; the tool list stays as last seen.
        server.health = "ok"
        return "ok"
    except ServerUnreachable as exc:
        log.warning("health: %s is down - %s", name, exc)
        server.health = "down"
        return "down"

    server.health = "ok"
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
