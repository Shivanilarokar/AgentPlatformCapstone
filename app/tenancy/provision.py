"""Creating a company: one CREATE SCHEMA, then that company's tables inside it.

Two separate jobs, deliberately:

  bootstrap_platform()  the shared schema - runs once, at startup
  create_tenant()       one company's private schema - runs on sign-up
"""

from sqlalchemy import text

from app.core.db import engine
from app.models.base import Base
from app.models.platform_ import PLATFORM_SCHEMA, PlatformBase
from app.tenancy.schema_names import schema_for


async def bootstrap_platform() -> None:
    """The shared schema: tenants, users, and later the marketplace."""
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PLATFORM_SCHEMA}"'))
        await conn.run_sync(PlatformBase.metadata.create_all)


async def create_tenant(tenant_key: str) -> str:
    """CREATE SCHEMA t_<key> and build this company's tables inside it."""
    schema = schema_for(tenant_key)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        # create_all issues unqualified CREATE TABLE, so search_path decides
        # which schema they land in. Same trick as the request-time gate.
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
    return schema


async def drop_tenant(tenant_key: str) -> None:
    """Used by tests to start from a known-empty state."""
    schema = schema_for(tenant_key)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
