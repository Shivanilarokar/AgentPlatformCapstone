/* Playground and Runs - the two tabs where an agent is actually exercised.
 *
 * The approval card is the centre of the screen. It is drawn from a run whose
 * status is `awaiting_approval`: a LangGraph interrupt raised inside the tool
 * wrapper, parked in this company's checkpoint tables. Nothing has been sent.
 * Reload the page, restart the server - the card is still here, because the
 * browser only ever held a run id.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ago, get, post, stream } from "../api";
import { Badge, Card, Check, Note, RiskBadge, SectionTitle } from "../ui";

const STATUS_TONE = { ok: "ok", awaiting_approval: "warn", rejected: "", error: "danger", running: "" };
const STATUS_LABEL = { ok: "ok", awaiting_approval: "approval", rejected: "rejected", error: "error", running: "running" };

export function RunStatus({ status }) {
  return <Badge tone={STATUS_TONE[status] ?? ""} dot>{STATUS_LABEL[status] ?? status}</Badge>;
}

/* ------------------------------------------------------------ transcript */

function Line({ text }) {
  // "[reporter] slack.slack_send_message -> ok" -> badge + mono call
  const m = text.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (!m) return <p>{text}</p>;
  const [, who, rest] = m;
  const arrow = rest.match(/^->\s*(\S+)$/);
  if (arrow) return <p><Badge>{who}</Badge> → <Badge>{arrow[1]}</Badge></p>;
  const call = rest.match(/^(\S+\.\S+)\s*->\s*(.*)$/);
  if (call) {
    const failed = call[2].startsWith("ERROR");
    return (
      <p className="muted" style={{ fontSize: 12.5 }}>
        <Badge>{who}</Badge> called <code>{call[1]}</code> —{" "}
        {failed ? <span style={{ color: "var(--danger)" }}>{call[2].replace(/^ERROR from \S+:\s*/, "the server answered: ")}</span> : call[2]}
      </p>
    );
  }
  return <p><Badge>{who}</Badge> {rest}</p>;
}

function Approval({ run, onDecide, busy }) {
  const p = run.pending;
  const args = Object.entries(p.args ?? {});
  return (
    <div className="approval">
      <div className="ah">Waiting for approval · {p.tool}</div>
      <div className="ab">
        {args.map(([k, v]) => (
          <div key={k} style={{ marginBottom: 8 }}>
            <div className="faint" style={{ fontSize: 11.5, marginBottom: 3 }}>{k}</div>
            <pre style={{ margin: 0, fontSize: 12, whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }}>
              {typeof v === "string" ? v : JSON.stringify(v, null, 2)}
            </pre>
          </div>
        ))}
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary sm" disabled={busy} onClick={() => onDecide("approve")}>Approve</button>
          <button className="btn sm" disabled={busy} onClick={() => onDecide("reject")}>Reject</button>
          <span className="faint" style={{ fontSize: 12 }}>
            {p.risk} tool · nothing runs until you click
          </span>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------- before a run: readiness */

const CONN_LABEL = {
  connected: ["ok", "connected"],
  no_credential_needed: ["ok", "no credential needed"],
  needs_credential: ["danger", "not connected"],
  not_registered: ["danger", "not in your registry"],
};

function NeedsConnection({ readiness, onRecheck, onRunAnyway }) {
  return (
    <div className="interrupt">
      <div className="ihead">
        <span>Before it runs · check connections</span>
        <span>{readiness.missing.length} missing</span>
      </div>
      <div className="ibody">
        <p style={{ marginTop: 0 }}>This agent uses servers you are not connected to yet:</p>
        {Object.entries(readiness.connections).map(([name, st]) => {
          const [tone, label] = CONN_LABEL[st];
          return (
            <div className="row" style={{ gap: 10, padding: "5px 0" }} key={name}>
              <Badge tone={tone} dot>{label}</Badge><b>{name}</b>
            </div>
          );
        })}
        <hr className="sep" style={{ margin: "8px 0 12px" }} />
        <p className="muted" style={{ margin: "0 0 10px", fontSize: 12.5 }}>
          Add your own credential and it is encrypted immediately. It is never written into the agent.
        </p>
        <div className="row wrap">
          {readiness.missing.map((n) => (
            readiness.connections[n] === "not_registered"
              ? <Link key={n} className="btn primary sm" to="/registry">Register {n}</Link>
              : <Link key={n} className="btn primary sm" to={`/connections?add=${n}`}>Connect {n}</Link>
          ))}
          <button className="btn sm" onClick={onRecheck}>I have connected it</button>
          <button className="btn sm" onClick={onRunAnyway}>Run anyway (degraded)</button>
        </div>
      </div>
    </div>
  );
}

function Activity({ a }) {
  if (!a) return null;
  const who = a.worker ? <Badge>{a.worker}</Badge> : null;
  if (a.kind === "thinking") return <p className="muted" style={{ fontSize: 12.5 }}>{who} thinking…</p>;
  if (a.kind === "route") return <p className="muted" style={{ fontSize: 12.5 }}><Badge>supervisor</Badge> → handing the work to <Badge>{a.to}</Badge></p>;
  if (a.kind === "tool_call") {
    return (
      <p className="muted" style={{ fontSize: 12.5 }}>
        {who} calling <code>{a.tool}</code> <RiskBadge risk={a.risk} />
        {a.asks && <span className="faint"> — a human will be asked first</span>}
      </p>
    );
  }
  if (a.kind === "tool_result") {
    return (
      <p className="muted" style={{ fontSize: 12.5 }}>
        {who} <code>{a.tool}</code> {a.ok ? "answered" : <span style={{ color: "var(--danger)" }}>failed</span>}
      </p>
    );
  }
  return null;
}

/* ------------------------------------------------------------- playground */

export function PlaygroundTab({ agent }) {
  const [runs, setRuns] = useState([]);
  const [current, setCurrent] = useState(null); // the run shown in the chat
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [gate, setGate] = useState(null);      // the readiness card, when a run was refused
  const [pendingInput, setPendingInput] = useState("");
  const [live, setLive] = useState([]);        // activity events for the run in progress
  const [now, setNow] = useState(null);        // the latest activity, shown under the composer
  const [score, setScore] = useState(null);
  const bottom = useRef(null);

  const loadScore = useCallback(() => get(`/v1/agents/${agent.id}/scores`).then(setScore).catch(() => {}), [agent.id]);

  const checkReady = useCallback(async () => {
    const r = await get(`/v1/agents/${agent.id}/readiness`);
    setReadiness(r);
    return r;
  }, [agent.id]);

  const load = useCallback(async () => {
    const rows = await get(`/v1/agents/${agent.id}/runs`);
    setRuns(rows);
    // Come back after a restart: the run that is still waiting is what you see.
    const waiting = rows.find((r) => r.status === "awaiting_approval");
    setCurrent((cur) => waiting ?? (cur ? rows.find((r) => r.id === cur.id) ?? cur : rows[0] ?? null));
  }, [agent.id]);

  useEffect(() => { load().catch((e) => setError(e.message)); checkReady().catch(() => {}); loadScore(); }, [load, checkReady, loadScore]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [current, busy, live]);

  // One streaming call for both starting and answering: activity arrives live,
  // transcript lines as each step completes, the finished run last.
  async function drive(body, seed) {
    setBusy(true); setError(null); setLive([]); setNow(null);
    setCurrent(seed);
    const lines = [...(seed.transcript ?? [])];
    try {
      await stream(`/v1/agents/${agent.id}/stream`, body, (name, data) => {
        if (name === "run") setCurrent((c) => ({ ...c, id: data.id }));
        else if (name === "activity") { setLive((l) => [...l, data]); setNow(data); }
        else if (name === "step") { lines.push(data.line); setCurrent((c) => ({ ...c, transcript: [...lines] })); }
        else { // ok | awaiting_approval | rejected | error - the finished RunOut
          setCurrent(data);
          setRuns((rs) => (rs.some((x) => x.id === data.id) ? rs.map((x) => (x.id === data.id ? data : x)) : [data, ...rs]));
          setNow(null);
        }
      });
    } catch (e) { setError(e.message); }
    setBusy(false);
    loadScore();
  }

  async function send(force = false) {
    const text = (force ? pendingInput : input).trim();
    if (!text) return;
    setError(null);
    // Check connections FIRST. A missing credential is asked for, not run into.
    if (!force) {
      const r = await checkReady().catch(() => null);
      if (r && !r.ready) { setGate(r); setPendingInput(text); return; }
    }
    setGate(null);
    setInput("");
    await drive({ input: text }, { id: "pending", input: text, status: "running", transcript: [], pending: null, output: "" });
  }

  async function decide(decision) {
    await drive({ run_id: current.id, decision }, { ...current, status: "running", pending: null });
  }

  async function rate(value) {
    const r = await post(`/v1/agents/${agent.id}/runs/${current.id}/feedback`, { value });
    setCurrent(r);
    setRuns((rs) => rs.map((x) => (x.id === r.id ? r : x)));
  }

  const r = current;
  return (
    <div className="two">
      <div>
        <div className="chat">
          {!r && (
            <div className="msg bot">
              <div className="who-av">F</div>
              <div className="body">
                <p style={{ marginTop: 0 }}>Talk to <b>{agent.name}</b>. Every tool it calls shows
                  up here; anything that writes stops and asks you first.</p>
              </div>
            </div>
          )}

          {r && <div className="msg user"><div className="body">{r.input}</div></div>}

          {r && r.transcript.length > 0 && (
            <div className="msg bot">
              <div className="who-av">F</div>
              <div className="body">{r.transcript.map((t, i) => <Line key={i} text={t} />)}</div>
            </div>
          )}

          {r && r.status === "running" && (
            <div className="msg bot"><div className="who-av">F</div>
              <div className="body" style={{ width: "100%" }}>
                {live.length === 0 && <p className="muted" style={{ margin: 0 }}>Starting — compiling the agent from its configuration and asking each server for its tools…</p>}
                {live.slice(-6).map((a, i) => <Activity key={i} a={a} />)}
                {now && <p className="faint" style={{ fontSize: 12, margin: 0 }}>⋯ live — a real model and real servers; each line appears the moment it happens</p>}
              </div></div>
          )}

          {r && r.status === "awaiting_approval" && r.pending && (
            <div className="msg bot"><div className="who-av">F</div>
              <div className="body" style={{ width: "100%" }}>
                <Approval run={r} onDecide={decide} busy={busy} />
              </div></div>
          )}

          {r && (r.status === "ok" || r.status === "rejected" || r.status === "error") && (
            <div className="msg bot"><div className="who-av">F</div>
              <div className="body">
                {r.status === "error" ? <p className="muted">The run failed: <code>{r.output}</code></p>
                  : <p style={{ whiteSpace: "pre-wrap" }}>{r.output}</p>}
              </div></div>
          )}

          {gate && (
            <div className="msg bot"><div className="who-av">F</div>
              <div className="body" style={{ width: "100%" }}>
                <NeedsConnection readiness={gate}
                  onRecheck={async () => { const r = await checkReady(); if (r.ready) { setGate(null); send(true); } else setGate(r); }}
                  onRunAnyway={() => send(true)} />
              </div></div>
          )}

          {error && <div className="note warn">{error}</div>}
          <div ref={bottom} />
        </div>

        <div className="composer" style={{ marginTop: 14 }}>
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && !busy && send()}
                 placeholder={`Ask ${agent.name} to do something…`} disabled={busy} />
          <button className="btn primary sm" onClick={() => send()} disabled={busy || !input.trim()}>
            {busy ? "Running…" : "Send"}
          </button>
        </div>
      </div>

      <div>
        <SectionTitle style={{ marginTop: 0 }}>This run</SectionTitle>
        <Card>
          {r && r.id !== "pending" ? (
            <>
              <dl className="kv" style={{ gridTemplateColumns: "84px 1fr", fontSize: 12.5 }}>
                <dt>Status</dt><dd><RunStatus status={r.status} /></dd>
                <dt>Started</dt><dd className="muted">{ago(r.started_at)}</dd>
                <dt>Latency</dt><dd className="mono">{r.latency_ms != null ? `${(r.latency_ms / 1000).toFixed(1)}s` : "—"}</dd>
                <dt>Steps</dt><dd className="mono">{r.transcript.length}</dd>
                <dt>Run</dt><dd className="mono faint" style={{ fontSize: 11.5 }}>{r.id.slice(0, 8)}…</dd>
              </dl>
              <hr className="sep" />
              <div className="row">
                <button className={`btn sm ${r.feedback === 1 ? "primary" : ""}`} onClick={() => rate(r.feedback === 1 ? 0 : 1)}>👍</button>
                <button className={`btn sm ${r.feedback === -1 ? "primary" : ""}`} onClick={() => rate(r.feedback === -1 ? 0 : -1)}>👎</button>
                <span className="faint" style={{ fontSize: 12 }}>feeds the score</span>
              </div>
            </>
          ) : <div className="muted" style={{ fontSize: 13 }}>No run yet.</div>}
        </Card>

        {score && (
          <>
            <SectionTitle>Score</SectionTitle>
            <Card>
              <div className="between">
                <div><div className="faint" style={{ fontSize: 11.5 }}>Does it work?</div><div style={{ fontSize: 22, fontWeight: 700 }}>{score.runs ? score.quality : "—"}</div></div>
                <div style={{ textAlign: "right" }}><div className="faint" style={{ fontSize: 11.5 }}>Is it safe?</div><div style={{ fontSize: 22, fontWeight: 700 }}>{score.grade}</div></div>
              </div>
              <hr className="sep" style={{ margin: "8px 0" }} />
              <div className="faint" style={{ fontSize: 12 }}>
                {score.can_publish
                  ? <>Publishable — <Link to={`/agents/${agent.id}?tab=settings`}>Settings → Publish</Link>.</>
                  : <>Not yet: {score.blocked_by.join("; ")}. Every point comes from a run — <Link to={`/agents/${agent.id}?tab=overview`}>see why</Link>.</>}
              </div>
            </Card>
          </>
        )}

        {readiness && (
          <>
            <SectionTitle>Connections</SectionTitle>
            <Card>
              {Object.entries(readiness.connections).map(([name, st]) => (
                <Check key={name} ok={CONN_LABEL[st][0] === "ok"}><b>{name}</b> <span className="muted">— {CONN_LABEL[st][1]}</span></Check>
              ))}
              {readiness.connections && Object.keys(readiness.connections).length === 0 && <span className="muted">needs none</span>}
            </Card>
          </>
        )}

        {r && r.status === "awaiting_approval" && (
          <Note style={{ marginTop: 16, fontSize: 12.2 }}>
            This run is parked on an interrupt. Restart the server and come back — the approval
            is still here, and answering it carries on from the same place.
          </Note>
        )}

        {runs.length > 1 && (
          <>
            <SectionTitle>Earlier</SectionTitle>
            <Card style={{ padding: 0 }}>
              {runs.slice(0, 6).map((x) => (
                <button key={x.id} className="linkish" style={{ display: "block", width: "100%", textAlign: "left", padding: "8px 12px", borderBottom: "1px solid var(--border)" }}
                        onClick={() => setCurrent(x)}>
                  <RunStatus status={x.status} /> <span className="muted" style={{ fontSize: 12 }}>{ago(x.started_at)} · {x.input.slice(0, 40)}</span>
                </button>
              ))}
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ runs */

export function RunsTab({ agent }) {
  const [runs, setRuns] = useState(null);
  useEffect(() => { get(`/v1/agents/${agent.id}/runs`).then(setRuns); }, [agent.id]);
  if (!runs) return <div className="muted">Loading…</div>;
  if (runs.length === 0) return <Note>No runs yet. Try it in the Playground.</Note>;
  return (
    <Card style={{ padding: 0, maxWidth: 920 }}>
      <table className="t">
        <thead><tr><th>When</th><th>Trigger</th><th>Status</th><th>Latency</th><th>Feedback</th><th>Result</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td className="muted">{ago(r.started_at)}</td>
              <td><span className="chip">{r.trigger}</span></td>
              <td><RunStatus status={r.status} /></td>
              <td className="mono" style={{ fontSize: 12 }}>{r.latency_ms != null ? `${(r.latency_ms / 1000).toFixed(1)}s` : "—"}</td>
              <td>{r.feedback === 1 ? "👍" : r.feedback === -1 ? "👎" : <span className="faint">—</span>}</td>
              <td className="muted" style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.status === "awaiting_approval" ? `waiting: ${r.pending?.tool}` : r.output}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
