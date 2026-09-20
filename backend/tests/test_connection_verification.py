"""POST /v1/connections checks the token before it stores it.

Found in use: revoke a GitHub connection, add it back with a WRONG token, and it
saved as "active" - nothing ever asked GitHub. The failure only showed up later,
inside an agent's run. Now the token is tried against the server first (the same
check the Registry form makes) and nothing is saved if it is refused.

The "rejected" test uses no mock: a local HTTP server that answers 401 to every
request, so the real client code decides. The others script the answer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.builder.schema import Risk
from app.core.db import tenant_session
from app.mcp_registry import registry
from app.mcp_registry.mcp_client import AuthRequired, DiscoveredTool
from app.models.tenant import Connection, McpServer

from test_registry_edit_delete import ANNE_C, ARUN_C, as_user  # noqa: F401
from test_registry_endpoints import A, ANNE, server_that_wants_a_token, two_workspaces  # noqa: F401

TOKEN = "ghp_THIS_IS_A_WRONG_TOKEN_0123456789abcdef"


async def add_server(url: str, *, auth_type: str = "api_key", name: str = "github") -> None:
    """A registered server, without going through discovery (the 401 server could not pass it)."""
    async with tenant_session(A, ANNE) as s:
        s.add(McpServer(name=name, transport="http", endpoint=url, auth_type=auth_type,
                        visibility="company", health="ok"))


async def stored(name: str = "github") -> list[Connection]:
    async with tenant_session(A, ANNE) as s:
        return list(await s.scalars(select(Connection).where(Connection.server_name == name)))


async def test_a_token_the_server_refuses_is_not_saved(two_workspaces, server_that_wants_a_token, as_user):
    await add_server(server_that_wants_a_token)
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": TOKEN})
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "credential_rejected"
    assert TOKEN not in r.text  # the refusal must not echo the credential
    assert await stored() == []


async def test_a_wrong_token_cannot_bring_back_a_revoked_connection(two_workspaces, server_that_wants_a_token,
                                                                    as_user, monkeypatch):
    await add_server(server_that_wants_a_token)

    async def accept(_ep, _token):
        return [DiscoveredTool("t", "t", {}, Risk.READ)]

    monkeypatch.setattr(registry, "discover", accept)
    async with as_user(ANNE_C) as c:
        assert (await c.post("/v1/connections", json={"server_name": "github", "secret": "good"})).status_code == 201
        assert (await c.delete("/v1/connections/github")).status_code == 200
        monkeypatch.undo()  # from here the real client talks to the 401 server again
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": TOKEN})
    assert r.status_code == 401
    (conn,) = await stored()
    assert conn.status == "revoked" and conn.encrypted_secret == b""


async def test_a_revoked_connection_comes_back_with_a_good_token(two_workspaces, server_that_wants_a_token,
                                                                 as_user, monkeypatch):
    """What the Reconnect button does: same endpoint as adding, on a revoked row."""
    await add_server(server_that_wants_a_token)

    async def accept(_ep, _token):
        return [DiscoveredTool("t", "t", {}, Risk.READ)]

    monkeypatch.setattr(registry, "discover", accept)
    async with as_user(ANNE_C) as c:
        await c.post("/v1/connections", json={"server_name": "github", "secret": "first"})
        await c.delete("/v1/connections/github")
        assert (await stored())[0].status == "revoked"

        r = await c.post("/v1/connections", json={"server_name": "github", "secret": "second"})
        assert r.status_code == 201 and r.json()["status"] == "active"
        rows = (await c.get("/v1/connections")).json()
    assert [x["status"] for x in rows if x["server_name"] == "github"] == ["active"]  # same row, not a second one
    (conn,) = await stored()
    assert conn.status == "active" and conn.encrypted_secret


async def test_a_token_the_server_accepts_is_saved(two_workspaces, server_that_wants_a_token, as_user, monkeypatch):
    await add_server(server_that_wants_a_token)
    seen = {}

    async def accept(ep, token):
        seen["token"] = token
        return [DiscoveredTool("t", "t", {}, Risk.READ)]

    monkeypatch.setattr(registry, "discover", accept)
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": "good-token"})
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert seen["token"] == "good-token"  # it was tried with the very token being saved
    assert "good-token" not in r.text
    (conn,) = await stored()
    assert conn.status == "active" and conn.encrypted_secret != b"good-token"


async def test_a_server_that_cannot_be_reached_saves_nothing(two_workspaces, as_user, monkeypatch):
    await add_server("http://127.0.0.1:9/mcp")  # nothing listens here

    async def down(_ep, _token):
        raise registry.ServerUnreachable("scripted outage")

    monkeypatch.setattr(registry, "discover", down)
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": "whatever"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unreachable"
    assert await stored() == []


async def test_a_colleague_is_checked_too(two_workspaces, server_that_wants_a_token, as_user):
    """The admin's company server, a different person's token: same check."""
    await add_server(server_that_wants_a_token)
    async with as_user(ARUN_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": TOKEN})
    assert r.status_code == 401


async def test_nothing_to_check_against_still_saves(two_workspaces, as_user, monkeypatch):
    """No such server registered yet (an installed agent's connection can come first):
    there is nothing to try the token on, so it is stored as before."""

    async def boom(*_a):
        raise AssertionError("must not contact anything")

    monkeypatch.setattr(registry, "discover", boom)
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "jira", "secret": "tok"})
    assert r.status_code == 201


async def test_a_server_that_needs_no_credential_is_not_checked(two_workspaces, as_user, monkeypatch):
    await add_server("http://127.0.0.1:9/mcp", auth_type="none")

    async def boom(*_a):
        raise AssertionError("must not contact anything")

    monkeypatch.setattr(registry, "discover", boom)
    async with as_user(ANNE_C) as c:
        r = await c.post("/v1/connections", json={"server_name": "github", "secret": "tok"})
    assert r.status_code == 201
