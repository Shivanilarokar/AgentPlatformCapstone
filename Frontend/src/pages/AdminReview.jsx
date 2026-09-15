/* Admin Review - screens/review.html, made real. Platform admin only.
 *
 * Each item in the queue is a publish run PARKED on an interrupt in the
 * author's company. Approve / Request changes / Reject resume that run; the
 * decision travels back into the author's schema and, on approval, into the
 * marketplace. Nothing reaches the marketplace any other way.
 */

import { useCallback, useEffect, useState } from "react";

import { ago, get, post } from "../api";
import { Badge, Card, Check, RiskBadge, SectionTitle, TopBar } from "../ui";

export default function AdminReview() {
  const [queue, setQueue] = useState(null);
  const [selected, setSelected] = useState(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [showDecided, setShowDecided] = useState(false);

  const load = useCallback(async () => {
    const q = await get("/v1/review");
    setQueue(q);
    setSelected((cur) => q.find((x) => x.submission_id === cur?.submission_id)
      ?? q.find((x) => x.status === "pending") ?? null);
  }, []);

  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  async function decide(decision) {
    setBusy(true); setError(null);
    try {
      await post(`/v1/review/${selected.submission_id}/decide`, { decision, notes });
      setNotes("");
      await load();
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!queue) return <div className="content muted">Loading…</div>;

  const waiting = queue.filter((q) => q.status === "pending");
  const shown = showDecided ? queue : waiting;
  const s = selected;
  const checks = s?.checks?.safety_checks ?? [];

  return (
    <>
      <TopBar title="Admin Review" sub="Nothing reaches the marketplace without a person approving it."
              actions={<Badge tone="warn">{waiting.length} waiting</Badge>} />
      <div className="content">
        <div className="two">
          <div>
            <div className="between">
              <SectionTitle style={{ marginTop: 0 }}>Queue</SectionTitle>
              <button className="linkish" onClick={() => setShowDecided(!showDecided)}>
                {showDecided ? "waiting only" : "show decided"}
              </button>
            </div>
            {shown.length === 0 && <Card><span className="muted">Nothing waiting.</span></Card>}
            {shown.map((q) => (
              <div key={q.submission_id} className={`qitem ${q.submission_id === s?.submission_id ? "sel" : ""}`}
                   onClick={() => setSelected(q)} style={{ cursor: "pointer" }}>
                <div style={{ flex: 1 }}>
                  <div className="between" style={{ marginBottom: 4 }}>
                    <b style={{ fontSize: 14 }}>{q.listing.name}</b>
                    <div className="scores">
                      <span className="score">Score <b>{q.quality}</b></span>
                      <span className="score"><b>{q.grade}</b></span>
                      {q.status !== "pending" && <Badge>{q.status.replace("_", " ")}</Badge>}
                    </div>
                  </div>
                  <div className="muted" style={{ fontSize: 12.8, marginBottom: 7 }}>{q.listing.description}</div>
                  <div className="row wrap" style={{ gap: 6 }}>
                    {q.listing.requires_connections.map((r) => <span className="chip" key={r}>{r}</span>)}
                    <span className="faint" style={{ fontSize: 11.5 }}>{q.company} · {ago(q.submitted_at)}</span>
                  </div>
                </div>
              </div>
            ))}

            {s && (
              <>
                <SectionTitle>Reviewing: {s.listing.name}</SectionTitle>
                <Card>
                  <div style={{ fontWeight: 600, marginBottom: 9 }}>Automatic checks</div>
                  {checks.map((c) => <Check key={c.key} ok={c.passed}>{c.label}{c.passed ? "" : ` — ${c.detail}`}</Check>)}

                  <hr className="sep" />
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>What it can do</div>
                  <table className="t" style={{ marginBottom: 12 }}>
                    <tbody>
                      {s.listing.tools.map((t) => (
                        <tr key={t.ref}>
                          <td className="mono">{t.ref}</td>
                          <td><RiskBadge risk={t.risk} /></td>
                          <td className="muted">{t.approval === "ask" ? "asks a human" : "runs automatically"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {s.listing.topology.type === "supervisor" && (
                    <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
                      Coordinator + {s.listing.topology.specialists.map((x) => x.name).join(", ")}.
                    </div>
                  )}

                  <hr className="sep" />
                  <div style={{ fontWeight: 600, marginBottom: 6 }}>What the reviewer has to judge</div>
                  <p className="muted" style={{ fontSize: 13, margin: "0 0 12px" }}>
                    The checks above are mechanical. A human is here for the thing a script cannot decide:
                    is this agent's description honest about what it does, and should other companies be
                    running it?
                  </p>
                  {s.status === "pending" ? (
                    <>
                      <div className="fld" style={{ marginBottom: 12 }}>
                        <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)}
                                  placeholder="Notes to the author…" style={{ width: "100%" }} />
                      </div>
                      <div className="row wrap">
                        <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>Approve &amp; publish</button>
                        <button className="btn" disabled={busy} onClick={() => decide("changes")}>Request changes</button>
                        <button className="btn" disabled={busy} onClick={() => decide("reject")}>Reject</button>
                      </div>
                    </>
                  ) : <Badge>{s.status.replace("_", " ")}</Badge>}
                </Card>
              </>
            )}
          </div>

          <div>
            <SectionTitle style={{ marginTop: 0 }}>Why this screen is hard</SectionTitle>
            <Card>
              <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
                When the author hit Publish, a run started and then <b>stopped</b> here, waiting for you.
              </p>
              {s && (
                <dl className="kv" style={{ gridTemplateColumns: "88px 1fr", fontSize: 12.5 }}>
                  <dt>Waiting</dt><dd>{s.waiting_hours < 1 ? `${Math.round(s.waiting_hours * 60)} min` : `${s.waiting_hours} h`}</dd>
                  <dt>State</dt><dd><Badge tone={s.status === "pending" ? "warn" : ""} dot>{s.status === "pending" ? "paused" : s.status.replace("_", " ")}</Badge></dd>
                  <dt>From</dt><dd>{s.company}</dd>
                  <dt>Thread</dt><dd className="mono faint" style={{ fontSize: 11.5 }}>pub-{s.submission_id.slice(0, 8)}…</dd>
                </dl>
              )}
              <hr className="sep" />
              <div className="faint" style={{ fontSize: 12.2 }}>
                That run survives deploys and weekends in the author's company checkpoint tables.
                Answering here picks it up from exactly where it stopped — approving publishes,
                requesting changes sends it back to the author with your notes.
              </div>
            </Card>

            <SectionTitle>Decisions</SectionTitle>
            <Card>
              <Check ok mark="✓"><b>Approve</b> — goes live in the marketplace</Check>
              <Check ok mark="↩"><b>Request changes</b> — back to the author, still theirs</Check>
              <Check ok={false}><b>Reject</b> — closed, author is told why</Check>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
