"""MCP Registry endpoints.

    GET  /v1/servers               what I can use: mine + my company's + everyone's
    GET  /v1/servers/catalogue     quick-picks: the vendors' own servers
    POST /v1/servers/discover      connect and LIST the tools; saves nothing
    POST /v1/servers               register: name, transport, endpoint, auth type,
                                   and which tools to switch on (`enabled_tools`)
                                   (409 if the name is taken - it never overwrites)
    PATCH /v1/servers/{name}       edit a server I own            (admins)
    DELETE /v1/servers/{name}      delete a server I own          (admins)
    POST /v1/servers/{name}/refresh
    POST /v1/servers/health-sweep  run the scheduled check now (platform admin)

Who may register with which visibility:
    private   anyone with a workspace       -> t_<company>.mcp_servers, owner = me
    company   the company's admin           -> same table, marked company
    everyone  the platform admin only       -> platform.mcp_servers

Who may edit or delete: an admin, and only a server they own - a company admin
their own rows (which they can also flip between private and company), the
platform admin the shared ones. Seeing a colleague's company server is not
owning it.

The rule this screen exists to enforce: the platform connects to a server and
asks it what tools it has. A tool name is never typed in by a human, and a
server that does not answer is not saved.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import NOT_FOUND, current_user, require_platform_admin
from app.core.db import platform_session, tenant_session
from app.core.security import Claims
from app.mcp_registry import health, registry
from app.mcp_registry.catalogue import CATALOGUE
from app.mcp_registry.mcp_client import AuthRequired
from app.vault import connections as vault

router = APIRouter(prefix="/v1/servers", tags=["registry"])


class ToolOut(BaseModel):
    name: str
    description: str
    risk: str
    #: on the admin's allow-list? Off = stored, but no agent can use it.
    enabled: bool = True


class DiscoverIn(BaseModel):
    """What the form knows before it saves anything: how to reach the server."""

    transport: str = Field(pattern=r"^(stdio|http|sse)$")
    endpoint: str = Field(min_length=1, max_length=500)
    auth_type: str = Field(default="none", pattern=r"^(none|api_key|oauth)$")
    credential_env_var: str | None = Field(default=None, max_length=80)
    token: str | None = None


class DiscoveredOut(BaseModel):
    name: str
    description: str
    risk: str
    #: what the picker ticks to begin with: read-only tools
    suggested: bool
    #: may THIS caller switch it on? An ordinary user may not enable a
    #: destructive tool - the picker shows it, greyed out.
    selectable: bool


class ServerOut(BaseModel):
    name: str
    transport: str
    endpoint: str
    auth_type: str
    credential_env_var: str | None
    description: str
    health: str  # ok | down
    visibility: str  # private | company | everyone
    shared_by: str
    registered_by: str
    mine: bool
    connected: bool
    #: may THIS caller edit / delete it? An admin, and only their own server.
    editable: bool
    last_checked_at: str | None
    tools: list[ToolOut]


class CatalogueOut(BaseModel):
    """A quick-pick: fills the form in, nothing more."""

    name: str
    transport: str
    endpoint: str
    auth_type: str
    credential_env_var: str | None
    description: str
    credential_hint: str
    homepage: str


class RegisterIn(BaseModel):
    """Exactly the form on the mockup: name, transport, endpoint, auth type."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50, description="name")
    transport: str = Field(pattern=r"^(stdio|http|sse)$")
    endpoint: str = Field(min_length=1, max_length=500,
                          description="a URL, or a stdio command line")
    auth_type: str = Field(default="none", pattern=r"^(none|api_key|oauth)$")
    #: stdio + api_key: which env var the subprocess reads its credential from
    credential_env_var: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=500)
    #: "private" = just me. "company" = my company (admin). "everyone" = platform admin.
    visibility: str = Field(default="private", pattern=r"^(private|company|everyone)$")
    #: The remote servers (GitHub, Slack, Atlassian) refuse to list their tools
    #: without one. When given, it is used for discovery and then saved -
    #: encrypted - as MY connection, so "Connect & save" means both.
    token: str | None = None
    #: The tools to switch on, from the picker. Every tool is stored either
    #: way. Omit it to switch them all on.
    enabled_tools: list[str] | None = None


class PatchIn(BaseModel):
    """The Edit page. Everything optional: only what is sent changes. The name
    is not here - it is the server's identity, and agents refer to it."""

    transport: str | None = Field(default=None, pattern=r"^(stdio|http|sse)$")
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)
    auth_type: str | None = Field(default=None, pattern=r"^(none|api_key|oauth)$")
    credential_env_var: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    #: private <-> company for a company admin. The platform admin's shared
    #: servers stay "everyone": they live in another table.
    visibility: str | None = Field(default=None, pattern=r"^(private|company|everyone)$")
    #: A new credential. It is tried against the server first, then replaces
    #: MY saved connection. Leave it out to keep the one already saved.
    token: str | None = None
    #: The allow-list: these tools on, every other stored tool off. Leave it
    #: out to leave the tools as they are. Needs no network.
    enabled_tools: list[str] | None = None


def _session(claims: Claims):
    """The platform admin has no company, so only the shared schema exists for
    them. Everyone else gets their company's schema and their own rows."""
    if claims.is_platform_admin:
        return platform_session()
    return tenant_session(claims.tenant_key, claims.user_id)


def _editable(claims: Claims, v: registry.ServerView) -> bool:
    """The platform admin sees only shared servers, all of them theirs. A
    company admin edits the rows they own, not a colleague's company server."""
    return claims.is_platform_admin or (claims.is_company_admin and v.mine)


def _may_enable(claims: Claims, risk: str) -> bool:
    """Destructive tools are switched on by an admin only; an ordinary user
    picks from the read and write tools."""
    return risk != "destructive" or claims.is_platform_admin or claims.is_company_admin


def _require_admin(claims: Claims) -> None:
    if not (claims.is_platform_admin or claims.is_company_admin):
        raise HTTPException(403, detail={
            "error": "not_allowed",
            "detail": "Only an admin can edit or delete a server.",
        })


@contextmanager
def _as_http_errors():
    """Turn what registry raises into what the screen understands."""
    try:
        yield
    except registry.DestructiveNotAllowed as exc:
        raise HTTPException(403, detail={"error": "destructive_not_allowed", "detail": str(exc)}) from None
    except registry.UnknownTool as exc:
        raise HTTPException(422, detail={"error": "unknown_tool", "detail": str(exc)}) from None
    except registry.ServerExists as exc:
        raise HTTPException(409, detail={
            "error": "already_exists",
            "detail": f"A server named '{exc}' is already registered. Edit it instead.",
        }) from None
    except ValueError as exc:  # a malformed endpoint
        raise HTTPException(422, detail={"error": "bad_endpoint", "detail": str(exc)}) from None
    except AuthRequired as exc:
        # Alive, but it will not list its tools anonymously - or it refused
        # the credential it was given. The form shows which.
        code = "credential_rejected" if exc.had_token else "auth_required"
        raise HTTPException(401, detail={"error": code, "detail": str(exc)}) from None
    except registry.ServerUnreachable as exc:
        # Nothing was saved. A registry entry with an unverified tool list would
        # be worse than no entry at all.
        raise HTTPException(422, detail={"error": "unreachable", "detail": str(exc)}) from None


def _out(v: registry.ServerView, connected: set[str], claims: Claims) -> ServerOut:
    return ServerOut(
        name=v.name,
        transport=v.transport,
        endpoint=v.endpoint,
        auth_type=v.auth_type,
        credential_env_var=v.credential_env_var,
        description=v.description,
        health=v.health,
        visibility=v.visibility,
        shared_by=v.shared_by,
        registered_by=v.registered_by,
        mine=v.mine,
        # "connected" means an agent here can use it: a stored credential, or
        # a server that never needed one.
        connected=v.name in connected or v.auth_type == "none",
        editable=_editable(claims, v),
        last_checked_at=v.last_checked_at.isoformat() if v.last_checked_at else None,
        tools=[
            ToolOut(name=t.name, description=t.description, risk=t.risk, enabled=t.enabled)
            for t in sorted(v.tools, key=lambda t: t.name)
        ],
    )


@router.get("", response_model=list[ServerOut])
async def list_servers(claims: Claims = Depends(current_user)):
    async with _session(claims) as db:
        if claims.is_platform_admin:
            return [_out(v, set(), claims)
                    for v in await registry.list_shared(db, include_disabled=True)]
        connected = await registry.connected_servers(db)
        return [_out(v, connected, claims)
                for v in await registry.list_servers(db, include_disabled=True)]


@router.get("/catalogue", response_model=list[CatalogueOut])
async def catalogue():
    """The vendors' own MCP servers, as addresses. Pre-fills the form, nothing more."""
    return [
        CatalogueOut(
            name=s.name, transport=s.transport, endpoint=s.endpoint, auth_type=s.auth_type,
            credential_env_var=s.credential_env_var, description=s.description,
            credential_hint=s.credential_hint, homepage=s.homepage,
        )
        for s in CATALOGUE.values()
    ]


@router.post("/discover", response_model=list[DiscoveredOut])
async def discover_tools(body: DiscoverIn, claims: Claims = Depends(current_user)):
    """Step one of connecting: reach the server and list its tools. Saves
    nothing - not the server, not the token - so the admin can look at what is
    there and tick what agents may use before anything is stored."""
    with _as_http_errors():
        found = await registry.preview(
            body.transport, body.endpoint, body.auth_type, body.credential_env_var, body.token,
        )
    return [
        DiscoveredOut(name=t.name, description=t.description[:300], risk=str(t.risk),
                      suggested=str(t.risk) == "read",
                      selectable=_may_enable(claims, str(t.risk)))
        for t in sorted(found, key=lambda t: t.name)
    ]


@router.post("", response_model=ServerOut, status_code=201)
async def register_server(body: RegisterIn, claims: Claims = Depends(current_user)):
    allowed = {
        "everyone": claims.is_platform_admin,
        "company": claims.is_company_admin,
        "private": not claims.is_platform_admin,
    }[body.visibility]
    if not allowed:
        raise HTTPException(403, detail={"error": "not_allowed", "detail": {
            "everyone": "Only the platform admin can share a server with every company.",
            "company": "Only your company's admin can share a server with the whole company.",
            "private": "The platform admin has no workspace of their own.",
        }[body.visibility]})

    async with _session(claims) as db:
        with _as_http_errors():
            view = await registry.register(
                db,
                name=body.name,
                transport=body.transport,
                endpoint=body.endpoint,
                auth_type=body.auth_type,
                credential_env_var=body.credential_env_var,
                description=body.description,
                visibility=body.visibility,
                shared_by="platform" if body.visibility == "everyone" else "",
                token=body.token,
                enabled_tools=body.enabled_tools,
                allow_destructive=claims.is_platform_admin or claims.is_company_admin,
            )

        if body.token and not claims.is_platform_admin:
            # Discovery worked with it, so it is MY connection now. Encrypted on
            # the way in; the plaintext leaves scope with this request.
            await vault.add(db, tenant=claims.tenant_key, user_id=claims.user_id,
                            server_name=body.name, secret=body.token, added_by=claims.email)

        connected = set() if claims.is_platform_admin else await registry.connected_servers(db)
        return _out(view, connected, claims)


@router.patch("/{name}", response_model=ServerOut)
async def edit_server(name: str, body: PatchIn, claims: Claims = Depends(current_user)):
    """Change a server I own. The only way to change one: registering the same
    name again is a 409."""
    _require_admin(claims)

    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if k not in ("token", "enabled_tools") and (v is not None or k == "credential_env_var")}
    if "credential_env_var" in fields:
        fields["credential_env_var"] = fields["credential_env_var"] or None

    vis = fields.get("visibility")
    if vis == "everyone" and not claims.is_platform_admin:
        raise HTTPException(403, detail={"error": "not_allowed", "detail":
            "Only the platform admin can share a server with every company."})
    if vis is not None and vis != "everyone" and claims.is_platform_admin:
        raise HTTPException(422, detail={"error": "bad_visibility", "detail":
            "A server shared with every company stays shared. Delete it to unshare."})

    async with _session(claims) as db:
        # No new credential? Borrow the one I saved, for this one check, so a
        # changed address on a token-protected server can be verified.
        token = body.token
        if not token and not claims.is_platform_admin:
            token = await vault.use(db, tenant=claims.tenant_key, user_id=claims.user_id,
                                    server_name=name)
        try:
            with _as_http_errors():
                try:
                    view = await registry.update(
                        db, name, shared=claims.is_platform_admin, fields=fields,
                        token=token, rediscover=bool(body.token),
                        enabled_tools=body.enabled_tools,
                    )
                except KeyError:
                    raise HTTPException(404, detail=NOT_FOUND) from None
        finally:
            del token

        if body.token and not claims.is_platform_admin:
            await vault.add(db, tenant=claims.tenant_key, user_id=claims.user_id,
                            server_name=name, secret=body.token, added_by=claims.email)

        connected = set() if claims.is_platform_admin else await registry.connected_servers(db)
        return _out(view, connected, claims)


@router.delete("/{name}", status_code=204)
async def delete_server(name: str, claims: Claims = Depends(current_user)):
    """Delete a server I own, and the tools discovered from it. Stored
    connections are left for their owners to revoke."""
    _require_admin(claims)
    async with _session(claims) as db:
        try:
            await registry.remove(db, name, shared=claims.is_platform_admin)
        except KeyError:
            raise HTTPException(404, detail=NOT_FOUND) from None
    return Response(status_code=204)


@router.post("/{name}/refresh", response_model=ServerOut)
async def refresh_server(name: str, claims: Claims = Depends(current_user)):
    """Re-check now. My own server: with my credential, in my session. A server
    shared with everyone: through the platform session, the same way the
    scheduled sweep does it - a company's role may only READ that table."""
    if not claims.is_platform_admin:
        async with tenant_session(claims.tenant_key, claims.user_id) as db:
            token = await vault.use(db, tenant=claims.tenant_key, user_id=claims.user_id,
                                    server_name=name)
            try:
                await registry.refresh(db, name, token=token, private_only=True)
                await db.flush()
                mine = True
            except KeyError:
                mine = False
            finally:
                del token
    else:
        mine = False

    if not mine:
        async with platform_session() as db:
            try:
                await registry.refresh(db, name, token=None, shared=True)
            except KeyError:
                raise HTTPException(404, detail=NOT_FOUND) from None

    async with _session(claims) as db:
        views = await (registry.list_shared(db, include_disabled=True) if claims.is_platform_admin
                       else registry.list_servers(db, include_disabled=True))
        view = next((v for v in views if v.name == name), None)
        if view is None:
            raise HTTPException(404, detail=NOT_FOUND)
        connected = set() if claims.is_platform_admin else await registry.connected_servers(db)
        return _out(view, connected, claims)


@router.post("/health-sweep")
async def run_health_sweep(_: Claims = Depends(require_platform_admin)):
    """Run the scheduled check right now, so it can be demonstrated on demand.

    The same function the background task calls every five minutes.
    """
    return await health.check_everything()
