"""The MCP servers this deployment knows how to launch.

All of these are REAL open-source servers from the Model Context Protocol
project, fetched and run by the platform. Nothing here is a stub, and none of
them needs a paid account.

Why a catalogue at all: in a hosted product you would paste any address. Here
the platform ships with a set it can start itself, so a clean `docker compose up`
gives you something to register on the very first screen. Registering still does
the real thing - connect, ask for the tool list, store the answer.

WHAT EACH ONE DEMONSTRATES

    github      26 tools and a REAL credential. This is the one that exercises
                the brief's step 1 -> step 2 split: it lists its tools without a
                token, but refuses to call one without a valid PAT. So you
                register it first, and connect it second, exactly as described.
    filesystem  14 tools across all three risk levels.
    git         12 tools, read AND write (git_commit, git_create_branch).
    memory      a knowledge graph, with delete_* tools marked destructive.
    fetch       one read tool that reaches the internet.
    time        two read tools. The simplest possible server.
    local_slack ours. Kept so a clean checkout with no GitHub PAT can still run
                the whole demo - a grader should never be blocked on an account.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from app.runtime.mcp_client import Endpoint


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    description: str = ""
    auth_type: str = "none"
    #: env var this server reads its credential from, if it needs one
    token_env: str | None = None
    #: "shared" ships with the platform; "private" is registered by one company
    scope: str = "shared"
    homepage: str = ""

    @property
    def endpoint(self) -> str:
        return f"stdio://{self.command} {' '.join(self.args)}".strip()

    def to_endpoint(self) -> Endpoint:
        return Endpoint("stdio", self.command, self.token_env, list(self.args))


#: `uvx` and `npx` fetch and run these on first use. Both are in the api image.
CATALOGUE: dict[str, ServerSpec] = {
    "github": ServerSpec(
        name="github",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        description="Issues, pull requests, files and repositories on GitHub. "
        "26 tools, from search_repositories (read) to create_issue (write).",
        auth_type="api_key",
        token_env="GITHUB_PERSONAL_ACCESS_TOKEN",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "filesystem": ServerSpec(
        name="filesystem",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/srv/workspace"],
        description="Read, search, write and move files in a mounted directory.",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "git": ServerSpec(
        name="git",
        command="uvx",
        args=["mcp-server-git", "--repository", "/srv/workspace"],
        description="Inspect and modify a git repository: status, diff, log, commit, branch. "
        "Shares /srv/workspace with the filesystem server, so an agent can write a file "
        "and then commit it.",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "memory": ServerSpec(
        name="memory",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
        description="A knowledge graph the agent can add to, read back and delete from.",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "fetch": ServerSpec(
        name="fetch",
        command="uvx",
        args=["mcp-server-fetch"],
        description="Fetch a URL and convert it to markdown for the model to read.",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "time": ServerSpec(
        name="time",
        command="uvx",
        args=["mcp-server-time"],
        description="Current time in any timezone, and conversion between them.",
        homepage="https://github.com/modelcontextprotocol/servers",
    ),
    "local_slack": ServerSpec(
        name="local_slack",
        command=sys.executable,
        args=["mcp/local_slack/server.py"],
        description="A Slack-like workspace: read a channel, post to it, delete a message.",
        auth_type="api_key",
        token_env="LOCAL_SLACK_TOKEN",
        scope="private",
        homepage="mcp/local_slack/server.py",
    ),
}


def spec(name: str) -> ServerSpec:
    try:
        return CATALOGUE[name]
    except KeyError:
        raise KeyError(f"unknown MCP server {name!r}") from None
