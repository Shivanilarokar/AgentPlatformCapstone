"""The Playground and the Runs tab - one agent, executed by one person.

    POST /v1/agents/{id}/invoke                 run it; may stop at an approval
    POST /v1/agents/{id}/runs/{run}/resume      Approve / Reject the pending tool
    GET  /v1/agents/{id}/runs                   history: status, latency, outcome
    GET  /v1/agents/{id}/runs/{run}             one run, incl. what it is waiting for
    POST /v1/agents/{id}/runs/{run}/feedback    thumbs up / down -> feeds the score

The approval is a LangGraph interrupt raised INSIDE the tool wrapper
(app/runtime/guarded_tool.py), so a write tool cannot run until someone answers
here. The run is parked in this company's checkpoint tables meanwhile: restart
the server and the same approval is still waiting.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import NOT_FOUND, tenant_db, workspace_user
from app.builder.schema import AgentConfig
from app.core.security import Claims
from app.models.tenant import Agent, Run
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext
from app.tenancy.checkpointers import checkpointer_for
from app.vault.resolver import registry_disabled, registry_endpoints, vault_resolver

router = APIRouter(prefix="/v1/agents/{agent_id}", tags=["runs"])


MAX_HISTORY = 6  # earlier turns that are carried along; older ones are dropped


class Turn(BaseModel):
    """One earlier exchange of the same conversation."""

    input: str = Field(max_length=4000)
    output: str = Field(default="", max_length=4000)


class InvokeIn(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    #: The earlier turns of this conversation, oldest first. Every run starts
    #: from nothing, so a reply to a question ("which repository?") would arrive
    #: as a task with no context - the agent would ask again, forever. Sending
    #: the turns lets the reply be read as a reply.
    history: list[Turn] = Field(default_factory=list, max_length=20)


class ResumeIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")


class FeedbackIn(BaseModel):
    value: int = Field(ge=-1, le=1)


class RunOut(BaseModel):
    id: str
    agent_id: str
    trigger: str
    status: str  # running | awaiting_approval | ok | rejected | error
    input: str
    output: str
    transcript: list[str]
    pending: dict | None  # {"tool", "risk", "args"} while awaiting_approval
    latency_ms: int | None
    feedback: int | None
    started_at: str
    finished_at: str | None


def _out(r: Run) -> RunOut:
    return RunOut(
        id=str(r.id), agent_id=str(r.agent_id), trigger=r.trigger, status=r.status,
        input=r.input, output=r.output, transcript=list(r.transcript or []),
        pending=r.pending, latency_ms=r.latency_ms, feedback=r.feedback,
        started_at=r.started_at.isoformat() if r.started_at else "",
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
    )


async def _agent(db: AsyncSession, agent_id: UUID) -> Agent:
    row = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if row is None:
        raise HTTPException(404, detail=NOT_FOUND)  # not mine == does not exist
    return row


async def _run(db: AsyncSession, agent_id: UUID, run_id: UUID) -> Run:
    row = await db.scalar(select(Run).where(Run.id == run_id, Run.agent_id == agent_id))
    if row is None:
        raise HTTPException(404, detail=NOT_FOUND)
    return row


async def _graph(claims: Claims, agent: Agent, thread_id: str):
    """The agent, compiled from its stored configuration, for THIS person.

    The token and endpoint resolvers are bound to the caller, so the tools use
    the caller's own credentials - never the author's.
    """
    cfg = AgentConfig.model_validate(agent.config)
    ctx = RunContext(
        tenant_id=claims.tenant_key,
        thread_id=thread_id,
        resolve_token=vault_resolver(claims.tenant_key, claims.user_id),
        resolve_endpoint=registry_endpoints(claims.tenant_key, claims.user_id),
        resolve_disabled=registry_disabled(claims.tenant_key, claims.user_id),
    )
    return await compile_agent(cfg, ctx, checkpointer=await checkpointer_for(claims.tenant_key))


def _apply(run: Run, result: dict[str, Any], started: float) -> None:
    """Fold a graph result into the run row.

    `latency_ms` is the agent's OWN working time, summed across the segments
    before and after each approval. The minutes a human spends deciding are not
    the agent's latency and must not cost it score points.
    """
    run.transcript = list(result.get("transcript", []))
    run.latency_ms = (run.latency_ms or 0) + int((time.perf_counter() - started) * 1000)
    pending = result.get("__interrupt__")
    if pending:
        run.status = "awaiting_approval"
        run.pending = pending[0].value
        return

    run.pending = None
    run.finished_at = datetime.now(timezone.utc)
    results = result.get("results", {})
    summaries = [v for k, v in results.items() if k.endswith(".summary")]
    run.output = summaries[-1] if summaries else "(no answer)"
    rejected = any("Rejected by the user" in str(v) for v in results.values())
    run.status = "rejected" if rejected else "ok"


def _task(text: str, history: list[Turn] | tuple = ()) -> str:
    """What the workers are told the task is. On its own when there is no history;
    otherwise the recent conversation first, so a value given earlier (a
    repository, a channel) still counts as given."""
    if not history:
        return text
    earlier = "\n".join(f"User: {t.input}\nAgent: {t.output}" for t in list(history)[-MAX_HISTORY:])
    return (
        f"Earlier in this conversation:\n{earlier}\n\n"
        f"The user's new message: {text}\n\n"
        "Read the new message as a reply to that conversation and carry on with the job it started. "
        "Anything the user already said there - a repository, a channel, a recipient - counts as given."
    )


def _state(run: Run, history: list[Turn] | tuple = ()) -> dict:
    return {"task": _task(run.input, history), "transcript": [], "finished": [], "results": {}}


# ------------------------------------------------------------------ routes


@router.post("/invoke", response_model=RunOut, status_code=202)
async def invoke(
    agent_id: UUID, body: InvokeIn, request: Request,
    claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db, scope="function"),
):
    agent = await _agent(db, agent_id)
    # a browser session is the playground; a `forge_` API token is the public API
    trigger = "api" if (request.headers.get("authorization") or "").startswith("Bearer forge_") else "playground"
    run = Run(agent_id=agent.id, input=body.input, trigger=trigger,
              thread_id=f"{claims.user_id}/run-{uuid.uuid4().hex[:12]}")
    db.add(run)
    await db.flush()

    graph = await _graph(claims, agent, run.thread_id)
    started = time.perf_counter()
    try:
        result = await graph.ainvoke(_state(run, body.history), config={"configurable": {"thread_id": run.thread_id}})
        _apply(run, result, started)
    except Exception as exc:  # noqa: BLE001 - the run fails; the platform does not
        run.status, run.output = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
        run.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return _out(run)


@router.post("/runs/{run_id}/resume", response_model=RunOut, status_code=202)
async def resume(
    agent_id: UUID, run_id: UUID, body: ResumeIn,
    claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db, scope="function"),
):
    """Answer the approval. This is the Approve / Reject button in the chat."""
    agent = await _agent(db, agent_id)
    run = await _run(db, agent_id, run_id)
    if run.status != "awaiting_approval":
        raise HTTPException(409, detail={"error": "not_waiting", "detail": f"run is {run.status}"})

    graph = await _graph(claims, agent, run.thread_id)
    started = time.perf_counter()
    try:
        result = await graph.ainvoke(
            Command(resume=body.decision), config={"configurable": {"thread_id": run.thread_id}}
        )
        _apply(run, result, started)
    except Exception as exc:  # noqa: BLE001
        run.status, run.output = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
        run.finished_at = datetime.now(timezone.utc)
    await db.flush()
    return _out(run)


@router.get("/runs", response_model=list[RunOut])
async def list_runs(agent_id: UUID, db: AsyncSession = Depends(tenant_db, scope="function")):
    await _agent(db, agent_id)
    rows = await db.scalars(select(Run).where(Run.agent_id == agent_id).order_by(Run.started_at.desc()))
    return [_out(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(agent_id: UUID, run_id: UUID, db: AsyncSession = Depends(tenant_db, scope="function")):
    return _out(await _run(db, agent_id, run_id))


@router.post("/runs/{run_id}/feedback", response_model=RunOut)
async def feedback(agent_id: UUID, run_id: UUID, body: FeedbackIn, db: AsyncSession = Depends(tenant_db, scope="function")):
    run = await _run(db, agent_id, run_id)
    run.feedback = body.value or None
    await db.flush()
    return _out(run)
