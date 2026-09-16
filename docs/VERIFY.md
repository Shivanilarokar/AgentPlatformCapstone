# Verifying the platform — Docker, the database, and what every screen writes

## Why Docker at all

The product is five programs that must run together: Postgres, the Python API, the React dev
server, pgAdmin, and - inside the API - the MCP servers it launches (`npx`, `uvx`, `git`). Docker
gives every teammate and every grader the same five, on the same ports, from one command, with no
"works on my machine": `docker compose up -d --build`. The brief requires exactly this: *"docker
compose up and it runs."*

| Container | Image | Port | What it is | Talks to |
|---|---|---|---|---|
| `db` | `postgres:16` | 5432 | the only state: every schema, every checkpoint | — |
| `api` | built from `Dockerfile` | 8000 | FastAPI + LangGraph runtime; launches MCP servers as subprocesses | `db` as role `forge_app`; GitHub/Slack/Atlassian over HTTPS |
| `frontend` | built from `Frontend/Dockerfile` | 5173 | Vite dev server; proxies `/auth`, `/v1` to `api` | `api` |
| `pgadmin` | `dpage/pgadmin4` | 5050 | a browser for the database, for humans | `db` as superuser `forge` |

Containers find each other by service name (`db`, `api`) on Docker's private network; your
browser reaches them on `localhost:<port>`. Data lives in named volumes (`pgdata`,
`forge_workspace`, `forge_var`), so `docker compose down` keeps it and `down -v` wipes it.

## Commands to remember

```bash
docker compose up -d --build      # start (or rebuild + restart what changed)
docker compose ps                 # the four services and their ports
docker compose logs -f api        # watch the API: every request, the health sweep, tracebacks
docker compose restart api        # THE demo move: kill the API mid-pause, come back, it resumes
docker compose exec db psql -U forge -d forge          # SQL shell as the superuser
docker compose exec api sh                              # a shell inside the API container
docker compose exec api ls /srv/workspace               # the folder the filesystem/git servers touch
docker compose down               # stop everything, keep data
docker compose down -v            # stop and DELETE all data (fresh start; sign up again)
```

## Where each screen's data lands - what to open in pgAdmin after each action

pgAdmin: <http://localhost:5050> → Servers → forge → Databases → forge → Schemas. Open
Tools → Query Tool and paste the query for the thing you just did. Replace `t_northwind_labs`
with the company you are signed in as.

| You did (UI) | Look here | What proves it worked |
|---|---|---|
| **Signed up** a new company | `platform.tenants`, `platform.users`, Schemas list, Login/Group Roles | a `tenants` row; a `users` row with `role = admin`; a new schema `t_<key>` with 9 tables; a new Postgres role `t_<key>` |
| **Signed in** | nothing - a JWT in a cookie; `SELECT current_user` inside a request would show `t_<key>` | — |
| **Registered** an MCP server (Just my workspace) | `t_<co>.mcp_servers`, `t_<co>.mcp_tools` | one server row with `health = ok`, `visibility = private`, `owner_id` = you; N tool rows with `risk` and `input_schema` - every one came from the server's `tools/list` |
| **Registered** as Everyone (platform admin) | `platform.mcp_servers` / `platform.mcp_tools` | same, in the shared schema |
| **Pasted a credential** (Connect / Connect & save) | `t_<co>.connections` | one row: `encrypted_secret` (bytes), `secret_nonce`, `encrypted_data_key`, `data_key_nonce` - **no column contains the token**; `added_by` = your email |
| Health sweep (every 5 min, or *re-check*) | `mcp_servers.health`, `last_checked_at` | timestamps move; a dead server flips to `down` |
| **Build** → paused at *Pick tools* | `t_<co>.checkpoints`, `t_<co>.checkpoint_writes` | rows with `thread_id = '<your user id>/build-…'`; **the pause itself** is a `checkpoint_writes` row with `channel = '__interrupt__'` |
| Build → **restart the API** → reload | same rows, unchanged | that is why the same question is still on screen |
| Build finished | `t_<co>.agents` | one row: `config` (the whole AgentConfig JSON), `status = draft`, `owner_id` = you |
| **Playground** run | `t_<co>.runs`; checkpoints under `…/run-…` | `status` `running → ok`; `transcript` (one line per step); `latency_ms` |
| Playground **paused on Approve / Reject** | `runs.status = awaiting_approval`, `runs.pending` (tool, risk, redacted args); `checkpoint_writes` `__interrupt__` row for that thread | the run is parked; restart the API, the row is still there |
| Approve / Reject | `runs.status` → `ok` / `rejected`, `pending` → NULL, `output` filled | for a real write: the effect exists outside (GitHub issue, Slack message, `/srv/workspace` file) |
| 👍 / 👎 | `runs.feedback` = `1` / `-1` | — |
| Overview scores | `agents.quality_score`, `safety_grade`, `checks` (JSON of every check) | recomputed on every view |
| **Publish** | `t_<co>.submissions` (`status = pending`, `listing` = the sanitized JSON, `score`); `platform.submission_index` (same, for the admin); `agents.status = pending_review`; checkpoints under `…/pub-…` with an `__interrupt__` row | the run is parked in your company waiting for the admin |
| Admin **Approve** | `submissions.status = approved`, `notes`; `submission_index.status`; `platform.listings` gets ONE row; `agents.status = live` | nothing else writes `listings` |
| Admin **Request changes** | `submissions.status = changes_requested`, `notes` | no listing row |
| Another company **installs** | `t_<other>.agents` new row with `installed_from` = listing id, `owner_id` = installer; `platform.listings.installs + 1` | the publisher's rows untouched; `t_<other>.connections` still empty until they connect |
| **Download Postman** / mint token | `platform.api_tokens` | `token_hash` (sha256), `prefix`; never the token |
| Postman **Send** | `t_<co>.runs` with `trigger = api` | — |

## The queries, ready to paste

```sql
-- who exists, which schema, which role
SELECT coalesce(t.name,'(platform)') AS company, u.email, u.role FROM platform.users u
LEFT JOIN platform.tenants t ON t.id = u.tenant_id ORDER BY 1, 3;
SELECT rolname FROM pg_roles WHERE rolname LIKE 't\_%' OR rolname LIKE 'forge%';

-- the registry, with owners (pgAdmin is superuser, so you see every person's rows)
SELECT s.name, s.visibility, s.health, u.email AS owner, count(t.id) AS tools
FROM t_northwind_labs.mcp_servers s JOIN platform.users u ON u.id = s.owner_id
LEFT JOIN t_northwind_labs.mcp_tools t ON t.server_id = s.id GROUP BY 1,2,3,4;

-- credentials: sizes only, never a value
SELECT server_name, status, added_by, length(encrypted_secret) AS secret_bytes, last_used_at
FROM t_northwind_labs.connections;

-- agents and their scores
SELECT name, status, quality_score, safety_grade, config->'topology'->>'type' AS shape,
       config->'requires_connections' AS needs FROM t_northwind_labs.agents;

-- runs
SELECT status, trigger, latency_ms, feedback, left(output, 60) AS output, started_at
FROM t_northwind_labs.runs ORDER BY started_at DESC;

-- EVERYTHING that is parked on a human right now (builds, runs, publish reviews)
SELECT left(thread_id, 60) AS thread, checkpoint_id
FROM t_northwind_labs.checkpoint_writes WHERE channel = '__interrupt__' ORDER BY 2 DESC;

-- the review queue and the marketplace
SELECT company, status, quality, grade, listing->>'name' AS agent FROM platform.submission_index;
SELECT name, publisher, quality, grade, installs FROM platform.listings;
```

## The isolation, live in psql (the demo move for Rule 2)

```
docker compose exec db psql -U forge_app -d forge
SET ROLE t_northwind_labs;  SET search_path TO t_northwind_labs, platform;
SELECT count(*) FROM agents;                                   -- 0: no person named yet (RLS fails closed)
SELECT set_config('app.user_id',(SELECT id::text FROM platform.users WHERE email='shivani@northwind.example'),false);
SELECT name FROM agents;                                       -- Shivani's agents only
SELECT count(*) FROM t_maven.agents;                           -- permission denied for schema t_maven
```

