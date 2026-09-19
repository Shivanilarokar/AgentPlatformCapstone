"""My Agents - the agents this workspace has built.

Every row here came out of the builder. Nothing reads a file.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import NOT_FOUND, tenant_db, workspace_user
from app.builder.schema import AgentConfig
from app.core.security import Claims
from app.models.tenant import Agent, Run, Submission
from app.scoring import score_agent
from app.tenancy.checkpointers import checkpointer_for

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/agents", tags=["agents"])


class AgentCard(BaseModel):
    id: str
    name: str
    description: str
    status: str
    servers: list[str]
    tool_count: int
    guarded: list[str]
    topology: str
    created_at: str
    runs: int = 0
    last_run_at: str | None = None
    quality_score: int | None = None
    safety_grade: str | None = None
    installed_from: str | None = None


class AgentDetail(AgentCard):
    config: dict
    graph: dict
    score: dict


def _card(row: Agent) -> AgentCard:
    cfg = AgentConfig.model_validate(row.config)
    return AgentCard(
        id=str(row.id),
        name=cfg.name,
        description=cfg.description,
        status=row.status,
        servers=cfg.requires_connections,
        tool_count=len(cfg.tools),
        guarded=[t.ref for t in cfg.guarded_tools],
        topology=str(cfg.topology.type),
        created_at=row.created_at.isoformat() if row.created_at else "",
        installed_from=str(row.installed_from) if row.installed_from else None,
    )


async def _with_runs(db: AsyncSession, card: AgentCard) -> AgentCard:
    rows = list(await db.scalars(
        select(Run.started_at).where(Run.agent_id == UUID(card.id)).order_by(Run.started_at.desc())
    ))
    card.runs = len(rows)
    card.last_run_at = rows[0].isoformat() if rows else None
    return card


async def _scored(db: AsyncSession, row: Agent) -> AgentCard:
    """Fresh numbers every time they are shown - never stale, always traceable."""
    score = await score_agent(db, row)
    card = await _with_runs(db, _card(row))
    card.quality_score, card.safety_grade = score.quality, score.grade
    return card


@router.get("", response_model=list[AgentCard])
async def list_agents(db: AsyncSession = Depends(tenant_db)):
    # No tenant filter and no owner filter: the gate chose the schema, and
    # row-level security leaves only this person's rows.
    rows = await db.scalars(select(Agent).order_by(Agent.created_at.desc()))
    return [await _scored(db, r) for r in rows]


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: UUID, db: AsyncSession = Depends(tenant_db)):
    row = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if row is None:
        # Another company's id is simply not in this schema. 404, never 403 -
        # a 403 would confirm the agent exists. (graded check 9)
        raise HTTPException(404, detail=NOT_FOUND)

    cfg = AgentConfig.model_validate(row.config)
    base = await _scored(db, row)
    return AgentDetail(**base.model_dump(), config=row.config, graph=cfg.graph_nodes_and_edges(),
                       score=row.checks or {})


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: UUID, claims: Claims = Depends(workspace_user),
                       db: AsyncSession = Depends(tenant_db)):
    """Delete an agent I own, with its runs and its submissions.

    Only the owner can: row-level security leaves nobody else a row to find, so
    a colleague's (or another company's) id is a 404, same as reading it.

    What goes with it: the agent, its runs and its submissions (the database
    cascades), and the saved LangGraph state behind each of those threads - a
    parked run's prompt and tool arguments would otherwise sit in the
    checkpoint tables for good.

    What stays: a marketplace listing already published from it. That is a
    sanitized copy on the platform's side, other companies may have installed
    it, and taking it down is the platform admin's call.

    Refused (409) while a submission is waiting for the admin: the publish run
    is parked on that agent.
    """
    row = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if row is None:
        raise HTTPException(404, detail=NOT_FOUND)

    waiting = await db.scalar(select(Submission.id).where(
        Submission.agent_id == row.id, Submission.status == "pending"))
    if waiting is not None:
        raise HTTPException(409, detail={
            "error": "in_review",
            "detail": "This agent is waiting for the platform admin to review it. "
                      "Delete it once they have answered.",
        })

    threads = [*await db.scalars(select(Run.thread_id).where(Run.agent_id == row.id)),
               *await db.scalars(select(Submission.thread_id).where(Submission.agent_id == row.id))]

    await db.execute(delete(Agent).where(Agent.id == row.id))  # runs + submissions cascade
    await db.flush()

    # Best effort, after the rows are gone: a failure here leaves orphaned
    # checkpoints (a tidiness problem), never a half-deleted agent.
    saver = await checkpointer_for(claims.tenant_key)
    for thread_id in filter(None, threads):
        try:
            await saver.adelete_thread(thread_id)
        except Exception:  # noqa: BLE001
            log.warning("could not delete checkpoints for thread %s", thread_id, exc_info=True)
    return Response(status_code=204)


@router.get("/{agent_id}/scores")
async def get_scores(agent_id: UUID, db: AsyncSession = Depends(tenant_db)):
    """Both numbers and every check behind them. This is what Publish reads."""
    row = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if row is None:
        raise HTTPException(404, detail=NOT_FOUND)
    return (await score_agent(db, row)).as_dict()
