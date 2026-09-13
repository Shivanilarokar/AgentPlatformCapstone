/* Marketplace - agents other companies published and the platform admin approved.
 * Everything here is the sanitized design: what it does and what access it needs.
 */

import { useEffect, useState } from "react";

import { ago, get } from "../api";
import { Badge, Card, Empty, RiskBadge, TopBar } from "../ui";

export default function Marketplace() {
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => { get("/v1/listings").then(setItems).catch((e) => setError(e.message)); }, []);

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!items) return <div className="content muted">Loading…</div>;

  return (
    <>
      <TopBar title="Marketplace" sub="Agent designs other companies published. Install one and it runs with your credentials, not theirs." />
      <div className="content">
        {items.length === 0 && <Empty title="Nothing published yet">An agent appears here once its author publishes it and the platform admin approves.</Empty>}
        <div className="grid">
          {items.map((l) => (
            <Card key={l.id} className="srv">
              <div className="between">
                <b style={{ fontSize: 14.5 }}>{l.name}</b>
                <div className="scores">
                  <span className="score">{l.quality}</span><span className="score">{l.grade}</span>
                </div>
              </div>
              <div className="muted" style={{ fontSize: 12.8 }}>{l.description}</div>
              <div className="row wrap" style={{ gap: 6 }}>
                {l.requires_connections.map((r) => <span className="chip" key={r}>needs {r}</span>)}
                {l.topology === "supervisor" && <Badge tone="accent">multi-agent</Badge>}
              </div>
              <div>
                {l.tools.slice(0, 4).map((t) => (
                  <div className="toolrow" key={t.ref}><span className="mono">{t.ref}</span><RiskBadge risk={t.risk} /></div>
                ))}
              </div>
              <div className="between">
                <span className="faint" style={{ fontSize: 11.5 }}>by {l.publisher} · {ago(l.published_at)} · {l.installs} installs</span>
                <button className="btn primary sm" disabled title="Install arrives with step 6">Add to my workspace</button>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </>
  );
}
