"""A write must be committed BEFORE its response reaches the browser.

Found in use: revoke a connection, add it back with a good token, and the
Connections screen still showed "revoked". The row WAS saved. FastAPI's default
runs a `yield` dependency's exit code - our transaction's COMMIT - only after
the response has been sent, so the screen's immediate reload read the old rows.
Measured against the running API: 40 of 40 reads straight after a write were
stale; with `scope="function"` on the dependency, 0 of 40.

An in-process test client cannot show this (it waits for the whole app,
including the after-response commit), so this asserts the cause instead: every
route that takes a DB session from `tenant_db` / `platform_db` asks for the
early commit - except the one that streams, whose generator keeps using the
session after the handler has returned and so needs it open.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from app.api.deps import platform_db, tenant_db
from app.api.routers import agents, auth, builds, connections, public_api, publishing, runs, servers

#: Server-Sent Events: `events()` still flushes through `db` while the body streams.
STREAMS_WITH_THE_SESSION = {"/v1/agents/{agent_id}/stream"}


def _routes():
    """Every route, read from the router modules (FastAPI wraps included routers
    in its own objects, so app.routes does not list them flat)."""
    for module in (agents, auth, builds, connections, public_api, publishing, runs, servers):
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                yield route


def _session_dependencies(dependant):
    for dep in dependant.dependencies:
        if dep.call in (tenant_db, platform_db):
            yield dep
        yield from _session_dependencies(dep)


def test_writes_commit_before_the_response_is_sent():
    checked, late = 0, []
    for route in _routes():
        for dep in _session_dependencies(route.dependant):
            checked += 1
            if dep.scope != "function" and route.path not in STREAMS_WITH_THE_SESSION:
                late.append(f"{sorted(route.methods)} {route.path}")
    assert checked > 20, "found no session dependencies - is the test looking in the right place?"
    assert not late, "commits after the response is sent (add scope=\"function\"): " + "; ".join(late)


def test_the_streaming_route_keeps_its_session_open():
    """The exception is deliberate: closing the session early would break the stream."""
    route = next(r for r in _routes() if r.path in STREAMS_WITH_THE_SESSION)
    scopes = {d.scope for d in _session_dependencies(route.dependant)}
    assert scopes and "function" not in scopes
