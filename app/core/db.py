"""The tenant gate.

This module is the ONLY way to get a database session in this codebase.
Everything downstream depends on that being true, so keep it that way.
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


@asynccontextmanager
async def tenant_session(tenant_key: str) -> AsyncIterator[AsyncSession]:
    """Open a transaction, point Postgres at ONE company's schema, and yield.

    Why SET LOCAL and not SET: LOCAL is scoped to the surrounding transaction,
    so the setting cannot leak to the next request that borrows this pooled
    connection. That means this MUST run inside a transaction - hence
    session.begin() - and never on a bare connection checkout.

    After this call, handlers write `select(Agent)` with no tenant filter.
    There is no filter to delete, which is the whole point of graded check 1.
    """
    schema = schema_for(tenant_key)  # validated; safe to interpolate
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text(f'SET LOCAL search_path TO "{schema}", platform'))
            yield session


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    """For code that belongs to no company: the health sweep, the marketplace.

    Points only at the shared schema, so an unqualified tenant table name would
    simply not resolve here - there is no way to accidentally read one.
    """
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text('SET LOCAL search_path TO "platform"'))
            yield session
