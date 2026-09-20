"""Connections endpoints.

Note what is missing from this file: there is no GET that returns a secret, and
no response model with a field that could hold one. `ConnectionOut` shows a row
of dots because that is genuinely all the platform will ever tell you.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import NOT_FOUND, current_user, tenant_db
from app.core.security import Claims
from app.mcp_registry import registry
from app.mcp_registry.catalogue import CATALOGUE
from app.mcp_registry.mcp_client import AuthRequired
from app.vault import connections

router = APIRouter(prefix="/v1/connections", tags=["connections"])


class ConnectionOut(BaseModel):
    server_name: str
    status: str
    added_by: str | None = None
    created_at: str
    last_used_at: str | None
    #: Always literally this. There is no code path that fills it with anything.
    secret: str = "••••••••••••"


class AddIn(BaseModel):
    server_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=50)
    secret: str = Field(min_length=1, max_length=4096)


def _out(c) -> ConnectionOut:
    return ConnectionOut(
        server_name=c.server_name,
        status=c.status,
        added_by=c.added_by,
        created_at=c.created_at.isoformat() if c.created_at else "",
        last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(db: AsyncSession = Depends(tenant_db, scope="function")):
    return [_out(c) for c in await connections.list_connections(db)]


@router.post("", response_model=ConnectionOut, status_code=201)
async def add_connection(
    body: AddIn,
    claims: Claims = Depends(current_user),
    db: AsyncSession = Depends(tenant_db, scope="function"),
):
    if body.server_name not in CATALOGUE:
        raise HTTPException(404, detail=NOT_FOUND)

    # Prove the token works BEFORE it is stored. Without this, a mistyped or
    # revoked token saves as an "active" connection and only fails later, in the
    # middle of an agent's run. Same check the registry form makes: connect to
    # the server with it and ask for the tools. Nothing is saved if that fails.
    #
    # Skipped when there is nothing to check against: no registered server of
    # that name yet (an installed agent's connection can be added first), or a
    # server that needs no credential. A stdio server may also accept any token
    # at this stage; only a server that checks credentials up front can refuse one.
    ep = await registry.endpoint_for(db, body.server_name)
    if ep is not None and ep.needs_token:
        try:
            await registry.discover(ep, body.secret)
        except AuthRequired as exc:
            raise HTTPException(401, detail={"error": "credential_rejected", "detail": str(exc)}) from None
        except registry.ServerUnreachable as exc:
            raise HTTPException(422, detail={
                "error": "unreachable",
                "detail": f"Could not check the credential, so it was not saved. {exc}",
            }) from None

    conn = await connections.add(
        db,
        tenant=claims.tenant_key,
        user_id=claims.user_id,
        server_name=body.server_name,
        secret=body.secret,
        added_by=claims.email,
    )
    # body.secret goes out of scope here and is never referenced again.
    return _out(conn)


@router.delete("/{server_name}", response_model=ConnectionOut)
async def revoke_connection(server_name: str, db: AsyncSession = Depends(tenant_db, scope="function")):
    """Revoke, do not delete. Agents that used it must degrade, not vanish."""
    try:
        conn = await connections.revoke(db, server_name)
    except KeyError:
        raise HTTPException(404, detail=NOT_FOUND) from None
    return _out(conn)
