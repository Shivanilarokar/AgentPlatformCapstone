# Forge — Teammate Guide

Multi-tenant platform that builds, runs and (soon) publishes LangGraph agents from
**configuration documents**. Brief: <https://fnusatvik07.github.io/agent-platform-capstone/>
Due **20 September 2026**. Team of four.

Read `docs/ARCHITECTURE.md` first (plain language, why each decision), then `docs/DESIGN.md`
(HLD / LLD / UML). `docs/architecture.drawio` opens in diagrams.net.

---

## Where we stand — 11 Sep 2026 (9 days to submission)

### The brief's six steps

| Step | Status | What exists today | Files |
|---|---|---|---|
| 1 Register a tool server | ✅ done | Form: name, transport (stdio / http / sse), endpoint, auth type, shared or private. The platform connects out and calls `tools/list` — no tool is typed by hand. Read / write / destructive marking. Health job every 5 min marks dead servers `down`. Sharing with everyone = `platform_admin` only. | `app/api/routers/servers.py` · `app/registry/service.py` · `app/registry/catalogue.py` · `app/registry/health.py` · `app/runtime/mcp_client.py` (transports + `classify_risk`) · `app/models/tenant.py` (`McpServer`, `McpTool`) · `app/models/platform_.py` (`SharedServer`, `SharedTool`) · `web/src/pages/Registry.jsx` |
| 2 Connect to it | ✅ done | AES-256-GCM envelope encryption, per-row data key, AAD = tenant + server. No endpoint returns a secret; the UI shows dots. A test stores a sentinel token and greps every table in every schema for it. | `app/vault/envelope.py` · `app/vault/service.py` · `app/vault/resolver.py` · `app/api/routers/connections.py` · `app/models/tenant.py` (`Connection`) · `web/src/pages/Connections.jsx` |
| 3 Describe an agent | ✅ except score | Builder graph with two LangGraph interrupts (`select_tools`, `missing_connection`); survives `docker compose restart api`; form mode drives the *same* graph. **No score shown yet.** | `app/builder/schema.py` (`AgentConfig`) · `app/builder/graph.py` · `app/api/routers/builds.py` · `app/tenancy/checkpointers.py` · `web/src/pages/Build.jsx` |
| 4 Test it | ⚠️ half | Agent page, graph drawn from the stored config, tools + approval table, raw configuration JSON. **No playground, no runs, no scores, no API tab.** | done: `app/api/routers/agents.py` · `app/runtime/compiler.py` · `app/runtime/guarded_tool.py` · `app/runtime/models.py` · `web/src/pages/MyAgents.jsx` · `web/src/pages/AgentDetail.jsx` — pending: `app/api/routers/runs.py`, `app/scoring/`, Playground/Runs tabs in `AgentDetail.jsx` |
| 5 Publish it | ❌ not started | | to create: `app/publishing/graph.py`, `app/publishing/sanitize.py`, `app/api/routers/submissions.py`, `web/src/pages/AdminReview.jsx` (replaces `Soon.jsx`) |
| 6 Someone else installs it | ❌ not started | | to create: `app/api/routers/listings.py`, `platform.listings` in `app/models/platform_.py`, `web/src/pages/Marketplace.jsx` |

Cross-cutting, already done: `app/core/db.py` + `app/api/deps.py` (tenant gate), `app/core/security.py` +
`app/api/routers/auth.py` (sign-up / sign-in), `app/tenancy/provision.py` (schema per company),
`app/server.py` (FastAPI app, lifespan), `web/src/Shell.jsx` + `App.jsx` (nav, routes),
`docker-compose.yml` + `Dockerfile`.

### The nine graded checks

| # | Check | Status | Mechanism / what is missing | Files |
|---|---|---|---|---|
| 1 | User A cannot reach B's agents, even with the app filter removed | ✅ | one Postgres schema per company; `SET LOCAL search_path` per request; no `WHERE tenant_id` anywhere | `app/api/deps.py` · `app/core/db.py` · `app/tenancy/provision.py` · `tests/graded/test_check_01_isolation.py` · `tests/graded/test_no_schema_qualified_queries.py` |
| 2 | A supplied credential appears nowhere in stored data | ✅ | envelope encryption; token never enters graph state, prompts or logs | `app/vault/envelope.py` · `app/vault/service.py` · `app/runtime/guarded_tool.py` (`redact`) · `tests/graded/test_check_02_credentials.py` |
| 3 | Agent with an unguarded write tool cannot be published | ✅ (at save) | `enforce_approvals()` rewrites a lying config from the registry's risk; the publish gate itself is pending | `app/builder/schema.py` · `tests/test_agent_config.py` — pending: `app/publishing/` |
| 4 | Planted confidential material does not survive publication | ❌ | needs the publish graph + allowlist projection | to create: `app/publishing/sanitize.py` · `tests/graded/test_check_04_sanitize.py` |
| 5 | Kill the server mid-build, it resumes on restart | ✅ | `interrupt()` + per-tenant `AsyncPostgresSaver` | `app/builder/graph.py` · `app/tenancy/checkpointers.py` · `app/api/routers/builds.py` · `tests/graded/test_form_and_chat_agree.py` |
| 6 | Approval pending overnight still resumes | ❌ | same checkpointer; needs the admin-review graph | to create: `app/publishing/graph.py` · `app/api/routers/submissions.py` · `tests/graded/test_check_06_overnight.py` |
| 7 | Playground runs the multi-agent demo incl. approval | ✅ (runtime) | supervisor topology compiled from config; approval inside the tool wrapper — the chat surface is pending | `app/runtime/compiler.py` · `app/runtime/guarded_tool.py` · `tests/graded/test_check_07_runtime.py` · `scripts/run_agent.py` — pending: `app/api/routers/runs.py`, Playground tab |
| 8 | Downloaded Postman collection gets a real response | ❌ | needs public `/v1/agents/{id}/invoke` + generated collection | to create: `app/api/routers/public.py` · `app/api/postman.py` · `tests/graded/test_check_08_postman.py` |
| 9 | Another company's agent by id does not reveal it exists | ✅ | wrong schema → 0 rows → 404, byte-identical body | `app/api/routers/agents.py` · `app/api/deps.py` (`NOT_FOUND`) · `tests/graded/test_check_01_isolation.py` |

**6 of 9 passing.**

### Health of the tree

- `uv run pytest -q` → **118 passed, 1 skipped** (the skip is the opt-in live-model test).
- `cd web && npm run build` → clean.
- `docker compose up -d --build` → 4 containers up (`db`, `api`, `web`, `pgadmin`).
- Dead code, scratch files and the `slug` naming were cleaned up on 11 Sep. `tenants.slug` is the
  one deliberate slug left: it is the key that becomes the schema name.
- **Nothing is committed yet.** First action for whoever reads this: `git add -A && git commit`.
- No Alembic yet — every model change means `uv run python scripts/reset_db.py` and signing up again.

### What is pending — build order

| # | Build | Unlocks | Files to create / touch | Est. | Owner |
|---|---|---|---|---|---|
| 1 | **Playground** — `invoke` / `resume` over SSE, Approve / Reject inline in the chat, runs table | check 7 visible; step 4 complete | `app/api/routers/runs.py` (new) · `Run` model in `app/models/tenant.py` · `web/src/pages/AgentDetail.jsx` (Playground + Runs tabs) · reuse `app/runtime/compiler.py` | 1 day | |
| 2 | **Scoring** — quality 0–100 and safety A–D, every point traceable to an `agent_checks` row | step 3's score; step 5's gate | `app/scoring/quality.py`, `app/scoring/safety.py` (new) · `AgentCheck` model · `GET /v1/agents/{id}/scores` in `agents.py` · Scores card in `AgentDetail.jsx` | ½ day | |
| 3 | **Publish + Admin Review** — a second graph that parks on `interrupt("admin_review")`; `platform.submission_index` so an admin can resume a run in another schema | checks 4, 6 | `app/publishing/graph.py`, `app/publishing/sanitize.py` (new) · `app/api/routers/submissions.py` (new) · `Submission`, `SubmissionIndex` models · `web/src/pages/AdminReview.jsx` · Settings tab in `AgentDetail.jsx` | 1 day | |
| 4 | **Marketplace + install** — allowlist projection into `platform.listings`; install copies the config into the installer's schema and asks for their own connections | step 6 | `app/api/routers/listings.py` (new) · `Listing` model in `platform_.py` · `web/src/pages/Marketplace.jsx` (replaces `Soon.jsx`) | ½ day | |
| 5 | **Public API + Postman** — `/v1/agents/{id}/invoke`, `/stream`, `/resume`; Postman v2.1 download with the caller's own token pre-filled | check 8 | `app/api/routers/public.py`, `app/api/postman.py` (new) · `ApiToken` model in `platform_.py` · API tab in `AgentDetail.jsx` | ½ day | |
| 6 | **Alembic** — two trees (platform, tenant template) + `migrate_all.py` | replaces `reset_db.py` | `alembic/platform/`, `alembic/tenant/` (new) · `scripts/migrate_all.py` · call from `app/tenancy/provision.py` | ½ day | |

≈ 4 engineer-days across four people. **Freeze features 18 Sep.** 19–20 Sep: the nine checks as
automated tests, demo recording, design write-up.

### Two things to do today

1. Commit the tree (see above).
2. Rotate the Gemini key and the GitHub PAT that were pasted into a chat window during development.

---

## 1. Run it (every teammate, first time)

You need **Docker Desktop**, **uv** (<https://docs.astral.sh/uv/>) and **Node 20+** (only for
`npm run build` / lint; the UI itself runs in Docker).

```bash
git clone <repo> && cd AgentPlatformCapstone

# 1. Secrets. Never commit .env - it is gitignored.
cat > .env <<'EOF'
GOOGLE_API_KEY=<your Gemini key from https://aistudio.google.com/apikey>
GROQ_API_KEY=<optional, free at https://console.groq.com - used when Gemini is rate-limited>
EOF

# 2. Python deps on the host (for tests and scripts). Creates .venv.
uv sync --extra dev

# 3. Everything else runs in Docker.
docker compose up -d --build
```

Expected: five containers.

```
$ docker compose ps --format "table {{.Service}}\t{{.Status}}"
SERVICE       STATUS
api           Up ...
db            Up ... (healthy)
pgadmin       Up ...
web           Up ...

$ curl localhost:8000/health
{"ok":true}
```

| URL | What | Login |
|---|---|---|
| <http://localhost:5173> | the product (React + Vite) | sign up — first signup ever becomes `platform_admin` |
| <http://localhost:8000/docs> | FastAPI Swagger | cookie from the UI, or paste the JWT |
| <http://localhost:5050> | pgAdmin (the database, in a browser) | `admin@forge.dev` / `admin`; server `forge` is pre-registered |

Code under `app/` and `web/src/` is volume-mounted: edit locally, the containers reload.
Rebuild (`--build`) only when `pyproject.toml`, `Dockerfile` or `web/package.json` change.

### When the models change

There is no Alembic yet. After any change to `app/models/*.py`:

```bash
uv run python scripts/reset_db.py      # drops every schema and rebuilds them - all data is gone
```

Then sign up again in the UI.

### Run the tests

```bash
uv run pytest -q                        # needs `docker compose up` (Postgres) - ~50 s
# expected: 118 passed, 1 skipped   (the skip is the live-model test; opt in with LIVE_MODEL=1)
```

`tests/graded/` has one file per graded check that is implemented so far (1, 2, 7 plus the
form-vs-chat invariant and the CI check that no query ever schema-qualifies a tenant table).

---

## 2. Walk the product end to end (what a grader will do)

1. **Sign up** at <http://localhost:5173> — company `Northwind Labs`, any email/password.
   A schema `t_northwind_labs` is created for you at that moment.
2. **MCP Registry** → paste an address, or click a quick pick. All six are the vendors' own servers:
   - `filesystem`, `git`, `sqlite` — the reference servers, launched over stdio inside the `api`
     container (`/srv/workspace`, `/srv/var/forge.sqlite`). No credential. Register and they are
     usable immediately: 14, 12 and 6 tools respectively, straight from `tools/list`.
   - `github` — `https://api.githubcopilot.com/mcp/`, GitHub's remote server. It answers 401 to an
     anonymous `tools/list`, so the form asks for a PAT, discovers the tools with it, and saves it
     encrypted as your connection in the same click.
   - `slack` — `https://mcp.slack.com/mcp`, Slack's remote server. Needs a user OAuth token
     (`xoxp-…`) from a Slack app installed to your workspace.
   - `jira` — `https://mcp.atlassian.com/v2/mcp`, Atlassian's remote server. Needs a scoped API
     token, and your org admin must have enabled API-token auth for the Rovo MCP server.
   The platform connects out, calls `tools/list`, and stores what came back. Nobody types a tool in.
   A `platform_admin` can tick **Everyone**; anyone can register **Just my workspace**.
3. **Connections** → a credential for any server you registered without one. Encrypted before it
   touches the database; the list shows dots, never the value.
4. **Build** → *"read my open GitHub issues and post a summary to Slack"*. The build pauses twice
   (pick tools, missing connection). Kill the API mid-pause — `docker compose restart api` — and
   reload the page: the same question is still there (check 5).
5. **My Agents** → the agent → Overview: graph drawn from the stored config, tools with risk and
   approval, the raw configuration JSON.

---

## 3. See the database

### pgAdmin (browser)

<http://localhost:5050> → Servers → **forge** → Databases → forge → **Schemas**. You will see:

- `platform` — shared: `tenants`, `users`, `mcp_servers` + `mcp_tools` (shared servers only)
- `t_northwind_labs` — yours: `agents`, `mcp_servers`, `mcp_tools`, `connections`, and LangGraph's
  `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`

Right-click a table → *View/Edit Data → All Rows*, or open the Query Tool:

```sql
SELECT name, transport, endpoint, status, scope, last_checked_at FROM t_northwind_labs.mcp_servers;

SELECT s.name AS server, t.name AS tool, t.risk
FROM t_northwind_labs.mcp_tools t JOIN t_northwind_labs.mcp_servers s ON s.id = t.server_id
ORDER BY 1, 3, 2;

-- the credential row: ciphertext only. Nothing here is readable.
SELECT server_name, status, added_by, key_version, length(ciphertext) AS bytes, last_used_at
FROM t_northwind_labs.connections;

SELECT name, status, config->'topology'->>'type' AS shape, config->'requires_connections' AS needs
FROM t_northwind_labs.agents;

-- who is on the platform
SELECT t.name AS company, t.slug AS schema_key, u.email, u.role FROM platform.tenants t
JOIN platform.users u ON u.tenant_id = t.id;
```

### Terminal

```bash
docker compose exec db psql -U forge -d forge -c '\dn'                      # list schemas
docker compose exec db psql -U forge -d forge -c '\dt t_northwind_labs.*'   # tables in yours
uv run python scripts/inspect_db.py                                         # every table, every schema
```

### Naming

`tenants.slug` is the only "slug" left in the schema — it is the key that becomes the schema name
(`northwind_labs` → `t_northwind_labs`); `tenants.name` holds `Northwind Labs`. MCP servers are
keyed by `mcp_servers.name` and connections by `connections.server_name`.

---

---

## 4. Repo map

```
app/
  api/        deps.py (tenant gate: SET LOCAL search_path), routers/ (auth, servers, connections, builds, agents)
  builder/    schema.py (AgentConfig - THE document), graph.py (the build graph, two interrupts)
  core/       config.py (.env), db.py (engine, tenant_session), security.py (argon2, JWT)
  models/     platform_.py (shared schema), tenant.py (per-company schema)
  registry/   catalogue.py (public servers we know), service.py (register/discover/refresh), health.py
  runtime/    mcp_client.py (real MCP over stdio/http/sse, risk classification), guarded_tool.py
              (the approval gate), compiler.py (config -> LangGraph), models.py (provider fallbacks)
  tenancy/    provision.py (create/drop schema), checkpointers.py (per-tenant AsyncPostgresSaver)
  vault/      envelope.py (the only place plaintext exists), service.py, resolver.py
  server.py   FastAPI app, lifespan (bootstrap platform schema, start health sweep)
web/src/           React + Vite: pages/ (SignIn, Registry, Connections, Build, MyAgents, AgentDetail)
scripts/           reset_db.py · inspect_db.py · run_agent.py (run a config from the terminal)
tests/             graded/ (one file per implemented check) · fixtures/ (two sample configs)
docker/pgadmin/    pre-registered server + password for pgAdmin
docs/              ARCHITECTURE.md · DESIGN.md · architecture.drawio
```

## 5. House rules

- **An agent is configuration, not code.** Nothing generates Python. `compile_agent()` reads the
  document; if you want new behaviour, add a field and teach the runtime to read it.
- **No `WHERE tenant_id` anywhere.** Isolation is `search_path`. A query that names a `t_…` schema
  fails CI (`tests/graded/test_no_schema_qualified_queries.py`).
- **A plaintext credential exists in exactly one function** — `vault.service.use()` — and is
  `del`'d after the tool call. Never log `kwargs`, never put a token in graph state.
- **Approval comes from the registry's risk, never from the config.** `enforce_approvals()` rewrites
  a lying config and reports it.
- **404, never 403**, for anything that is not in the caller's schema.
- Rotate any key you have ever pasted into a chat window.
