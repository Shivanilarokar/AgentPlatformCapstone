"""MCP Registry endpoints.

    GET  /v1/servers               this company's registry: private + shared
    GET  /v1/servers/catalogue     quick-picks the platform can launch itself
    POST /v1/servers               register: name, transport, endpoint, auth type
    POST /v1/servers/{name}/refresh
    POST /v1/servers/health-sweep  run the scheduled check now (admin)

The rule this screen exists to enforce: the platform connects to a server and
asks it what tools it has. A tool name is never typed in by a human, and a
server that does not answer is not saved.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import NOT_FOUND, current_user, require_admin, tenant_db
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


class ServerOut(BaseModel):
    name: str
    transport: str
    endpoint: str
    auth_type: str
    description: str
    health: str  # ok | down
    visibility: str  # private | shared
    shared_by: str
    connected: bool
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
    #: "private" = just my workspace. "shared" = everyone; admins only.
    visibility: str = Field(default="private", pattern=r"^(private|shared)$")
    #: The remote servers (GitHub, Slack, Atlassian) refuse to list their tools
    #: without one. When given, it is used for discovery and then saved -
    #: encrypted - as this workspace's connection, so "Connect & save" means both.
    token: str | None = None


def _out(v: registry.ServerView, connected: set[str]) -> ServerOut:
    return ServerOut(
        name=v.name,
        transport=v.transport,
        endpoint=v.endpoint,
        auth_type=v.auth_type,
        description=v.description,
        health=v.health,
        visibility=v.visibility,
        shared_by=v.shared_by,
        # "connected" means an agent here can use it: a stored credential, or
        # a server that never needed one.
        connected=v.name in connected or v.auth_type == "none",
        last_checked_at=v.last_checked_at.isoformat() if v.last_checked_at else None,
        tools=[
            ToolOut(name=t.name, description=t.description, risk=t.risk)
            for t in sorted(v.tools, key=lambda t: t.name)
        ],
    )


@router.get("", response_model=list[ServerOut])
async def list_servers(db: AsyncSession = Depends(tenant_db)):
    connected = await registry.connected_servers(db)
    return [_out(v, connected) for v in await registry.list_servers(db)]


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


@router.post("", response_model=ServerOut, status_code=201)
async def register_server(
    body: RegisterIn,
    claims: Claims = Depends(current_user),
    db: AsyncSession = Depends(tenant_db),
):
    if body.visibility == "shared" and not claims.is_admin:
        raise HTTPException(403, detail={"error": "admin_only",
                                         "detail": "Only an admin can share a server with everyone."})
    try:
        view = await registry.register(
            db,
            name=body.name,
            transport=body.transport,
            endpoint=body.endpoint,
            auth_type=body.auth_type,
            credential_env_var=body.credential_env_var,
            description=body.description,
            visibility=body.visibility,
            shared_by=claims.tenant_key if body.visibility == "shared" else "",
            token=body.token,
        )
    except ValueError as exc:  # a malformed endpoint
        raise HTTPException(422, detail={"error": "bad_endpoint", "detail": str(exc)}) from None
    except AuthRequired as exc:
        # Alive, but it will not list its tools anonymously. The form shows a
        # credential field and the user tries again with one.
        raise HTTPException(401, detail={"error": "auth_required", "detail": str(exc)}) from None
    except registry.ServerUnreachable as exc:
        # Nothing was saved. A registry entry with an unverified tool list would
        # be worse than no entry at all.
        raise HTTPException(422, detail={"error": "unreachable", "detail": str(exc)}) from None

    if body.token:
        # Discovery worked with it, so it is this workspace's connection now.
        # Encrypted on the way in; the plaintext leaves visibility with this request.
        await vault.add(db, tenant=claims.tenant_key, server_name=body.name,
                        secret=body.token, added_by=claims.email)

    return _out(view, await registry.connected_servers(db))


@router.post("/{name}/refresh", response_model=ServerOut)
async def refresh_server(
    name: str, claims: Claims = Depends(current_user), db: AsyncSession = Depends(tenant_db)
):
    """Re-check now, with this workspace's own credential if it has one."""
    token = await vault.use(db, tenant=claims.tenant_key, server_name=name)
    try:
        await registry.refresh(db, name, token=token)
    except KeyError:
        raise HTTPException(404, detail=NOT_FOUND) from None
    await db.flush()
    connected = await registry.connected_servers(db)
    view = next((v for v in await registry.list_servers(db) if v.name == name), None)
    if view is None:
        raise HTTPException(404, detail=NOT_FOUND)
    return _out(view, connected)


@router.post("/health-sweep")
async def run_health_sweep(_: Claims = Depends(require_admin)):
    """Run the scheduled check right now, so it can be demonstrated on demand.

    The same function the background task calls every five minutes.
    """
    return await health.check_everything()
