/* MCP Registry.
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
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";

import { ago, get, post } from "../api";
import { Badge, Card, Check, Empty, Field, Loading, RiskBadge, SectionTitle, TopBar } from "../ui";

const TOOLS_SHOWN = 4;

/* --------------------------------------------------------------- one card */

function ServerCard({ server, onRefresh }) {
  const [checking, setChecking] = useState(false);
  const [all, setAll] = useState(false);

  const tools = all ? server.tools : server.tools.slice(0, TOOLS_SHOWN);
  const hidden = server.tools.length - tools.length;

  async function recheck() {
    setChecking(true);
    try { await post(`/v1/servers/${server.name}/refresh`); } catch { /* card will say down */ }
    await onRefresh();
    setChecking(false);
  }

  return (
    <Card className="srv">
      <div className="between">
        <div className="row" style={{ gap: 8 }}>
          <b style={{ fontSize: 14.5 }}>{server.name}</b>
          <Badge tone={server.status === "ok" ? "ok" : "danger"} dot>{server.status}</Badge>
          {server.scope === "private" && <Badge>private</Badge>}
        </div>
        {server.auth_type === "none" ? (
          <Badge tone="ok">no credential needed</Badge>
        ) : server.connected ? (
          <Badge tone="ok">connected</Badge>
        ) : (
          <Link className="btn primary sm" to={`/connections?add=${server.name}`}>Connect</Link>
        )}
      </div>

      <div className="muted" style={{ fontSize: 12.8 }}>{server.description || "—"}</div>

      <div className="row wrap" style={{ gap: 6 }}>
        <span className="chip">{server.transport}</span>
        <span className="chip">{server.auth_type}</span>
        <span className="chip">{server.tools.length} tools</span>
      </div>
      <div className="mono faint" style={{ fontSize: 11, wordBreak: "break-all" }}>
        {server.endpoint}
      </div>

      <hr className="sep" style={{ margin: "4px 0" }} />

      <div>
        {tools.map((t) => (
          <div className="toolrow" key={t.name}>
            <span className="mono" title={t.description}>{t.name}</span>
            <RiskBadge risk={t.risk} />
          </div>
        ))}
        {hidden > 0 && <button className="linkish" onClick={() => setAll(true)}>+ {hidden} more</button>}
      </div>

      <div className="between">
        <span className="faint" style={{ fontSize: 11.5 }}>
          checked {ago(server.last_checked_at)}
          {server.scope === "shared" && server.shared_by && ` · shared by ${server.shared_by}`}
        </span>
        <button className="btn sm" onClick={recheck} disabled={checking}>
          {checking ? "checking…" : "Re-check"}
        </button>
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------------- the form */

const EMPTY = {
  name: "", transport: "http", endpoint: "", auth_type: "none",
  token_env: "", description: "", visibility: "private", token: "",
};

function RegisterForm({ catalogue, isAdmin, onRegistered }) {
  const [f, setF] = useState(EMPTY);
  const [error, setError] = useState(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  function quickPick(c) {
    setF({
      ...EMPTY,
      name: c.name, transport: c.transport, endpoint: c.endpoint,
      auth_type: c.auth_type, token_env: c.token_env ?? "", description: c.description,
    });
    setError(null); setStatus("");
  }

  async function save() {
    setError(null); setStatus("connecting…"); setBusy(true);
    try {
      const created = await post("/v1/servers", {
        name: f.name.trim(), transport: f.transport, endpoint: f.endpoint.trim(),
        auth_type: f.auth_type, token_env: f.token_env.trim() || null,
        description: f.description.trim(), visibility: f.visibility,
        token: f.token || null,
      });
      setStatus(`found ${created.tools.length} tools`);
      setF(EMPTY);
      await onRegistered();
    } catch (e) {
      setStatus("");
      setError(e.message);
    }
    setBusy(false);
  }

  const placeholder = {
    http: "https://mcp.example.com/mcp   or   http://local_slack:9001/mcp",
    sse: "https://mcp.example.com/sse",
    stdio: "npx -y @modelcontextprotocol/server-memory",
  }[f.transport];

  return (
    <div id="register-form">
      <SectionTitle>Registering a server</SectionTitle>
      <div className="row wrap" style={{ gap: 24, alignItems: "flex-start" }}>
        <div className="modal">
          <div className="mh">
            <div style={{ fontWeight: 650, fontSize: 14.5 }}>Register an MCP server</div>
            <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
              Saved only after the platform successfully connects.
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
            <Field label="Endpoint" hint={f.transport === "stdio" ? "(a command line)" : "(an address)"}>
              <input className="mono" value={f.endpoint} onChange={set("endpoint")}
                     placeholder={placeholder} autoComplete="off" />
            </Field>
            <Field label="Authentication">
              <select value={f.auth_type} onChange={set("auth_type")}>
                <option value="none">none</option>
                <option value="api_key">api_key</option>
                <option value="oauth">oauth</option>
              </select>
            </Field>
            {f.transport === "stdio" && f.auth_type !== "none" && (
              <Field label="Credential env var" hint="(what the subprocess reads)">
                <input className="mono" value={f.token_env} onChange={set("token_env")}
                       placeholder="GITHUB_PERSONAL_ACCESS_TOKEN" />
              </Field>
            )}
            <Field label="Visible to">
              <select value={f.visibility} onChange={set("visibility")}>
                <option value="private">Just my workspace</option>
                <option value="shared" disabled={!isAdmin}>
                  Everyone{isAdmin ? "" : " (platform admin only)"}
                </option>
              </select>
            </Field>
            <Field label="Credential" hint="(used for this call only, if the server needs one to answer)">
              <input type="password" className="mono" value={f.token} onChange={set("token")}
                     placeholder={f.auth_type === "none" ? "not needed" : "optional for discovery"} />
            </Field>

            {error && (
              <div className="note warn" style={{ marginBottom: 12 }}>
                <b>Nothing was saved.</b> {error}
              </div>
            )}

            <div className="row">
              <button className="btn primary sm" onClick={save}
                      disabled={busy || !f.name.trim() || !f.endpoint.trim()}>
                Connect &amp; save
              </button>
              <button className="btn sm" onClick={() => { setF(EMPTY); setError(null); setStatus(""); }}>
                Clear
              </button>
              <span className="faint" style={{ fontSize: 12 }}>{status}</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 280 }}>
          <Card>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>What happens on save</div>
            <Check ok mark="1">Open a connection to the endpoint</Check>
            <Check ok mark="2">Ask it for its tool list</Check>
            <Check ok mark="3">Mark each tool read, write or destructive</Check>
            <Check ok mark="4">Store the list; mark the server healthy</Check>
            <Check ok={false}><span className="muted">If it does not answer, nothing is saved</span></Check>
            <hr className="sep" />
            <div className="faint" style={{ fontSize: 12 }}>
              A scheduled job re-checks every server every five minutes and marks the dead ones.
              Agents that depend on a dead server show as degraded.
            </div>
          </Card>

          <Card style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Quick picks</div>
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
              Real open-source MCP servers the platform can launch itself. Click one to fill the
              form, then save it like anything else.
            </div>
            <div className="row wrap" style={{ gap: 6 }}>
              {catalogue.map((c) => (
                <button key={c.name} className="btn sm" onClick={() => quickPick(c)} title={c.description}>
                  {c.name}
                </button>
              ))}
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

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!servers) return <Loading what="the registry" />;

  const shared = servers.filter((s) => s.scope === "shared");
  const priv = servers.filter((s) => s.scope === "private");

  return (
    <>
      <TopBar
        title="MCP Registry"
        sub="Tool servers your agents can use. Register one, connect once, reuse everywhere."
        actions={
          <button className="btn primary"
                  onClick={() => document.getElementById("register-form")?.scrollIntoView({ behavior: "smooth" })}>
            + Register a server
          </button>
        }
      />
      <div className="content">
        {servers.length === 0 && (
          <Empty title="No servers yet">
            Register one below — paste an address, or pick one the platform can launch.
          </Empty>
        )}

        {shared.length > 0 && (
          <>
            <SectionTitle style={{ marginTop: 0 }}>Shared servers</SectionTitle>
            <div className="grid">
              {shared.map((s) => <ServerCard key={s.name} server={s} onRefresh={load} />)}
            </div>
          </>
        )}

        {priv.length > 0 && (
          <>
            <SectionTitle>Private to {me?.company ?? "this workspace"}</SectionTitle>
            <div className="grid">
              {priv.map((s) => <ServerCard key={s.name} server={s} onRefresh={load} />)}
            </div>
          </>
        )}

        <RegisterForm catalogue={catalogue} isAdmin={me?.role === "platform_admin"} onRegistered={load} />
      </div>
    </>
  );
}
