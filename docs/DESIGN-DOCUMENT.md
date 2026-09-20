# Forge — Design Document

*Agent Platform Capstone · Data Sense · submitted 20 September 2026*
*Repository: <https://github.com/Shivanilarokar/AgentPlatformCapstone> · `docker compose up -d --build` and it runs (README).*

This is the two-page document the brief asks for: **how we made each rule true**, and **what we would
do differently with another month**. The longer companions are `ARCHITECTURE.md` (plain language),
`DESIGN.md` (HLD/LLD), `BACKEND.md` (the code) and `VERIFY.md` (see it in the database).

---

## The one decision everything follows from

An agent is a JSON document — `AgentConfig` in `backend/app/builder/schema.py` — that names a
model, a topology (a coordinator with specialists, or a single agent), the tools each worker may
use with their risk and approval, and the *kinds* of servers it needs. The builder writes that
document; one runtime, `runtime/compiler.py::compile_agent`, reads it and assembles a LangGraph
graph. Nothing is generated. Because the agent is data, it can be validated, scored, sanitized,
copied to another company and run there against that company's credentials — which is rules 1, 5
and 6 and the marketplace in one stroke.

The second decision: **every pause is a LangGraph `interrupt()`**, checkpointed by an
`AsyncPostgresSaver` that lives inside the company's own schema. The builder pauses twice, a run
pauses on a risky tool, publishing pauses on the admin. Same mechanism, three places, and all three
survive `docker compose restart api` because nothing is running while they wait — the pause is a
row in `checkpoint_writes`.

## How each rule was made true

**1 · An agent is configuration, not code.** `AgentConfig` is a Pydantic document with validators:
a write tool cannot declare `approval: auto`, a token-shaped string cannot enter any field, a
specialist cannot use a tool it was not granted, `requires_connection` must name a server kind.
`compile_agent` is the *only* code that runs an agent — the Playground and the public API both call
it. The graph picture on the agent page is derived from the document (`graph_nodes_and_edges`),
so it cannot go stale.

**2 · One person cannot see another person's things.** Two locks, both set on the database
connection by one function (`core/db.py::_point_at`), never written into a query. *Company:* one
Postgres schema `t_<company>` **and** a Postgres role of the same name that has `USAGE` on that
schema only; each request runs `SET LOCAL ROLE` and `SET LOCAL search_path`. An unqualified table
name can only resolve inside the company; naming another company's schema is *permission denied*.
*Person:* `FORCE ROW LEVEL SECURITY` on `agents, runs, submissions, connections, mcp_servers`
with the policy `owner_id = current_setting('app.user_id')`; `owner_id` defaults to that setting so
no handler passes an owner. Handlers write `select(Agent)` with no `WHERE` — there is no filter to
delete, which is exactly the grader's test. LangGraph's checkpoint tables have no owner column, so
the owner is put *into* the thread id (`<user_id>/build-…`). The app connects as `forge_app`,
`NOSUPERUSER NOBYPASSRLS`, because a superuser bypasses RLS silently — a real bug we hit. Another
company's agent id finds zero rows → `404`, byte-identical to an unknown id.

**3 · Credentials go in once and vanish.** `vault/envelope.py` seals a token with AES-256-GCM
under a fresh per-row data key, wraps that key under `FORGE_MASTER_KEY`, and binds the ciphertext
to `company:user:server` as AAD — a row copied to another schema, another person or another server
will not decrypt. The plaintext exists in one function, `vault/connections.py::use()`, called per
tool call and deleted after. Graph state carries server *names*, tool arguments are redacted
before they enter an interrupt payload, the MCP client never echoes headers into an error, and the
`ConnectionOut` model's `secret` field is a constant string of dots. Connections are per person.

**4 · Risky actions ask a human first.** Every tool the model can call is
`runtime/guarded_tool.py`: if `approval is ASK` it calls `interrupt()` *before* resolving the
credential or contacting the server; a rejection is returned to the model as a normal result. The
marking comes from the registry (`risk.py` classifies each discovered tool as
read / write / destructive from its own name and description) and is applied at build time in
`assemble()` — never from the model or the user. A prompt is a suggestion; this wrapper is a
control.

**5 · Publishing strips the company out.** `publishing/sanitize.py::listing_from` is an
**allowlist projection**: it copies only nine named fields of the document (name, description,
model, topology, tools as ref/risk/approval/connection kind, policy, required server kinds,
schedule). Anything planted in a field it does not name never exists on the other side. The free
text it does copy is scrubbed of emails, URLs, internal hosts, IPs, paths, ticket references,
secret-shaped strings and the company's own name; the author sees the projection before
confirming. `Listing(` is constructed in exactly one place — the publish graph's `decide` node
after the platform admin approves — and a test asserts it.

**6 · Scores must mean something.** `scoring/score.py` computes both numbers from rows only:
quality 0–100 from six weighted checks (tested, success rate, thumbs-up ratio, tool coverage,
latency, stability), safety A–D from five booleans (writes ask, servers healthy and tools present,
no secrets in config or traces, no granted tool unused, tested). Every check is stored with its
reason and shown on the Overview. Publish needs quality ≥ 70 and grade ≥ B; below that the button
is disabled and names the failing check, and the API answers `409 score_too_low`. An unguarded write
tool fails safety check 1 and the validator before it — it cannot be published (check 3).

**7 · Every agent gets an endpoint.** `POST /v1/agents/{id}/invoke`, `/stream` (SSE),
`/runs/{run}/resume`, `/readiness`. A `forge_…` API token (sha256 hash stored, plaintext shown once)
resolves to its owner in the same dependency the browser cookie does, so the same handlers serve
both. *Download Postman collection* mints a token and embeds it in a v2.1 collection — import,
Send, real response. Another company's token asking for this agent reaches a schema where it does
not exist: `404`, never `403`.

**8 · A real multi-agent system, built through the platform.** *GitHub Issue Daily Summary* — a
coordinator delegating to `collector` (`github.list_issues`) and `poster`
(`slack.slack_send_message`, which asks first) — was built from the brief's own sentence in the
Build screen, run against real GitHub issues, rated, scored A, published by Northwind Labs,
approved by the platform admin, and installed by Maven, who was asked for their own credentials.
The builder always shapes a read-then-write job this way, so the worker holding the write tool
holds nothing else.

**The nine checks** are automated in `backend/tests/graded/` against a real Postgres with two
throwaway companies: 172 tests pass. Checks 5 and 6 are also demonstrated by hand — restart the API
during any pause and the same question is on screen.

## What we would do differently with another month

1. **Deploy it to the cloud.** Today it is `docker compose` on a laptop. The plan: the `api` image
   as-is on a container service (Azure Container Apps / AWS ECS / Fly.io), a managed PostgreSQL 16
   with the `forge_app` role created by the same `init.sql`, the frontend built with `vite build`
   and served as static files behind HTTPS, secrets (`GOOGLE_API_KEY`, `JWT_SECRET`,
   `FORGE_MASTER_KEY`) from the provider's secret store instead of `.env`. Nothing in the isolation
   design changes — it is Postgres roles and RLS, not infrastructure. The stdio MCP servers would
   move to their own container or be replaced by their HTTP variants.
2. **Migrations.** There is no Alembic; a model change means `docker compose down -v`. Two
   migration trees (platform, tenant template) and a loop over every company schema.
3. **Joining a company by invite.** Today anyone who knows a company's name can join it as a user
   (isolation still holds; they just get a seat). An admin invite link closes that.
4. **OAuth connections** instead of pasted tokens for GitHub, Slack and Atlassian, with refresh
   and revocation — the brief's own "if you finish early" item, and the one users asked for first.
5. **Schedules and versioning.** `schedule` is already in the document but nothing runs it; add a
   scheduler and keep every version of a document so an installed agent can be rolled back.
6. **A larger model behind the same `ModelSpec`.** A small free model occasionally over-selects a
   tool; pause 1 is where the person corrects it today.
7. **Small debts we know about:** an agent stays `pending_review` after *request changes* (one line
   in `publishing/graph.py`); the per-request `SET LOCAL` statements are three round trips that a
   server-side function could make one.

*What we would not change:* configuration-not-code, the two locks on the connection, the allowlist
projection, and the approval inside the tool wrapper. Those four are what made the nine checks
structural rather than a matter of discipline.
