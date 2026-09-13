/* Marketplace - screens/marketplace.html, made real.
 *
 * The only place data crosses between companies, which is why a human guards
 * it. Everything here is a sanitized design: what an agent does and what kinds
 * of access it needs. "Add to my workspace" copies that design into MY schema
 * as MY agent; I supply my own credentials; the publisher never sees my runs.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ago, get, post } from "../api";
import { Badge, Card, Check, Empty, Loading, RiskBadge, SectionTitle, TopBar } from "../ui";

function ListingCard({ l }) {
  return (
    <Link className="agent-card" to={`/marketplace/${l.id}`}>
      <div className="head">
        <div className="name">{l.name}</div>
        <div className="scores"><span className="score">{l.quality}</span><span className="score">{l.grade}</span></div>
      </div>
      <div className="desc">{l.description}</div>
      <div className="tools">
        {l.requires_connections.map((r) => <span className="chip" key={r}>needs {r}</span>)}
        {l.topology === "supervisor" && <Badge tone="accent">multi-agent</Badge>}
      </div>
      <div className="foot">
        <div className="muted">by {l.publisher}</div>
        <div>{l.installs} install{l.installs === 1 ? "" : "s"} · {ago(l.published_at)}</div>
      </div>
    </Link>
  );
}

export default function Marketplace() {
  const { id } = useParams();
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!id) get("/v1/listings").then(setItems).catch((e) => setError(e.message));
  }, [id]);

  if (id) return <ListingPage id={id} />;
  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!items) return <Loading what="the marketplace" />;

  return (
    <>
      <TopBar title="Marketplace"
              sub="Agents that other workspaces published and an admin approved. Install one and it becomes yours." />
      <div className="content">
        {items.length === 0 && (
          <Empty title="Nothing published yet">
            An agent appears here once its author publishes it and the platform admin approves.
          </Empty>
        )}
        <div className="grid">{items.map((l) => <ListingCard key={l.id} l={l} />)}</div>
      </div>
    </>
  );
}

const CONN = {
  connected: ["✓", true, "you are already connected"],
  no_credential_needed: ["✓", true, "needs no credential"],
  needs_credential: ["＋", false, "you will be asked to connect"],
  not_registered: ["＋", false, "not in your registry yet — register it first"],
};

function ListingPage({ id }) {
  const [m, setM] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => { get(`/v1/listings/${id}`).then(setM).catch((e) => setError(e.message)); }, [id]);

  async function install() {
    setBusy(true); setError(null);
    try {
      const r = await post(`/v1/listings/${id}/install`);
      navigate(`/agents/${r.agent_id}?tab=connections`);
    } catch (e) { setError(e.message); setBusy(false); }
  }

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!m) return <Loading what="this listing" />;

  return (
    <>
      <TopBar title={m.name} sub={`Published by ${m.publisher} · ${m.installs} installs`}
              actions={<>
                <Link className="btn" to="/marketplace">← Marketplace</Link>
                <button className="btn primary" onClick={install} disabled={busy}>
                  {busy ? "Installing…" : "Add to my workspace"}
                </button>
              </>} />
      <div className="content">
        <div className="two">
          <div>
            <Card>
              <div style={{ fontSize: 13.5 }}>{m.description}</div>
              <hr className="sep" />
              <dl className="kv" style={{ gridTemplateColumns: "120px 1fr", fontSize: 12.8 }}>
                <dt>Shape</dt><dd>{m.topology === "supervisor"
                  ? <>coordinator + specialists <Badge tone="accent">multi-agent</Badge></> : "single agent"}</dd>
                <dt>Publisher</dt><dd>{m.publisher}</dd>
                <dt>Score</dt><dd><b>{m.quality}</b> · <b>{m.grade}</b></dd>
                <dt>Reviewed</dt><dd><Badge tone="ok" dot>approved by an admin</Badge></dd>
              </dl>
            </Card>

            <SectionTitle>What it will need from you</SectionTitle>
            <Card>
              <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
                You supply your own credentials. The publisher's are not included and never were.
              </p>
              {m.requires_connections.map((r) => {
                const [mark, ok, text] = CONN[m.connections[r]] ?? CONN.not_registered;
                return <Check key={r} ok={ok} mark={mark}><b>{r}</b> — <span className="muted">{text}</span></Check>;
              })}
              <hr className="sep" />
              <button className="btn primary" onClick={install} disabled={busy}>Add to my workspace</button>
              <p className="faint" style={{ fontSize: 12, marginBottom: 0 }}>
                You get your own copy in My Agents. Editing it changes nothing for anyone else, and the
                publisher never sees your runs.
              </p>
              {error && <div className="note warn" style={{ marginTop: 10 }}>{error}</div>}
            </Card>

            <SectionTitle>What it can do</SectionTitle>
            <Card style={{ padding: 0 }}>
              <table className="t">
                <thead><tr><th>Tool</th><th>Risk</th><th>Before it runs</th></tr></thead>
                <tbody>
                  {m.tools.map((t) => (
                    <tr key={t.ref}>
                      <td className="mono">{t.ref}</td>
                      <td><RiskBadge risk={t.risk} /></td>
                      <td>{t.approval === "ask" ? <Badge tone="warn">asks a human</Badge> : <span className="muted">runs automatically</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>

          <div>
            <SectionTitle style={{ marginTop: 0 }}>What is not here</SectionTitle>
            <Card>
              <Check ok={false}>Who built it, beyond a company name</Check>
              <Check ok={false}>Any credential or token</Check>
              <Check ok={false}>Internal URLs, hosts, paths or people</Check>
              <Check ok={false}>Their runs, threads or data</Check>
              <hr className="sep" />
              <div className="faint" style={{ fontSize: 12 }}>
                The listing is built by allowlist from the author's configuration and scrubbed; the
                author saw exactly this before submitting, and an admin approved it.
              </div>
            </Card>
            <SectionTitle>The design</SectionTitle>
            <Card style={{ padding: 0 }}>
              <pre style={{ margin: 0, padding: 14, fontSize: 11.5, overflowX: "auto", maxHeight: 420 }}>
                {JSON.stringify(m.config, null, 2)}
              </pre>
            </Card>
          </div>
        </div>
      </div>
    </>
  );
}
