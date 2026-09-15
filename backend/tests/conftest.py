"""Two companies, two people in the first one, one agent each. Rebuilt from
scratch for every test session.

Requires `docker compose up` to be running - Postgres is reached on the port
compose publishes to localhost.
"""

import uuid

import pytest_asyncio

from app.core.db import engine, tenant_session
from app.models.tenant import Agent
from app.tenancy.provision import bootstrap_platform, create_tenant, drop_tenant

ALPHA = "regtest_alpha"
BETA = "regtest_beta"

#: People. Stable ids so tests can name them; nothing else about them matters.
ALPHA_ANNE = str(uuid.UUID(int=0xA1))  # works at alpha
ALPHA_ARUN = str(uuid.UUID(int=0xA2))  # also works at alpha
BETA_BEN = str(uuid.UUID(int=0xB1))    # works at beta


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def two_companies():
    await bootstrap_platform()

    for key in (ALPHA, BETA):
        await drop_tenant(key)
        await create_tenant(key)

    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        s.add(Agent(name="annes-agent", config={"owner": "anne"}))
    async with tenant_session(ALPHA, ALPHA_ARUN) as s:
        s.add(Agent(name="aruns-agent", config={"owner": "arun"}))
    async with tenant_session(BETA, BETA_BEN) as s:
        s.add(Agent(name="bens-agent", config={"owner": "ben"}))

    yield

    for key in (ALPHA, BETA):
        await drop_tenant(key)
    await engine.dispose()
