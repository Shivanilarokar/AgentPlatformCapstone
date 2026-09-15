/* Settings tab - Publish to the Marketplace.
 *
 * Rule 6 made visible: the button reads the score's `blocked_by`. Below the
 * threshold it is disabled and says which check failed. Above it, publishing
 * shows the author exactly what will leave (the sanitized listing) and starts a
 * run that parks in Admin Review.
 */

import { useCallback, useEffect, useState } from "react";

import { ago, get, post } from "../api";
import { Badge, Card, Check, Note, SectionTitle } from "../ui";

const SUB_TONE = { pending: "warn", approved: "ok", changes_requested: "", rejected: "danger" };
const SUB_LABEL = { pending: "waiting for review", approved: "published", changes_requested: "changes requested", rejected: "rejected" };

export function SettingsTab({ agent, onChanged }) {
  const [preview, setPreview] = useState(null);
  const [subs, setSubs] = useState([]);
  const [showListing, setShowListing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    const [p, s] = await Promise.all([
      get(`/v1/agents/${agent.id}/publish/preview`),
      get(`/v1/agents/${agent.id}/submissions`),
    ]);
    setPreview(p); setSubs(s);
  }, [agent.id]);

  useEffect(() => { load().catch((e) => setError(e.message)); }, [load]);

  async function publish() {
    setBusy(true); setError(null);
    try {
      await post(`/v1/agents/${agent.id}/publish`);
      await load();
      onChanged?.();
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  if (!preview) return <div className="muted">Loading…</div>;
  const pending = subs.find((s) => s.status === "pending");
  const latest = subs[0];
  const qualityOk = preview.quality >= preview.min_quality;
  const gradeOk = preview.grade <= preview.min_grade;

  return (
    <div style={{ maxWidth: 760 }}>
      <SectionTitle style={{ marginTop: 0 }}>Sharing</SectionTitle>
      <Card>
        <div style={{ fontWeight: 600 }}>Private to you</div>
        <div className="muted" style={{ fontSize: 12.5 }}>
          Nobody else in your company can see this agent, and no other company can reach it at all.
          {agent.status === "live" && " A sanitized copy of its design is in the Marketplace."}
        </div>
      </Card>

      <SectionTitle>Publish to the Marketplace</SectionTitle>
      <Card>
        <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
          Publishing sends the agent's design — what it does and what kinds of access it needs — to
          the platform admin for review. Your credentials and anything internal to your company are
          stripped first.
        </p>
        <Check ok={qualityOk}>Score {preview.quality} <span className="faint">(needs {preview.min_quality})</span></Check>
        <Check ok={gradeOk}>Safety {preview.grade} <span className="faint">(needs {preview.min_grade})</span></Check>
        {preview.blocked_by.map((b) => <Check key={b} ok={false}><span className="muted">{b}</span></Check>)}
        <hr className="sep" />

        {pending ? (
          <>
            <Badge tone="warn" dot>waiting for review</Badge>
            <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
              Submitted {ago(pending.submitted_at)}. A run is parked in Admin Review until the platform
              admin answers — it survives restarts and can wait days.
            </p>
          </>
        ) : (
          <>
            <div className="row">
              <button className="btn primary" disabled={!preview.can_publish || busy} onClick={publish}
                      title={preview.can_publish ? "" : preview.blocked_by.join("; ")}>
                {busy ? "Submitting…" : "Publish to Marketplace"}
              </button>
              <button className="linkish" onClick={() => setShowListing(!showListing)}>
                {showListing ? "hide" : "show"} exactly what will leave
              </button>
            </div>
            <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
              {preview.can_publish
                ? "The platform admin reviews it before anyone else can see it."
                : "To unlock: run it in the Playground, approve what it asks, and rate the run 👍. A score that blocks nothing is decoration."}
            </p>
          </>
        )}
        {error && <div className="note warn" style={{ marginTop: 10 }}>{error}</div>}
      </Card>

      {showListing && (
        <>
          <SectionTitle>What the marketplace will see</SectionTitle>
          <Card style={{ padding: 0 }}>
            <pre style={{ margin: 0, padding: 14, fontSize: 11.5, overflowX: "auto" }}>
              {JSON.stringify(preview.listing, null, 2)}
            </pre>
          </Card>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 9 }}>
            Built by allowlist: only the fields shown here have a path out. Emails, links, internal
            hosts, paths and your company's name are replaced in the free text.
          </p>
        </>
      )}

      {subs.length > 0 && (
        <>
          <SectionTitle>Submissions</SectionTitle>
          <Card style={{ padding: 0 }}>
            <table className="t">
              <thead><tr><th>When</th><th>Status</th><th>Score</th><th>Admin's notes</th></tr></thead>
              <tbody>
                {subs.map((s) => (
                  <tr key={s.id}>
                    <td className="muted">{ago(s.submitted_at)}</td>
                    <td><Badge tone={SUB_TONE[s.status]} dot>{SUB_LABEL[s.status]}</Badge></td>
                    <td className="mono">{s.quality} · {s.grade}</td>
                    <td className="muted">{s.notes || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          {latest?.status === "changes_requested" && (
            <Note tone="warn" style={{ marginTop: 12 }}>
              <b>Sent back.</b> {latest.notes} — fix it, then publish again.
            </Note>
          )}
        </>
      )}
    </div>
  );
}
