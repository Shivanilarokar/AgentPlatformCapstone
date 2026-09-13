/* My Agents.
 *
 * Every card here is a row in t_<tenant>.agents, and every one of those rows was
 * written by the builder. Nothing on this screen reads a file.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ago, get } from "../api";
import { Badge, Card, Empty, Loading, TopBar } from "../ui";

function AgentCard({ a }) {
  return (
    <Link className="agent-card" to={`/agents/${a.id}`}>
      <div className="head">
        <div className="name">{a.name}</div>
        <Badge tone={a.status === "live" ? "ok" : ""} dot>{a.status}</Badge>
      </div>
      <div className="desc">{a.description}</div>
      <div className="tools">
        {a.servers.map((s) => <span className="chip" key={s}>{s}</span>)}
        {a.topology === "supervisor" && <Badge tone="accent">multi-agent</Badge>}
        {a.installed_from && <Badge>installed</Badge>}
      </div>
      <div className="foot">
        <div className="scores">
          <span className="score" title="Does it work? 0-100">{a.quality_score ?? "—"}</span>
          <span className="score" title="Is it safe? A-D">{a.safety_grade ?? "—"}</span>
          <span className="score">{a.runs} run{a.runs === 1 ? "" : "s"}</span>
        </div>
        <div>{a.last_run_at ? `last ${ago(a.last_run_at)}` : a.guarded.length ? `${a.guarded.length} need approval` : "read-only"}</div>
      </div>
    </Link>
  );
}

export default function MyAgents() {
  const [agents, setAgents] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    get("/v1/agents").then(setAgents).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!agents) return <Loading what="your agents" />;

  return (
    <>
      <TopBar
        title="My Agents"
        sub="Only you can see these. Nothing here is visible to another workspace."
        actions={<Link className="btn primary" to="/build">✦ Build an agent</Link>}
      />
      <div className="content">
        {agents.length === 0 ? (
          <Empty title="No agents yet">
            Go to <Link to="/build">Build</Link> and describe one in a sentence.
          </Empty>
        ) : (
          <>
            <div className="row wrap" style={{ gap: 8, marginBottom: 18 }}>
              <Badge tone="accent">{agents.length} agents</Badge>
              <Badge>{agents.filter((a) => a.guarded.length).length} with approvals</Badge>
            </div>
            <div className="grid">
              {agents.map((a) => <AgentCard a={a} key={a.id} />)}
            </div>
          </>
        )}
      </div>
    </>
  );
}
