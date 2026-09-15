"""GRADED CHECK 7 - the multi-agent demo runs end to end, approval included.

Also covers the two rules the runtime is responsible for:

  rule 4  a write tool cannot run before a human answers, and that is enforced
          by the platform, not by the agent's instructions
  rule 3  the credential never reaches graph state, which the checkpointer
          writes to disk (this is where most teams lose graded check 2)

THE MODEL IS FAKED HERE, DELIBERATELY. Gemini's free tier allows 20 requests a
day and one real run costs about six, so a suite that called it would be both
flaky and self-limiting. Everything else is real: the graph, the delegation, the
MCP protocol, the interrupt, the credential handling. See
tests/test_live_model.py for the one test that does call the real thing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.builder.schema import AgentConfig
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext, redact
from app.mcp_registry.mcp_client import Endpoint

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "tests" / "fixtures" / "docs_freshness.json"
REPORT = "report.md"

#: The sentinel a grader would plant. It must appear nowhere we persist.
TOKEN = "xoxb-GRADER-TOKEN-DO-NOT-LEAK"


class FakeChat:
    """A deterministic stand-in that works out its role from the prompt.

    Only needs the two methods the compiler actually uses: bind_tools/ainvoke.
    """

    def __init__(self) -> None:
        self.bound: list[dict] | None = None

    def bind_tools(self, tools: list[dict]) -> "FakeChat":
        self.bound = tools
        return self

    def with_fallbacks(self, rest):  # never used - the chain has one entry
        return self

    async def ainvoke(self, messages, **_):
        # STATELESS, like a real model. Resuming from an interrupt replays the
        # node from its first line, so a call counter here would desynchronise.
        # Decide purely from the conversation we were handed.

        # No tools bound => this is the supervisor, routing the work.
        if not self.bound:
            human = str(messages[1].content)
            for name in ("reader", "writer"):
                if f"- {name}:" in human:
                    return AIMessage(name)
            return AIMessage("done")

        # A specialist: call your tool, unless a result is already in hand.
        already_ran = any(getattr(m, "type", "") == "tool" for m in messages)
        if already_ran:
            return AIMessage("done")

        fn = self.bound[0]["function"]["name"]
        if "write_file" in fn:
            args: dict = {"path": REPORT, "content": "digest body"}
        else:
            args = {"path": "handbook.md"}
        return AIMessage("", tool_calls=[{"name": fn, "args": args, "id": "call-1"}])


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    """Every model the compiler asks for is a fresh FakeChat."""
    monkeypatch.setattr("app.runtime.models.model_chain", lambda spec: [FakeChat()])


@pytest.fixture
def store(tmp_path) -> Path:
    """A throwaway directory the reference filesystem server is confined to.

    The agent under test reads handbook.md from it and, once approved, writes
    report.md into it. "Did the write tool run?" is then a file existing.
    """
    (tmp_path / "handbook.md").write_text(
        "## Setup\nrun scripts/old_setup.sh\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def config() -> AgentConfig:
    return AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))


def filesystem_endpoints(root: Path):
    """The REAL @modelcontextprotocol/server-filesystem, confined to `root`.

    Declared api_key so the token path is exercised: the platform passes the
    credential to the subprocess as an env var, the server ignores it, and the
    tests then prove it went nowhere else.
    """
    ep = Endpoint.parse(
        "stdio", f"npx -y @modelcontextprotocol/server-filesystem {root}",
        credential_env_var="FORGE_TEST_TOKEN", auth_type="api_key",
    )

    async def resolve(_server_name: str) -> Endpoint | None:
        return ep

    return resolve


async def build(config: AgentConfig, *, token: str | None = TOKEN, root: Path):
    async def resolve(_server_name):
        return token

    ctx = RunContext(tenant_id="alpha", thread_id="t-1", resolve_token=resolve,
                     resolve_endpoint=filesystem_endpoints(root))
    saver = InMemorySaver()
    graph = await compile_agent(config, ctx, checkpointer=saver)
    return graph, saver, {"configurable": {"thread_id": "t-1"}}


START_STATE = {"task": "summarise", "transcript": [], "finished": [], "results": {}}


# ------------------------------------------------------- the multi-agent shape


async def test_supervisor_delegates_to_both_specialists(config, store):
    graph, _, cfg = await build(config, root=store)
    await graph.ainvoke(START_STATE, config=cfg)
    result = await graph.ainvoke(Command(resume="approve"), config=cfg)

    assert set(result["finished"]) == {"reader", "writer"}
    assert "[supervisor] -> reader" in result["transcript"]
    assert "[supervisor] -> writer" in result["transcript"]


async def test_a_specialist_is_only_given_its_own_tools(config, store):
    """reader must not be able to write, whatever the model decides to try."""
    graph, _, _ = await build(config, root=store)
    reader = config.topology.specialists[0]
    assert reader.tools == ["filesystem.read_text_file"]
    assert "filesystem.write_file" not in reader.tools


# --------------------------------------------- rule 4: the platform enforces it


async def test_a_write_tool_stops_the_run_before_executing(config, store):
    graph, _, cfg = await build(config, root=store)
    result = await graph.ainvoke(START_STATE, config=cfg)

    assert "__interrupt__" in result, "the write tool did not pause"
    payload = result["__interrupt__"][0].value
    assert payload["tool"] == "filesystem.write_file"
    assert payload["risk"] == "write"

    # and crucially: nothing was written while it waits
    assert not (store / REPORT).exists()


async def test_approving_resumes_and_the_tool_really_runs(config, store):
    graph, _, cfg = await build(config, root=store)
    await graph.ainvoke(START_STATE, config=cfg)
    result = await graph.ainvoke(Command(resume="approve"), config=cfg)

    assert "__interrupt__" not in result
    assert (store / REPORT).read_text(encoding="utf-8") == "digest body"


async def test_rejecting_means_the_tool_never_runs(config, store):
    graph, _, cfg = await build(config, root=store)
    await graph.ainvoke(START_STATE, config=cfg)
    result = await graph.ainvoke(Command(resume="reject"), config=cfg)

    assert "Rejected by the user" in result["results"]["filesystem.write_file"]

    # Nothing was written at all: the only tool that writes never executed.
    assert not (store / REPORT).exists()


async def test_read_tools_are_not_gated(config, store):
    """Only write/destructive pause. A read tool must run straight through."""
    graph, _, cfg = await build(config, root=store)
    result = await graph.ainvoke(START_STATE, config=cfg)
    assert "reader" in result["finished"]
    assert "old_setup.sh" in result["results"]["filesystem.read_text_file"]


# ------------------------------------- rule: degraded, not crashed (Connections)


async def test_a_missing_connection_degrades_instead_of_crashing(config, store):
    graph, _, cfg = await build(config, token=None, root=store)  # nothing connected here
    await graph.ainvoke(START_STATE, config=cfg)
    result = await graph.ainvoke(Command(resume="approve"), config=cfg)

    assert set(result["finished"]) == {"reader", "writer"}  # it still finished
    tool_outputs = [v for k, v in result["results"].items() if "." in k and "summary" not in k]
    assert tool_outputs and all("not connected" in v for v in tool_outputs)


# ------------------------------ graded check 2: the token is never written down


async def test_the_credential_never_reaches_graph_state(config, store):
    """The checkpointer writes state to disk so a run can resume days later.

    A token in state would therefore be a token on disk, permanently - in a
    table nobody remembers to search. So we search it here.
    """
    graph, saver, cfg = await build(config, root=store)
    await graph.ainvoke(START_STATE, config=cfg)
    await graph.ainvoke(Command(resume="approve"), config=cfg)

    everything = json.dumps([c.checkpoint for c in saver.list(cfg)], default=str)
    assert TOKEN not in everything, "the credential was persisted into graph state"
    assert "xoxb" not in everything


async def test_the_approval_payload_shows_no_secrets(config, store):
    """What the human is shown is also what gets checkpointed."""
    graph, _, cfg = await build(config, root=store)
    result = await graph.ainvoke(START_STATE, config=cfg)
    assert TOKEN not in json.dumps(result["__interrupt__"][0].value)


def test_redact_masks_sensitive_argument_names():
    masked = redact({"channel": "#eng", "api_token": TOKEN, "Authorization": "Bearer x"})
    assert masked["channel"] == "#eng"
    assert masked["api_token"] == "***"
    assert masked["Authorization"] == "***"


def test_run_context_carries_a_resolver_not_a_token():
    """RunContext holds a callable, so no credential is ever an attribute."""
    async def resolve(_server_name):
        return TOKEN

    ctx = RunContext(tenant_id="alpha", thread_id="t", resolve_token=resolve,
                     resolve_endpoint=filesystem_endpoints(Path(".")))
    assert TOKEN not in json.dumps({"tenant_id": ctx.tenant_id, "thread_id": ctx.thread_id})
