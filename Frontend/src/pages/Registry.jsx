/* MCP Registry - screens/registry.html, made real.
 *
 * The five things this screen must do, from the mockup:
 *
 *   1. A form to register an MCP server: name, transport, endpoint, auth type.
 *   2. On save, the platform connects and asks the server what tools it has.
 *   3. Each tool is stored as read, write or destructive.
 *   4. A scheduled job re-checks every server and marks the dead ones.
 *   5. Servers can be shared with everyone or private to one company.
 *
 * There is no field on this form where you could type a tool name, because if
 * there were, the registry would be wrong the moment a server changed.
 *
 * The three remote servers (GitHub, Slack, Atlassian) answer 401 to an
 * anonymous tools/list. The API reports that as `auth_required`; the form then
 * shows a credential field. Connecting is two steps: "Connect" lists the tools
 * (nothing is saved), the admin ticks which ones agents may use, then "Save".
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Link, useOutletContext } from "react-router-dom";

import { ago, del, get, patch, post } from "../api";
import { Badge, Card, Check, Empty, Field, Loading, RiskBadge, SectionTitle, TopBar } from "../ui";
import { ToolPicker } from "./ToolPicker";

const TOOLS_SHOWN = 4;
const RISKS = ["destructive", "write", "read"];

/* The four tools a card shows before "+ N more" cover every risk level the
 * server has, strictest first - so a card never looks read-only when it is
 * not. Alphabetical-first-four hid git_reset (destructive) behind git_add. */
function sample(tools) {
  const byRisk = RISKS.map((r) => tools.filter((t) => t.risk === r));
  const out = [];
  for (let i = 0; out.length < Math.min(TOOLS_SHOWN, tools.length); i++) {
    for (const bucket of byRisk) {
      if (bucket[i] && out.length < TOOLS_SHOWN) out.push(bucket[i]);
    }
  }
  return out;
}

/* --------------------------------------------------------------- one card */

function ServerCard({ server, onRefresh, platformAdmin, companyAdmin }) {
  const [checking, setChecking] = useState(false);
  const [all, setAll] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState(null);

  async function share(visibility) {
    setChecking(true);
    try { await patch(`/v1/servers/${server.name}`, { visibility }); } finally { setChecking(false); }
    await onRefresh();
  }

  // Only what the admin switched on: that is all an agent can ever use.
  const enabled = server.tools.filter((t) => t.enabled);
  const tools = all ? enabled : sample(enabled);
  const hidden = enabled.length - tools.length;
  const counts = RISKS.map((r) => [r, enabled.filter((t) => t.risk === r).length])
    .filter(([, n]) => n > 0);

  async function recheck() {
    setChecking(true);
    try { await post(`/v1/servers/${server.name}/refresh`); } catch { /* the card will say down */ }
    await onRefresh();
    setChecking(false);
  }

  async function remove() {
    const who = server.visibility === "company" ? " Everyone in your company loses it." : "";
    const shared = server.visibility === "everyone" ? " Every company loses it." : "";
    if (!window.confirm(
      `Delete "${server.name}"?${who}${shared} Agents that use its tools will stop working with it. ` +
      "Saved connections are kept, and you can register it again later."
    )) return;
    setDeleting(true); setError(null);
    try {
      await del(`/v1/servers/${server.name}`);
      await onRefresh();  // the card unmounts
    } catch (e) {
      setError(e.message);
      setDeleting(false);
    }
  }

  return (
    <Card className="srv">
      <div className="between">
        <div className="row" style={{ gap: 8 }}>
          <b style={{ fontSize: 14.5 }} title={server.endpoint}>{server.name}</b>
          <Badge tone={server.health === "ok" ? "ok" : "danger"} dot>{server.health}</Badge>
          {server.visibility === "private" && <Badge>just me</Badge>}
          {server.visibility === "company" && <Badge tone="ok">whole company</Badge>}
        </div>
        {platformAdmin ? null : server.connected
          ? <Badge tone="ok">connected</Badge>
          : <Link className="btn primary sm" to={`/connections?add=${server.name}`}>Connect</Link>}
      </div>

      <div className="muted" style={{ fontSize: 12.8 }}>{server.description || "—"}</div>

      <div className="row wrap" style={{ gap: 6 }}>
        <span className="chip">{server.transport}</span>
        <span className="chip">{server.auth_type}</span>
        <span className="chip" title="tools switched on / tools the server reports">
          {enabled.length === server.tools.length
            ? `${enabled.length} tools`
            : `${enabled.length} of ${server.tools.length} tools on`}
        </span>
        {counts.map(([r, n]) => <RiskBadge key={r} risk={r} label={`${n} ${r}`} />)}
      </div>

      <hr className="sep" style={{ margin: "4px 0" }} />
      <div>
        {enabled.length === 0 && (
          <div className="faint" style={{ fontSize: 12.5 }}>No tools switched on — agents can't use this server.</div>
        )}
        {tools.map((t) => (
          <div className="toolrow" key={t.name}>
            <span className="mono" title={t.description}>{t.name}</span>
            <RiskBadge risk={t.risk} />
          </div>
        ))}
        {hidden > 0 && (
          <button className="linkish" onClick={() => setAll(true)}>+ {hidden} more</button>
        )}
      </div>

      <div className="between">
        <span className="faint" style={{ fontSize: 11.5 }}>
          {server.registered_by && <>registered by {server.mine ? "you" : server.registered_by} · </>}
          checked {ago(server.last_checked_at)}
        </span>
        <span className="row" style={{ gap: 10 }}>
          {server.mine && companyAdmin && server.visibility === "private" && (
            <button className="linkish" style={{ padding: 0 }} onClick={() => share("company")} disabled={checking}
                    title="Everyone in your company sees its tools and is asked for their own credential">
              share with company
            </button>
          )}
          {server.mine && companyAdmin && server.visibility === "company" && (
            <button className="linkish" style={{ padding: 0 }} onClick={() => share("private")} disabled={checking}>
              make private
            </button>
          )}
          <button className="linkish" style={{ padding: 0 }} onClick={recheck} disabled={checking}>
            {checking ? "working…" : "re-check"}
          </button>
        </span>
      </div>

      {error && <div className="note warn" style={{ fontSize: 12 }}>{error}</div>}

      {/* Only an admin, and only for a server they own: the API says so. */}
      {server.editable && (
        <div className="row" style={{ gap: 8 }}>
          <Link className="btn sm" to={`/registry/${server.name}/edit`}>Edit</Link>
          <button className="btn sm" style={{ color: "var(--danger)" }} onClick={remove} disabled={deleting}>
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- the form */

const EMPTY = {
  name: "", transport: "http", endpoint: "", auth_type: "oauth",
  credential_env_var: "", description: "", visibility: "private", token: "",
};

const STEPS = [
  "Open a connection to the endpoint",
  "Ask it for its tool list",
  "Mark each tool read, write or destructive",
  "You pick which tools agents may use",
  "Store the list; mark the server healthy",
];

/* Changing any of these means the tool list on screen came from somewhere else. */
const CONNECTION_KEYS = ["transport", "endpoint", "auth_type", "credential_env_var", "token"];

function RegisterForm({ catalogue, role, onRegistered, prefill }) {
  const platformAdmin = role === "platform_admin";
  // The platform admin's only choice is "Everyone", so that is the value to start from.
  const blank = platformAdmin ? { ...EMPTY, visibility: "everyone" } : EMPTY;
  const [f, setF] = useState(blank);
  const [error, setError] = useState(null);
  const [needsToken, setNeedsToken] = useState(false);
  const [phase, setPhase] = useState("idle"); // idle | connecting | ok | failed
  const [hint, setHint] = useState("");
  // Step one ("Connect") fills these; step two ("Save") sends the ticked names.
  const [found, setFound] = useState(null); // null until the server has been asked
  const [picked, setPicked] = useState(new Set());

  const set = (k) => (e) => {
    setF({ ...f, [k]: e.target.value });
    if (CONNECTION_KEYS.includes(k)) setFound(null);
  };

  const quickPick = useCallback((c) => {
    setF({
      ...blank,
      name: c.name, transport: c.transport, endpoint: c.endpoint,
      auth_type: c.auth_type, credential_env_var: c.credential_env_var ?? "", description: c.description,
    });
    setHint(c.credential_hint || "");
    setNeedsToken(c.transport !== "stdio" && c.auth_type !== "none");
    setError(null); setPhase("idle"); setFound(null);
  }, [platformAdmin]);

  // Arriving from the builder's "Register slack" button: /registry?add=slack
  useEffect(() => {
    const c = prefill && catalogue.find((x) => x.name === prefill);
    if (c) {
      quickPick(c);
      document.getElementById("register-form")?.scrollIntoView({ behavior: "smooth" });
    }
  }, [prefill, catalogue, quickPick]);

  function fail(e) {
    setPhase("failed");
    if (e.code === "auth_required") {
      setNeedsToken(true);
      setError(`${f.name || "That server"} answered but will not list its tools without a credential. Paste one and try again.`);
    } else if (e.code === "credential_rejected") {
      setNeedsToken(true);
      setError(`${f.name || "That server"} rejected that credential. ${e.message.split("(").pop().replace(")", "")}. ${hint}`);
    } else if (e.code === "already_exists") {
      setError(`${f.name} is already registered. To change it, use Edit on its card above.`);
    } else {
      setError(e.message);
    }
  }

  /* Step one: reach the server and list its tools. Nothing is saved. */
  async function connect() {
    setError(null); setPhase("connecting");
    try {
      const list = await post("/v1/servers/discover", {
        transport: f.transport, endpoint: f.endpoint.trim(), auth_type: f.auth_type,
        credential_env_var: f.credential_env_var.trim() || null, token: f.token || null,
      });
      setFound(list);
      setPicked(new Set(list.filter((t) => t.suggested).map((t) => t.name))); // read-only to start
      setPhase("idle");
    } catch (e) {
      fail(e);
    }
  }

  /* Step two: save the server with the tools the admin ticked. */
  async function save() {
    setError(null); setPhase("connecting");
    try {
      const created = await post("/v1/servers", {
        name: f.name.trim(), transport: f.transport, endpoint: f.endpoint.trim(),
        auth_type: f.auth_type, credential_env_var: f.credential_env_var.trim() || null,
        description: f.description.trim(), visibility: f.visibility,
        token: f.token || null, enabled_tools: [...picked],
      });
      setPhase("ok");
      setError(null);
      setF(blank); setNeedsToken(false); setHint(""); setFound(null); setPicked(new Set());
      await onRegistered(created);
    } catch (e) {
      fail(e);
    }
  }

  function cancel() {
    setF(blank);
    setError(null); setPhase("idle"); setNeedsToken(false); setHint(""); setFound(null); setPicked(new Set());
  }

  const placeholder = {
    http: "https://api.githubcopilot.com/mcp/",
    sse: "https://mcp.example.com/sse",
    stdio: "npx -y @modelcontextprotocol/server-filesystem /srv/workspace",
  }[f.transport];

  const showToken = needsToken || (f.auth_type !== "none" && f.transport !== "stdio");

  return (
    <div id="register-form">
      <SectionTitle>Registering a server</SectionTitle>
      <div className="row wrap" style={{ gap: 24, alignItems: "flex-start" }}>
        <div className="modal">
          <div className="mh">
            <div style={{ fontWeight: 650, fontSize: 14.5 }}>Register an MCP server</div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
              Connect first, then choose which tools agents may use. Nothing is saved until you do.
            </div>
            <div className="row wrap" style={{ gap: 5, marginTop: 8 }}>
              {catalogue.map((c) => (
                <button key={c.name} className="chip" style={{ cursor: "pointer" }}
                        onClick={() => quickPick(c)} title={c.endpoint}>
                  {c.name}
                </button>
              ))}
            </div>
          </div>
          <div className="mb">
            <Field label="Name">
              <input value={f.name} onChange={set("name")} placeholder="slack"
                     pattern="[a-z][a-z0-9_]*" autoComplete="off" />
            </Field>
            <Field label="Transport">
              <select value={f.transport} onChange={set("transport")}>
                <option value="http">http</option>
                <option value="sse">sse</option>
                <option value="stdio">stdio</option>
              </select>
            </Field>
            <Field label="Endpoint">
              <input className="mono" value={f.endpoint} onChange={set("endpoint")}
                     placeholder={placeholder} autoComplete="off" />
            </Field>
            <Field label="Authentication">
              <select value={f.auth_type} onChange={set("auth_type")}>
                <option value="oauth">oauth</option>
                <option value="api_key">api_key</option>
                <option value="none">none</option>
              </select>
            </Field>
            {f.transport === "stdio" && f.auth_type !== "none" && (
              <Field label="Credential env var" hint="(what the subprocess reads)">
                <input className="mono" value={f.credential_env_var} onChange={set("credential_env_var")}
                       placeholder="GITHUB_PERSONAL_ACCESS_TOKEN" autoComplete="off" />
              </Field>
            )}
            {showToken && (
              <Field label="Credential" hint={platformAdmin
                  ? "(used once to ask the server for its tools, then dropped - every person connects with their own)"
                  : "(encrypted; saved as your connection)"}>
                <input type="password" className="mono" value={f.token} onChange={set("token")}
                       placeholder="paste it here" autoComplete="new-password" />
                {hint && <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>{hint}</div>}
              </Field>
            )}
            <Field label="Visible to">
              <select value={f.visibility} onChange={set("visibility")} style={{ marginBottom: 2 }}>
                {platformAdmin ? (
                  <option value="everyone">Everyone (admin only)</option>
                ) : (
                  <>
                    <option value="private">Just my workspace</option>
                    {role === "admin" && <option value="company">My whole company</option>}
                    <option value="everyone" disabled>Everyone (admin only)</option>
                  </>
                )}
              </select>
            </Field>

            {found && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                  Tools agents may use <span className="faint" style={{ fontWeight: 400 }}>({found.length} found)</span>
                </div>
                <ToolPicker tools={found} selected={picked} onChange={setPicked} />
                <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>
                  Read-only tools are ticked to start. Write and destructive tools still ask a person to
                  approve every call. You can change this later with Edit.
                </div>
              </div>
            )}

            {error && (
              <div className="note warn" style={{ marginBottom: 12 }}>
                <b>Nothing was saved.</b> {error}
              </div>
            )}

            <div className="row">
              {found ? (
                <button className="btn primary sm" onClick={save}
                        disabled={phase === "connecting" || picked.size === 0 || !f.name.trim()}
                        title={picked.size === 0 ? "Tick at least one tool" : !f.name.trim() ? "Give it a name" : ""}>
                  {phase === "connecting" ? "Saving…" : `Save server (${picked.size} tool${picked.size === 1 ? "" : "s"})`}
                </button>
              ) : (
                <button className="btn primary sm" onClick={connect}
                        disabled={phase === "connecting" || !f.endpoint.trim()}>
                  {phase === "connecting" ? "Connecting…" : "Connect"}
                </button>
              )}
              <button className="btn sm" onClick={cancel}>Cancel</button>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 280 }}>
          <Card>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>What happens on save</div>
            {STEPS.map((s, i) => (
              <Check key={s} ok={phase !== "failed"} mark={phase === "ok" ? "✓" : i + 1}>
                {phase === "connecting" && i === 0 ? <b>{s}…</b> : s}
              </Check>
            ))}
            <Check ok={false}>
              <span className={phase === "failed" ? "" : "muted"}>
                If it does not answer, nothing is saved
              </span>
            </Check>
            <hr className="sep" />
            <div className="faint" style={{ fontSize: 12 }}>
              A scheduled job re-checks every server and marks the dead ones. Agents that depend
              on a dead server show as degraded.
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- screen */

export default function Registry() {
  const me = useOutletContext();
  const [params] = useSearchParams();
  const [servers, setServers] = useState(null);
  const [catalogue, setCatalogue] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([get("/v1/servers"), get("/v1/servers/catalogue")]);
      setServers(s);
      setCatalogue(c);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function scrollToForm() {
    document.getElementById("register-form")?.scrollIntoView({ behavior: "smooth" });
  }

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!servers) return <Loading what="the registry" />;

  const platformAdmin = me?.role === "platform_admin";
  const shared = servers.filter((s) => s.visibility === "everyone");
  // The mockup's "Private to Northwind Labs": what this person can use inside
  // the company - their own registrations, plus any the admin opened company-wide.
  const priv = servers.filter((s) => s.visibility !== "everyone");

  return (
    <>
      <TopBar
        title="MCP Registry"
        sub="Tool servers your agents can use. Register one, connect once, reuse everywhere."
        actions={<button className="btn primary" onClick={scrollToForm}>+ Register a server</button>}
      />
      <div className="content">
        {servers.length === 0 && (
          <Empty title="No servers yet">
            {platformAdmin
              ? "Register one below and every company will see it."
              : "Register one below — paste an address, or pick GitHub, Slack, Jira, filesystem, git or sqlite and the platform will connect to it."}
          </Empty>
        )}

        {shared.length > 0 && (
          <>
            <SectionTitle style={{ marginTop: 0 }}>Shared servers</SectionTitle>
            <div className="grid">
              {shared.map((s) => (
                <ServerCard key={s.name} server={s} onRefresh={load} platformAdmin={platformAdmin} />
              ))}
            </div>
          </>
        )}

        {priv.length > 0 && (
          <>
            <SectionTitle>Private to {me?.company ?? "this workspace"}</SectionTitle>
            <div className="grid">
              {priv.map((s) => <ServerCard key={s.name} server={s} onRefresh={load} companyAdmin={me?.role === "admin"} />)}
            </div>
          </>
        )}

        <RegisterForm catalogue={catalogue} role={me?.role} onRegistered={load} prefill={params.get("add")} />
      </div>
    </>
  );
}
