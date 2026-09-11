"""One LangGraph checkpointer per company, writing into that company's schema.

WHY THIS EXISTS
---------------
LangGraph's checkpoint tables are keyed by `thread_id` and nothing else. They
have no tenant column, so a guessed thread id would read another company's
paused run - and a paused run is exactly where an approval payload and its tool
arguments sit.

Row-level security cannot help: there is no column to write a policy against.
Putting each company's checkpoint tables inside its own schema does, because a
connection whose `search_path` is t_northwind_labs simply cannot see
t_helios.checkpoints.

So every connection this pool hands out is opened with
`options=-c search_path=t_<tenant>`, and `setup()` therefore creates the
checkpoint tables inside that schema.

Savers are cached because `setup()` is slow and only needs to run once.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.tenancy.schema_names import schema_for

log = logging.getLogger(__name__)

_savers: dict[str, AsyncPostgresSaver] = {}
_pools: dict[str, AsyncConnectionPool] = {}


def _psycopg_dsn() -> str:
    """SQLAlchemy wants postgresql+psycopg://, psycopg wants postgresql://."""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


async def checkpointer_for(tenant_slug: str) -> AsyncPostgresSaver:
    """The saver for one company. Built once, then reused."""
    if (saver := _savers.get(tenant_slug)) is not None:
        return saver

    schema = schema_for(tenant_slug)  # validated before it reaches a DSN

    pool = AsyncConnectionPool(
        conninfo=_psycopg_dsn(),
        min_size=0,
        max_size=4,
        open=False,
        kwargs={
            "autocommit": True,  # the checkpointer manages its own transactions
            "row_factory": "dict_row",
            # This is the whole trick: every connection from this pool is already
            # pointed at one company's schema.
            "options": f"-c search_path={schema}",
        },
    )
    await pool.open(wait=True)

    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # CREATE TABLE ... inside `schema`, not public

    _pools[tenant_slug] = pool
    _savers[tenant_slug] = saver
    log.info("checkpointer ready for %s", schema)
    return saver


async def close_all() -> None:
    """Called on shutdown so the app exits cleanly."""
    for pool in _pools.values():
        await pool.close()
    _pools.clear()
    _savers.clear()
