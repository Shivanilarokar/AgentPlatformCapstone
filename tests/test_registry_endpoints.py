"""The MCP Registry screen's five requirements, as tests.

    1. A form to register an MCP server: name, transport, endpoint, auth type.
    2. On save, the platform connects and asks the server what tools it has.
    3. Each tool is stored as read, write or destructive.
    4. A scheduled job re-checks every server and marks the dead ones.
    5. Servers can be shared with everyone or private to one company.

The stdio registrations use the real @modelcontextprotocol/server-filesystem
(npx), confined to a temporary directory, so they need no account and no
network beyond the first npm download.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from app.core.db import platform_session, tenant_session
from app.registry import service
from app.registry.catalogue import spec
from app.runtime.mcp_client import Endpoint
from app.tenancy.provision import create_tenant, drop_tenant, bootstrap_platform

A, B = "regtest_a", "regtest_b"


@pytest.fixture
def filesystem(tmp_path) -> str:
    """A stdio address for the real filesystem server, rooted in tmp_path."""
    return f"npx -y @modelcontextprotocol/server-filesystem {tmp_path}"


@pytest.fixture
def server_that_wants_a_token():
    """A local HTTP endpoint that answers 401 to everything, like GitHub's does."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):  # keep pytest output clean
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    httpd.shutdown()


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
    ep = spec("git").to_endpoint()
    typed = Endpoint.parse("stdio", "uvx mcp-server-git --repository /srv/workspace")
    assert (ep.transport, ep.target, ep.args) == (typed.transport, typed.target, typed.args)

    remote = spec("github").to_endpoint()
    assert remote.transport == "http"
    assert remote.target == "https://api.githubcopilot.com/mcp/"
    assert remote.needs_token


# --------------------------------------- 2 + 3. connect, ask, store with risk


async def test_registering_asks_the_server_and_stores_risk(two_workspaces, filesystem):
    async with tenant_session(A) as s:
        view = await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem)
    by_name = {t.name: t.risk for t in view.tools}
    # Straight from the server's tools/list - 14 tools in the current release.
    assert len(by_name) >= 12
    assert by_name["read_text_file"] == "read"
    assert by_name["search_files"] == "read"
    assert by_name["write_file"] == "write"
    assert by_name["edit_file"] == "write"
    assert by_name["move_file"] == "write"


async def test_a_server_that_wants_a_token_is_reported_not_marked_down(
    two_workspaces, server_that_wants_a_token
):
    """GitHub, Slack and Atlassian all 401 an anonymous tools/list. That is
    "alive, needs a credential" - a different answer from "unreachable"."""
    from app.runtime.mcp_client import AuthRequired

    async with tenant_session(A) as s:
        with pytest.raises(AuthRequired):
            await service.register(s, name="remote", transport="http",
                                   endpoint=server_that_wants_a_token, auth_type="api_key")
    async with tenant_session(A) as s:
        assert [v.name for v in await service.list_servers(s)] == []


async def test_a_server_that_does_not_answer_is_not_saved(two_workspaces):
    async with tenant_session(A) as s:
        with pytest.raises(service.ServerUnreachable):
            await service.register(
                s, name="ghost", transport="stdio", endpoint="python -c 'import sys; sys.exit(1)'",
            )
    async with tenant_session(A) as s:
        assert [v.name for v in await service.list_servers(s)] == []


# --------------------------------------------------- 4. the scheduled re-check


async def test_refresh_marks_a_dead_server_down_and_a_live_one_ok(two_workspaces, filesystem):
    async with tenant_session(A) as s:
        await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem)

    # sabotage the stored endpoint so the next check cannot reach it
    from sqlalchemy import select, update

    from app.models.tenant import McpServer

    async with tenant_session(A) as s:
        await s.execute(update(McpServer).where(McpServer.name == "filesystem")
                        .values(endpoint="stdio://python -c 'raise SystemExit(1)'"))
    async with tenant_session(A) as s:
        assert await service.refresh(s, "filesystem") == "down"
        row = await s.scalar(select(McpServer).where(McpServer.name == "filesystem"))
        assert row.status == "down"

    # repair it: the next check brings it back
    async with tenant_session(A) as s:
        await s.execute(update(McpServer).where(McpServer.name == "filesystem")
                        .values(endpoint=f"stdio://{filesystem}"))
    async with tenant_session(A) as s:
        assert await service.refresh(s, "filesystem") == "ok"


async def test_the_sweep_visits_every_workspace(two_workspaces, filesystem):
    from app.registry import health

    async with tenant_session(A) as s:
        await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem)
    results = await health.check_everything()
    assert f"{A}/filesystem" in results


# --------------------------------------------------- 5. shared versus private


async def test_a_private_server_is_invisible_to_another_company(two_workspaces, filesystem):
    async with tenant_session(A) as s:
        await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem)
    async with tenant_session(B) as s:
        assert [v.name for v in await service.list_servers(s)] == []


async def test_a_shared_server_is_visible_to_every_company(two_workspaces, filesystem):
    async with tenant_session(A) as s:
        view = await service.register(
            s, name="filesystem", transport="stdio", endpoint=filesystem,
            scope="shared", shared_by=A,
        )
    assert view.scope == "shared"

    for tenant in (A, B):
        async with tenant_session(tenant) as s:
            seen = {v.name: v.scope for v in await service.list_servers(s)}
            assert seen == {"filesystem": "shared"}


async def test_a_private_server_shadows_a_shared_one_with_the_same_name(two_workspaces, filesystem):
    """A company can override a shared entry with its own copy."""
    async with tenant_session(A) as s:
        await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem,
                               scope="shared", shared_by=A)
    async with tenant_session(B) as s:
        await service.register(s, name="filesystem", transport="stdio", endpoint=filesystem,
                               description="Helios' own")
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
