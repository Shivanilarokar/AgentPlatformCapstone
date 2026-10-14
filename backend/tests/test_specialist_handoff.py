"""The next specialist gets the whole of what the last one wrote.

Found in use: "top 5 trending AI repos from the web, report to Slack". The
researcher wrote a 2,226-character report of five repositories; the sender was
handed it clipped to 400 characters, newlines flattened, ending in "..." - and
posted exactly that to Slack: one and a half repositories.

A worker's summary is its hand-off and goes over whole. Raw tool output stays
clipped: the summary already says what mattered in it, and one web search
result runs to pages.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app.builder.schema import AgentConfig
from app.runtime import compiler
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext

CONFIG = AgentConfig.model_validate({
    "schema_version": "1.0",
    "name": "Trending Repo Reporter",
    "description": "Finds trending AI repositories and posts a report.",
    "model": {"provider": "google_genai", "name": "gemini-flash-lite-latest", "temperature": 0},
    "topology": {"type": "supervisor",
                 "supervisor": {"instructions": "Researcher first, then poster.",
                                "delegates_to": ["researcher", "poster"]},
                 "specialists": [
                     {"name": "researcher", "instructions": "Write the report.", "tools": ["github.list_issues"]},
                     {"name": "poster", "instructions": "Post the report.", "tools": []},
                 ]},
    "tools": [{"ref": "github.list_issues", "risk": "read", "approval": "auto",
               "requires_connection": "github"}],
    "policy": {"approval_required_for": ["write", "destructive"], "max_tool_calls": 25},
    "requires_connections": ["github"],
    "schedule": None,
})

REPORT = "# Trending AI repositories\n\n" + "\n".join(
    f"{i}. **owner/repo-{i}**\n   * Description: " + "a long line about what it does. " * 6
    for i in range(1, 6)
)


class Stub:
    """Supervisor always says 'researcher' (the graph falls back to the next
    worker once that one is done); the researcher writes REPORT; the poster
    records what it was handed."""

    def __init__(self):
        self.poster_saw = ""

    def bind_tools(self, _tools):
        return self

    def with_fallbacks(self, _rest):
        return self

    async def ainvoke(self, messages, **_):
        system = str(messages[0].content)
        if system.startswith("You are 'researcher'"):
            return AIMessage(REPORT)
        if system.startswith("You are 'poster'"):
            self.poster_saw = str(messages[1].content)
            return AIMessage("Sent.")
        return AIMessage("researcher")


async def no_endpoint(_name):
    return None


async def no_token(_name):
    return None


async def test_the_poster_is_handed_the_whole_report(monkeypatch):
    model = Stub()
    monkeypatch.setattr(compiler, "chat_model_with_tools", lambda _spec, _tools: model)
    monkeypatch.setattr(compiler, "chat_model", lambda _spec: model)
    ctx = RunContext(tenant_id="t", thread_id="th", resolve_token=no_token, resolve_endpoint=no_endpoint)
    graph = await compile_agent(CONFIG, ctx)
    await graph.ainvoke({"task": "Report the top 5 repos", "transcript": [], "finished": [], "results": {}})

    assert len(REPORT) > 1000                       # well past the old 400-character clip
    assert REPORT in model.poster_saw               # all five, line breaks and all


def test_raw_tool_output_is_still_clipped():
    page = "result " * 2000
    context = compiler._context({"results": {"tavily.tavily-search": page, "researcher.summary": REPORT}})
    assert REPORT in context
    assert len(context) < len(REPORT) + 500
