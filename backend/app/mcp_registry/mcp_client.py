"""Talking to MCP servers, over any of the three transports.

    stdio            a command the platform launches as a subprocess
    streamable-http  a URL, the modern MCP transport
    sse              a URL, the older one

Two jobs:

  list_tools()  ask a server what it has        -> the registry
  call_tool()   run one of them with a token    -> the runtime

The brief is explicit that nobody types tool names in by hand: "if I could, the
whole registry would be a lie the moment a server changed". So discovery goes
through the real protocol, whichever transport the server speaks.

CREDENTIALS: `token` arrives here and is handed to the server for the life of
one call - as an environment variable for stdio, as a bearer header for HTTP.
It is never stored, logged or returned.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from app.builder.schema import Risk
from app.mcp_registry.risk import classify_risk

# ------------------------------------------------------------------ endpoints


@dataclass(frozen=True)
class Endpoint:
    """How to reach one server. Built from the catalogue or from a pasted address."""

    transport: str  # stdio | http | sse
    #: stdio: the command line. http/sse: the URL.
    target: str
    #: stdio only - env var the server reads its credential from
    credential_env_var: str | None = None
    #: extra args for stdio commands
    args: list[str] = field(default_factory=list)
    #: none | api_key | oauth. "none" means a missing token is not a problem.
    auth_type: str = "none"

    @classmethod
    def parse(
        cls,
        transport: str,
        endpoint: str,
        credential_env_var: str | None = None,
        auth_type: str = "none",
    ) -> "Endpoint":
        """From what a user typed into the registration form."""
        transport = transport.lower()
        endpoint = endpoint.strip()
        if transport == "stdio":
            # "stdio://npx -y some-server" or just "npx -y some-server"
            cmdline = endpoint.removeprefix("stdio://")
            # shlex is POSIX: a backslash escapes the next character, which
            # silently destroys a Windows path like C:\tools\server.exe.
            if os.name == "nt":
                cmdline = cmdline.replace("\\", "\\\\")
            parts = shlex.split(cmdline)
            if not parts:
                raise ValueError("a stdio endpoint needs a command")
            return cls("stdio", parts[0], credential_env_var, parts[1:], auth_type)
        if transport in ("http", "sse"):
            if not endpoint.startswith(("http://", "https://")):
                raise ValueError(f"a {transport} endpoint must be a URL")
            return cls(transport, endpoint, credential_env_var, [], auth_type)
        raise ValueError(f"unknown transport {transport!r}")

    @property
    def display(self) -> str:
        if self.transport == "stdio":
            return f"stdio://{self.target} {' '.join(self.args)}".strip()
        return self.target

    @property
    def needs_token(self) -> bool:
        return self.auth_type != "none"


class AuthRequired(Exception):
    """The server is alive but will not even list its tools without a credential.

    GitHub, Slack and Atlassian all answer 401 to an anonymous `initialize`.
    That is not "unreachable" - it is the registry's cue to ask for a token.
    `had_token` says whether one was sent: if so, the server REJECTED it.
    """

    def __init__(self, message: str, *, had_token: bool = False, reason: str = ""):
        super().__init__(message)
        self.had_token = had_token
        self.reason = reason


@dataclass(frozen=True)
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict
    risk: Risk


# ---------------------------------------------------------------- transport


def _resolve(command: str) -> str:
    """npx and uvx are .cmd shims on Windows, plain binaries in the container."""
    return shutil.which(command) or shutil.which(f"{command}.cmd") or command


_INITIALIZE = {
    "jsonrpc": "2.0", "id": 0, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "forge", "version": "0"}},
}


async def _probe(ep: Endpoint, token: str | None) -> None:
    """One plain HTTP request before the MCP session, to tell 401 from "down".

    The MCP client folds every HTTP failure into one generic error, so the only
    way to know a server wants a credential is to ask it ourselves first.
    """
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    reason = ""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        if ep.transport == "sse":
            async with http.stream("GET", ep.target, headers=headers) as r:
                status = r.status_code
        else:
            r = await http.post(ep.target, json=_INITIALIZE, headers=headers)
            status = r.status_code
            try:  # servers say why: Slack -> "invalid_token", GitHub -> plain text
                body = r.json()
                reason = str(body.get("error", {}).get("message") or body.get("error") or "")
            except Exception:  # noqa: BLE001
                reason = r.text.strip()[:80]
    if status in (401, 403):
        if token:
            raise AuthRequired(
                f"{ep.target} rejected the credential ({status}{': ' + reason if reason else ''})",
                had_token=True, reason=reason,
            )
        raise AuthRequired(f"{ep.target} answered {status}: a credential is required")


@asynccontextmanager
async def _connect(ep: Endpoint, token: str | None) -> AsyncIterator[ClientSession]:
    """One initialised session, over whichever transport the server speaks."""
    if ep.transport != "stdio":
        await _probe(ep, token)

    if ep.transport == "stdio":
        env = dict(os.environ)
        if ep.credential_env_var:
            if token:
                env[ep.credential_env_var] = token  # the only place the plaintext travels
            else:
                env.pop(ep.credential_env_var, None)
        params = StdioServerParameters(command=_resolve(ep.target), args=list(ep.args), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if ep.transport == "sse":
        async with sse_client(ep.target, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    # streamable-http, the modern default for a pasted URL
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(ep.target, http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


# --------------------------------------------------------------------- api


async def list_tools(ep: Endpoint, token: str | None = None) -> list[DiscoveredTool]:
    """Ask a server what it can do - the registry's discovery call."""
    async with _connect(ep, token) as session:
        result = await session.list_tools()
        return [
            DiscoveredTool(
                name=t.name,
                description=t.description or "",
                input_schema=t.input_schema or {},
                risk=classify_risk(t.name, t.description or ""),
            )
            for t in result.tools
        ]


async def call_tool(ep: Endpoint, tool: str, args: dict[str, Any], token: str | None) -> str:
    """Run one tool. Returns text, because that is what goes back to a model."""
    async with _connect(ep, token) as session:
        result = await session.call_tool(tool, args)
        text = "\n".join(
            block.text for block in getattr(result, "content", []) if hasattr(block, "text")
        )
        if getattr(result, "is_error", False):
            # Surface the failure to the model as text, never as an exception -
            # a broken tool must leave the agent DEGRADED, not crashed.
            return f"ERROR from {tool}: {_redact(text)}"
        return text or "(no output)"


def _redact(text: str) -> str:
    """Never let a credential reach a log or a saved conversation (check 2)."""
    text = re.sub(r"xox[baprs]-[A-Za-z0-9-]+", "xoxb-***", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}", "ghp_***", text)
    return re.sub(r"Bearer\s+\S+", "Bearer ***", text)
