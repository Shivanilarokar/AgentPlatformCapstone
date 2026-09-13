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
from app.mcp_registry.catalogue import CATALOGUE
from app.vault import connections

router = APIRouter(prefix="/v1/connections", tags=["connections"])


class ConnectionOut(BaseModel):
    server_name: str
    status: str
    added_by: str
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
async def list_connections(db: AsyncSession = Depends(tenant_db)):
    return [_out(c) for c in await connections.list_connections(db)]


@router.post("", response_model=ConnectionOut, status_code=201)
async def add_connection(
    body: AddIn,
    claims: Claims = Depends(current_user),
    db: AsyncSession = Depends(tenant_db),
):
    if body.server_name not in CATALOGUE:
        raise HTTPException(404, detail=NOT_FOUND)

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
async def revoke_connection(server_name: str, db: AsyncSession = Depends(tenant_db)):
    """Revoke, do not delete. Agents that used it must degrade, not vanish."""
    try:
        conn = await connections.revoke(db, server_name)
    except KeyError:
        raise HTTPException(404, detail=NOT_FOUND) from None
    return _out(conn)
