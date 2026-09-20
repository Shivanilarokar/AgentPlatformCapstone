"""Edit and delete for MCP servers, and "register never overwrites".

    * registering a name that is taken is refused (409) - it used to upsert
    * the only way to change a server is edit (PATCH), and only its owner may
    * delete removes the server and the tools discovered from it
    * seeing a colleague's company server is not owning it: row-level security
      would let a company admin DELETE it (DELETE is checked against the read
      rule), so ownership is enforced in code. These tests are what stops that
      regressing.

Same setup as test_registry_endpoints.py: two throwaway companies, the real
filesystem MCP server over stdio, and the shared dev database.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from app.api.deps import current_user
from app.core.db import platform_session, tenant_session
from app.core.security import Claims
from app.mcp_registry import registry
from app.models.platform_ import SharedServer, SharedTool
from app.models.tenant import McpServer, McpTool
from app.server import app

# Fixtures are shared with the sibling module; pytest only sees them if imported.
from test_registry_endpoints import (  # noqa: F401
    A, ANNE, ARUN, B, BEN, NAME, filesystem, mine, two_workspaces,
)

# Anne is company A's admin; Arun is an ordinary user there; Ben admins company B.
ANNE_C = Claims(ANNE, "t-a", A, "anne@a.test", "Anne", "admin")
ARUN_C = Claims(ARUN, "t-a", A, "arun@a.test", "Arun", "user")
BEN_C = Claims(BEN, "t-b", B, "ben@b.test", "Ben", "admin")
PLATFORM_C = Claims("p", None, None, "admin@forge.dev", "Platform admin", "platform_admin")


@pytest.fixture
def as_user():
    """An HTTP client that is signed in as whoever the test says."""

    def make(claims: Claims) -> httpx.AsyncClient:
        app.dependency_overrides[current_user] = lambda: claims
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    yield make
    app.dependency_overrides.pop(current_user, None)


def form(filesystem, **extra):
    return {"name": NAME, "transport": "stdio", "endpoint": filesystem, **extra}


# ----------------------------------------------------- register never overwrites


async def test_registering_a_taken_name_is_refused_and_changes_nothing(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                description="the original")
    with pytest.raises(registry.ServerExists):
        async with tenant_session(A, ANNE) as s:
            await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                    description="the overwrite", visibility="company")
    async with tenant_session(A, ANNE) as s:
        (v,) = mine(await registry.list_servers(s))
        assert v.description == "the original" and v.visibility == "private"


async def test_a_taken_shared_name_is_refused_too(two_workspaces, filesystem):
    async with platform_session() as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                visibility="everyone", shared_by=A)
    with pytest.raises(registry.ServerExists):
        async with platform_session() as s:
            await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                    visibility="everyone", shared_by=A)


async def test_two_people_may_still_each_register_the_same_name(two_workspaces, filesystem):
    """The rule is per owner, as the table's unique constraint always was."""
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)
    async with tenant_session(A, ARUN) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)


async def test_the_http_screen_gets_a_409(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        first = await c.post("/v1/servers", json=form(filesystem))
        again = await c.post("/v1/servers", json=form(filesystem, description="overwrite"))
    assert first.status_code == 201
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "already_exists"


# ------------------------------------------------------------------------ edit


async def test_changing_visibility_needs_no_network_and_shares_with_the_company(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)
    async with tenant_session(A, ARUN) as s:
        assert mine(await registry.list_servers(s)) == []

    async with tenant_session(A, ANNE) as s:
        view = await registry.update(s, NAME, fields={"visibility": "company"})
    assert view.visibility == "company"

    async with tenant_session(A, ARUN) as s:
        assert [v.visibility for v in mine(await registry.list_servers(s))] == ["company"]

    async with tenant_session(A, ANNE) as s:  # and back again
        await registry.update(s, NAME, fields={"visibility": "private"})
    async with tenant_session(A, ARUN) as s:
        assert mine(await registry.list_servers(s)) == []


async def test_a_changed_address_is_verified_before_it_is_saved(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)

    with pytest.raises(registry.ServerUnreachable):
        async with tenant_session(A, ANNE) as s:
            await registry.update(s, NAME, fields={"endpoint": "definitely-not-a-real-binary-xyz"})

    async with tenant_session(A, ANNE) as s:  # unchanged, still healthy
        (v,) = mine(await registry.list_servers(s))
        assert "npx" in v.endpoint and "not-a-real-binary" not in v.endpoint
        assert v.health == "ok" and v.tools


async def test_editing_the_description_keeps_the_tools(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        before = await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)
    async with tenant_session(A, ANNE) as s:
        after = await registry.update(s, NAME, fields={"description": "now documented"})
    assert after.description == "now documented"
    assert sorted(t.name for t in after.tools) == sorted(t.name for t in before.tools)


async def test_the_platform_admin_edits_a_shared_server(two_workspaces, filesystem):
    async with platform_session() as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                visibility="everyone", shared_by=A)
    async with platform_session() as s:
        view = await registry.update(s, NAME, shared=True, fields={"description": "for all"})
    assert view.visibility == "everyone" and view.description == "for all"


# --------------------------------------------- only the owner may edit or delete


async def test_a_colleague_cannot_edit_or_delete_a_company_server(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                visibility="company")

    # Arun can SEE it (that is what "company" means) ...
    async with tenant_session(A, ARUN) as s:
        assert len(mine(await registry.list_servers(s))) == 1

    # ... and row-level security would even let his DELETE through. Ownership is our job.
    with pytest.raises(KeyError):
        async with tenant_session(A, ARUN) as s:
            await registry.remove(s, NAME)
    with pytest.raises(KeyError):
        async with tenant_session(A, ARUN) as s:
            await registry.update(s, NAME, fields={"description": "hijacked"})

    async with tenant_session(A, ANNE) as s:
        (v,) = mine(await registry.list_servers(s))
        assert v.description == "" and v.mine


async def test_a_user_who_is_not_an_admin_gets_403(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        await c.post("/v1/servers", json=form(filesystem, visibility="company"))
    async with as_user(ARUN_C) as c:
        assert (await c.patch(f"/v1/servers/{NAME}", json={"description": "x"})).status_code == 403
        assert (await c.delete(f"/v1/servers/{NAME}")).status_code == 403
    async with as_user(ANNE_C) as c:
        assert (await c.get("/v1/servers")).status_code == 200  # still there


async def test_editable_is_true_only_for_the_owning_admin(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        await c.post("/v1/servers", json=form(filesystem, visibility="company"))

    async def editable(claims):
        async with as_user(claims) as c:
            rows = [r for r in (await c.get("/v1/servers")).json() if r["name"] == NAME]
            return [r["editable"] for r in rows]

    assert await editable(ANNE_C) == [True]   # her own
    assert await editable(ARUN_C) == [False]  # sees it, does not own it


async def test_a_company_admin_cannot_share_with_everyone_by_editing(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        await c.post("/v1/servers", json=form(filesystem))
        r = await c.patch(f"/v1/servers/{NAME}", json={"visibility": "everyone"})
    assert r.status_code == 403


async def test_a_shared_server_cannot_be_edited_into_a_company_one(two_workspaces, filesystem, as_user):
    async with platform_session() as s:
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                visibility="everyone", shared_by=A)
    async with as_user(PLATFORM_C) as c:
        r = await c.patch(f"/v1/servers/{NAME}", json={"visibility": "company"})
    assert r.status_code == 422


async def test_editing_or_deleting_something_that_is_not_yours_is_a_404(two_workspaces, filesystem, as_user):
    async with as_user(BEN_C) as c:
        assert (await c.patch("/v1/servers/nope", json={"description": "x"})).status_code == 404
        assert (await c.delete("/v1/servers/nope")).status_code == 404


# ---------------------------------------------------------------------- delete


async def test_delete_removes_the_server_and_its_tools(two_workspaces, filesystem):
    async with tenant_session(A, ANNE) as s:
        view = await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)
    assert view.tools

    async with tenant_session(A, ANNE) as s:
        await registry.remove(s, NAME)

    async with tenant_session(A, ANNE) as s:
        assert mine(await registry.list_servers(s)) == []
        assert await s.scalar(select(func.count()).select_from(McpTool)) == 0
        assert await s.scalar(select(func.count()).select_from(McpServer)) == 0

    async with tenant_session(A, ANNE) as s:  # the name is free again
        await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem)


async def test_deleting_a_shared_server_takes_its_tools_with_it(two_workspaces, filesystem):
    async with platform_session() as s:
        view = await registry.register(s, name=NAME, transport="stdio", endpoint=filesystem,
                                       visibility="everyone", shared_by=A)
        server_id = (await s.scalar(select(SharedServer).where(SharedServer.name == NAME))).id
    assert view.tools

    async with platform_session() as s:
        await registry.remove(s, NAME, shared=True)

    async with platform_session() as s:
        assert await s.scalar(select(SharedServer).where(SharedServer.name == NAME)) is None
        assert await s.scalar(
            select(func.count()).select_from(SharedTool).where(SharedTool.server_id == server_id)
        ) == 0


async def test_the_http_delete_is_204_then_404(two_workspaces, filesystem, as_user):
    async with as_user(ANNE_C) as c:
        await c.post("/v1/servers", json=form(filesystem))
        assert (await c.delete(f"/v1/servers/{NAME}")).status_code == 204
        assert (await c.delete(f"/v1/servers/{NAME}")).status_code == 404
        assert [r for r in (await c.get("/v1/servers")).json() if r["name"] == NAME] == []
