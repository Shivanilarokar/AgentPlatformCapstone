"""The tenant gate.

This module is the ONLY way to get a database session in this codebase.
Everything downstream depends on that being true, so keep it that way.

Two layers of isolation, both set on the connection rather than written into
queries, so there is no filter anywhere in a handler that could be forgotten:

    company  SET LOCAL ROLE "t_<company>"
             SET LOCAL search_path TO "t_<company>", platform
             -> the role can USE only this company's schema (plus the shared
                platform tables), so an unqualified table name can only resolve
                here, and a qualified one for another company is denied

    person   SET LOCAL app.user_id = '<uuid>'
             -> row-level security on agents / connections / mcp_servers lets
                this session see only rows this person owns (plus servers the
                company admin marked "company"). Postgres enforces it, not us.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.tenancy.schema_names import schema_for

# Windows ships ProactorEventLoop by default and psycopg's async driver cannot
# use it. Harmless no-op on Linux, so the containers are unaffected - this only
# matters when you run pytest or uvicorn directly on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def _point_at(session: AsyncSession, tenant_key: str, user_id: str | None, system: bool) -> None:
    schema = schema_for(tenant_key)  # validated; safe to interpolate
    await session.execute(text(f'SET LOCAL ROLE "{schema}"'))
    await session.execute(text(f'SET LOCAL search_path TO "{schema}", platform'))
    if system:
        # The health sweep: sees every row in the schema so it can re-check every
        # server. Never used by a request handler.
        await session.execute(text("SELECT set_config('app.role', 'system', true)"))
    else:
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(user_id)}
        )


@asynccontextmanager
async def tenant_session(
    tenant_key: str, user_id: str | None = None, *, system: bool = False
) -> AsyncIterator[AsyncSession]:
    """Open a transaction, point Postgres at ONE company and ONE person, yield.

    Why SET LOCAL and not SET: LOCAL is scoped to the surrounding transaction,
    so the settings cannot leak to the next request that borrows this pooled
    connection. That means this MUST run inside a transaction - hence
    session.begin() - and never on a bare connection checkout.

    After this call, handlers write `select(Agent)` with no tenant filter and
    no owner filter. There is no filter to delete, which is the whole point of
    graded check 1.
    """
    if user_id is None and not system:
        raise ValueError("tenant_session needs the signed-in user_id (or system=True)")
    async with SessionLocal() as session:
        async with session.begin():
            await _point_at(session, tenant_key, user_id, system)
            yield session


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    """For code that belongs to no company: sign-in, the platform admin, the
    health sweep over shared servers, later the marketplace.

    Points only at the shared schema, so an unqualified tenant table name would
    simply not resolve here - there is no way to accidentally read one.
    """
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text('SET LOCAL search_path TO "platform"'))
            yield session
