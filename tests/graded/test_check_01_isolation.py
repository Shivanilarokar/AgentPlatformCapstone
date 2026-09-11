"""GRADED CHECK 1 - one company cannot reach another company's agents.

  "Do not enforce this by remembering to add a filter to every query.
   One forgotten filter is a data breach. Find a way to make it
   structurally impossible.
   How this is tested: we delete your application-level check and try again."

So every query below is written WITHOUT a tenant filter, on purpose. There is
nothing for a grader to delete. Isolation comes from search_path alone.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.db import tenant_session
from app.models.tenant import Agent
from app.tenancy.schema_names import schema_for
from tests.conftest import ALPHA, BETA


async def test_alpha_sees_only_its_own_agents():
    async with tenant_session(ALPHA) as s:
        rows = (await s.scalars(select(Agent))).all()  # note: no WHERE clause
    assert [r.name for r in rows] == ["alpha-agent"]


async def test_beta_sees_only_its_own_agents():
    async with tenant_session(BETA) as s:
        rows = (await s.scalars(select(Agent))).all()  # note: no WHERE clause
    assert [r.name for r in rows] == ["beta-agent"]


async def test_beta_cannot_fetch_alphas_agent_by_id():
    """The foundation of graded check 9: not found, so the handler returns 404."""
    async with tenant_session(ALPHA) as s:
        alpha_agent = (await s.scalars(select(Agent))).one()
        alpha_id = alpha_agent.id

    async with tenant_session(BETA) as s:
        found = await s.scalar(select(Agent).where(Agent.id == alpha_id))

    assert found is None, "beta reached into alpha's schema"


async def test_unknown_id_is_indistinguishable_from_another_tenants_id():
    """Both return None, so both produce an identical 404. No 403 leak."""
    async with tenant_session(ALPHA) as s:
        alpha_id = (await s.scalars(select(Agent))).one().id

    async with tenant_session(BETA) as s:
        cross_tenant = await s.scalar(select(Agent).where(Agent.id == alpha_id))
        never_existed = await s.scalar(select(Agent).where(Agent.id == uuid.uuid4()))

    assert cross_tenant is never_existed is None


def test_schema_name_rejects_injection():
    """The one string we interpolate into SQL is validated, not trusted."""
    assert schema_for("alpha") == "t_alpha"
    for evil in ('a"; DROP SCHEMA public CASCADE; --', "a-b c", "../x", ""):
        with pytest.raises(ValueError):
            schema_for(evil)
