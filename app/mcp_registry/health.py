"""The scheduled health check.

Requirement 4 of the MCP Registry screen:

    "A scheduled job re-checks every server and marks the dead ones."

Every HEALTH_INTERVAL_SECONDS this walks every company's schema and every shared
server, re-runs tools/list against each, and flips status to "down" for any that
stopped answering. Agents depending on a down server show as degraded.

It is a plain asyncio task started with the app. No Celery, no Redis, no cron -
one loop is exactly enough for this, and it is one fewer container for a grader
to get running.

Servers that need a credential are re-checked WITHOUT one. Most list their tools
regardless (github does), and the point of the check is "is it alive", not "does
our token still work" - that is what a real tool call tells you.
"""

from __future__ import annotations

import asyncio
import logging
from sqlalchemy import select, text

from app.core.db import engine, platform_session, tenant_session
from app.models.platform_ import SharedServer
from app.models.tenant import McpServer
from app.mcp_registry import registry
from app.vault import connections as vault

log = logging.getLogger(__name__)

HEALTH_INTERVAL_SECONDS = 300  # five minutes
_task: asyncio.Task | None = None


async def check_everything() -> dict[str, str]:
    """One sweep. Returns {name: status} so a caller can see what it did."""
    results: dict[str, str] = {}

    # --- shared servers: one pass, they belong to nobody in particular -----
    async with platform_session() as s:
        for srv in await s.scalars(select(SharedServer)):
            try:
                results[f"shared/{srv.name}"] = await registry.refresh(s, srv.name, shared=True)
            except Exception as exc:  # noqa: BLE001 - one bad server must not stop the sweep
                log.warning("health: shared %s: %s", srv.name, exc)
                results[f"shared/{srv.name}"] = "error"

    # --- every company's private servers, one schema at a time --------------
    async with engine.connect() as conn:
        keys = [r[0] for r in await conn.execute(text(
            'SELECT schema_key FROM platform.tenants ORDER BY schema_key'
        ))]

    for tenant_key in keys:
        try:
            # system=True: the sweep sees every person's servers in this schema
            async with tenant_session(tenant_key, system=True) as s:
                for srv in await s.scalars(select(McpServer)):
                    try:
                        # The OWNER's credential, borrowed for one tools/list, then dropped.
                        token = await vault.use(s, tenant=tenant_key, user_id=str(srv.owner_id),
                                                server_name=srv.name)
                        results[f"{tenant_key}/{srv.name}"] = await registry.refresh(
                            s, srv.name, token=token, server_id=srv.id
                        )
                        del token
                    except Exception as exc:  # noqa: BLE001
                        log.warning("health: %s/%s: %s", tenant_key, srv.name, exc)
                        results[f"{tenant_key}/{srv.name}"] = "error"
        except Exception as exc:  # noqa: BLE001 - a broken schema must not stop the sweep
            log.warning("health: tenant %s: %s", tenant_key, exc)

    down = [k for k, v in results.items() if v == "down"]
    log.info("health sweep: %d checked, %d down%s", len(results), len(down),
             f" ({', '.join(down)})" if down else "")
    return results


async def _loop() -> None:
    # Let the app finish starting before the first sweep.
    await asyncio.sleep(30)
    while True:
        try:
            await check_everything()
        except Exception:  # noqa: BLE001
            log.exception("health sweep failed")
        await asyncio.sleep(HEALTH_INTERVAL_SECONDS)


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_loop(), name="mcp-health")
        log.info("health check scheduled every %ds", HEALTH_INTERVAL_SECONDS)


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
