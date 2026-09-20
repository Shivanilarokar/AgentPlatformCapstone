"""The admin's tool allow-list.

    * connecting is two steps: /discover lists the tools and saves NOTHING, then
      register stores them with the ones the admin ticked switched on
    * every tool is stored, but only enabled ones reach the builder, scoring and
      the runtime - the default readers hide the rest
    * a re-check never re-enables anything, and a tool seen for the first time
      starts OFF (a vendor update must not quietly grant agents new powers)
    * an edit that only changes the selection needs no network
    * the runtime removes a switched-off tool from an agent that already has it

Registry logic is tested with a scripted `discover` so the tool list can change
between calls; the HTTP tests use the real filesystem MCP server over stdio.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.builder.schema import AgentConfig, Risk
from app.core.db import platform_session, tenant_session
from app.mcp_registry import registry
from app.mcp_registry.mcp_client import DiscoveredTool
from app.models.tenant import McpServer
from app.runtime.compiler import compile_agent
from app.runtime.guarded_tool import RunContext

from test_registry_edit_delete import ANNE_C, ARUN_C, PLATFORM_C, as_user, form  # noqa: F401
from test_registry_endpoints import (  # noqa: F401
    A, ANNE, ARUN, NAME, filesystem, mine, two_workspaces,
)

CONFIG = Path(__file__).resolve().parent / "fixtures" / "docs_freshness.json"


def tool(name: str, risk: Risk = Risk.READ) -> DiscoveredTool:
    return DiscoveredTool(name=name, description=f"{name} tool", input_schema={}, risk=risk)


@pytest.fixture
def scripted(monkeypatch):
    """`registry.discover` answers from a list the test can change."""
    box = {"tools": [tool("a"), tool("b"), tool("c", Risk.WRITE)], "down": False}

    async def fake(ep, token):
        if box["down"]:
            raise registry.ServerUnreachable("scripted outage")
        return list(box["tools"])

    monkeypatch.setattr(registry, "discover", fake)
    return box


async def register(s, enabled=None, **kw):
    return await registry.register(s, name=NAME, transport="stdio", endpoint="echo hi",
                                   enabled_tools=enabled, **kw)


def names(view, only_enabled=False):
    return sorted(t.name for t in view.tools if t.enabled or not only_enabled)


# ------------------------------------------------------------- what gets stored


async def test_every_tool_is_stored_but_only_the_ticked_ones_are_enabled(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        view = await register(s, enabled=["a"])
    assert names(view) == ["a", "b", "c"] and names(view, only_enabled=True) == ["a"]

    async with tenant_session(A, ANNE) as s:
        # what the builder, scoring and the runtime read: the allow-list only
        (v,) = mine(await registry.list_servers(s))
        assert names(v) == ["a"]
        # what the Registry screen reads: everything, flagged
        (v,) = mine(await registry.list_servers(s, include_disabled=True))
        assert names(v) == ["a", "b", "c"] and names(v, only_enabled=True) == ["a"]


async def test_leaving_the_selection_out_enables_everything(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        view = await register(s)  # the old behaviour, for scripts and older callers
    assert names(view, only_enabled=True) == ["a", "b", "c"]


async def test_naming_a_tool_the_server_does_not_have_is_refused_and_saves_nothing(two_workspaces, scripted):
    with pytest.raises(registry.UnknownTool):
        async with tenant_session(A, ANNE) as s:
            await register(s, enabled=["a", "nope"])
    async with tenant_session(A, ANNE) as s:
        assert mine(await registry.list_servers(s, include_disabled=True)) == []


async def test_a_colleague_sees_the_company_servers_allow_list(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        await register(s, enabled=["b"], visibility="company")
    async with tenant_session(A, ARUN) as s:
        (v,) = mine(await registry.list_servers(s))
        assert names(v) == ["b"]
        assert await registry.disabled_refs(s) == {f"{NAME}.a", f"{NAME}.c"}


async def test_a_shared_server_has_an_allow_list_too(two_workspaces, scripted):
    async with platform_session() as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint="echo hi",
                                visibility="everyone", shared_by=A, enabled_tools=["a", "c"])
    async with tenant_session(A, ARUN) as s:
        (v,) = mine(await registry.list_servers(s))
        assert names(v) == ["a", "c"]


# ------------------------------------------------------------------- re-checking


async def test_a_recheck_keeps_the_choice_and_new_tools_start_off(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        await register(s, enabled=["a"])

    scripted["tools"] = [tool("a"), tool("b"), tool("c", Risk.WRITE), tool("d")]  # the vendor added d
    async with tenant_session(A, ANNE) as s:
        assert await registry.refresh(s, NAME) == "ok"
    async with tenant_session(A, ANNE) as s:
        (v,) = mine(await registry.list_servers(s, include_disabled=True))
        assert names(v) == ["a", "b", "c", "d"]
        assert names(v, only_enabled=True) == ["a"]  # d is NOT switched on for us

    scripted["tools"] = [tool("a"), tool("d")]  # b and c went away
    async with tenant_session(A, ANNE) as s:
        await registry.refresh(s, NAME)
    async with tenant_session(A, ANNE) as s:
        (v,) = mine(await registry.list_servers(s, include_disabled=True))
        assert names(v) == ["a", "d"] and names(v, only_enabled=True) == ["a"]


# ------------------------------------------------------------------------ editing


async def test_changing_the_selection_needs_no_network(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        await register(s, enabled=["a"])
    scripted["down"] = True  # the server is unreachable, and nobody minds
    async with tenant_session(A, ANNE) as s:
        view = await registry.update(s, NAME, fields={}, enabled_tools=["b", "c"])
    assert names(view, only_enabled=True) == ["b", "c"]


async def test_an_edit_that_re_reads_the_server_switches_new_tools_off_then_applies_the_list(
    two_workspaces, scripted,
):
    async with tenant_session(A, ANNE) as s:
        await register(s, enabled=["a"])
    scripted["tools"] = [tool("a"), tool("b"), tool("c", Risk.WRITE), tool("d")]
    async with tenant_session(A, ANNE) as s:
        view = await registry.update(s, NAME, fields={}, rediscover=True)  # e.g. a new credential
    assert names(view, only_enabled=True) == ["a"]  # d arrived off

    async with tenant_session(A, ANNE) as s:
        view = await registry.update(s, NAME, fields={}, enabled_tools=["a", "d"])
    assert names(view, only_enabled=True) == ["a", "d"]


async def test_the_selection_cannot_name_a_tool_that_is_not_stored(two_workspaces, scripted):
    async with tenant_session(A, ANNE) as s:
        await register(s, enabled=["a"])
    with pytest.raises(registry.UnknownTool):
        async with tenant_session(A, ANNE) as s:
            await registry.update(s, NAME, fields={}, enabled_tools=["zzz"])


# ---------------------------------------------------------------------- over HTTP


async def test_discover_lists_the_tools_and_saves_nothing(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/servers/discover", json={
            "transport": "stdio", "endpoint": filesystem, "auth_type": "none"})
        assert r.status_code == 200
        tools = {t["name"]: t for t in r.json()}
        assert "read_text_file" in tools and "write_file" in tools
        assert tools["read_text_file"]["suggested"] is True   # read: pre-ticked
        assert tools["write_file"]["suggested"] is False      # write: opt-in
        assert tools["write_file"]["risk"] in ("write", "destructive")

        assert [s for s in (await c.get("/v1/servers")).json() if s["name"] == NAME] == []

    async with tenant_session(A, ANNE) as s:
        assert await s.scalar(select(func.count()).select_from(McpServer)) == 0


async def test_discover_reports_a_dead_endpoint(two_workspaces, as_user):
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/servers/discover", json={
            "transport": "stdio", "endpoint": "definitely-not-a-real-binary-xyz"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unreachable"


async def test_register_over_http_with_a_selection_then_change_it(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        found = (await c.post("/v1/servers/discover", json={
            "transport": "stdio", "endpoint": filesystem})).json()
        ticked = [t["name"] for t in found if t["suggested"]]

        r = await c.post("/v1/servers", json=form(filesystem, enabled_tools=ticked))
        assert r.status_code == 201
        tools = r.json()["tools"]
        assert len(tools) == len(found)                      # all stored
        assert sorted(t["name"] for t in tools if t["enabled"]) == sorted(ticked)

        listed = [s for s in (await c.get("/v1/servers")).json() if s["name"] == NAME][0]
        assert sum(t["enabled"] for t in listed["tools"]) == len(ticked)

        r = await c.patch(f"/v1/servers/{NAME}", json={"enabled_tools": ["write_file"]})
        assert r.status_code == 200
        assert [t["name"] for t in r.json()["tools"] if t["enabled"]] == ["write_file"]

        r = await c.patch(f"/v1/servers/{NAME}", json={"enabled_tools": ["nope"]})
        assert r.status_code == 422 and r.json()["detail"]["error"] == "unknown_tool"


async def test_a_user_cannot_change_the_selection_of_a_server_they_do_not_own(
    two_workspaces, filesystem, as_user,
):
    async with as_user(ANNE_C) as c:
        await c.post("/v1/servers", json=form(filesystem, visibility="company"))
    async with as_user(ARUN_C) as c:
        r = await c.patch(f"/v1/servers/{NAME}", json={"enabled_tools": []})
    assert r.status_code == 403


# ------------------------------------------------------------------------ runtime


class _Quiet:
    """Stands in for a chat model: compiling never calls it."""


async def test_a_tool_the_admin_switched_off_is_not_offered_to_the_model(monkeypatch):
    offered: list[list[str]] = []

    def bound(_spec, tools):
        offered.append([t["function"]["name"] for t in tools])
        return _Quiet()

    monkeypatch.setattr("app.runtime.compiler.chat_model_with_tools", bound)
    monkeypatch.setattr("app.runtime.compiler.chat_model", lambda _spec: _Quiet())

    config = AgentConfig.model_validate_json(CONFIG.read_text(encoding="utf-8"))

    async def no_token(_name):
        return None

    async def no_endpoint(_name):  # nothing to introspect: compile still works
        return None

    async def compile_with(off):
        async def resolve_disabled():
            return off

        ctx = RunContext(tenant_id="alpha", thread_id="t", resolve_token=no_token,
                         resolve_endpoint=no_endpoint, resolve_disabled=resolve_disabled)
        offered.clear()
        await compile_agent(config, ctx)
        return {n for group in offered for n in group}

    assert {"filesystem__read_text_file", "filesystem__write_file"} <= await compile_with(set())
    seen = await compile_with({"filesystem.write_file"})
    assert "filesystem__write_file" not in seen and "filesystem__read_text_file" in seen
    assert json.loads(CONFIG.read_text(encoding="utf-8"))["tools"]  # the config itself is untouched


# ------------------------------------- an ordinary user never enables a destructive tool


def with_destructive(scripted):
    scripted["tools"] = [tool("a"), tool("w", Risk.WRITE), tool("d", Risk.DESTRUCTIVE)]


async def test_a_user_who_names_a_destructive_tool_is_refused_and_nothing_is_saved(two_workspaces, scripted):
    with_destructive(scripted)
    async with tenant_session(A, ARUN) as s:
        with pytest.raises(registry.DestructiveNotAllowed):
            await register(s, enabled=["a", "d"], allow_destructive=False)
    async with tenant_session(A, ARUN) as s:
        assert await s.scalar(select(func.count()).select_from(McpServer)) == 0


async def test_a_user_who_makes_no_pick_gets_everything_but_the_destructive_tools(two_workspaces, scripted):
    with_destructive(scripted)
    async with tenant_session(A, ARUN) as s:
        view = await register(s, allow_destructive=False)
    assert names(view) == ["a", "d", "w"]                            # all stored and listed
    assert names(view, only_enabled=True) == ["a", "w"]              # the destructive one stays off


async def test_an_admin_may_enable_a_destructive_tool(two_workspaces, scripted):
    with_destructive(scripted)
    async with tenant_session(A, ANNE) as s:
        view = await register(s, enabled=["a", "d"])
    assert names(view, only_enabled=True) == ["a", "d"]


async def test_discover_marks_destructive_tools_unselectable_for_a_user_only(two_workspaces, scripted, as_user):
    with_destructive(scripted)
    body = {"transport": "stdio", "endpoint": "echo hi", "auth_type": "none"}

    async def selectable(claims):
        async with as_user(claims) as c:
            r = await c.post("/v1/servers/discover", json=body)
            assert r.status_code == 200
            return {t["name"]: t["selectable"] for t in r.json()}

    assert await selectable(ARUN_C) == {"a": True, "d": False, "w": True}
    assert await selectable(ANNE_C) == {"a": True, "d": True, "w": True}


async def test_over_http_a_user_gets_403_for_a_destructive_pick_and_an_admin_does_not(
    two_workspaces, scripted, as_user,
):
    with_destructive(scripted)
    payload = {"name": NAME, "transport": "stdio", "endpoint": "echo hi", "enabled_tools": ["a", "d"]}

    async with as_user(ARUN_C) as c:
        r = await c.post("/v1/servers", json=payload)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "destructive_not_allowed"
        assert [s for s in (await c.get("/v1/servers")).json() if s["name"] == NAME] == []  # nothing saved

        ok = await c.post("/v1/servers", json={**payload, "enabled_tools": ["a", "w"]})
        assert ok.status_code == 201

    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/servers", json=payload)
        assert r.status_code == 201
        assert sorted(t["name"] for t in r.json()["tools"] if t["enabled"]) == ["a", "d"]
