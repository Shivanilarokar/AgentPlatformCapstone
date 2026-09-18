/* One agent, in full.
 *
 * Six tabs, matching the mockup. All real except API, which says which step
 * builds it rather than showing numbers that are not true.
 *
 * The graph on Overview is DRAWN FROM THE STORED CONFIGURATION. Edit the config
 * and the picture changes - it is not a hand-maintained diagram.
 */

import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { ago, del, get } from "../api";
import { Badge, Card, Check, Loading, Note, RiskBadge, SectionTitle, TopBar } from "../ui";
import { ApiTab } from "./ApiTab";
import { PlaygroundTab, RunsTab } from "./Playground";
import { SettingsTab } from "./Settings";

const TABS = [
  ["overview", "Overview"],
  ["playground", "Playground"],
  ["connections", "Connections"],
  ["runs", "Runs"],
  ["api", "API"],
  ["settings", "Settings"],
];

/* ------------------------------------------------------------------ graph */

function AgentGraph({ graph }) {
  const nodes = graph.nodes ?? [];
  const edges = graph.edges ?? [];

  const workers = nodes.filter((n) => ["agent", "specialist", "coordinator"].includes(n.kind));
  const tools = nodes.filter((n) => n.kind === "tool");

  const COL = { input: 40, worker: 230, tool: 470 };
  const place = {};
  place.message = { x: COL.input, y: 20 + (Math.max(workers.length, tools.length) * 62) / 2 - 20 };
  workers.forEach((n, i) => { place[n.id] = { x: COL.worker, y: 20 + i * 74 }; });
  tools.forEach((n, i) => { place[n.id] = { x: COL.tool, y: 20 + i * 62 }; });

  const height = 40 + Math.max(workers.length * 74, tools.length * 62, 60);
  const W = { input: 90, worker: 150, tool: 210 };
  const H = 40;

  const box = (id) => {
    const p = place[id];
    const n = nodes.find((x) => x.id === id) ?? { kind: "input" };
    const w = n.kind === "tool" ? W.tool : n.kind === "input" ? W.input : W.worker;
    return { ...p, w, h: H, cx: p.x + w, cy: p.y + H / 2, lx: p.x, n };
  };

  return (
    <div className="graph">
      <svg viewBox={`0 0 700 ${height}`} role="img" aria-label="Agent graph">
        <defs>
          <marker id="ar" viewBox="0 0 8 8" refX="7" refY="4"
                  markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--border-2)" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const a = place[e.from], b = place[e.to];
          if (!a || !b) return null;
          const from = box(e.from), to = box(e.to);
          const guarded = nodes.find((n) => n.id === e.to)?.approval === "ask";
          return (
            <path key={i} className="gedge" markerEnd="url(#ar)"
                  stroke={guarded ? "var(--warn)" : undefined}
                  d={`M${from.cx},${from.cy} C${from.cx + 40},${from.cy} ${to.lx - 40},${to.cy} ${to.lx},${to.cy}`} />
          );
        })}

        {nodes.map((n) => {
          const b = box(n.id);
          const cls = n.kind === "coordinator" ? "gnode accent"
            : n.approval === "ask" ? "gnode warnn" : "gnode";
          return (
            <g key={n.id}>
              <rect className={cls} x={b.x} y={b.y} width={b.w} height={H} rx="8" />
              <text className="gtext" x={b.x + b.w / 2} y={b.y + (n.kind === "tool" ? 17 : 24)}
                    textAnchor="middle">
                {n.kind === "tool" ? n.id.split(".")[1] : n.id}
              </text>
              {n.kind === "tool" && (
                <text className="gsub" x={b.x + b.w / 2} y={b.y + 30} textAnchor="middle">
                  {n.risk}{n.approval === "ask" ? " · asks first" : ""}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------ scores */

function ScoresCard({ score }) {
  if (!score || !score.safety_checks) return <Card><span className="muted">Not scored yet.</span></Card>;
  const passed = score.safety_checks.filter((c) => c.passed).length;
  const untested = !score.runs;
  return (
    <Card>
      <div className="between" style={{ marginBottom: 12 }}>
        <div>
          <div className="faint" style={{ fontSize: 11.5 }}>Does it work?</div>
          <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.1 }}>{score.quality}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="faint" style={{ fontSize: 11.5 }}>Is it safe?</div>
          <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.1 }}>{score.grade}</div>
        </div>
      </div>
      {untested && (
        <Note style={{ fontSize: 12, marginBottom: 10 }}>
          Not run yet. Every quality point is earned by a run in the Playground — and by rating it.
        </Note>
      )}

      <div className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>
        Quality · {score.quality} of 100
      </div>
      <table className="t" style={{ fontSize: 12 }}>
        <tbody>
          {score.quality_checks.map((c) => (
            <tr key={c.key}>
              <td style={{ paddingLeft: 0 }}>
                <span style={{ color: c.passed ? "var(--ok)" : "var(--muted)" }}>{c.passed ? "✓" : "○"}</span>{" "}
                {c.label}
              </td>
              <td className="muted">{c.detail}</td>
              <td className="mono" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{c.points} / {c.max_points}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <hr className="sep" style={{ margin: "10px 0" }} />
      <div className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4 }}>
        Safety · {passed} of 5 → {score.grade}
      </div>
      {score.safety_checks.map((c) => (
        <Check key={c.key} ok={c.passed}>{c.label}{c.passed ? "" : ` — ${c.detail}`}</Check>
      ))}
      <hr className="sep" style={{ margin: "10px 0" }} />
      <div className="faint" style={{ fontSize: 12 }}>
        Every number here traces to a check you can point at. No model guesses a score.
        Publishing needs quality ≥ 70 and safety B or better.
      </div>
      {score.blocked_by?.length > 0 && (
        <Note tone="warn" style={{ marginTop: 10, fontSize: 12 }}>
          <b>Cannot be published yet:</b> {score.blocked_by.join("; ")}.
        </Note>
      )}
    </Card>
  );
}

/* ------------------------------------------------------------------- tabs */

function Overview({ a }) {
  const cfg = a.config;
  const guarded = cfg.tools.filter((t) => t.approval === "ask");

  return (
    <div className="two">
      <div>
        <SectionTitle style={{ marginTop: 0 }}>Agent graph</SectionTitle>
        <AgentGraph graph={a.graph} />
        <p className="muted" style={{ fontSize: 12.5, marginTop: 10 }}>
          Drawn from this agent's configuration, not hand-maintained. Change the config and
          the picture changes.
        </p>

        <SectionTitle>Instructions</SectionTitle>
        <Card className="muted" style={{ fontSize: 13 }}>
          {cfg.topology.type === "supervisor" ? (
            <>
              <div><b>Coordinator:</b> {cfg.topology.supervisor?.instructions}</div>
              {cfg.topology.specialists.map((sp) => (
                <div key={sp.name} style={{ marginTop: 6 }}><b>{sp.name}:</b> {sp.instructions}</div>
              ))}
            </>
          ) : (cfg.topology.supervisor?.instructions || cfg.description)}
        </Card>

        <SectionTitle>Tools</SectionTitle>
        <Card style={{ padding: 0 }}>
          <table className="t">
            <thead><tr><th>Tool</th><th>Risk</th><th>Before it runs</th></tr></thead>
            <tbody>
              {cfg.tools.map((t) => (
                <tr key={t.ref}>
                  <td className="mono">{t.ref}</td>
                  <td><RiskBadge risk={t.risk} /></td>
                  <td>{t.approval === "ask"
                    ? <Badge tone="warn">asks a human</Badge>
                    : <span className="muted">runs automatically</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 9 }}>
          Anything that writes is forced to ask. The platform enforces that — it is not left
          to the agent's instructions.
        </p>
      </div>

      <div>
        <SectionTitle style={{ marginTop: 0 }}>Scores</SectionTitle>
        <ScoresCard score={a.score} />

        <SectionTitle>Details</SectionTitle>
        <Card>
          <dl className="kv" style={{ gridTemplateColumns: "96px 1fr", fontSize: 12.5 }}>
            <dt>Model</dt><dd className="mono">{cfg.model.name}</dd>
            <dt>Shape</dt><dd>{cfg.topology.type === "supervisor"
              ? `coordinator + ${cfg.topology.specialists.length} specialists` : "single agent"}</dd>
            <dt>Schedule</dt><dd>{cfg.schedule || "—"}</dd>
            <dt>Runs</dt><dd>{a.runs}{a.last_run_at ? ` · last ${ago(a.last_run_at)}` : ""}</dd>
            <dt>Needs</dt><dd>{cfg.requires_connections.join(", ")}</dd>
            <dt>Visible to</dt><dd>you only</dd>
          </dl>
        </Card>

        <SectionTitle>Configuration</SectionTitle>
        <Card style={{ padding: 0 }}>
          <pre style={{ margin: 0, padding: 14, fontSize: 11.5, overflowX: "auto" }}>
            {JSON.stringify(cfg, null, 2)}
          </pre>
        </Card>
        <p className="muted" style={{ fontSize: 12.5, marginTop: 9 }}>
          This document is the agent. One runtime reads it and assembles the graph — no Python
          was generated.
        </p>
      </div>
    </div>
  );
}

function ConnectionsTab({ a }) {
  const [servers, setServers] = useState(null);
  const [conns, setConns] = useState([]);
  const load = () => Promise.all([
    get("/v1/servers").then(setServers).catch(() => setServers([])),
    get("/v1/connections").then(setConns).catch(() => setConns([])),
  ]);
  useEffect(() => { load(); }, []);
  const byName = Object.fromEntries((servers ?? []).map((s) => [s.name, s]));
  const connByName = Object.fromEntries(conns.map((c) => [c.server_name, c]));
  async function revoke(name) {
    await del(`/v1/connections/${name}`);
    await load();
  }
  const status = (name) => {
    const s = byName[name];
    if (!servers) return ["", "…"];
    if (!s) return ["danger", "not in your registry"];
    if (s.health !== "ok") return ["danger", "server down"];
    if (s.auth_type === "none") return ["ok", "no credential needed"];
    return s.connected ? ["ok", "active"] : ["warn", "not connected"];
  };
  const missing = servers ? a.config.requires_connections.filter((n) => status(n)[0] !== "ok") : [];

  return (
    <>
      {a.installed_from && (
        <Note style={{ marginBottom: 14, maxWidth: 820 }}>
          <b>Installed from the Marketplace.</b> This is your own copy. It runs with <b>your</b>
          credentials — the publisher's were never included.
          {missing.length > 0 && <> Connect <b>{missing.join(", ")}</b> below to use it.</>}
        </Note>
      )}
      <Card style={{ padding: 0, maxWidth: 820 }}>
        <table className="t">
          <thead><tr><th>Server</th><th>Status</th><th>Used by this agent</th><th>Last used</th><th></th></tr></thead>
          <tbody>
            {a.config.requires_connections.map((name) => {
              const [tone, label] = status(name);
              return (
                <tr key={name}>
                  <td><b>{name}</b> <span className="faint mono" style={{ fontSize: 11.5 }}>{byName[name]?.auth_type ?? ""}</span></td>
                  <td><Badge tone={tone} dot>{label}</Badge></td>
                  <td className="mono" style={{ fontSize: 12 }}>
                    {a.config.tools.filter((t) => t.requires_connection === name)
                      .map((t) => t.ref.split(".")[1]).join(", ")}
                  </td>
                  <td className="muted">{connByName[name]?.last_used_at ? ago(connByName[name].last_used_at) : "—"}</td>
                  <td>
                    {tone === "warn" && <Link className="btn primary sm" to={`/connections?add=${name}`}>Connect</Link>}
                    {tone === "danger" && !byName[name] && <Link className="btn sm" to="/registry">Register</Link>}
                    {tone === "ok" && connByName[name]?.status === "active" && <button className="btn sm" onClick={() => revoke(name)}>Revoke</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
      <Note style={{ marginTop: 18, maxWidth: 820 }}>
        These are <b>your</b> credentials, not the agent's. The configuration only says
        <span className="mono"> requires_connection: "{a.config.requires_connections[0]}"</span>.
        That is why it can be published to the marketplace without leaking anything — whoever
        installs it plugs in their own.
      </Note>
      <Note tone="warn" style={{ marginTop: 12, maxWidth: 820 }}>
        Revoke one on the <Link to="/connections">Connections</Link> screen and this agent goes
        <b> degraded</b>: it keeps working for everything else and reports the broken tool.
        It must not crash.
      </Note>
    </>
  );
}

/* ----------------------------------------------------------------- screen */

export default function AgentDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "overview";

  const [a, setA] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    get(`/v1/agents/${id}`).then(setA).catch((e) => setError(e.message));
  }, [id]);

  if (error) {
    return (
      <div className="content">
        <Note tone="warn">
          <b>Not found.</b> Another workspace's agent id returns exactly this — a 404, never a
          403. A 403 would confirm the agent exists.
        </Note>
      </div>
    );
  }
  if (!a) return <Loading what="this agent" />;

  return (
    <>
      <TopBar
        title={<span className="row" style={{ gap: 9 }}>{a.name} <Badge dot>{a.status}</Badge></span>}
        sub={a.description}
        actions={<>
          <Link className="btn" to="/agents">← My Agents</Link>
          <Link className="btn primary" to={`/agents/${a.id}?tab=playground`} style={{ marginLeft: 6 }}>Open playground</Link>
        </>}
      />

      <div className="tabs">
        {TABS.map(([k, label]) => (
          <a key={k} className={`tab ${k === tab ? "active" : ""}`} href={`#${k}`}
             onClick={(e) => { e.preventDefault(); setParams({ tab: k }, { replace: true }); }}>
            {label}
          </a>
        ))}
      </div>

      <div className="content">
        {tab === "overview" && <Overview a={a} />}
        {tab === "connections" && <ConnectionsTab a={a} />}
        {tab === "playground" && <PlaygroundTab agent={a} />}
        {tab === "runs" && <RunsTab agent={a} />}
        {tab === "api" && <ApiTab agent={a} />}
        {tab === "settings" && <SettingsTab agent={a} onChanged={() => get(`/v1/agents/${id}`).then(setA)} />}
      </div>
    </>
  );
}
