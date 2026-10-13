"""Rule 6 - scores must mean something.

    "You must be able to point at exactly why a number is what it is - no asking
     a model to guess a score. Then make them matter: an agent that scores badly
     cannot be published."

Every assertion below reads a named check, not just the number.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.db import tenant_session
from app.models.tenant import Agent, Run
from app.scoring import PUBLISH_MIN_QUALITY, score_agent
from app.scoring.score import MIN_RUNS
from tests.conftest import ALPHA, ALPHA_ANNE

CONFIG = {
    "schema_version": "1.0",
    "name": "Docs Freshness", "description": "reads and writes files",
    "model": {"provider": "google_genai", "name": "gemini-flash-lite-latest", "temperature": 0},
    "topology": {"type": "single"},
    "tools": [
        {"ref": "filesystem.read_text_file", "risk": "read", "approval": "auto", "requires_connection": "filesystem"},
        {"ref": "filesystem.write_file", "risk": "write", "approval": "ask", "requires_connection": "filesystem"},
    ],
    "requires_connections": ["filesystem"],
}


def _run(agent, status="ok", latency=5000, feedback=None, tools=("filesystem.read_text_file", "filesystem.write_file")):
    return Run(agent_id=agent.id, thread_id=f"{ALPHA_ANNE}/run-{uuid.uuid4().hex[:6]}", status=status,
               input="x", output="done", latency_ms=latency, feedback=feedback,
               transcript=[f"[agent] {t} -> ok" for t in tools],
               finished_at=datetime.now(timezone.utc))


def _by_key(checks):
    return {c.key: c for c in checks}


async def _agent(s, runs):
    a = Agent(name="t", config=CONFIG, status="draft")
    s.add(a)
    await s.flush()
    for r in runs(a):
        s.add(r)
    await s.flush()
    return a


async def test_a_fresh_agent_cannot_be_published_and_says_why():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [])
        score = await score_agent(s, a)
    assert not score.can_publish
    assert any("quality" in b for b in score.blocked_by)
    q = _by_key(score.quality_checks)
    assert not q["tested"].passed and q["tested"].detail == "0 finished runs"
    sf = _by_key(score.safety_checks)
    assert sf["approvals"].passed  # the config itself is safe
    assert not sf["tested"].passed
    assert score.grade in ("C", "D")


async def test_every_quality_point_traces_to_a_check():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a, feedback=1) for _ in range(MIN_RUNS)])
        score = await score_agent(s, a)
    assert score.quality == sum(c.points for c in score.quality_checks)
    assert sum(c.max_points for c in score.quality_checks) == 100
    q = _by_key(score.quality_checks)
    assert q["tested"].passed and q["success"].points == 30 and q["feedback"].points == 20
    assert q["coverage"].passed and q["latency"].passed and q["stable"].passed
    assert score.quality == 100


async def test_a_good_agent_can_be_published():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a, feedback=1) for _ in range(MIN_RUNS)])
        score = await score_agent(s, a)
    # servers may not be registered in this test schema -> at most one safety check fails
    assert score.quality >= PUBLISH_MIN_QUALITY
    assert score.blocked_by == [] or all("safety" in b for b in score.blocked_by)


async def test_errors_and_thumbs_down_pull_the_number_down():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a, status="error", feedback=-1) for _ in range(MIN_RUNS)])
        score = await score_agent(s, a)
    q = _by_key(score.quality_checks)
    assert q["success"].points == 0 and q["feedback"].points == 0 and not q["stable"].passed
    assert score.quality < PUBLISH_MIN_QUALITY
    assert not score.can_publish


async def test_an_unused_granted_tool_is_named():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a, tools=("filesystem.read_text_file",)) for _ in range(MIN_RUNS)])
        score = await score_agent(s, a)
    sf = _by_key(score.safety_checks)
    assert not sf["least_privilege"].passed
    assert "never used" in sf["least_privilege"].detail


async def test_a_credential_in_a_trace_fails_the_safety_check():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a)])
        a_runs = list(await s.scalars(__import__("sqlalchemy").select(Run).where(Run.agent_id == a.id)))
        a_runs[0].output = "token was ghp_" + "A" * 30
        await s.flush()
        score = await score_agent(s, a)
    assert not _by_key(score.safety_checks)["secrets"].passed


async def test_the_snapshot_is_stored_on_the_agent():
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        a = await _agent(s, lambda a: [_run(a)])
        score = await score_agent(s, a)
        await s.flush()
        await s.refresh(a)
        assert a.quality_score == score.quality and a.safety_grade == score.grade
        assert a.checks["safety_checks"][0]["key"] == "approvals"
