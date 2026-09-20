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

REGISTER CREATES, UPDATE CHANGES
--------------------------------
`register()` never overwrites: a name that is already taken raises
`ServerExists`, and the only way to change a server is `update()` (the Edit
page). `update()` and `remove()` act on the caller's OWN row only - row-level
security lets a company read a colleague's "company" server, and even delete it
(DELETE is checked against the read rule), so ownership is enforced here.

THE ADMIN'S ALLOW-LIST
----------------------
Every tool a server reports is stored, but each has an `enabled` flag the
registrant sets. `list_servers()` / `list_shared()` return ENABLED tools only by
default, so everything downstream (the builder's catalogue, scoring, the
runtime) sees exactly the allow-list without knowing it exists. The Registry
screen passes `include_disabled=True`. A tool that shows up for the first time
on a later re-check is stored OFF: a vendor update must not quietly hand agents
new powers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_ import SharedServer, SharedTool
from app.models.tenant import Connection, McpServer, McpTool
from app.mcp_registry.mcp_client import AuthRequired, DiscoveredTool, Endpoint, list_tools

log = logging.getLogger(__name__)


class ServerUnreachable(Exception):
    """The server did not answer, so nothing was saved."""


class ServerExists(Exception):
    """That name is already registered here. Nothing was changed - edit it instead."""


class UnknownTool(Exception):
    """The caller named a tool the server does not have."""


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
    registered_by: str = ""  # the email of whoever registered it
    mine: bool = False  # registered by the signed-in person (or, for the platform admin, shared)
    credential_env_var: str | None = None  # stdio: the env var the subprocess reads its token from


# --------------------------------------------------------------------- read


async def list_shared(session: AsyncSession, *, include_disabled: bool = False) -> list[ServerView]:
    """What the platform admin shared with every company. Works from any session:
    platform is always on the search_path."""
    shared = list(await session.scalars(select(SharedServer).order_by(SharedServer.name)))
    q = select(SharedTool)
    if not include_disabled:
        q = q.where(SharedTool.enabled)
    shared_tools = list(await session.scalars(q))
    return [
        ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.health,
            "everyone", s.shared_by, s.last_checked_at,
            [t for t in shared_tools if t.server_id == s.id],
            registered_by=s.shared_by,
            credential_env_var=s.credential_env_var,
        )
        for s in shared
    ]


async def list_servers(session: AsyncSession, *, include_disabled: bool = False) -> list[ServerView]:
    """What THIS person can use: shared with everyone, shared in the company,
    and their own. Nearer wins on a name clash (mine > company > everyone).

    Tools the admin switched off are left out unless `include_disabled`.

    No tenant filter and no owner filter on the private query: the gate pointed
    us at one company's schema, and row-level security hides every row that is
    neither mine nor marked "company".
    """
    mine_or_company = list(await session.scalars(select(McpServer).order_by(McpServer.name)))
    me = await _me(session)

    views: dict[str, ServerView] = {
        v.name: v for v in await list_shared(session, include_disabled=include_disabled)
    }
    for s in sorted(mine_or_company, key=lambda s: s.visibility != "company"):  # company first, mine overrides
        views[s.name] = ServerView(
            s.name, s.transport, s.endpoint, s.auth_type, s.description, s.health,
            "private" if s.owner_id == me and s.visibility == "private" else "company",
            "", s.last_checked_at, [t for t in s.tools if include_disabled or t.enabled],
            registered_by=s.registered_by or "", mine=s.owner_id == me,
            credential_env_var=s.credential_env_var,
        )
    return sorted(views.values(), key=lambda v: v.name)


async def _me(session: AsyncSession):
    """Who the gate signed in, as row-level security sees them."""
    return await session.scalar(text("SELECT NULLIF(current_setting('app.user_id', true), '')::uuid"))


async def disabled_refs(session: AsyncSession) -> set[str]:
    """`server.tool` refs the admin switched off, for the servers THIS person
    sees (nearest wins, so a shadowing server's own switches count). The
    runtime removes these from any agent, however old."""
    views = await list_servers(session, include_disabled=True)
    return {f"{v.name}.{t.name}" for v in views for t in v.tools if not t.enabled}


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


async def preview(
    transport: str, endpoint: str, auth_type: str = "none",
    credential_env_var: str | None = None, token: str | None = None,
) -> list[DiscoveredTool]:
    """Connect and list the tools, saving NOTHING - not the server, not the token.
    Step one of the two-step form: the admin sees what is there and picks."""
    ep = Endpoint.parse(transport, endpoint, credential_env_var, auth_type)
    return await discover(ep, token)


def _chosen(enabled_tools: list[str] | None, known: set[str]) -> Callable[[str], bool]:
    """Who is switched on. None = everything (API callers that never heard of the
    picker). A name the server does not have is an error, not a silent skip."""
    if enabled_tools is None:
        return lambda _name: True
    unknown = set(enabled_tools) - known
    if unknown:
        raise UnknownTool(f"this server has no tool named {', '.join(sorted(unknown))}")
    return set(enabled_tools).__contains__


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
    enabled_tools: list[str] | None = None,
) -> ServerView:
    """Connect, ask what tools it has, store the answer. In that order.

    `enabled_tools` is the registrant's pick from `preview()`. All the tools are
    stored; only these are switched on. None switches them all on.

        private   mine - the row's owner is whoever the gate signed in
        company   mine, but visible to everyone in my company (admins only)
        everyone  platform.mcp_servers - the platform admin only

    The router enforces who may pick which; row-level security enforces that
    the row is written as the signed-in person's.
    """
    ep = Endpoint.parse(transport, endpoint, credential_env_var, auth_type)

    # Cheap check first, so a taken name is refused before we open a network
    # connection. The unique constraint below still catches a race.
    if visibility == "everyone":
        taken = await session.scalar(select(SharedServer.id).where(SharedServer.name == name))
    else:
        # RLS: this can only find MY row of that name (a colleague's private
        # row is invisible), so two people may each register "github".
        taken = await session.scalar(
            select(McpServer.id).where(McpServer.name == name, McpServer.owner_id == await _me(session))
        )
    if taken is not None:
        raise ServerExists(name)

    discovered = await discover(ep, token)
    enable = _chosen(enabled_tools, {t.name for t in discovered})
    now = datetime.now(timezone.utc)

    if visibility == "everyone":
        server = SharedServer(name=name, shared_by=shared_by)
        tool_cls, fk = SharedTool, SharedTool.server_id
    else:
        server = McpServer(name=name, visibility=visibility)
        tool_cls, fk = McpTool, McpTool.server_id
    session.add(server)

    server.transport = ep.transport
    server.endpoint = ep.display
    server.auth_type = auth_type
    server.credential_env_var = credential_env_var
    server.description = description
    server.health = "ok"
    server.last_checked_at = now
    try:
        await session.flush()
    except IntegrityError as exc:  # two requests raced past the check above
        raise ServerExists(name) from exc

    await _replace_tools(session, server.id, discovered, tool_cls, fk, enable_new=enable)
    await session.flush()

    tools = list(await session.scalars(select(tool_cls).where(fk == server.id)))
    return ServerView(
        server.name, server.transport, server.endpoint, server.auth_type, server.description,
        server.health, visibility, shared_by, now, tools, mine=True,
        credential_env_var=server.credential_env_var,
    )


async def _own_row(session: AsyncSession, name: str, shared: bool):
    """The one row this caller may change: their own server, or - for the
    platform admin - the shared one. KeyError if there is none.

    Never looks a row up by name alone: a colleague's "company" server is
    visible to me, and must not be editable or deletable by me.
    """
    if shared:
        row = await session.scalar(select(SharedServer).where(SharedServer.name == name))
        tool_cls, fk = SharedTool, SharedTool.server_id
    else:
        row = await session.scalar(
            select(McpServer).where(McpServer.name == name, McpServer.owner_id == await _me(session))
        )
        tool_cls, fk = McpTool, McpTool.server_id
    if row is None:
        raise KeyError(name)
    return row, tool_cls, fk


async def update(
    session: AsyncSession,
    name: str,
    *,
    shared: bool = False,
    fields: dict,
    token: str | None = None,
    rediscover: bool = False,
    enabled_tools: list[str] | None = None,
) -> ServerView:
    """Change a server I own. The name is the identity and does not change.

    `fields` holds only what the caller sent: transport, endpoint, auth_type,
    credential_env_var, description, visibility. If anything about HOW to reach
    the server changed (or `rediscover`), we connect and ask again first, and
    save nothing when it does not answer - the same rule as register. A
    description, visibility or tool-selection change needs no network at all, so
    it still works when the server is down or its token has expired.

    `enabled_tools` replaces the allow-list: the named tools on, every other
    stored tool off. Tools first seen during this edit's re-connect start off,
    then this list has the last word.
    """
    server, tool_cls, fk = await _own_row(session, name, shared)

    ep = Endpoint.parse(
        fields.get("transport", server.transport),
        fields.get("endpoint", server.endpoint),
        fields.get("credential_env_var", server.credential_env_var),
        fields.get("auth_type", server.auth_type),
    )
    reach_changed = rediscover or (
        (ep.transport, ep.display, ep.auth_type, ep.credential_env_var)
        != (server.transport, server.endpoint, server.auth_type, server.credential_env_var)
    )

    discovered = await discover(ep, token) if reach_changed else None  # raises before anything is touched

    if "description" in fields:
        server.description = fields["description"]
    if "visibility" in fields and not shared:
        server.visibility = fields["visibility"]
    if reach_changed:
        server.transport = ep.transport
        server.endpoint = ep.display
        server.auth_type = ep.auth_type
        server.credential_env_var = ep.credential_env_var
        server.health = "ok"
        server.last_checked_at = datetime.now(timezone.utc)
    await session.flush()

    if discovered is not None:
        await _replace_tools(session, server.id, discovered, tool_cls, fk, enable_new=lambda _n: False)
        await session.flush()

    if enabled_tools is not None:
        stored = list(await session.scalars(select(tool_cls).where(fk == server.id)))
        chosen = _chosen(enabled_tools, {t.name for t in stored})
        for t in stored:
            t.enabled = chosen(t.name)
        await session.flush()

    tools = list(await session.scalars(select(tool_cls).where(fk == server.id)))
    return ServerView(
        server.name, server.transport, server.endpoint, server.auth_type, server.description,
        server.health, "everyone" if shared else server.visibility,
        server.shared_by if shared else "", server.last_checked_at, tools, mine=True,
        registered_by=server.shared_by if shared else server.registered_by or "",
        credential_env_var=server.credential_env_var,
    )


async def remove(session: AsyncSession, name: str, *, shared: bool = False) -> None:
    """Delete a server I own, and its tools. KeyError if it is not mine.

    Connections (stored tokens) are left alone: they belong to people, not to
    the server row, and a person revokes their own on the Connections page.
    Agents that use it are not rewritten: when one next runs, the compiler
    finds the server unregistered, logs it and carries on without that
    server's tools (runtime/compiler.py, _tool_schemas).
    """
    server, _, _ = await _own_row(session, name, shared)
    if shared:
        # platform.mcp_tools has no ORM relationship; delete its rows explicitly.
        await session.execute(delete(SharedTool).where(SharedTool.server_id == server.id))
    await session.delete(server)  # McpServer.tools cascades through the ORM
    await session.flush()


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
    await _replace_tools(session, server.id, discovered, tool_cls, fk, enable_new=lambda _n: False)
    return "ok"


async def _replace_tools(
    session, server_id, discovered, tool_cls, fk, *, enable_new: Callable[[str], bool],
) -> None:
    """The server's answer is the truth. Tools it no longer reports are dropped.

    A tool already stored keeps its `enabled` flag - the admin's choice survives
    every re-check. Only a tool seen for the first time gets `enable_new(name)`.

    Re-deriving risk on every refresh matters: a tool that gains a destructive
    verb tightens automatically, and cannot be loosened by editing a config.
    """
    existing = {t.name: t for t in await session.scalars(select(tool_cls).where(fk == server_id))}

    seen: set[str] = set()
    for tool in discovered:
        seen.add(tool.name)
        row = existing.get(tool.name) or tool_cls(
            server_id=server_id, name=tool.name, enabled=enable_new(tool.name))
        row.description = tool.description
        row.input_schema = tool.input_schema
        row.risk = str(tool.risk)
        session.add(row)

    for name, row in existing.items():
        if name not in seen:
            await session.delete(row)
