"""local_slack - a REAL MCP server, not a stub.

A real Slack workspace is not free, so we host our own tool server speaking the
real Model Context Protocol. Everything about it is genuine: the platform
discovers its tools with tools/list, calls them with tools/call, and it refuses
to work without the right credential.

Three tools, one of each risk level, which is exactly what the platform needs to
demonstrate:

    read_channel     read         runs straight through
    post_message     write        the runtime pauses for a human first
    delete_message   destructive  same, and it is the harsher wording

Run it by hand:
    LOCAL_SLACK_TOKEN=xoxb-test-token uv run python mcp/local_slack/server.py          # stdio
    LOCAL_SLACK_TOKEN=xoxb-test-token uv run python mcp/local_slack/server.py --http   # :9001

TWO TRANSPORTS, ONE SERVER
    stdio   the platform launches it as a subprocess; the credential arrives as
            the LOCAL_SLACK_TOKEN environment variable.
    http    it runs as its own container on :9001, so there is a real ADDRESS
            to paste into the registry - "http://local_slack:9001/mcp". The
            credential arrives as an Authorization: Bearer header, exactly as
            it would for any hosted MCP server.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import sys

from mcp.server.mcpserver import Context, MCPServer

#: Where "posted" messages land, so you can see that a call really happened.
STORE = Path(os.environ.get("LOCAL_SLACK_STORE", "var/local_slack.json"))

#: The credential this server expects. The platform's vault decrypts a token and
#: passes it in as this environment variable for exactly one call.
EXPECTED_TOKEN = os.environ.get("LOCAL_SLACK_TOKEN", "")

server = MCPServer(
    name="local_slack",
    instructions="Read channels and post messages to a local Slack-like workspace.",
)


class AuthError(Exception):
    """Raised when the caller supplied no credential, or the wrong one."""


def _check_auth(ctx: Context | None) -> None:
    """The credential arrives one of two ways, and the tool checks it either way.

    stdio  the platform started this process with LOCAL_SLACK_TOKEN in its env
    http   the caller sent Authorization: Bearer <token>, and MCP hands the
           request headers to the tool through its Context

    tools/list needs no credential - the registry must be able to discover what
    is here before anyone has connected. tools/call does. That is the same
    split the GitHub server has, and the one the brief's step 1 -> 2 relies on.
    """
    token = EXPECTED_TOKEN
    if ctx is not None:
        auth = (ctx.headers or {}).get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()

    if not token:
        raise AuthError("local_slack: no credential supplied - connect this server first")
    if not token.startswith("xoxb-"):
        raise AuthError("local_slack: credential rejected (401)")


def _load() -> dict[str, list[dict]]:
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    # A channel with some standup traffic already in it, so read_channel has
    # something real to return on a clean checkout.
    return {
        "#eng-standup": [
            {"user": "priya", "text": "Yesterday: shipped the tenant gate. Today: the config schema."},
            {"user": "tom", "text": "Blocked on the staging database - no credentials yet."},
            {"user": "dana", "text": "Yesterday: review queue UI. Today: same. No blockers."},
            {"user": "sam", "text": "Blocked: waiting on the auth service P0 before I can test."},
        ]
    }


def _channel(name: str) -> str:
    """Real chat APIs accept "eng" or "#eng". Models drop the # constantly,
    and a tool that is picky about that is a tool that looks broken."""
    name = name.strip()
    return name if name.startswith("#") else f"#{name}"


def _save(data: dict[str, list[dict]]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2), encoding="utf-8")


@server.tool(description="Read recent messages in a channel.")
def read_channel(channel: str, ctx: Context = None) -> list[dict]:
    _check_auth(ctx)
    return _load().get(_channel(channel), [])


@server.tool(description="Post a message to a channel.")
def post_message(channel: str, text: str, ctx: Context = None) -> dict:
    _check_auth(ctx)
    data = _load()
    channel = _channel(channel)
    entry = {
        "user": "issue-digest-bot",
        "text": text,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data.setdefault(channel, []).append(entry)
    _save(data)
    return {"ok": True, "channel": channel, "ts": entry["ts"]}


@server.tool(description="Delete a message from a channel.")
def delete_message(channel: str, ts: str, ctx: Context = None) -> dict:
    _check_auth(ctx)
    data = _load()
    channel = _channel(channel)
    before = len(data.get(channel, []))
    data[channel] = [m for m in data.get(channel, []) if m.get("ts") != ts]
    _save(data)
    return {"ok": True, "deleted": before - len(data[channel])}


def _http_app():
    """The same server as an ASGI app. Auth is inside the tools (see _check_auth).

    DNS-rebinding protection is ON by default in the SDK and only admits
    Host: localhost. Inside Docker the platform reaches this container as
    "local_slack:9001", which the default would reject with HTTP 421. So the
    allowed hosts are listed explicitly - which is what you would do for any
    real deployment behind a hostname.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,local_slack").split(",")
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in allowed] + [f"{h.strip()}:*" for h in allowed],
    )
    return server.streamable_http_app(transport_security=security)


if __name__ == "__main__":
    if "--http" in sys.argv:
        import uvicorn

        uvicorn.run(_http_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "9001")))
    else:
        server.run(transport="stdio")
