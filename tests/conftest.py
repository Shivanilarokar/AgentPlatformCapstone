"""Two companies, one agent each. Rebuilt from scratch for every test session.

Requires `docker compose up` to be running - Postgres is reached on the port
compose publishes to localhost.
"""

import pytest_asyncio

from app.core.db import engine, tenant_session
from app.models.tenant import Agent
from app.tenancy.provision import create_tenant, drop_tenant, bootstrap_platform

ALPHA = "alpha"
BETA = "beta"


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def two_companies():
    await bootstrap_platform()

    for key in (ALPHA, BETA):
        await drop_tenant(key)
        await create_tenant(key)

    async with tenant_session(ALPHA) as s:
        s.add(Agent(name="alpha-agent", config={"owner": "alpha"}))
    async with tenant_session(BETA) as s:
        s.add(Agent(name="beta-agent", config={"owner": "beta"}))

    yield

    await engine.dispose()
