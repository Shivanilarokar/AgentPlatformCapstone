# Architecture — explained

Read this before writing any code. Open `docs/architecture.drawio` alongside it (draw.io, or the
"Draw.io Integration" extension in VS Code). Five pages, one per section below.

---

## 1. What we are building, in one paragraph

A user signs in, types *"I want an agent that reads my open GitHub issues every morning and posts a
summary to Slack"*, and our platform builds that agent, deploys it, and hands them a chat window to
test it in. When they are happy, they publish it; an admin reviews it; once approved, someone in a
**different company** can install it into their own workspace **with their own credentials**.

We are not building an agent. We are building **the thing that builds agents**.

---

## 2. The one idea everything hangs off

> **An agent is a configuration document, not generated code.**

This is Rule 1 in the brief, and it is the decision that makes every other requirement possible.

The obvious approach — "the user describes an agent, an LLM writes Python, we save the file and run
it" — fails three ways at once:

| Problem | Why it is fatal here |
|---|---|
| You cannot safely hand generated Python to another company | Rule 6 of the brief *requires* cross-company installation |
| You cannot force generated code to ask permission | The code would have to be *trusted* to ask. Rule 4 says the platform must enforce it |
| You cannot version, score, diff or explain it | Rule 6 requires scores you can point at, and grading requires explainability |

Splitting it into **"a config document" + "one runtime that reads config documents"** fixes all
three simultaneously:

- the **runtime is ours**, so *we* decide where it stops for approval — the agent has no say
- the **config is just data**, so it can be versioned, scored, sanitized and shipped elsewhere
- the graph picture on the agent page is *rendered from the config*, so it can never be out of date

Everything else in this document is a consequence of that one sentence.

### The document itself

```jsonc
{
  "schema_version": "1.0",
  "name": "Issue Digest",
  "description": "Triages open GitHub issues by severity and posts a morning digest to Slack.",
  "model": { "provider": "google_genai", "name": "gemini-2.5-flash", "temperature": 0 },

  "topology": {
    "type": "supervisor",                       // "single" | "supervisor"
    "supervisor": { "instructions": "...", "delegates_to": ["triager", "reporter"] },
    "specialists": [
      { "name": "triager",  "instructions": "...", "tools": ["github.list_issues"] },
      { "name": "reporter", "instructions": "...", "tools": ["slack.post_message"] }
    ]
  },

  "tools": [
    { "ref": "github.list_issues", "risk": "read",  "approval": "auto", "requires_connection": "github" },
    { "ref": "slack.post_message", "risk": "write", "approval": "ask",  "requires_connection": "slack" }
  ],

  "policy": { "approval_required_for": ["write", "destructive"], "max_tool_calls": 25 },
  "requires_connections": ["github", "slack"],
  "schedule": null
}
```

**Three invariants. Learn these; they come up in every design conversation for the next two weeks.**

**(a) `requires_connection` names a server *name*, never a connection id and never a token.**
This one line is the entire reason a marketplace can exist. The config says *"this needs a Slack
connection"* — it does not say *which* one. At run time the runtime resolves
`server name + whoever is running this → their connection row`. Northwind's config, installed by Helios,
transparently uses Helios's token.

**(b) `approval` is never trusted from the config.** On save and again on every run, the platform
recomputes it from the registry's recorded risk:

```
risk ∈ {write, destructive}  ⇒  approval = "ask", always
```

A config claiming `"approval": "auto"` on a write tool is rejected at save and blocked at publish.
That is graded check 3.

**(c) The config is the only thing that travels.** Nothing else is copied to the marketplace, and
nothing else is needed to reconstruct the agent.

---

## 3. The five boxes

*(draw.io page 1)*

```
Browser (React SPA)
   │  Bearer JWT { user_id, tenant_id, role }
   ▼
FastAPI  ──►  tenant dependency: SET LOCAL search_path = t_<tenant>, platform
   │
   ├── Registry service   connects to an MCP server, asks it for its tools
   ├── Vault              encrypts/decrypts connection tokens
   ├── Builder graph      chat  → config document      (2 pauses)
   └── Runtime compiler   config → live LangGraph      (approval gate)
                             │
                          Postgres:  platform schema + one schema per company
```

**Only two of these are real programs: the Builder graph and the Runtime compiler.** The registry is
an HTTP client plus a table. The vault is forty lines of `cryptography`. The API is CRUD. Say this
in the presentation — it shows you understand where the difficulty actually is.

**Data flows one way:** SPA → FastAPI → Registry/Builder → Postgres → Runtime.

---

## 4. Tenant isolation — why schema-per-tenant

*(draw.io page 2)*

The brief is unusually specific here:

> Do not enforce this by remembering to add a filter to every query. One forgotten filter is a data
> breach. **How this is tested: we delete your application-level check and try again.**

So any design where the answer is *"we're careful to write `WHERE tenant_id = ...`"* fails by
definition. There were two real candidates.

| | Row-Level Security | **Schema-per-tenant (chosen)** |
|---|---|---|
| How | Policies in the DB filter every row by a session variable | One Postgres schema per company; `search_path` picks which one |
| Survives filter deletion? | Yes | Yes |
| Migrations | One set of tables | **Must run across every schema** — the real cost |
| LangGraph checkpoints | **Hole** — see below | Solved for free |

### The deciding factor: LangGraph checkpoint tables

LangGraph stores paused graph state in `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`. Those
tables are keyed by **`thread_id` only**. They have no `tenant_id` column, so an RLS policy has
nothing to filter on — a guessed or leaked thread id would read another company's paused run, which
is exactly where approval payloads and tool arguments live.

With schema-per-tenant, each company's checkpoint tables sit **inside that company's schema** and are
unreachable from another `search_path`. The hole closes by itself. This is the strongest single
argument for the choice, and it is the answer to give if a grader asks "what about the checkpointer?"

### How the switch works

One FastAPI dependency, and only one:

```python
async def tenant_session(claims = Depends(current_user)) -> AsyncSession:
    async with SessionLocal() as s, s.begin():          # a transaction
        await s.execute(text(f"SET LOCAL search_path = {schema_for(claims.tenant_id)}, platform"))
        yield s                                          # every handler gets this
```

After that line, handlers write:

```python
agents = await s.scalars(select(Agent))       # note: no WHERE tenant_id, anywhere
```

**There is no filter to delete**, so deleting it changes nothing. That is check 1.

### Three things that will break it

1. **`SET LOCAL` only lives inside a transaction.** Use the dependency above; never take a bare
   connection from the pool.
2. **Never schema-qualify a tenant table** (`t_helios.agents`). One such query defeats the whole
   design. Add a CI grep on day 2 that fails the build on `t_` inside a SQL string or a SQLAlchemy
   `schema=` argument.
3. **The app role has `USAGE` on every tenant schema**, so isolation rests on the search path rather
   than on grants. Be honest about this in the design document, and name per-tenant database roles
   as the "with another month" answer. Do not overclaim.

### Check 9 falls out for free

Tom at Helios asks for Priya's agent id. It simply is not in `t_helios`. The query returns zero rows,
so the natural handler is:

```python
if not agent:
    raise HTTPException(404)
```

There is no ownership branch, so there is no way to accidentally return 403. A 403 would confirm the
agent exists. Return a **byte-identical** 404 body for unknown, malformed and cross-tenant ids.

### What is deliberately shared

`platform` schema: `tenants`, `users`, `api_tokens`, global MCP servers, and **`listings`** — the
marketplace. The brief calls the marketplace "the single deliberate exception — which is exactly why
an admin guards it."

One extra table earns its place: `platform.submission_index(submission_id, tenant_id, agent_id,
thread_id, status)`. An admin has to resume a publish run that is parked in *someone else's* schema,
so the queue needs a cross-tenant index to route each decision to the right tenant's checkpointer.

---

## 5. Credentials — in once, then gone

The test is blunt: *"we search everything your system stored for the token we gave you. Finding it
anywhere is a fail."*

**Encryption.** AES-256-GCM per connection, data key wrapped by a master key from `FORGE_MASTER_KEY`,
with a `master_key_version` column for rotation. Use `tenant_id || server_id` as the GCM **AAD** — so a
encrypted_secret row copied into another schema simply refuses to decrypt. Cheap to build, strong to say.

**Lifecycle, enforced in exactly one function:**

```python
# vault.use() is the ONLY place a plaintext token exists in this codebase.
token = vault.use(tenant_id, server_slug)     # decrypt
try:
    return await mcp_call(spec, kwargs, token)  # use
finally:
    del token                                    # drop
```

**Where teams lose this check.** The checkpointer writes graph state to Postgres so runs can resume.
A token that lands in graph state even once is now permanently on disk, in a table you forgot about.
So:

- graph state carries a **server name**, never a value
- tool arguments are logged from an **allowlist of keys**, never `repr(kwargs)`
- the MCP client never echoes request headers into an error message
- `runs.outcome` stores a summary string, never a raw tool response

**Revoking must degrade, not crash.** If a connection is missing or expired, the wrapper returns an
error *string* to the model ("slack connection expired — cannot post"). The agent finishes its other
work and reports the broken tool. Status renders as `degraded`.

---

## 6. The Builder graph — the two pauses

*(draw.io page 3)*

```
understand → search_registry → [⏸ interrupt: select_tools]
           → check_connections → [⏸ interrupt: missing_connection]?
           → assemble_config → score → persist
```

The brief: *"Those two moments where it stops and waits for me are the centre of this project."*

**`interrupt("select_tools")`** — we search `mcp_tools` (global + this company's private servers) and
show the servers we think are needed. The user ticks boxes. The graph resumes with
`Command(resume=chosen_refs)`.

**`interrupt("missing_connection")`** — fires only when a required server has no active connection.
Resumes on "Connect Slack" or on "Skip — build without posting", which drops that tool from the
config.

**Why these survive a restart.** Both use `interrupt()` from `langgraph.types`, and the graph is
compiled with `AsyncPostgresSaver`. After every node the entire graph state is written to Postgres
against a `thread_id`. Close the tab, kill the container, come back tomorrow — resuming that thread
picks up at exactly that node. That is check 5, and the same mechanism is check 6.

**The form mode does not get its own code path.** The mockup is explicit: *"Both paths must produce
the same agent configuration — do not write the builder twice."* The form invokes this same graph
with the answers pre-seeded so both interrupts resolve immediately.

---

## 7. The Runtime — where approval is actually enforced

*(draw.io page 4)*

One function, `compile_agent(config) -> CompiledGraph`, cached by config hash. **It is the only code
in the system that ever runs an agent** — the playground and the public `/v1` API both call it, so
there is no second path where a control could be missing.

- `topology.type == "single"` → agent node + tool node
- `topology.type == "supervisor"` → supervisor routes to specialists via `Command(goto=…)`, each
  specialist holding only its own tools, handing back to the supervisor

### The guarded tool wrapper

```python
def guarded_tool(spec, ctx):
    async def _run(**kwargs):
        if spec.risk in ("write", "destructive"):
            decision = interrupt({
                "type": "tool_approval",
                "tool": spec.ref, "risk": spec.risk,
                "args": redact(kwargs),
            })
            if decision != "approve":
                return f"Rejected by the user. {spec.ref} was not executed."
        token = vault.use(ctx.tenant_id, spec.requires_connection)
        try:
            return await mcp_call(spec, kwargs, token)
        finally:
            del token
    return _run
```

**This is the sentence to say out loud in the presentation:**

> The approval is enforced by this wrapper, not by text in the agent's instructions. Putting
> *"always ask before posting"* in a prompt is a **suggestion**, and models do not always follow
> suggestions. This is a **control**: the tool physically cannot execute before a human answers.

That distinction is Rule 4, and they will ask about it.

---

## 8. Scores that mean something

Two numbers, both computed from checks you can point at. Every check writes a row to `agent_checks`,
so the UI can show *why* — the brief forbids asking a model to guess a score.

**Quality, 0–100 — "does it work?"**

| Weight | Check |
|---|---|
| 20 | run at least 5 times in the playground |
| 30 | success rate over the last 20 runs |
| 20 | thumbs-up ratio |
| 15 | every granted tool used at least once |
| 10 | median latency under threshold |
| 5 | no unhandled error in the last 5 runs |

**Safety, A–D — "is it set up safely?"** Five booleans: writes behind approval · servers healthy ·
no credential-shaped string in config or traces · no granted-but-unused tools · tested at least N
times. 5/5 = A, 4/5 = B, 3/5 = C, else D.

**The gate: `quality ≥ 70 AND safety ≥ B`.** Below that the Publish button is **disabled and names
the failing check**. The brief: *"A score that blocks nothing is decoration."*

---

## 9. Publishing and the marketplace

*(draw.io page 5)*

Publishing starts a **second LangGraph**: `sanitize → [⏸ admin_review] → list_or_return`.

The admin queue **is** that graph, parked on an interrupt. It may wait three days across two deploys
and a weekend. Approve, Request changes and Reject all resume the *same* parked run.

### Sanitize by allowlist, never by denylist

Build the listing by **copying only these fields**: name, description, topology shape, tool refs +
risk + approval, required server names, score, grade, publisher **org name only**.

Nothing else is copied — so confidential material planted in any field we did not name **cannot
escape, by construction**. A denylist ("strip anything that looks secret") loses this test the moment
the grader plants something you did not think of.

The residual risk is free text: `description` and `instructions`, which the agent is useless without.
We publish them, run a scrubber over them (internal hostnames, emails, long high-entropy strings) and
**show the author a diff to confirm before submission**. The allowlist is what passes the check; the
scrubber is belt-and-braces.

### Install

The sanitized config is copied into the installer's schema as a brand-new `agents` row, and they are
walked through connecting each required server with **their own** credentials. They never touch the
original; the publisher never sees their runs.

---

## 10. Every agent gets an endpoint

| Endpoint | Purpose |
|---|---|
| `POST /v1/agents/{id}/invoke` | run it |
| `POST /v1/agents/{id}/stream` | same, over SSE |
| `POST /v1/agents/{id}/resume` | answer a pending approval |
| `GET  /v1/agents/{id}/postman` | the collection, as JSON |

Bearer token → `platform.api_tokens` → tenant → `SET LOCAL search_path` → the agent is in that schema
or it is not. Another company's token gets **404**.

Check 8 is about the download *actually working*: generate a Postman v2.1 collection server-side with
`{{base_url}}` and `{{token}}` variables **pre-filled with the caller's own token and the running base
URL**, so "download it, hit Send, get a response" is literally true.

---

## 11. Glossary — terms that will come up daily

| Term | What it means here |
|---|---|
| **MCP** | Model Context Protocol. A standard way for a tool server to describe and expose its tools. We call `tools/list` to discover them and `tools/call` to run one. Nobody types a tool name by hand. |
| **Risk** | Our marking on each discovered tool: `read`, `write`, `destructive`. It is the input to the approval gate. |
| **Connection** | One company's encrypted credential for one server. Never named in a config. |
| **Interrupt** | LangGraph's pause. `interrupt(payload)` stops the graph and persists it; `Command(resume=value)` continues, and `interrupt()` returns that value. |
| **Checkpointer** | Where paused graph state is stored. We use `AsyncPostgresSaver`, per tenant schema. |
| **Thread id** | The handle for one paused or ongoing run. Always resolve it through our own tables first; never hand a user-supplied one straight to the checkpointer. |
| **`search_path`** | The Postgres setting that decides which schema an unqualified table name resolves to. Our entire tenant isolation. |
| **Topology** | `single` or `supervisor`. Rule 8 requires at least one real `supervisor` agent built through the platform. |

---

## 12. How each graded check is satisfied

| # | Check | Where it is handled |
|---|---|---|
| 1 | Isolation with the app filter removed | §4 — schema per tenant; there is no filter |
| 2 | Credential appears nowhere | §5 — envelope encryption, server name only in graph state |
| 3 | Unguarded write tool cannot be published | §2(b) + §8 — approval recomputed from risk, publish gate |
| 4 | Planted material does not survive publication | §9 — allowlist projection |
| 5 | Killing the server mid-build resumes | §6 — `interrupt()` + `AsyncPostgresSaver` |
| 6 | Approval left overnight still resumes | §9 — admin review is a parked graph |
| 7 | Playground runs the multi-agent demo | §7 — supervisor topology, approval in the wrapper |
| 8 | Postman collection gets a real response | §10 — server-generated, token pre-filled |
| 9 | Another company's agent id does not reveal it | §4 — 0 rows → 404, no ownership branch |

---

## 13. This is a real product, not a mockup

The published screens are reference mockups driven by a fixtures file (`assets/data.js`). We take
their **behaviour and layout** and throw the fixtures away.

- **No fixture data in our repo.** No hardcoded agent arrays, no stubbed API responses in the SPA.
- **No fake states.** Revoke a real connection and the agent *becomes* degraded. A real failing check
  *disables* the Publish button.
- **Real MCP servers over the real protocol.** The registry connects out and calls `tools/list`.
- **Real tokens, really encrypted.** The credential you paste is the credential the tool call uses.
- **The demo agent is built by using our own product**, through the builder chat — not inserted.

Every server in the registry's quick-picks is the vendor's own: GitHub's remote MCP server
(`api.githubcopilot.com/mcp`), Slack's (`mcp.slack.com/mcp`), Atlassian's for Jira
(`mcp.atlassian.com/v2/mcp`), and the reference `filesystem`, `git` and `sqlite` servers launched
over stdio. Their tool lists are never typed in — they are whatever `tools/list` returned. The
remote three refuse an anonymous `tools/list`, so the registry asks for a credential at that point
and saves it as the connection in the same step.

**Acceptance bar for every screen:** delete the database, `docker compose up`, sign up, and reach
that screen's finished state using only the product.

---

## 14. What we are deliberately not building

The brief lists these as time sinks that teach little here, and we are taking it at its word:
code generation with sandboxing, single sign-on, token exchange protocols, per-agent service
accounts, canary deployments.

Also worth knowing: **LangGraph Platform is not free** and self-hosting its Agent Server needs a paid
licence key. We run our own server from day one. Free tracing is fine to turn on.

---

## 15. Where to go next

`docs/DESIGN.md` carries the technical design — HLD, LLD, UML sequence and class diagrams, the data
model, the API contract and the module layout.

Nothing parallel starts until two things exist and are tested: **the config schema in §2** and **the
tenancy switch in §4**. Those are days 1–2, all four people together.
