"""Deleting an agent: DELETE /v1/agents/{id}.

    * the owner can; nobody else can even find it (404, like reading it)
    * its runs and submissions go with it, and so do the saved LangGraph
      checkpoints behind them (a parked run's prompt would otherwise stay)
    * refused while a submission is waiting for the platform admin
    * a decided submission (changes requested / rejected) does not block it -
      the agent stays "pending_review" after those, so status is not the test

Uses the two companies from conftest.py, but creates - and removes - its own
agents: the ones conftest seeds are shared with other tests.
"""

from __future__ import annotations

import uuid
from typing import TypedDict

import httpx
import pytest
from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, func, select, text

from app.api.deps import current_user
from app.core.db import engine, tenant_session
from app.core.security import Claims
from app.models.tenant import Agent, Run, Submission
from app.server import app
from app.tenancy.checkpointers import checkpointer_for
from app.tenancy.schema_names import schema_for
from conftest import ALPHA, ALPHA_ANNE, ALPHA_ARUN, BETA, BETA_BEN

ANNE = Claims(ALPHA_ANNE, "t-a", ALPHA, "anne@a.test", "Anne", "admin")
ARUN = Claims(ALPHA_ARUN, "t-a", ALPHA, "arun@a.test", "Arun", "user")
BEN = Claims(BETA_BEN, "t-b", BETA, "ben@b.test", "Ben", "admin")
PLATFORM = Claims("p", None, None, "admin@forge.dev", "Platform admin", "platform_admin")

PREFIX = "deltest-"


@pytest.fixture
def as_user():
    def make(claims: Claims) -> httpx.AsyncClient:
        app.dependency_overrides[current_user] = lambda: claims
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    yield make
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture(autouse=True)
async def tidy():
    yield
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        await s.execute(delete(Agent).where(Agent.name.like(f"{PREFIX}%")))


async def make_agent(status: str = "draft") -> uuid.UUID:
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = Agent(name=f"{PREFIX}{uuid.uuid4().hex[:6]}", config={}, status=status)
        s.add(a)
        await s.flush()
        return a.id


async def exists(agent_id) -> bool:
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        return await s.scalar(select(Agent.id).where(Agent.id == agent_id)) is not None


async def checkpoints_for(thread_id: str) -> int:
    async with engine.connect() as c:
        return await c.scalar(
            text(f'SELECT count(*) FROM "{schema_for(ALPHA)}".checkpoints WHERE thread_id = :t'),
            {"t": thread_id},
        )


class _S(TypedDict):
    x: int


async def save_a_checkpoint(thread_id: str) -> None:
    """Run a one-node graph so the company's saver really writes rows."""
    g = StateGraph(_S)
    g.add_node("n", lambda s: {"x": 1})
    g.add_edge(START, "n")
    g.add_edge("n", END)
    graph = g.compile(checkpointer=await checkpointer_for(ALPHA))
    await graph.ainvoke({"x": 0}, config={"configurable": {"thread_id": thread_id}})


async def test_the_owner_can_delete_and_it_is_gone(as_user):
    agent_id = await make_agent()
    async with as_user(ANNE) as c:
        assert (await c.delete(f"/v1/agents/{agent_id}")).status_code == 204
        assert (await c.get(f"/v1/agents/{agent_id}")).status_code == 404
        assert (await c.delete(f"/v1/agents/{agent_id}")).status_code == 404  # already gone
    assert not await exists(agent_id)


async def test_runs_submissions_and_checkpoints_go_with_it(as_user):
    agent_id = await make_agent()
    run_thread = f"{ALPHA_ANNE}/run-{uuid.uuid4().hex[:8]}"
    sub_thread = f"{ALPHA_ANNE}/pub-{uuid.uuid4().hex[:8]}"
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        s.add(Run(agent_id=agent_id, thread_id=run_thread, input="secret prompt"))
        s.add(Submission(agent_id=agent_id, thread_id=sub_thread, status="approved"))
    await save_a_checkpoint(run_thread)
    await save_a_checkpoint(sub_thread)
    assert await checkpoints_for(run_thread) > 0 and await checkpoints_for(sub_thread) > 0

    async with as_user(ANNE) as c:
        assert (await c.delete(f"/v1/agents/{agent_id}")).status_code == 204

    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        assert await s.scalar(select(func.count()).select_from(Run).where(Run.agent_id == agent_id)) == 0
        assert await s.scalar(
            select(func.count()).select_from(Submission).where(Submission.agent_id == agent_id)) == 0
    assert await checkpoints_for(run_thread) == 0
    assert await checkpoints_for(sub_thread) == 0


async def test_a_colleague_cannot_delete_it_and_cannot_tell_it_exists(as_user):
    agent_id = await make_agent()
    async with as_user(ARUN) as c:  # same company, different person
        r = await c.delete(f"/v1/agents/{agent_id}")
    assert r.status_code == 404
    assert await exists(agent_id)


async def test_another_company_cannot_delete_it(as_user):
    agent_id = await make_agent()
    async with as_user(BEN) as c:
        r = await c.delete(f"/v1/agents/{agent_id}")
    assert r.status_code == 404
    assert await exists(agent_id)


async def test_the_platform_admin_has_no_workspace_to_delete_from(as_user):
    agent_id = await make_agent()
    async with as_user(PLATFORM) as c:
        assert (await c.delete(f"/v1/agents/{agent_id}")).status_code == 404
    assert await exists(agent_id)


async def test_an_agent_waiting_for_review_cannot_be_deleted(as_user):
    agent_id = await make_agent(status="pending_review")
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        s.add(Submission(agent_id=agent_id, thread_id=f"{ALPHA_ANNE}/pub-x", status="pending"))
    async with as_user(ANNE) as c:
        r = await c.delete(f"/v1/agents/{agent_id}")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "in_review"
    assert await exists(agent_id)


@pytest.mark.parametrize("decision", ["changes_requested", "rejected", "approved"])
async def test_once_the_admin_has_answered_it_can_be_deleted(as_user, decision):
    # after changes_requested / rejected the agent is still "pending_review"
    agent_id = await make_agent(status="live" if decision == "approved" else "pending_review")
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        s.add(Submission(agent_id=agent_id, thread_id=f"{ALPHA_ANNE}/pub-{decision}", status=decision))
    async with as_user(ANNE) as c:
        assert (await c.delete(f"/v1/agents/{agent_id}")).status_code == 204
    assert not await exists(agent_id)


async def test_deleting_one_agent_leaves_the_others(as_user):
    keep, drop = await make_agent(), await make_agent()
    async with as_user(ANNE) as c:
        assert (await c.delete(f"/v1/agents/{drop}")).status_code == 204
    assert await exists(keep) and not await exists(drop)
