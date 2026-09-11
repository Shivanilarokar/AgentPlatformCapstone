"""The MCP Registry screen's five requirements, as tests.

    1. A form to register an MCP server: name, transport, endpoint, auth type.
    2. On save, the platform connects and asks the server what tools it has.
    3. Each tool is stored as read, write or destructive.
    4. A scheduled job re-checks every server and marks the dead ones.
    5. Servers can be shared with everyone or private to one company.

These talk to our own local_slack server over stdio, so they need no network
and no credential beyond a made-up one.
"""

from __future__ import annotations

import sys

import pytest

from app.core.db import platform_session, tenant_session
from app.registry import service
from app.registry.catalogue import spec
from app.runtime.mcp_client import Endpoint
from app.tenancy.provision import create_tenant, drop_tenant, bootstrap_platform

A, B = "regtest_a", "regtest_b"
LOCAL_SLACK = f"{sys.executable} mcp/local_slack/server.py"


@pytest.fixture
async def two_workspaces():
    """Two companies, the way sign-up makes them: a tenants row AND a schema.

    The health sweep finds workspaces through platform.tenants, so a schema
    with no row there is invisible to it - as it should be.
    """
    from sqlalchemy import delete, select

    from app.models.platform_ import SharedServer, SharedTool, Tenant

    await bootstrap_platform()
    for t in (A, B):
        await drop_tenant(t)
        await create_tenant(t)
    async with platform_session() as s:
        await s.execute(delete(Tenant).where(Tenant.slug.in_([A, B])))
        s.add_all([Tenant(name=A, slug=A), Tenant(name=B, slug=B)])

    yield

    async with platform_session() as s:
        # Only what THIS test shared. An unscoped delete here wiped a real
        # shared server out of the dev database - tests share it with you.
        mine = select(SharedServer.id).where(SharedServer.shared_by.in_([A, B]))
        await s.execute(delete(SharedTool).where(SharedTool.server_id.in_(mine)))
        await s.execute(delete(SharedServer).where(SharedServer.shared_by.in_([A, B])))
        await s.execute(delete(Tenant).where(Tenant.slug.in_([A, B])))
    for t in (A, B):
        await drop_tenant(t)


# ------------------------------------------------- 1. the endpoint is an address


def test_an_endpoint_can_be_a_url_or_a_command():
    http = Endpoint.parse("http", "https://mcp.example.com/mcp")
    assert http.transport == "http" and http.target.startswith("https://")

    stdio = Endpoint.parse("stdio", "npx -y @modelcontextprotocol/server-memory")
    assert stdio.transport == "stdio"
    assert stdio.target == "npx"
    assert stdio.args == ["-y", "@modelcontextprotocol/server-memory"]


def test_a_malformed_address_is_refused_before_any_network_call():
    with pytest.raises(ValueError):
        Endpoint.parse("http", "not a url")
    with pytest.raises(ValueError):
        Endpoint.parse("stdio", "")
    with pytest.raises(ValueError):
        Endpoint.parse("carrier_pigeon", "x")


def test_the_catalogue_is_just_prefilled_addresses():
    """Quick-picks produce the same Endpoint a hand-typed form would."""
    ep = spec("time").to_endpoint()
    typed = Endpoint.parse("stdio", "uvx mcp-server-time")
    assert (ep.transport, ep.target, ep.args) == (typed.transport, typed.target, typed.args)


# --------------------------------------- 2 + 3. connect, ask, store with risk


async def test_registering_asks_the_server_and_stores_risk(two_workspaces):
    async with tenant_session(A) as s:
        view = await service.register(
            s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
            auth_type="api_key", token_env="LOCAL_SLACK_TOKEN", token="xoxb-test",
        )
    by_name = {t.name: t.risk for t in view.tools}
    assert by_name == {
        "read_channel": "read",
        "post_message": "write",
        "delete_message": "destructive",
    }


async def test_a_server_that_does_not_answer_is_not_saved(two_workspaces):
    async with tenant_session(A) as s:
        with pytest.raises(service.ServerUnreachable):
            await service.register(
                s, name="ghost", transport="stdio", endpoint="python -c 'import sys; sys.exit(1)'",
            )
    async with tenant_session(A) as s:
        assert [v.name for v in await service.list_servers(s)] == []


# --------------------------------------------------- 4. the scheduled re-check


async def test_refresh_marks_a_dead_server_down_and_a_live_one_ok(two_workspaces):
    async with tenant_session(A) as s:
        await service.register(s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
                               auth_type="api_key", token_env="LOCAL_SLACK_TOKEN", token="xoxb-t")

    # sabotage the stored endpoint so the next check cannot reach it
    from sqlalchemy import select, update

    from app.models.tenant import McpServer

    async with tenant_session(A) as s:
        await s.execute(update(McpServer).where(McpServer.name == "slack")
                        .values(endpoint="stdio://python -c 'raise SystemExit(1)'"))
    async with tenant_session(A) as s:
        assert await service.refresh(s, "slack") == "down"
        row = await s.scalar(select(McpServer).where(McpServer.name == "slack"))
        assert row.status == "down"

    # repair it: the next check brings it back
    async with tenant_session(A) as s:
        await s.execute(update(McpServer).where(McpServer.name == "slack")
                        .values(endpoint=f"stdio://{LOCAL_SLACK}"))
    async with tenant_session(A) as s:
        assert await service.refresh(s, "slack", token="xoxb-t") == "ok"


async def test_the_sweep_visits_every_workspace(two_workspaces):
    from app.registry import health

    async with tenant_session(A) as s:
        await service.register(s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
                               auth_type="api_key", token_env="LOCAL_SLACK_TOKEN", token="xoxb-t")
    results = await health.check_everything()
    assert f"{A}/slack" in results


# --------------------------------------------------- 5. shared versus private


async def test_a_private_server_is_invisible_to_another_company(two_workspaces):
    async with tenant_session(A) as s:
        await service.register(s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
                               auth_type="api_key", token_env="LOCAL_SLACK_TOKEN", token="xoxb-t")
    async with tenant_session(B) as s:
        assert [v.name for v in await service.list_servers(s)] == []


async def test_a_shared_server_is_visible_to_every_company(two_workspaces):
    async with tenant_session(A) as s:
        view = await service.register(
            s, name="slack", transport="stdio", endpoint=LOCAL_SLACK, auth_type="api_key",
            token_env="LOCAL_SLACK_TOKEN", token="xoxb-t", scope="shared", shared_by=A,
        )
    assert view.scope == "shared"

    for tenant in (A, B):
        async with tenant_session(tenant) as s:
            seen = {v.name: v.scope for v in await service.list_servers(s)}
            assert seen == {"slack": "shared"}


async def test_a_private_server_shadows_a_shared_one_with_the_same_name(two_workspaces):
    """A company can override a shared entry with its own copy."""
    async with tenant_session(A) as s:
        await service.register(s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
                               auth_type="api_key", token_env="LOCAL_SLACK_TOKEN",
                               token="xoxb-t", scope="shared", shared_by=A)
    async with tenant_session(B) as s:
        await service.register(s, name="slack", transport="stdio", endpoint=LOCAL_SLACK,
                               auth_type="api_key", token_env="LOCAL_SLACK_TOKEN",
                               token="xoxb-t", description="Helios' own")
    async with tenant_session(B) as s:
        views = await service.list_servers(s)
        assert len(views) == 1 and views[0].scope == "private"
        assert views[0].description == "Helios' own"


def test_only_a_platform_admin_can_share():
    from app.core.security import Claims

    company_admin = Claims("u", "t", "helios", "tom@x", "Tom", "admin")
    platform_admin = Claims("u", "t", "northwind", "priya@x", "Priya", "platform_admin")
    assert not company_admin.is_admin and company_admin.is_workspace_admin
    assert platform_admin.is_admin and platform_admin.is_workspace_admin


def test_a_windows_path_survives_parsing():
    r"""shlex is POSIX and eats backslashes. A pasted C:\tools\x.exe must not."""
    import os

    if os.name != "nt":
        pytest.skip("windows-only behaviour")
    ep = Endpoint.parse("stdio", r"C:\tools\server.exe --port 1")
    assert ep.target == r"C:\tools\server.exe"
    assert ep.args == ["--port", "1"]
