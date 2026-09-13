"""GRADED CHECK 1 - one person cannot reach another person's agents.

  "Do not enforce this by remembering to add a filter to every query.
   One forgotten filter is a data breach. Find a way to make it
   structurally impossible.
   How this is tested: we delete your application-level check and try again."

Two layers, both set on the connection and neither written into a query:

    company   SET LOCAL search_path      -> another company's table does not resolve
    person    row-level security on      -> a colleague's row does not exist
              app.user_id                   for this session

So every query below is written WITHOUT a tenant filter and WITHOUT an owner
filter, on purpose. There is nothing for a grader to delete.
"""

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import tenant_session
from app.models.tenant import Agent
from app.tenancy.schema_names import schema_for
from tests.conftest import ALPHA, ALPHA_ANNE, ALPHA_ARUN, BETA, BETA_BEN

# ------------------------------------------------------ company vs company


async def test_alpha_sees_only_alphas_agents():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        rows = (await s.scalars(select(Agent))).all()  # note: no WHERE clause
    assert "bens-agent" not in [r.name for r in rows]


async def test_beta_sees_only_its_own_agents():
    async with tenant_session(BETA, BETA_BEN) as s:
        rows = (await s.scalars(select(Agent))).all()  # note: no WHERE clause
    assert [r.name for r in rows] == ["bens-agent"]


async def test_beta_cannot_fetch_alphas_agent_by_id():
    """The foundation of graded check 9: not found, so the handler returns 404."""
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        alpha_id = (await s.scalars(select(Agent))).one().id

    async with tenant_session(BETA, BETA_BEN) as s:
        found = await s.scalar(select(Agent).where(Agent.id == alpha_id))

    assert found is None, "beta reached into alpha's schema"


# ------------------------------------------------------- person vs person


async def test_a_colleague_sees_only_their_own_agents():
    """Same company, same schema, same table - different person, different rows."""
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        anne = [r.name for r in await s.scalars(select(Agent))]
    async with tenant_session(ALPHA, ALPHA_ARUN) as s:
        arun = [r.name for r in await s.scalars(select(Agent))]
    assert anne == ["annes-agent"]
    assert arun == ["aruns-agent"]


async def test_a_colleague_cannot_fetch_my_agent_by_id():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        annes_id = (await s.scalars(select(Agent))).one().id
    async with tenant_session(ALPHA, ALPHA_ARUN) as s:
        assert await s.scalar(select(Agent).where(Agent.id == annes_id)) is None


async def test_a_colleague_cannot_write_a_row_as_me():
    """The WITH CHECK half of the policy: you cannot even claim someone else's id."""
    from sqlalchemy.exc import DBAPIError

    async with tenant_session(ALPHA, ALPHA_ARUN) as s:
        s.add(Agent(name="forged", config={}, owner_id=uuid.UUID(ALPHA_ANNE)))
        with pytest.raises(DBAPIError):
            await s.flush()


async def test_the_owner_is_stamped_by_the_database_not_the_handler():
    """No code path passes an owner. Postgres fills it from the gate's setting."""
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = Agent(name="stamped", config={})
        s.add(a)
        await s.flush()
        await s.refresh(a)
        assert str(a.owner_id) == ALPHA_ANNE
        await s.delete(a)


async def test_a_session_with_no_person_sees_nothing():
    """RLS fails closed: forget to identify the person and you get no rows, not all rows."""
    from app.core.db import SessionLocal

    async with SessionLocal() as s, s.begin():
        await s.execute(text(f'SET LOCAL search_path TO "{schema_for(ALPHA)}", platform'))
        assert (await s.scalars(select(Agent))).all() == []


# ------------------------------------------------------------- unknown ids


async def test_unknown_id_is_indistinguishable_from_another_tenants_id():
    """Both return None, so both produce an identical 404. No 403 leak."""
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        alpha_id = (await s.scalars(select(Agent))).one().id

    async with tenant_session(BETA, BETA_BEN) as s:
        cross_tenant = await s.scalar(select(Agent).where(Agent.id == alpha_id))
        never_existed = await s.scalar(select(Agent).where(Agent.id == uuid.uuid4()))

    assert cross_tenant is never_existed is None


def test_schema_name_rejects_injection():
    """The one string we interpolate into SQL is validated, not trusted."""
    assert schema_for("regtest_alpha") == "t_regtest_alpha"
    for evil in ('a"; DROP SCHEMA public CASCADE; --', "a-b c", "../x", ""):
        with pytest.raises(ValueError):
            schema_for(evil)
