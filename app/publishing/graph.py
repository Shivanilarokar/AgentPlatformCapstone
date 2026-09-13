"""The publish graph - the second place this platform pauses for a human.

    sanitize -> [INTERRUPT: admin_review] -> decide

Hitting Publish starts this graph and it stops at the interrupt, parked in the
author's company checkpoint tables (thread "<owner>/pub-<submission>"). The
platform admin answers it from the review queue - maybe three days later,
across two deploys and a weekend (graded check 6). Approve writes the listing;
Request changes / Reject send it back to the author with notes. Nothing reaches
platform.listings any other way.

The graph carries NO company data beyond the sanitized listing: the state is a
handful of ids plus what the marketplace is allowed to show.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.core.db import platform_session, tenant_session
from app.models.platform_ import Listing, SubmissionIndex
from app.models.tenant import Agent, Submission


class PublishState(TypedDict, total=False):
    tenant: str
    owner: str
    company: str
    submission_id: str
    agent_id: str
    listing: dict  # already sanitized by the route; the graph never sees the raw config
    quality: int
    grade: str
    checks: dict
    log: list[str]
    # after the admin answers
    decision: str  # approve | changes | reject
    notes: str


async def sanitize(state: PublishState) -> dict:
    """The listing was projected before the graph started (so the author could
    see it). This node records that fact; it is the last thing that runs before
    the wait, so the checkpoint written here is exactly what the admin reviews."""
    return {"log": [f"Sanitized: {len(state['listing'].get('tools', []))} tools, publisher '{state['company']}'."]}


async def admin_review(state: PublishState) -> dict:
    """THE PAUSE. Everything up to here is checkpointed; the process may exit."""
    answer = interrupt({
        "type": "admin_review",
        "submission_id": state["submission_id"],
        "listing": state["listing"],
        "quality": state["quality"],
        "grade": state["grade"],
    })
    return {"decision": answer.get("decision", "reject"), "notes": answer.get("notes", "")}


async def decide(state: PublishState) -> dict:
    """Apply the admin's answer: a listing, or notes back to the author."""
    now = datetime.now(timezone.utc)
    status = {"approve": "approved", "changes": "changes_requested"}.get(state["decision"], "rejected")

    # the author's copy, in their schema (system: the admin is not its owner)
    async with tenant_session(state["tenant"], system=True) as s:
        sub = await s.scalar(select(Submission).where(Submission.id == state["submission_id"]))
        if sub is not None:
            sub.status, sub.notes, sub.decided_at = status, state.get("notes", ""), now
        if status == "approved":
            agent = await s.scalar(select(Agent).where(Agent.id == state["agent_id"]))
            if agent is not None:
                agent.status = "live"

    # the shared side: the index, and - only on approval - the marketplace
    async with platform_session() as p:
        idx = await p.scalar(select(SubmissionIndex).where(SubmissionIndex.submission_id == state["submission_id"]))
        if idx is not None:
            idx.status = status
        if status == "approved":
            p.add(Listing(
                submission_id=state["submission_id"],
                name=state["listing"]["name"],
                description=state["listing"]["description"],
                config=state["listing"],
                publisher=state["company"],
                quality=state["quality"],
                grade=state["grade"],
            ))

    return {"log": [f"Admin decision: {status}." + (f" Notes: {state['notes']}" if state.get("notes") else "")]}


def publish_graph(checkpointer):
    g = StateGraph(PublishState)
    g.add_node("sanitize", sanitize)
    g.add_node("admin_review", admin_review)
    g.add_node("decide", decide)
    g.add_edge(START, "sanitize")
    g.add_edge("sanitize", "admin_review")
    g.add_edge("admin_review", "decide")
    g.add_edge("decide", END)
    return g.compile(checkpointer=checkpointer)
