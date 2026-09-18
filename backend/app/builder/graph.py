"""The builder: a sentence in, an agent configuration in the database out.

    understand -> search_registry -> [INTERRUPT 1: pick servers]
               -> check_connections -> [INTERRUPT 2: missing credential]?
               -> assemble -> persist

The brief on those two stops:

    "Those two moments where it stops and waits for me are the centre of this
     project. They are LangGraph interrupts, and the build must survive a server
     restart while it is paused and pick up exactly where it left off."

They survive a restart because the graph is compiled with the per-company
AsyncPostgresSaver. While paused, nothing is running - the build is a row in
t_<company>.checkpoints, under a thread id that names its owner.
"""

from __future__ import annotations

import re

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
from app.mcp_registry import registry
from app.runtime.models import chat_model

log = logging.getLogger(__name__)

DESIGN_RULES = "You design agents for a platform. Given a request and the tools this workspace actually has, choose the SMALLEST set of tools that does the job - usually 1 to 3. Rules:\n- Use ONLY refs from the list, exactly as written.\n- Never include a tool for something the request did not ask for. 'Summarise issues' needs a list tool, not a create tool.\n- Prefer the most specific tool: read_text_file over read_file or read_media_file; list_issues over search_issues for 'my open issues'.\n- Include a write tool only if the request asks for something to be created, written, sent or posted.\n- If the job has distinct phases (gather, then act), split it into 2-3 specialists, each with only the tools for its phase, and write how the coordinator sequences them. A job that both READS (list, get, search) and then WRITES (send, post, create) is ALWAYS split: one specialist gathers, another acts, so the one that writes holds nothing else. A single-phase job gets no specialists and plain instructions instead.\n- Instructions are what the agent will be told at run time: concrete, second person, no tool names.\n- If the request needs something NO listed tool can do (e.g. posting to Slack when there is no slack tool), do not substitute another tool for it. Put it under `unmet` - the kind of server it would need (one lowercase word such as slack, github, jira) and what it was for - and design the rest without it."

DEFAULT_MODEL = ModelSpec(provider="google_genai", name="gemini-flash-lite-latest", temperature=0)


class BuildState(TypedDict, total=False):
    """Everything the build carries between nodes.

    Note what is NOT here: no credential. The build reads whether a connection
    exists, never what it contains.
    """

    prompt: str
    tenant: str
    user: str  # whose build this is: their servers, their connections, their agent

    
    name: str
    description: str
    reasoning: str
    suggested: list[str]  # tool refs the model proposed
    unmet: list[dict]  # [{need, why}] - parts of the request no registered server can do
    instructions: str  # for a single agent
    supervisor_instructions: str  # for a coordinator
    specialists: list[dict]  # [{name, instructions, tool_refs}] when the job splits

    # from search_registry()
    catalogue: list[dict]  # every tool this workspace has, for the picker

    # from the first interrupt
    selected: list[str]  # tool refs the USER chose

    # from check_connections()
    required: list[str]  # server names
    missing: list[str]  # of those, the ones with no active connection

    # the result
    config: dict
    agent_id: str

    log: Annotated[list[str], operator.add]


# --------------------------------------------------------------- what the model returns


class SpecialistDraft(BaseModel):
    name: str = Field(description="short lowercase identifier, e.g. 'triager'")
    instructions: str = Field(description="One or two sentences: this worker's job")
    tool_refs: list[str] = Field(default_factory=list, description="the subset of tool refs this worker needs")


class UnmetNeed(BaseModel):
    need: str = Field(description="the kind of server that would do it: one lowercase word, e.g. 'slack'")
    why: str = Field(description="what the request wanted it for, a few words")


class Draft(BaseModel):
    """The shape we force the model into. No free-form parsing."""

    name: str = Field(description="Short product-style name, 2-4 words")
    description: str = Field(description="One sentence: what this agent does")
    reasoning: str = Field(description="One sentence: why these tools")
    tool_refs: list[str] = Field(
        default_factory=list,
        description="The MINIMUM set of tool refs, each exactly 'server.tool' from the list given",
    )
    instructions: str = Field(
        default="", description="Instructions for the agent itself, if it does the whole job alone"
    )
    supervisor_instructions: str = Field(
        default="", description="If split into specialists: how the coordinator sequences them"
    )
    specialists: list[SpecialistDraft] = Field(
        default_factory=list,
        description="Split the job into 2-3 specialists ONLY when it has distinct phases "
        "(e.g. gather information, then act on it). Otherwise leave empty.",
    )
    unmet: list[UnmetNeed] = Field(
        default_factory=list,
        description="Parts of the request that NO tool in the list can do. Never substitute; say so here.",
    )


# ------------------------------------------------------------------------- nodes


async def understand(state: BuildState) -> dict:
    """Read the request, look at what this workspace actually has, propose a design.

    ONE model call for the whole build. The catalogue is loaded from the
    database first so the model can only propose tools that really exist.
    """
    async with tenant_session(state["tenant"], state["user"]) as s:
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
            SystemMessage(DESIGN_RULES),
            HumanMessage(f"Request:\n{state['prompt']}\n\nTools available:\n{listing}"),
        ]
    )

    valid = {c["ref"] for c in catalogue}
    suggested = [r for r in draft.tool_refs if r in valid]
    specialists = [
        {"name": _ident(sp.name), "instructions": sp.instructions,
         "tool_refs": [r for r in sp.tool_refs if r in valid]}
        for sp in draft.specialists
    ]
    # every specialist tool is part of the suggestion; nothing hides behind a worker
    for sp in specialists:
        suggested += [r for r in sp["tool_refs"] if r not in suggested]
    if len(specialists) < 2:
        specialists = _split_read_write(suggested, catalogue) if not specialists else []

    # What the request asked for that this person's registry cannot do. The
    # honest answer is to say so at the first pause, not to build a smaller
    # agent and hope nobody notices.
    have = {c["server"] for c in catalogue}
    unmet = [{"need": _ident(u.need), "why": u.why} for u in draft.unmet if _ident(u.need) not in have]

    shape = (f"coordinator + {', '.join(sp['name'] for sp in specialists)}"
             if specialists else "single agent")
    line = f"Understood: {draft.name} - {shape}. {draft.reasoning}"
    if unmet:
        line += " Not in your registry: " + ", ".join(f"{u['need']} ({u['why']})" for u in unmet) + "."
    return {
        "catalogue": catalogue,
        "name": draft.name,
        "description": draft.description,
        "reasoning": draft.reasoning,
        "suggested": suggested,
        "unmet": unmet,
        "instructions": draft.instructions,
        "supervisor_instructions": draft.supervisor_instructions
        or ("Send the work to the collector first, then hand what it found to the poster." if specialists else ""),
        "specialists": specialists,
        "log": [line],
    }


def _split_read_write(suggested: list[str], catalogue: list[dict]) -> list[dict]:
    """The one shape rule that does not depend on the model's mood: a job that
    reads and then writes is always coordinator + collector + poster, so the
    worker holding the write tool holds nothing else. Same request, same shape,
    whoever asks. Returns [] when there is only one phase."""
    risk = {c["ref"]: c["risk"] for c in catalogue}
    reads = [r for r in suggested if risk.get(r) == "read"]
    writes = [r for r in suggested if risk.get(r) in ("write", "destructive")]
    if not reads or not writes:
        return []
    return [
        {"name": "collector", "tool_refs": reads,
         "instructions": "Gather everything the request asks for and hand back a clear, complete summary of what you found."},
        {"name": "poster", "tool_refs": writes,
         "instructions": "Take what the collector found and carry out the action the request asks for, exactly once."},
    ]


def _ident(name: str) -> str:
    """'Issue Triager' -> 'issue_triager': the identifier shape the config allows."""
    out = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_")
    return out if out and out[0].isalpha() else f"w_{out or 'worker'}"


def search_registry(state: BuildState) -> Command[Literal["check_connections", "understand", "__end__"]]:
    """INTERRUPT 1 - show what the platform thinks it needs, and wait.

    The build stops here. Nothing after this line runs until a human answers.
    The answer is the list of tool refs to grant - or {"action": "rescan"}
    after registering a server the request needed, which sends the build back
    to `understand` to design again against the bigger registry.
    """
    if not state.get("catalogue"):
        return Command(goto=END)

    chosen: list[str] | dict = interrupt(
        {
            "type": "select_tools",
            "name": state.get("name", ""),
            "description": state.get("description", ""),
            "reasoning": state.get("reasoning", ""),
            "suggested": state.get("suggested", []),
            "unmet": state.get("unmet", []),
            "specialists": state.get("specialists", []),
            "catalogue": state["catalogue"],
        }
    )

    if isinstance(chosen, dict):
        if chosen.get("action") == "rescan":
            return Command(goto="understand", update={"log": ["Looking at your registry again..."]})
        chosen = chosen.get("selected") or []

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

    async with tenant_session(state["tenant"], state["user"]) as s:
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

    async with tenant_session(state["tenant"], state["user"]) as s:
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
        "topology": _topology(state, [t["ref"] for t in tools]),
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


def _topology(state: BuildState, granted: list[str]) -> dict:
    """Coordinator + specialists when the design split the job, else one agent.

    A specialist keeps only the tools the user actually granted; a selected
    tool no specialist claimed goes to the first one so nothing is lost. If
    fewer than two specialists survive, the shape collapses to a single agent.
    """
    specs = []
    claimed: set[str] = set()
    for sp in state.get("specialists", []):
        tools = [r for r in sp["tool_refs"] if r in granted]
        specs.append({"name": sp["name"], "instructions": sp["instructions"], "tools": tools})
        claimed.update(tools)
    if len(specs) >= 2:
        specs[0]["tools"] += [r for r in granted if r not in claimed]
        return {
            "type": "supervisor",
            "supervisor": {
                "instructions": state.get("supervisor_instructions")
                or "Send the work to each specialist in order, passing on what the previous one found.",
                "delegates_to": [sp["name"] for sp in specs],
            },
            "specialists": specs,
        }
    return {
        "type": "single",
        "supervisor": {
            "instructions": state.get("instructions") or state.get("description", ""),
            "delegates_to": [],
        },
        "specialists": [],
    }


async def persist(state: BuildState) -> dict:
    """INSERT INTO agents. This is the row My Agents will show."""
    if not state.get("config"):
        return {}

    async with tenant_session(state["tenant"], state["user"]) as s:
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
