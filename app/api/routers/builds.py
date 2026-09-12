"""Build endpoints - the two graded pauses, over HTTP.

    POST /v1/builds                  start; returns 202 + an interrupt payload
    POST /v1/builds/{thread}/resume  answer it; returns 202 again, or 200 + agent
    GET  /v1/builds/{thread}         what is this build waiting for?

The GET matters more than it looks: it is what makes "close the tab, restart the
server, come back tomorrow" work. The browser holds only a thread id; the state
lives in t_<tenant>.checkpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.deps import NOT_FOUND, current_user
from app.builder.graph import build_graph
from app.core.security import Claims
from app.tenancy.checkpointers import checkpointer_for

router = APIRouter(prefix="/v1/builds", tags=["build"])


class StartIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)


class ResumeIn(BaseModel):
    """Whatever the pending interrupt asked for.

    select_tools       -> {"selected": ["github.list_issues", ...]}
    missing_connection -> {"action": "connected"} or {"action": "skip"}
    """

    selected: list[str] | None = None
    action: str | None = None


class BuildOut(BaseModel):
    thread_id: str
    status: str  # waiting | done | nothing_to_do
    interrupt: dict | None = None
    log: list[str] = []
    agent_id: str | None = None
    config: dict | None = None


async def _graph(claims: Claims):
    return build_graph(await checkpointer_for(claims.tenant_key))


def _shape(thread_id: str, result: dict[str, Any]) -> BuildOut:
    pending = result.get("__interrupt__")
    if pending:
        return BuildOut(
            thread_id=thread_id,
            status="waiting",
            interrupt=pending[0].value,
            log=result.get("log", []),
        )
    if result.get("agent_id"):
        return BuildOut(
            thread_id=thread_id,
            status="done",
            log=result.get("log", []),
            agent_id=result["agent_id"],
            config=result.get("config"),
        )
    return BuildOut(thread_id=thread_id, status="nothing_to_do", log=result.get("log", []))


@router.post("", response_model=BuildOut, status_code=202)
async def start_build(body: StartIn, claims: Claims = Depends(current_user)):
    import uuid

    thread_id = f"build-{uuid.uuid4().hex[:12]}"
    graph = await _graph(claims)
    result = await graph.ainvoke(
        {"prompt": body.prompt, "tenant": claims.tenant_key, "log": []},
        config={"configurable": {"thread_id": thread_id}},
    )
    return _shape(thread_id, result)


class FormIn(BaseModel):
    """The form path. Same fields the two interrupts would have asked for."""

    prompt: str = Field(min_length=3, max_length=2000)
    selected: list[str] = Field(min_length=1, description="tool refs, 'server.tool'")
    on_missing: str = Field(default="skip", pattern=r"^(skip|connected)$")


@router.post("/form", response_model=BuildOut, status_code=202)
async def build_from_form(body: FormIn, claims: Claims = Depends(current_user)):
    """Build from a filled-in form instead of a chat.

    The mockup is explicit: "Both paths must produce the same agent
    configuration - do not write the builder twice."

    So this does not have its own logic. It drives the SAME graph, answering the
    same two interrupts with what the form already collected. Every validation,
    every approval rule and every database write is the same code. The only
    difference is that nobody had to wait.
    """
    import uuid

    thread_id = f"form-{uuid.uuid4().hex[:12]}"
    graph = await _graph(claims)
    cfg = {"configurable": {"thread_id": thread_id}}

    result = await graph.ainvoke(
        {"prompt": body.prompt, "tenant": claims.tenant_key, "log": []}, config=cfg
    )

    # Answer whatever the graph stops on, up to a small bound so a loop cannot
    # run away (check_connections -> ask -> check_connections is deliberate).
    for _ in range(4):
        pending = result.get("__interrupt__")
        if not pending:
            break
        kind = pending[0].value.get("type")
        if kind == "select_tools":
            answer: Any = body.selected
        elif kind == "missing_connection":
            # "connected" would loop back to re-check; from a form the honest
            # answer to a still-missing credential is to build without it.
            answer = {"action": body.on_missing if _ == 0 else "skip"}
        else:  # unknown interrupt: stop rather than guess
            break
        result = await graph.ainvoke(Command(resume=answer), config=cfg)

    return _shape(thread_id, result)


@router.post("/{thread_id}/resume", response_model=BuildOut, status_code=202)
async def resume_build(
    thread_id: str, body: ResumeIn, claims: Claims = Depends(current_user)
):
    graph = await _graph(claims)

    # The interrupt decides what a resume value looks like.
    payload: Any = body.selected if body.selected is not None else {"action": body.action}

    result = await graph.ainvoke(
        Command(resume=payload),
        config={"configurable": {"thread_id": thread_id}},
    )
    return _shape(thread_id, result)


@router.get("/{thread_id}", response_model=BuildOut)
async def get_build(thread_id: str, claims: Claims = Depends(current_user)):
    """Reload a build that is still paused.

    Reads the checkpointer for THIS tenant's schema, so a thread id belonging to
    another company simply is not there - 404, same as any unknown id.
    """
    graph = await _graph(claims)
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if snapshot is None or not snapshot.created_at:
        raise HTTPException(404, detail=NOT_FOUND)

    pending = snapshot.tasks and snapshot.tasks[0].interrupts
    if pending:
        return BuildOut(
            thread_id=thread_id,
            status="waiting",
            interrupt=pending[0].value,
            log=snapshot.values.get("log", []),
        )
    return BuildOut(
        thread_id=thread_id,
        status="done" if snapshot.values.get("agent_id") else "nothing_to_do",
        log=snapshot.values.get("log", []),
        agent_id=snapshot.values.get("agent_id"),
        config=snapshot.values.get("config"),
    )
