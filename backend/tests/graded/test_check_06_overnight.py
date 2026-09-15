"""GRADED CHECK 6 - an approval left pending overnight still resumes the next day.

    "That approval might sit there for three days. Across two deploys and a
     weekend. Your system has to handle that, and answering must resume the run
     from exactly where it stopped."

The publish graph is parked on `admin_review` in the author's company
checkpoint tables. "Overnight, across a deploy" is simulated the honest way:
every in-process object is thrown away - checkpointer pools closed, caches
emptied, a new graph compiled - and the run is answered from that cold start.
Only Postgres carries it across.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.core.db import platform_session, tenant_session
from app.models.platform_ import Listing, SubmissionIndex, Tenant
from app.models.tenant import Agent, Submission
from app.publishing.graph import publish_graph
from app.tenancy import checkpointers
from tests.conftest import ALPHA, ALPHA_ANNE
from tests.graded.test_check_04_sanitize import COMPANY, planted_config

from app.publishing.sanitize import listing_from


async def _submit() -> tuple[str, str]:
    listing = listing_from(planted_config(), company=COMPANY)
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        agent = Agent(name="overnight", config=planted_config().model_dump(mode="json"), status="draft")
        s.add(agent)
        await s.flush()
        sub = Submission(agent_id=agent.id, thread_id="", listing=listing, score={"quality": 90, "grade": "A"})
        s.add(sub)
        await s.flush()
        sub.thread_id = f"{ALPHA_ANNE}/pub-{sub.id}"
        sub_id, agent_id, thread = str(sub.id), str(agent.id), sub.thread_id
    async with platform_session() as p:
        p.add(SubmissionIndex(submission_id=uuid.UUID(sub_id), tenant_key=ALPHA, company=COMPANY,
                              owner_id=uuid.UUID(ALPHA_ANNE), agent_id=uuid.UUID(agent_id),
                              thread_id=thread, listing=listing, quality=90, grade="A", checks={}))

    graph = publish_graph(await checkpointers.checkpointer_for(ALPHA))
    result = await graph.ainvoke(
        {"tenant": ALPHA, "owner": ALPHA_ANNE, "company": COMPANY, "submission_id": sub_id,
         "agent_id": agent_id, "listing": listing, "quality": 90, "grade": "A", "checks": {}, "log": []},
        config={"configurable": {"thread_id": thread}},
    )
    assert "__interrupt__" in result and result["__interrupt__"][0].value["type"] == "admin_review"
    return sub_id, thread


async def _overnight() -> None:
    """Throw away everything the process holds. What survives is in Postgres."""
    await checkpointers.close_all()
    checkpointers._savers.clear()
    checkpointers._pools.clear()


async def _cleanup(sub_id: str) -> None:
    async with platform_session() as p:
        await p.execute(delete(Listing).where(Listing.submission_id == uuid.UUID(sub_id)))
        await p.execute(delete(SubmissionIndex).where(SubmissionIndex.submission_id == uuid.UUID(sub_id)))


async def test_a_parked_review_survives_a_restart_and_approves():
    sub_id, thread = await _submit()
    await _overnight()

    # a brand-new process: nothing cached, the graph compiled from scratch
    graph = publish_graph(await checkpointers.checkpointer_for(ALPHA))
    cfg = {"configurable": {"thread_id": thread}}
    snapshot = await graph.aget_state(cfg)
    assert snapshot.tasks and snapshot.tasks[0].interrupts, "the approval was not waiting after the restart"

    from langgraph.types import Command
    await graph.ainvoke(Command(resume={"decision": "approve", "notes": "looks honest"}), config=cfg)

    async with platform_session() as p:
        listing = await p.scalar(select(Listing).where(Listing.submission_id == uuid.UUID(sub_id)))
        assert listing is not None and listing.publisher == COMPANY
        assert "northwind" not in str(listing.config).lower()  # check 4, end to end
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        sub = await s.scalar(select(Submission).where(Submission.id == uuid.UUID(sub_id)))
        assert sub.status == "approved" and sub.notes == "looks honest"
        agent = await s.scalar(select(Agent).where(Agent.id == sub.agent_id))
        assert agent.status == "live"
    await _cleanup(sub_id)


async def test_request_changes_goes_back_to_the_author_and_publishes_nothing():
    sub_id, thread = await _submit()
    await _overnight()

    graph = publish_graph(await checkpointers.checkpointer_for(ALPHA))
    from langgraph.types import Command
    await graph.ainvoke(Command(resume={"decision": "changes", "notes": "drop the tool it never calls"}),
                        config={"configurable": {"thread_id": thread}})

    async with platform_session() as p:
        assert await p.scalar(select(Listing).where(Listing.submission_id == uuid.UUID(sub_id))) is None
    async with tenant_session(ALPHA, ALPHA_ANNE) as s:
        sub = await s.scalar(select(Submission).where(Submission.id == uuid.UUID(sub_id)))
        assert sub.status == "changes_requested"
        assert "never calls" in sub.notes
    await _cleanup(sub_id)


async def test_there_is_exactly_one_way_into_the_marketplace():
    """Structural: the only `Listing(` constructor in app/ is inside the graph's
    decide node, which only runs after the admin_review interrupt is answered."""
    import pathlib
    import re

    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    hits = [
        str(p.relative_to(app_dir.parent)) for p in app_dir.rglob("*.py")
        if re.search(r"(?<!class )\bListing\((?!PlatformBase)", p.read_text(encoding="utf-8"))
    ]
    assert [h.replace("\\", "/") for h in hits] == ["app/publishing/graph.py"]
