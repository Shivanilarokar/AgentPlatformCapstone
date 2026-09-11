"""The public MCP servers the registry offers as quick-picks.

Every entry is the vendor's own server, reached the way the vendor documents:

    github      GitHub's remote server      https://api.githubcopilot.com/mcp/
    slack       Slack's remote server       https://mcp.slack.com/mcp
    jira        Atlassian's remote server   https://mcp.atlassian.com/v2/mcp
    filesystem  @modelcontextprotocol/server-filesystem   (npx, stdio)
    git         mcp-server-git                            (uvx, stdio)
    sqlite      mcp-server-sqlite                         (uvx, stdio)

Nothing here lists a tool. Registering a quick-pick does exactly what pasting
the address by hand does: connect, call tools/list, store the answer. The three
remote servers answer 401 to an anonymous tools/list, so the registry asks for
a credential at that point and saves it as the connection in the same step.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.runtime.mcp_client import Endpoint


@dataclass(frozen=True)
class ServerSpec:
    name: str
    transport: str  # http | stdio
    #: a URL, or a stdio command line
    endpoint: str
    description: str
    auth_type: str = "none"  # none | api_key | oauth
    #: stdio only - env var the subprocess reads its credential from
    token_env: str | None = None
    #: what the Connections screen asks the user to paste
    credential_hint: str = ""
    homepage: str = ""

    def to_endpoint(self) -> Endpoint:
        return Endpoint.parse(self.transport, self.endpoint, self.token_env, self.auth_type)


CATALOGUE: dict[str, ServerSpec] = {
    "github": ServerSpec(
        name="github",
        transport="http",
        endpoint="https://api.githubcopilot.com/mcp/",
        description="GitHub's official remote MCP server: repositories, issues, pull "
        "requests, code search, Actions. Tools are discovered live from its tools/list.",
        auth_type="api_key",
        credential_hint="A GitHub personal access token (Settings → Developer settings → "
        "Tokens). Sent as Authorization: Bearer.",
        homepage="https://github.com/github/github-mcp-server",
    ),
    "slack": ServerSpec(
        name="slack",
        transport="http",
        endpoint="https://mcp.slack.com/mcp",
        description="Slack's official remote MCP server: search, read channels and "
        "threads, send messages, canvases and lists.",
        auth_type="oauth",
        credential_hint="A Slack user OAuth token (xoxp-…) from an app installed to your "
        "workspace. Sent as Authorization: Bearer.",
        homepage="https://docs.slack.dev/ai/slack-mcp-server",
    ),
    "jira": ServerSpec(
        name="jira",
        transport="http",
        endpoint="https://mcp.atlassian.com/v2/mcp",
        description="Atlassian's official remote MCP server (Rovo): Jira issues, JQL "
        "search, transitions, comments; Confluence pages.",
        auth_type="api_key",
        credential_hint="A scoped Atlassian API token, with API-token auth enabled by "
        "your org admin. Sent as Authorization: Bearer.",
        homepage="https://github.com/atlassian/atlassian-mcp-server",
    ),
    "filesystem": ServerSpec(
        name="filesystem",
        transport="stdio",
        endpoint="npx -y @modelcontextprotocol/server-filesystem /srv/workspace",
        description="The reference filesystem server, confined to /srv/workspace: read, "
        "search, write, edit and move files.",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    ),
    "git": ServerSpec(
        name="git",
        transport="stdio",
        endpoint="uvx mcp-server-git --repository /srv/workspace",
        description="The reference git server on the same /srv/workspace: status, diff, "
        "log, commit, branch, checkout, reset.",
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    ),
    "sqlite": ServerSpec(
        name="sqlite",
        transport="stdio",
        # The package predates MCP SDK 2.x; pin the SDK it was written against.
        endpoint="uvx --with mcp<2 mcp-server-sqlite --db-path /srv/var/forge.sqlite",
        description="The reference SQLite server: list and describe tables, run SELECT "
        "queries, run INSERT/UPDATE/DELETE, create tables.",
        homepage="https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite",
    ),
}


def spec(name: str) -> ServerSpec:
    try:
        return CATALOGUE[name]
    except KeyError:
        raise KeyError(f"unknown MCP server {name!r}") from None
