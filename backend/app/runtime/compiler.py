"""The runtime - turn an agent configuration into a running LangGraph graph.

    graph = await compile_agent(config, ctx)

This is the only code in the platform that ever runs an agent. The playground
(app/api/routers/runs.py) calls it; the public API will call the same function,
so there is no second path where the approval gate could be missing.

Nothing here is generated. One function reads a config document and assembles a
graph from it - which is why the same function can safely run an agent designed
by a different company.

THE SHAPE
    START -> supervisor -> specialist -> supervisor -> ... -> END

The supervisor is a model deciding who works next. Each specialist is a model
with ONLY its own tools bound, looping until it has nothing left to call. Every
tool call goes through guarded_tool(), so a write pauses the whole graph.
"""

from __future__ import annotations

import logging
import operator
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.builder.schema import Approval, AgentConfig, ToolSpec, TopologyType
from app.runtime.guarded_tool import RunContext, guarded_tool
from app.mcp_registry.mcp_client import list_tools
from app.runtime.models import chat_model, chat_model_with_tools

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6  # per specialist, so a confused model cannot loop forever

#: Added to every worker's instructions. Nothing in an agent's configuration
#: says WHICH repository, channel or recipient it works on; that comes from the
#: person's message. A model that is not told to ask will invent one - and a
#: GitHub search of an invented repository returns somebody else's issues.
NO_GUESSING = (
    "If a tool needs a value you were not given - a repository, a channel, an address, a file path, "
    "an ID - do NOT guess or make one up, and never use a placeholder such as my-org/my-repo, "
    "owner/repo, example or <name>. Use what the user's task says; if it does not say, call no tool "
    "and reply asking for exactly that value, in the form it needs. For a repository say: "
    "'Please provide the repository name in owner/repo format.' Never say that something could not "
    "be found when you were never given a real value for it."
)

#: What a model writes when it has no real value and fills the gap anyway:
#: my-org, your-repo, example-org, owner/repo, <repo>, {owner}. The prompt above
#: asks it not to; this is the check that does not depend on it obeying.
_PLACEHOLDER = re.compile(
    r"\b(?:my|your|example|sample)[-_ ](?:org|organi[sz]ation|owner|repo|repository|username)\b"
    r"|\b(?:owner|org|user|username)/(?:repo|repository)\b"
    r"|^(?:<[a-z_ -]{1,30}>|\{[a-z_ -]{1,30}\}|owner|org|repo|repository|username)$",
    re.IGNORECASE,
)


def _placeholder(args: Any, key: str = "") -> tuple[str, str] | None:
    """The first argument whose value is a placeholder rather than something the
    user gave, as (name, value); None when every value looks real."""
    if isinstance(args, str):
        return (key, args) if _PLACEHOLDER.search(args.strip()) else None
    items = args.items() if isinstance(args, dict) else enumerate(args) if isinstance(args, list) else ()
    for k, v in items:
        if hit := _placeholder(v, str(k)):
            return hit
    return None


class RunState(TypedDict):
    """What flows through the graph.

    NOTE what is absent: no token, no connection id, nothing decrypted. The
    checkpointer writes this whole dict to Postgres so a run can resume days
    later, so a credential in here would be a credential on disk forever.
    """

    task: str
    transcript: Annotated[list[str], operator.add]
    finished: Annotated[list[str], operator.add]
    results: Annotated[dict[str, str], operator.or_]


async def _tool_schemas(config: AgentConfig, ctx: RunContext) -> dict[str, dict]:
    """Ask each server for its tools, so the model sees the REAL argument schema.

    Nobody hand-writes a tool signature here either - same rule as the registry.
    """
    schemas: dict[str, dict] = {}
    for server in config.requires_connections:
        try:
            ep = await ctx.resolve_endpoint(server)
            if ep is None:
                log.warning("%s is not registered in this workspace", server)
                continue
            for discovered in await list_tools(ep, token=await ctx.resolve_token(server)):
                schemas[f"{server}.{discovered.name}"] = discovered.input_schema
        except Exception as exc:  # noqa: BLE001 - a dead server must degrade, not crash
            log.warning("could not introspect %s: %s", server, exc)
    return schemas


def _tool_definition(spec: ToolSpec, schema: dict) -> dict:
    """The tool as the MODEL sees it: name, description, real argument schema.

    Deliberately a plain OpenAI-format dict rather than a StructuredTool. The
    schema comes straight from the MCP server, and converting it into a Pydantic
    model first loses arguments - which showed up as tools being called with
    half their parameters missing.
    """
    return {
        "type": "function",
        "function": {
            "name": spec.ref.replace(".", "__"),  # models dislike dots in tool names
            "description": f"{spec.ref} - risk: {spec.risk}",
            "parameters": schema or {"type": "object", "properties": {}},
        },
    }


async def compile_agent(
    config: AgentConfig,
    ctx: RunContext,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Build a runnable graph from a config document."""
    # The admin's allow-list wins over what the configuration says: a tool they
    # switched off after this agent was built is simply not there. Read on every
    # compile, so a run resumed days later after a pause honours it too.
    off = await ctx.resolve_disabled() if ctx.resolve_disabled else set()
    dropped = off & {t.ref for t in config.tools}
    if dropped:
        log.warning("tools switched off in the registry, left out of this agent: %s",
                    ", ".join(sorted(dropped)))
    by_ref = {t.ref: t for t in config.tools if t.ref not in dropped}
    schemas = await _tool_schemas(config, ctx)

    # The guarded runner for each tool. EVERY call the model makes lands here.
    runners = {ref: guarded_tool(spec, ctx) for ref, spec in by_ref.items()}
    definitions = {ref: _tool_definition(spec, schemas.get(ref, {})) for ref, spec in by_ref.items()}

    llm = chat_model(config.model)  # supervisor: no tools, by design

    # ---------------------------------------------------------------- workers

    def make_worker(name: str, instructions: str, tool_refs: list[str], goto: str):
        """A node where a model works with only the tools it was granted."""
        granted = [r for r in tool_refs if r in definitions]
        bound = (
            chat_model_with_tools(config.model, [definitions[r] for r in granted])
            if granted
            else llm
        )
        # model's tool name -> our tool ref
        by_call_name = {definitions[r]["function"]["name"]: r for r in granted}

        async def worker(state: RunState) -> Command:
            # Live progress for whoever is watching (the playground's SSE). A
            # no-op when nobody streams. Never carries tool arguments.
            tell = get_stream_writer()
            messages: list[Any] = [
                SystemMessage(f"You are '{name}'. {instructions}\n\n{NO_GUESSING}"),
                HumanMessage(
                    f"Task: {state['task']}\n\n"
                    f"What earlier steps found:\n{_context(state) or '(nothing yet)'}"
                ),
            ]
            lines: list[str] = []
            results: dict[str, str] = {}
            last_output = ""
            answered = False

            for _ in range(MAX_TOOL_ROUNDS):
                tell({"kind": "thinking", "worker": name})
                reply: AIMessage = await bound.ainvoke(messages)
                messages.append(reply)

                if not reply.tool_calls:
                    answered = True
                    if text := _text(reply):
                        lines.append(f"[{name}] {_clip(text)}")
                        results[f"{name}.summary"] = text
                    break

                for call in reply.tool_calls:
                    ref = by_call_name.get(call["name"])
                    if ref is None:
                        output = f"No such tool: {call['name']}"
                    elif bad := _placeholder(call["args"]):
                        # Never reaches the server: an invented repository would either
                        # fail ("could not be found") or return a stranger's issues.
                        output = (
                            f"Not run: {bad[0]} = {bad[1]!r} is a placeholder, not something the user "
                            "gave. Do not guess. Reply to the user asking for the real value, in the form "
                            "the tool needs (a repository is 'owner/repo'). Call no tool."
                        )
                    else:
                        tell({"kind": "tool_call", "worker": name, "tool": ref,
                              "risk": str(by_ref[ref].risk), "asks": by_ref[ref].approval is Approval.ASK})
                        # -- this is where the graph may stop and wait for a human
                        output = await runners[ref](**call["args"])
                        results[ref] = output
                        tell({"kind": "tool_result", "worker": name, "tool": ref,
                              "ok": not output.startswith("ERROR"), "summary": _clip(output, 140)})
                    lines.append(f"[{name}] {ref or call['name']} -> {_clip(output)}")
                    messages.append(ToolMessage(content=output, tool_call_id=call["id"]))
                    last_output = output

            if not answered:
                # The model kept calling tools until the limit and never said anything.
                # A run must not end as a silent "(no answer)" that reads as success:
                # ask once, with the tools taken away, for what it found or what stopped
                # it - and if it still will not answer, say so ourselves.
                tell({"kind": "thinking", "worker": name})
                messages.append(HumanMessage(
                    "You have used all the tool calls you are allowed. Do NOT call any more tools. "
                    "In two or three sentences say what you found, or what stopped you and what you "
                    "need from the user to carry on."
                ))
                final = await bound.ainvoke(messages)
                text = "" if final.tool_calls else _text(final)
                text = text or (
                    f"I used all {MAX_TOOL_ROUNDS} of my tool calls without reaching an answer. "
                    f"The last thing a tool told me was: {_clip(last_output, 200)}"
                )
                lines.append(f"[{name}] {_clip(text)}")
                results[f"{name}.summary"] = text

            return Command(
                goto=goto,
                update={"transcript": lines, "finished": [name], "results": results},
            )

        worker.__name__ = name
        return worker

    builder = StateGraph(RunState)

    if config.topology.type is TopologyType.SUPERVISOR:
        roster = {s.name: s for s in config.topology.specialists}
        order = config.topology.supervisor.delegates_to

        async def supervisor(state: RunState) -> Command:
            """A model routes the work. It has no tools of its own, by design."""
            tell = get_stream_writer()
            remaining = [n for n in order if n not in state["finished"]]
            if not remaining:
                return Command(goto=END, update={"transcript": ["[supervisor] done"]})

            options = "\n".join(f"- {n}: {roster[n].instructions}" for n in remaining)
            tell({"kind": "thinking", "worker": "supervisor"})
            reply = await llm.ainvoke(
                [
                    SystemMessage(
                        config.topology.supervisor.instructions
                        + "\n\nReply with ONLY the name of the next worker, nothing else."
                    ),
                    HumanMessage(
                        f"Task: {state['task']}\n\nStill available:\n{options}\n\n"
                        f"Already done: {state['finished'] or 'nothing'}\n\n"
                        f"Findings so far:\n{_context(state) or '(nothing yet)'}"
                    ),
                ]
            )
            choice = _text(reply).strip().strip(".`'\"").lower()
            nxt = next((n for n in remaining if n.lower() in choice), remaining[0])
            tell({"kind": "route", "to": nxt})
            return Command(goto=nxt, update={"transcript": [f"[supervisor] -> {nxt}"]})

        builder.add_node("supervisor", supervisor)
        builder.add_edge(START, "supervisor")
        for s in config.topology.specialists:
            builder.add_node(s.name, make_worker(s.name, s.instructions, s.tools, "supervisor"))

    else:
        instructions = config.topology.supervisor.instructions if config.topology.supervisor else ""
        builder.add_node("agent", make_worker("agent", instructions, list(by_ref), END))
        builder.add_edge(START, "agent")

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------- helpers


def _text(message: Any) -> str:
    """Gemini returns content as a list of blocks; other providers return a str."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


def _context(state: RunState) -> str:
    return "\n".join(f"{k}: {_clip(v, 400)}" for k, v in state["results"].items())


def _clip(text: str, limit: int = 90) -> str:
    flat = " ".join(str(text).split())
    return flat[:limit] + ("..." if len(flat) > limit else "")
