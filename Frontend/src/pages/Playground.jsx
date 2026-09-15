/* Playground and Runs - the two tabs where an agent is actually exercised.
 *
 * The approval card is the centre of the screen. It is drawn from a run whose
 * status is `awaiting_approval`: a LangGraph interrupt raised inside the tool
 * wrapper, parked in this company's checkpoint tables. Nothing has been sent.
 * Reload the page, restart the server - the card is still here, because the
 * browser only ever held a run id.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ago, get, post } from "../api";
import { Badge, Card, Note, SectionTitle } from "../ui";

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
    return (
      <p className="muted" style={{ fontSize: 12.5 }}>
        <Badge>{who}</Badge> called <code>{call[1]}</code> — {call[2]}
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

/* ------------------------------------------------------------- playground */

export function PlaygroundTab({ agent }) {
  const [runs, setRuns] = useState([]);
  const [current, setCurrent] = useState(null); // the run shown in the chat
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const bottom = useRef(null);

  const load = useCallback(async () => {
    const rows = await get(`/v1/agents/${agent.id}/runs`);
    setRuns(rows);
    // Come back after a restart: the run that is still waiting is what you see.
    const waiting = rows.find((r) => r.status === "awaiting_approval");
    setCurrent((cur) => waiting ?? (cur ? rows.find((r) => r.id === cur.id) ?? cur : rows[0] ?? null));
  }, [agent.id]);

  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [current, busy]);

  async function send() {
    if (!input.trim()) return;
    setBusy(true); setError(null);
    const text = input; setInput("");
    setCurrent({ id: "pending", input: text, status: "running", transcript: [], pending: null, output: "" });
    try {
      const r = await post(`/v1/agents/${agent.id}/invoke`, { input: text });
      setCurrent(r);
      setRuns((rs) => [r, ...rs]);
    } catch (e) { setError(e.message); setCurrent(null); }
    setBusy(false);
  }

  async function decide(decision) {
    setBusy(true); setError(null);
    try {
      const r = await post(`/v1/agents/${agent.id}/runs/${current.id}/resume`, { decision });
      setCurrent(r);
      setRuns((rs) => rs.map((x) => (x.id === r.id ? r : x)));
    } catch (e) { setError(e.message); }
    setBusy(false);
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
              <div className="body muted">Working… (real model, real tools — this takes a moment)</div></div>
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

          {error && <div className="note warn">{error}</div>}
          <div ref={bottom} />
        </div>

        <div className="composer" style={{ marginTop: 14 }}>
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && !busy && send()}
                 placeholder={`Ask ${agent.name} to do something…`} disabled={busy} />
          <button className="btn primary sm" onClick={send} disabled={busy || !input.trim()}>
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
