"""The builder: a sentence in, an agent configuration in the database out.

    understand -> search_registry -> [INTERRUPT 1: pick servers]
               -> check_connections -> [INTERRUPT 2: missing credential]?
               -> assemble -> persist

The brief on those two stops:

    "Those two moments where it stops and waits for me are the centre of this
     project. They are LangGraph interrupts, and the build must survive a server
     restart while it is paused and pick up exactly where it left off."

They survive a restart because the graph is compiled with the per-tenant
AsyncPostgresSaver. While paused, nothing is running - the build is a row in
t_<tenant>.checkpoints.
"""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.builder.schema import (
    GUARDED,
    AgentConfig,
    Approval,
    ModelSpec,
    Risk,
)
from app.core.db import tenant_session
from app.models.tenant import Agent, Connection
from app.mcp_registry import service as registry
from app.runtime.models import chat_model

log = logging.getLogger(__name__)

DEFAULT_MODEL = ModelSpec(provider="google_genai", name="gemini-flash-lite-latest", temperature=0)


class BuildState(TypedDict, total=False):
    """Everything the build carries between nodes.

    Note what is NOT here: no credential. The build reads whether a connection
    exists, never what it contains.
    """

    prompt: str
    tenant: str

    # from understand()
    name: str
    description: str
    reasoning: str
    suggested: list[str]  # tool refs the model proposed

    # from search_registry()
    catalogue: list[dict]  # every tool this workspace has, for the picker

    # from the first interrupt
    selected: list[str]  # tool refs the USER chose

    # from check_connections()
    required: list[str]  # server slugs
    missing: list[str]  # of those, the ones with no active connection

    # the result
    config: dict
    agent_id: str

    log: Annotated[list[str], operator.add]


# --------------------------------------------------------------- what the model returns


class Draft(BaseModel):
    """The shape we force the model into. No free-form parsing."""

    name: str = Field(description="Short product-style name, 2-4 words")
    description: str = Field(description="One sentence: what this agent does")
    reasoning: str = Field(description="One sentence: why these tools")
    tool_refs: list[str] = Field(
        default_factory=list,
        description="Tool refs to use, each exactly 'server.tool' from the list given",
    )
    specialists: list[str] = Field(
        default_factory=list,
        description="2 short lowercase worker names if the job splits in two, else empty",
    )


# ------------------------------------------------------------------------- nodes


async def understand(state: BuildState) -> dict:
    """Read the request, look at what this workspace actually has, propose a design.

    ONE model call for the whole build. The catalogue is loaded from the
    database first so the model can only propose tools that really exist.
    """
    async with tenant_session(state["tenant"]) as s:
        views = await registry.list_servers(s)  # this company's + shared

    catalogue = [
        {
            "ref": f"{v.name}.{t.name}",
            "risk": t.risk,
            "description": t.description[:120],
            "server": v.name,
        }
        for v in views
        for t in v.tools
    ]

    if not catalogue:
        return {
            "catalogue": [],
            "name": "",
            "log": ["No tool servers registered yet - register one first."],
        }

    listing = "\n".join(f"  {c['ref']} ({c['risk']}) - {c['description']}" for c in catalogue)

    llm = chat_model(DEFAULT_MODEL).with_structured_output(Draft)
    draft: Draft = await llm.ainvoke(
        [
            SystemMessage(
                "You design agents for a platform. Given a request and the tools this "
                "workspace actually has, choose the smallest set of tools that does the "
                "job. Use ONLY refs from the list. Prefer read tools; include a write "
                "tool only if the request asks for something to be created or sent."
            ),
            HumanMessage(f"Request:\n{state['prompt']}\n\nTools available:\n{listing}"),
        ]
    )

    valid = {c["ref"] for c in catalogue}
    suggested = [r for r in draft.tool_refs if r in valid]

    return {
        "catalogue": catalogue,
        "name": draft.name,
        "description": draft.description,
        "reasoning": draft.reasoning,
        "suggested": suggested,
        "log": [f"Understood: {draft.name}. {draft.reasoning}"],
    }


def search_registry(state: BuildState) -> Command[Literal["check_connections", "__end__"]]:
    """INTERRUPT 1 - show what the platform thinks it needs, and wait.

    The build stops here. Nothing after this line runs until a human answers.
    """
    if not state.get("catalogue"):
        return Command(goto=END)

    chosen: list[str] = interrupt(
        {
            "type": "select_tools",
            "name": state.get("name", ""),
            "description": state.get("description", ""),
            "reasoning": state.get("reasoning", ""),
            "suggested": state.get("suggested", []),
            "catalogue": state["catalogue"],
        }
    )

    valid = {c["ref"] for c in state["catalogue"]}
    selected = [r for r in chosen if r in valid]
    rejected = [r for r in chosen if r not in valid]

    # Say what was dropped and why. Silently ignoring a request is how a user
    # ends up with an agent that is missing the tool they asked for.
    note = f"You chose {len(selected)} tools."
    if rejected:
        note += (
            f" Ignored {', '.join(rejected)} - not in this workspace's registry."
            " Register the server first."
        )

    return Command(
        goto="check_connections",
        update={"selected": selected, "log": [note]},
    )


async def check_connections(state: BuildState) -> dict:
    """Which servers does the chosen set need, and are we connected to each?"""
    required = sorted({ref.split(".", 1)[0] for ref in state.get("selected", [])})

    async with tenant_session(state["tenant"]) as s:
        active = {
            c.server_name
            for c in await s.scalars(select(Connection).where(Connection.status == "active"))
        }
        needs_auth = {v.name for v in await registry.list_servers(s) if v.auth_type != "none"}

    # A server that needs no credential is never "missing".
    missing = [name for name in required if name in needs_auth and name not in active]

    return {
        "required": required,
        "missing": missing,
        "log": [
            f"Connections: {', '.join(required) or 'none'}."
            + (f" Missing: {', '.join(missing)}." if missing else " All present.")
        ],
    }


def ask_for_connection(state: BuildState) -> Command[Literal["assemble", "check_connections"]]:
    """INTERRUPT 2 - only fires when a credential is genuinely missing."""
    answer: dict[str, Any] = interrupt(
        {
            "type": "missing_connection",
            "missing": state["missing"],
            "required": state["required"],
            "name": state.get("name", ""),
        }
    )

    if answer.get("action") == "skip":
        # Drop every tool belonging to a server we still cannot reach.
        keep = [r for r in state["selected"] if r.split(".", 1)[0] not in state["missing"]]
        return Command(
            goto="assemble",
            update={"selected": keep, "log": ["Skipped - built without those tools."]},
        )

    # "I have connected it now" is a CLAIM, so go and look rather than believe it.
    # If it is still missing, check_connections routes straight back here and the
    # user is asked again - which is the correct answer to clicking too early.
    return Command(
        goto="check_connections",
        update={"log": ["Checking the connection again..."]},
    )


async def assemble(state: BuildState) -> dict:
    """Write the configuration document.

    Risk and approval come from the REGISTRY, never from anything the model or
    the user said. That is graded check 3, applied at the moment of creation.
    """
    selected = state.get("selected", [])
    if not selected:
        return {"log": ["Nothing selected - no agent was created."]}

    async with tenant_session(state["tenant"]) as s:
        by_ref = {
            f"{v.name}.{t.name}": t
            for v in await registry.list_servers(s)
            for t in v.tools
        }

    tools = []
    for ref in selected:
        row = by_ref.get(ref)
        if row is None:
            continue
        risk = Risk(row.risk)
        tools.append(
            {
                "ref": ref,
                "risk": str(risk),
                # THE control: write and destructive always ask, whatever anyone wants.
                "approval": str(Approval.ASK if risk in GUARDED else Approval.AUTO),
                "requires_connection": ref.split(".", 1)[0],
            }
        )

    config = {
        "schema_version": "1.0",
        "name": state.get("name") or "Untitled agent",
        "description": state.get("description") or state["prompt"][:200],
        "model": DEFAULT_MODEL.model_dump(),
        "topology": {"type": "single", "supervisor": None, "specialists": []},
        "tools": tools,
        "policy": {"approval_required_for": ["write", "destructive"], "max_tool_calls": 25},
        "requires_connections": sorted({t["requires_connection"] for t in tools}),
        "schedule": None,
    }

    # Validate before it is allowed anywhere near the database.
    validated = AgentConfig.model_validate(config)
    guarded = [t.ref for t in validated.guarded_tools]

    return {
        "config": validated.model_dump(mode="json"),
        "log": [
            f"Built {validated.name} with {len(validated.tools)} tools."
            + (f" {', '.join(guarded)} will ask before running." if guarded else "")
        ],
    }


async def persist(state: BuildState) -> dict:
    """INSERT INTO agents. This is the row My Agents will show."""
    if not state.get("config"):
        return {}

    async with tenant_session(state["tenant"]) as s:
        agent = Agent(
            name=state["config"]["name"],
            config=state["config"],
            status="draft",
        )
        s.add(agent)
        await s.flush()
        agent_id = str(agent.id)

    return {"agent_id": agent_id, "log": ["Saved. Open it from My Agents."]}


# ------------------------------------------------------------------------- wiring


def _after_connections(state: BuildState) -> Literal["ask_for_connection", "assemble"]:
    return "ask_for_connection" if state.get("missing") else "assemble"


def build_graph(checkpointer):
    g = StateGraph(BuildState)
    g.add_node("understand", understand)
    g.add_node("search_registry", search_registry)
    g.add_node("check_connections", check_connections)
    g.add_node("ask_for_connection", ask_for_connection)
    g.add_node("assemble", assemble)
    g.add_node("persist", persist)

    g.add_edge(START, "understand")
    g.add_edge("understand", "search_registry")
    # search_registry and ask_for_connection route with Command, so no edge here.
    g.add_conditional_edges("check_connections", _after_connections)
    g.add_edge("assemble", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=checkpointer)
