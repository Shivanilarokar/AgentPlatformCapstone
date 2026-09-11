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
from app.registry import health, service
from app.registry.catalogue import CATALOGUE

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
    status: str
    scope: str  # private | shared
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
    token_env: str | None
    description: str
    homepage: str


class RegisterIn(BaseModel):
    """Exactly the form on the mockup: name, transport, endpoint, auth type."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50, description="name")
    transport: str = Field(pattern=r"^(stdio|http|sse)$")
    endpoint: str = Field(min_length=1, max_length=500,
                          description="a URL, or a stdio command line")
    auth_type: str = Field(default="none", pattern=r"^(none|api_key|oauth)$")
    #: stdio + api_key: which env var the subprocess reads its credential from
    token_env: str | None = Field(default=None, max_length=80)
    description: str = Field(default="", max_length=500)
    #: "private" = just my workspace. "shared" = everyone; admins only.
    visibility: str = Field(default="private", pattern=r"^(private|shared)$")
    #: Some servers refuse to list their tools without one. Used for this call
    #: only - storing it is the Connections screen's job.
    token: str | None = None


def _out(v: service.ServerView, connected: set[str]) -> ServerOut:
    return ServerOut(
        name=v.name,
        transport=v.transport,
        endpoint=v.endpoint,
        auth_type=v.auth_type,
        description=v.description,
        status=v.status,
        scope=v.scope,
        shared_by=v.shared_by,
        connected=v.name in connected,
        last_checked_at=v.last_checked_at.isoformat() if v.last_checked_at else None,
        tools=[
            ToolOut(name=t.name, description=t.description, risk=t.risk)
            for t in sorted(v.tools, key=lambda t: t.name)
        ],
    )


@router.get("", response_model=list[ServerOut])
async def list_servers(db: AsyncSession = Depends(tenant_db)):
    connected = await service.connected_slugs(db)
    return [_out(v, connected) for v in await service.list_servers(db)]


@router.get("/catalogue", response_model=list[CatalogueOut])
async def catalogue():
    """Real open-source MCP servers the platform can launch. Pre-fills the form.

    In a hosted product you would only ever paste an address. Here these exist
    so a clean `docker compose up` has something to register on screen one.
    """
    return [
        CatalogueOut(
            name=s.name, transport="stdio", endpoint=s.endpoint, auth_type=s.auth_type,
            token_env=s.token_env, description=s.description, homepage=s.homepage,
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
        view = await service.register(
            db,
            name=body.name,
            transport=body.transport,
            endpoint=body.endpoint,
            auth_type=body.auth_type,
            token_env=body.token_env,
            description=body.description,
            scope=body.visibility,
            shared_by=claims.tenant_slug if body.visibility == "shared" else "",
            token=body.token,
        )
    except ValueError as exc:  # a malformed endpoint
        raise HTTPException(422, detail={"error": "bad_endpoint", "detail": str(exc)}) from None
    except service.ServerUnreachable as exc:
        # Nothing was saved. A registry entry with an unverified tool list would
        # be worse than no entry at all.
        raise HTTPException(422, detail={"error": "unreachable", "detail": str(exc)}) from None

    return _out(view, await service.connected_slugs(db))


@router.post("/{name}/refresh", response_model=ServerOut)
async def refresh_server(name: str, token: str | None = None, db: AsyncSession = Depends(tenant_db)):
    try:
        await service.refresh(db, name, token=token)
    except KeyError:
        raise HTTPException(404, detail=NOT_FOUND) from None
    await db.flush()
    connected = await service.connected_slugs(db)
    view = next((v for v in await service.list_servers(db) if v.name == name), None)
    if view is None:
        raise HTTPException(404, detail=NOT_FOUND)
    return _out(view, connected)


@router.post("/health-sweep")
async def run_health_sweep(_: Claims = Depends(require_admin)):
    """Run the scheduled check right now, so it can be demonstrated on demand.

    The same function the background task calls every five minutes.
    """
    return await health.check_everything()
