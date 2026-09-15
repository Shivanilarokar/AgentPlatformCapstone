"""Publishing, reviewing, and the marketplace listings.

Author (any workspace user):
    GET  /v1/agents/{id}/publish/preview   what would leave: the sanitized listing + the gate
    POST /v1/agents/{id}/publish           start the publish graph; parks at admin_review
    GET  /v1/agents/{id}/submissions       my submissions for this agent, with the admin's notes

Platform admin only:
    GET  /v1/review                        the queue, across every company
    POST /v1/review/{submission}/decide    approve | changes | reject (+ notes) -> resumes the run

Everyone with a workspace:
    GET  /v1/listings                      the marketplace
    GET  /v1/listings/{id}                 one listing, plus which connections I already have
    POST /v1/listings/{id}/install         copy the design into MY workspace as MY agent
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import NOT_FOUND, current_user, platform_db, require_platform_admin, tenant_db, workspace_user
from app.builder.schema import AgentConfig
from app.core.db import platform_session
from app.core.security import Claims
from app.models.platform_ import Listing, SubmissionIndex, Tenant
from app.models.tenant import Agent, Submission
from app.publishing.graph import publish_graph
from app.publishing.sanitize import listing_from
from app.scoring import PUBLISH_MIN_GRADE, PUBLISH_MIN_QUALITY, score_agent
from app.tenancy.checkpointers import checkpointer_for

router = APIRouter(tags=["publishing"])


# ---------------------------------------------------------------- shapes


class Preview(BaseModel):
    listing: dict
    quality: int
    grade: str
    can_publish: bool
    blocked_by: list[str]
    min_quality: int = PUBLISH_MIN_QUALITY
    min_grade: str = PUBLISH_MIN_GRADE


class SubmissionOut(BaseModel):
    id: str
    agent_id: str
    status: str  # pending | approved | changes_requested | rejected
    notes: str
    quality: int
    grade: str
    submitted_at: str
    decided_at: str | None


class QueueItem(BaseModel):
    submission_id: str
    company: str
    agent_id: str
    status: str
    listing: dict
    quality: int
    grade: str
    checks: dict
    submitted_at: str
    waiting_hours: float


class DecideIn(BaseModel):
    decision: str = Field(pattern=r"^(approve|changes|reject)$")
    notes: str = Field(default="", max_length=2000)


class ListingOut(BaseModel):
    id: str
    name: str
    description: str
    publisher: str
    quality: int
    grade: str
    installs: int
    requires_connections: list[str]
    tools: list[dict]
    topology: str
    published_at: str


class ListingDetail(ListingOut):
    config: dict
    #: server name -> "connected" | "needs_credential" | "no_credential_needed" | "not_registered"
    connections: dict[str, str]


class InstallOut(BaseModel):
    agent_id: str
    name: str
    needs: list[str]  # servers the installer still has to connect


def _sub_out(s: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=str(s.id), agent_id=str(s.agent_id), status=s.status, notes=s.notes,
        quality=int((s.score or {}).get("quality", 0)), grade=(s.score or {}).get("grade", "D"),
        submitted_at=s.submitted_at.isoformat() if s.submitted_at else "",
        decided_at=s.decided_at.isoformat() if s.decided_at else None,
    )


def _listing_out(l: Listing) -> ListingOut:
    cfg = l.config or {}
    return ListingOut(
        id=str(l.id), name=l.name, description=l.description, publisher=l.publisher,
        quality=l.quality, grade=l.grade, installs=l.installs,
        requires_connections=cfg.get("requires_connections", []),
        tools=cfg.get("tools", []), topology=(cfg.get("topology") or {}).get("type", "single"),
        published_at=l.published_at.isoformat() if l.published_at else "",
    )


async def _company_name(tenant_id: str) -> str:
    async with platform_session() as p:
        t = await p.get(Tenant, UUID(tenant_id))
        return t.name if t else ""


# ---------------------------------------------------------------- author


@router.get("/v1/agents/{agent_id}/publish/preview", response_model=Preview)
async def preview(agent_id: UUID, claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db)):
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None:
        raise HTTPException(404, detail=NOT_FOUND)
    score = await score_agent(db, agent)
    company = await _company_name(claims.tenant_id)
    return Preview(listing=listing_from(AgentConfig.model_validate(agent.config), company=company),
                   quality=score.quality, grade=score.grade,
                   can_publish=score.can_publish, blocked_by=score.blocked_by)


@router.post("/v1/agents/{agent_id}/publish", response_model=SubmissionOut, status_code=202)
async def publish(agent_id: UUID, claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db)):
    """Rule 6 made to matter: below the threshold this is a 409 that names the check."""
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None:
        raise HTTPException(404, detail=NOT_FOUND)

    score = await score_agent(db, agent)
    if not score.can_publish:
        raise HTTPException(409, detail={"error": "score_too_low", "detail": "; ".join(score.blocked_by),
                                         "blocked_by": score.blocked_by})

    open_sub = await db.scalar(select(Submission).where(
        Submission.agent_id == agent.id, Submission.status == "pending"))
    if open_sub is not None:
        raise HTTPException(409, detail={"error": "already_pending", "detail": "This agent is already waiting for review."})

    company = await _company_name(claims.tenant_id)
    listing = listing_from(AgentConfig.model_validate(agent.config), company=company)

    sub = Submission(agent_id=agent.id, thread_id="", listing=listing, score=score.as_dict())
    db.add(sub)
    await db.flush()
    sub.thread_id = f"{claims.user_id}/pub-{sub.id}"
    agent.status = "pending_review"
    await db.flush()

    # the one cross-company row, written by the app role, not the company's
    async with platform_session() as p:
        p.add(SubmissionIndex(
            submission_id=sub.id, tenant_key=claims.tenant_key, company=company,
            owner_id=UUID(claims.user_id), agent_id=agent.id, thread_id=sub.thread_id,
            listing=listing, quality=score.quality, grade=score.grade, checks=score.as_dict(),
        ))

    graph = publish_graph(await checkpointer_for(claims.tenant_key))
    await graph.ainvoke(
        {"tenant": claims.tenant_key, "owner": claims.user_id, "company": company,
         "submission_id": str(sub.id), "agent_id": str(agent.id), "listing": listing,
         "quality": score.quality, "grade": score.grade, "checks": score.as_dict(), "log": []},
        config={"configurable": {"thread_id": sub.thread_id}},
    )  # returns at the interrupt; the run is now parked
    return _sub_out(sub)


@router.get("/v1/agents/{agent_id}/submissions", response_model=list[SubmissionOut])
async def submissions(agent_id: UUID, db: AsyncSession = Depends(tenant_db)):
    rows = await db.scalars(select(Submission).where(Submission.agent_id == agent_id)
                            .order_by(Submission.submitted_at.desc()))
    return [_sub_out(s) for s in rows]


# ---------------------------------------------------------------- admin


@router.get("/v1/review", response_model=list[QueueItem])
async def queue(_: Claims = Depends(require_platform_admin), db: AsyncSession = Depends(platform_db)):
    rows = await db.scalars(select(SubmissionIndex).order_by(SubmissionIndex.submitted_at.asc()))
    now = datetime.now(timezone.utc)
    return [
        QueueItem(
            submission_id=str(r.submission_id), company=r.company, agent_id=str(r.agent_id),
            status=r.status, listing=r.listing, quality=r.quality, grade=r.grade, checks=r.checks,
            submitted_at=r.submitted_at.isoformat(),
            waiting_hours=round((now - r.submitted_at).total_seconds() / 3600, 1),
        )
        for r in rows
    ]


@router.post("/v1/review/{submission_id}/decide", response_model=QueueItem)
async def decide(submission_id: UUID, body: DecideIn, _: Claims = Depends(require_platform_admin),
                 db: AsyncSession = Depends(platform_db)):
    """Resume the parked run in the author's company. Approve publishes;
    changes / reject go back to the author with the notes."""
    idx = await db.scalar(select(SubmissionIndex).where(SubmissionIndex.submission_id == submission_id))
    if idx is None:
        raise HTTPException(404, detail=NOT_FOUND)
    if idx.status != "pending":
        raise HTTPException(409, detail={"error": "already_decided", "detail": idx.status})

    graph = publish_graph(await checkpointer_for(idx.tenant_key))
    cfg = {"configurable": {"thread_id": idx.thread_id}}
    snapshot = await graph.aget_state(cfg)
    if snapshot is None or not snapshot.created_at:
        raise HTTPException(404, detail=NOT_FOUND)
    await graph.ainvoke(Command(resume={"decision": body.decision, "notes": body.notes}), config=cfg)

    await db.refresh(idx)
    return (await queue(_, db))[[str(r.submission_id) for r in [idx]][0] == str(idx.submission_id) and 0] \
        if False else next(q for q in await queue(_, db) if q.submission_id == str(submission_id))


# ---------------------------------------------------------------- marketplace


@router.get("/v1/listings", response_model=list[ListingOut])
async def listings(_: Claims = Depends(current_user), db: AsyncSession = Depends(platform_db)):
    rows = await db.scalars(select(Listing).order_by(Listing.published_at.desc()))
    return [_listing_out(l) for l in rows]


async def _connection_status(db: AsyncSession, cfg: dict) -> dict[str, str]:
    """For each server the design needs: what THIS person has. Their registry
    (shared + company + own) and their own credentials; nobody else's."""
    from app.mcp_registry import registry

    views = {v.name: v for v in await registry.list_servers(db)}
    connected = await registry.connected_servers(db)
    out: dict[str, str] = {}
    for name in cfg.get("requires_connections", []):
        v = views.get(name)
        if v is None:
            out[name] = "not_registered"
        elif v.auth_type == "none":
            out[name] = "no_credential_needed"
        elif name in connected:
            out[name] = "connected"
        else:
            out[name] = "needs_credential"
    return out


@router.get("/v1/listings/{listing_id}", response_model=ListingDetail)
async def listing(listing_id: UUID, claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db)):
    async with platform_session() as p:
        row = await p.get(Listing, listing_id)
        if row is None:
            raise HTTPException(404, detail=NOT_FOUND)
        base = _listing_out(row)
        config = row.config
    return ListingDetail(**base.model_dump(), config=config,
                         connections=await _connection_status(db, config))


@router.post("/v1/listings/{listing_id}/install", response_model=InstallOut, status_code=201)
async def install(listing_id: UUID, claims: Claims = Depends(workspace_user), db: AsyncSession = Depends(tenant_db)):
    """Install. The sanitized design becomes a brand-new agent in MY schema, owned
    by me (row-level security stamps the owner). It references servers by NAME,
    so it resolves to MY connections at run time. The publisher's agent, runs
    and credentials are not touched - they are not even reachable from here."""
    async with platform_session() as p:
        row = await p.get(Listing, listing_id)
        if row is None:
            raise HTTPException(404, detail=NOT_FOUND)
        cfg = AgentConfig.model_validate(row.config)  # still a valid design after sanitizing
        row.installs += 1
        config, name = row.config, row.name

    agent = Agent(name=name, config=config, status="draft", installed_from=listing_id)
    db.add(agent)
    await db.flush()

    status = await _connection_status(db, config)
    needs = [n for n, st in status.items() if st in ("needs_credential", "not_registered")]
    return InstallOut(agent_id=str(agent.id), name=cfg.name, needs=needs)
