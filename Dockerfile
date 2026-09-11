FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv  /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /usr/local/bin/uvx

# The platform launches the reference MCP servers as subprocesses:
#   uvx  -> the Python ones (mcp-server-git, mcp-server-sqlite)
#   npx  -> the Node one    (@modelcontextprotocol/server-filesystem)
# git itself is a runtime dependency of mcp-server-git.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# uvicorn is a console script, so sys.path[0] is /usr/local/bin, not /srv.
# This guarantees the "app" package is importable regardless.
ENV PYTHONPATH=/srv

# Dependencies first, so editing code does not reinstall the world on every build.
COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY app ./app

# /srv/workspace is the only directory the filesystem server may touch.
# /srv/var holds the sqlite server's database file.
# mcp-server-git needs an actual repository, so make one the agents can use.
RUN mkdir -p /srv/workspace /srv/var \
 && git config --global user.email "agent@forge.local" \
 && git config --global user.name "Forge Agent" \
 && git config --global init.defaultBranch main \
 && git config --global --add safe.directory /srv/workspace \
 && git init -q /srv/workspace \
 && echo "Scratch space for agents." > /srv/workspace/README.md \
 && git -C /srv/workspace add -A \
 && git -C /srv/workspace commit -qm "initial commit"

EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
