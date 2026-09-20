"""A request the workspace's tools cannot do must not become an agent.

Found in use: "list and delete open GitHub issues" with no delete tool. The
builder's own note said only `list_issues` was available, the user clicked on,
and it saved an "Issue Deletion Agent" whose instructions promised deletion and
whose only tool was a list. Now `understand` makes the model name a tool for
every action asked for; any action with none - or with a tool too weak for the
verb - ends the build there, with the reason, and no agent is created.

The model is stubbed: what is under test is what the platform does with its
answer, which must hold even when the model maps an action to the wrong tool.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from app.api.routers.builds import _shape
from app.builder.graph import Draft, Need, SpecialistDraft, build_graph, unmet_needs
from app.builder.schema import Risk
from app.core.db import tenant_session
from app.mcp_registry import registry
from app.mcp_registry.mcp_client import DiscoveredTool
from app.models.tenant import Agent

from test_registry_endpoints import A, ANNE, NAME, two_workspaces  # noqa: F401

LIST = f"{NAME}.list_issues"
CREATE = f"{NAME}.create_issue"
DELETE = f"{NAME}.delete_issue"

CATALOGUE = [
    {"ref": LIST, "risk": "read", "description": "", "server": NAME},
    {"ref": CREATE, "risk": "write", "description": "", "server": NAME},
    {"ref": DELETE, "risk": "destructive", "description": "", "server": NAME},
]


def need(action, tool):
    return Need(action=action, tool_ref=tool)


# ------------------------------------------------------ the deterministic rule


@pytest.mark.parametrize("needs, unmet", [
    ([need("list open issues", LIST)], []),
    ([need("list open issues", LIST), need("delete issues", None)], ["delete issues"]),
    # the model mapped the action to a tool that cannot do it - caught by the verb
    ([need("delete issues", LIST)], ["delete issues"]),
    ([need("delete issues", CREATE)], ["delete issues"]),
    ([need("delete issues", DELETE)], []),
    ([need("create an issue", CREATE)], []),
    ([need("create an issue", LIST)], ["create an issue"]),
    ([need("close issues", CREATE)], []),
    # a tool that is not in the catalogue - e.g. the admin switched it off
    ([need("list open issues", f"{NAME}.no_such_tool")], ["list open issues"]),
    ([need("delete issues", None), need("delete issues", None)], ["delete issues"]),  # said once
    ([], []),
])
def test_unmet_needs(needs, unmet):
    assert unmet_needs(needs, CATALOGUE) == unmet


# ------------------------------------------------------------- through the graph


class StubModel:
    """Whatever design the test wants the model to have come up with."""

    def __init__(self, draft: Draft):
        self.draft = draft

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return self.draft


@pytest.fixture
async def workspace(two_workspaces, monkeypatch):
    """Company A has one server offering list_issues and create_issue - no delete."""

    async def discover(_ep, _token):
        return [DiscoveredTool("list_issues", "list", {}, Risk.READ),
                DiscoveredTool("create_issue", "create", {}, Risk.WRITE)]

    monkeypatch.setattr(registry, "discover", discover)
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint="echo hi")


def use_model(monkeypatch, draft: Draft):
    monkeypatch.setattr("app.builder.graph.chat_model", lambda _spec: StubModel(draft))


async def run_build(prompt="list and delete all open issues"):
    graph = build_graph(InMemorySaver())
    return await graph.ainvoke(
        {"prompt": prompt, "tenant": A, "user": ANNE, "log": []},
        config={"configurable": {"thread_id": "t-gap"}},
    )


async def agent_count() -> int:
    async with tenant_session(A, ANNE) as s:
        return await s.scalar(select(func.count()).select_from(Agent))


def design(*needs: Need) -> Draft:
    """The design the model in the bug report produced: a gatherer, and an 'actor'
    with nothing to act with."""
    return Draft(
        name="Issue Deletion Agent", description="Checks all open issues and deletes them.",
        needs=list(needs), reasoning="Only list_issues is available.", tool_refs=[LIST],
        supervisor_instructions="Send the work to each specialist in order.",
        specialists=[SpecialistDraft(name="gatherer", instructions="List all open issues.", tool_refs=[LIST]),
                     SpecialistDraft(name="actor", instructions="Delete each of the gathered issues.")],
    )


async def test_a_request_that_needs_a_missing_tool_builds_nothing(workspace, monkeypatch):
    use_model(monkeypatch, design(need("list open issues", LIST), need("delete issues", None)))
    result = await run_build()

    assert "__interrupt__" not in result          # it never even asks which tools to use
    assert not result.get("agent_id")
    assert await agent_count() == 0
    (message,) = result["log"]
    assert "can't build" in message and "haven't created one" in message
    assert "delete issues" in message and NAME in message
    assert "Understood" not in message            # not phrased like a success
    assert _shape("t", result).status == "nothing_to_do"


async def test_the_model_mapping_delete_to_a_list_tool_does_not_get_it_through(workspace, monkeypatch):
    use_model(monkeypatch, design(need("list open issues", LIST), need("delete open issues", LIST)))
    result = await run_build()
    assert "__interrupt__" not in result and await agent_count() == 0
    assert "delete open issues" in result["log"][0]


async def test_a_request_the_tools_can_do_still_reaches_the_picker(workspace, monkeypatch):
    draft = Draft(name="Issue Lister", description="Lists open issues.", reasoning="One list tool.",
                  needs=[need("list open issues", LIST)], tool_refs=[LIST],
                  instructions="List the open issues.")
    use_model(monkeypatch, draft)
    result = await run_build("list all open issues")

    (pause,) = result["__interrupt__"]
    assert pause.value["type"] == "select_tools" and pause.value["suggested"] == [LIST]
    assert await agent_count() == 0               # nothing saved until the user answers


async def test_a_model_that_lists_no_needs_is_not_treated_as_a_refusal(workspace, monkeypatch):
    """No information is not evidence of a gap: behave as before."""
    draft = Draft(name="Issue Lister", description="d", reasoning="r", tool_refs=[LIST],
                  instructions="List the open issues.")
    use_model(monkeypatch, draft)
    result = await run_build("list all open issues")
    assert result["__interrupt__"][0].value["type"] == "select_tools"


async def test_an_admin_switched_off_tool_is_named_as_a_possible_reason(workspace, monkeypatch):
    async with tenant_session(A, ANNE) as s:
        await registry.update(s, NAME, fields={}, enabled_tools=["list_issues"])  # create_issue off
    use_model(monkeypatch, design(need("create an issue", None)))
    result = await run_build("create an issue")
    assert "switched off by your admin" in result["log"][0]
