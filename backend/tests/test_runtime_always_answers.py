"""A run must end with an answer a person can act on - never a silent "(no answer)".

Found in use: an agent built to "list the P1 bugs in a repository" was asked
"Show me all the bugs". Nothing said WHICH repository, so the model invented
owner/repo values and called `list_issues` again and again - GitHub answered
"Could not resolve to a Repository" or returned some stranger's issues - until
it hit the six-call limit. The run then finished as `ok` with output
"(no answer)": nothing told the user what happened.

Two changes, both tested here with a stub model so the limit is reached on
purpose:
  * every worker is told not to invent a value it was not given - ask instead;
  * when the limit is reached, the model is asked once (tools off) to say what it
    found or what stopped it, and if it still will not, the platform says so.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.builder.schema import AgentConfig
from app.runtime import compiler
from app.runtime.compiler import MAX_TOOL_ROUNDS, NO_GUESSING, _placeholder, compile_agent
from app.runtime.guarded_tool import RunContext

CONFIG = AgentConfig.model_validate({
    "schema_version": "1.0",
    "name": "P1 Bug Reader",
    "description": "Reads all P1 bugs from a GitHub repository.",
    "model": {"provider": "google_genai", "name": "gemini-flash-lite-latest", "temperature": 0},
    "topology": {"type": "single",
                 "supervisor": {"instructions": "List the issues in the repository and find the P1 bugs.",
                                "delegates_to": []},
                 "specialists": []},
    "tools": [{"ref": "github.list_issues", "risk": "read", "approval": "auto",
               "requires_connection": "github"}],
    "policy": {"approval_required_for": ["write", "destructive"], "max_tool_calls": 25},
    "requires_connections": ["github"],
    "schedule": None,
})

CALL = AIMessage("", tool_calls=[{"name": "github__list_issues", "args": {"repo": "guess"}, "id": "c1"}])


class Stub:
    """A model that calls the tool every time it is asked - like the one that
    guessed repositories - until it is told to stop; then answers or not."""

    def __init__(self, on_wrap_up: str | None):
        self.on_wrap_up = on_wrap_up
        self.calls: list[list] = []

    def bind_tools(self, _tools):
        return self

    def with_fallbacks(self, _rest):
        return self

    async def ainvoke(self, messages, **_):
        self.calls.append(list(messages))
        told_to_stop = isinstance(messages[-1], HumanMessage) and "Do NOT call any more tools" in str(messages[-1].content)
        if told_to_stop:
            return AIMessage(self.on_wrap_up) if self.on_wrap_up else CALL  # CALL = ignores the instruction
        return CALL


async def no_endpoint(_name):
    return None  # the tool answers "not in the registry" as text: no network here


async def no_token(_name):
    return None


async def run(monkeypatch, model):
    monkeypatch.setattr(compiler, "chat_model_with_tools", lambda _spec, _tools: model)
    monkeypatch.setattr(compiler, "chat_model", lambda _spec: model)
    ctx = RunContext(tenant_id="t", thread_id="th", resolve_token=no_token, resolve_endpoint=no_endpoint)
    graph = await compile_agent(CONFIG, ctx)
    return await graph.ainvoke({"task": "Show me all the bugs", "transcript": [], "finished": [], "results": {}})


async def test_running_out_of_tool_calls_still_ends_with_the_models_own_answer(monkeypatch):
    model = Stub(on_wrap_up="I could not tell which repository you mean. Which one should I look at?")
    result = await run(monkeypatch, model)

    summaries = [v for k, v in result["results"].items() if k.endswith(".summary")]
    assert summaries == ["I could not tell which repository you mean. Which one should I look at?"]
    assert len(model.calls) == MAX_TOOL_ROUNDS + 1          # six tries, then one "say what happened"
    assert result["transcript"][-1].endswith("Which one should I look at?")


async def test_a_model_that_will_not_answer_gets_an_honest_message_from_the_platform(monkeypatch):
    result = await run(monkeypatch, Stub(on_wrap_up=None))
    (summary,) = [v for k, v in result["results"].items() if k.endswith(".summary")]
    assert f"all {MAX_TOOL_ROUNDS} of my tool calls" in summary
    assert "not in this workspace's registry" in summary      # what the last tool actually said


async def test_every_worker_is_told_not_to_invent_a_value(monkeypatch):
    model = Stub(on_wrap_up="done")
    await run(monkeypatch, model)
    system = model.calls[0][0].content
    assert NO_GUESSING in system and "do NOT guess" in system
    assert "List the issues in the repository" in system      # the agent's own instructions are still first


async def test_an_agent_that_answers_straight_away_is_untouched(monkeypatch):
    class Answers(Stub):
        async def ainvoke(self, messages, **_):
            self.calls.append(list(messages))
            return AIMessage("Which repository do you mean?")

    model = Answers(on_wrap_up=None)
    result = await run(monkeypatch, model)
    assert len(model.calls) == 1                              # no extra wrap-up call
    summaries = [v for k, v in result["results"].items() if k.endswith(".summary")]
    assert summaries == ["Which repository do you mean?"]


# ------------------------------------------- placeholders never reach a tool


@pytest.mark.parametrize("args, is_placeholder", [
    ({"owner": "my-org", "repo": "my-repo"}, True),
    ({"repo": "owner/repo"}, True),
    ({"repo": "<repo>"}, True),
    ({"owner": "{owner}"}, True),
    ({"q": {"repo": "your-repo"}}, True),          # nested values are checked too
    ({"owner": "example-org"}, True),
    ({"repo": "guess"}, False),
    ({"owner": "microsoft", "repo": "vscode"}, False),
    ({"text": "Hi <@U123>, see <https://x.io|link> - my name is Sam"}, False),   # Slack syntax is not a placeholder
    ({"query": "is:open label:bug", "per_page": 5, "flag": True}, False),
])
def test_placeholder_values_are_recognised(args, is_placeholder):
    assert (_placeholder(args) is not None) is is_placeholder


def test_the_instruction_names_the_format_to_ask_for():
    assert "Please provide the repository name in owner/repo format." in NO_GUESSING
    assert "my-org/my-repo" in NO_GUESSING            # named as something NOT to use


async def test_a_placeholder_call_is_stopped_and_the_model_gets_to_ask(monkeypatch):
    ask = "Please provide the repository name in owner/repo format."

    class Invents(Stub):
        async def ainvoke(self, messages, **_):
            self.calls.append(list(messages))
            if isinstance(messages[-1], ToolMessage):        # it was told no: now it asks
                return AIMessage(ask)
            return AIMessage("", tool_calls=[{"name": "github__list_issues", "id": "c1",
                                              "args": {"owner": "my-org", "repo": "my-repo"}}])

    model = Invents(on_wrap_up=None)
    result = await run(monkeypatch, model)

    assert "github.list_issues" not in result["results"]          # the tool never ran
    assert "could not be found" not in " ".join(result["transcript"])
    (summary,) = [v for k, v in result["results"].items() if k.endswith(".summary")]
    assert summary == ask
    assert len(model.calls) == 2                                  # one refused try, then the question
    refusal = model.calls[1][-1]
    assert isinstance(refusal, ToolMessage) and "placeholder" in refusal.content
