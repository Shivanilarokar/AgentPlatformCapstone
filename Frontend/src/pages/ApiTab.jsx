/* API tab - screens/agent.html?tab=api, made real.
 *
 * Every agent has a URL. The Postman button downloads a v2.1 collection with a
 * fresh API token already in it: import, open Invoke, Send, get a response.
 * A token from another company calling this URL gets 404 - never 403.
 */

import { useEffect, useState } from "react";

import { ago, del, get, post } from "../api";
import { Badge, Card, SectionTitle } from "../ui";

export function ApiTab({ agent }) {
  const [tokens, setTokens] = useState([]);
  const [fresh, setFresh] = useState(null); // {name, token} shown once
  const [busy, setBusy] = useState(false);
  // The API is served from this same origin: vite (dev) and nginx (prod) both
  // proxy /v1 to the backend, so never point at the backend's own host/port.
  const base = window.location.origin;

  const load = () => get("/v1/tokens").then(setTokens).catch(() => setTokens([]));
  useEffect(() => { load(); }, []);

  async function download() {
    setBusy(true);
    try {
      const c = await get(`/v1/agents/${agent.id}/postman`);
      // The backend only sees the proxied host; the public URL is this one.
      for (const v of c.variable || []) if (v.key === "base_url") v.value = base;
      const blob = new Blob([JSON.stringify(c, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${agent.name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.postman_collection.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      await load();
    } finally { setBusy(false); }
  }

  async function mint() {
    setBusy(true);
    try {
      const t = await post("/v1/tokens", { name: `manual: ${agent.name}` });
      setFresh(t);
      await load();
    } finally { setBusy(false); }
  }

  async function revoke(id) {
    await del(`/v1/tokens/${id}`);
    await load();
  }

  const [shell, setShell] = useState("powershell");
  const url = `${base}/v1/agents/${agent.id}/invoke`;
  const tok = fresh?.token || "<paste your forge_ token>";
  const input = "Run the morning digest for the platform repo";
  const curl = shell === "powershell"
    ? `$env:FORGE_TOKEN = "${tok}"
curl.exe -X POST "${url}" \`
  -H "Authorization: Bearer $env:FORGE_TOKEN" \`
  -H "Content-Type: application/json" \`
  -d '{\\"input\\": \\"${input}\\"}'`
    : `export FORGE_TOKEN="${tok}"
curl -X POST "${url}" \\
  -H "Authorization: Bearer $FORGE_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"input": "${input}"}'`;

  return (
    <div style={{ maxWidth: 820 }}>
      <SectionTitle style={{ marginTop: 0 }}>Endpoint</SectionTitle>
      <Card>
        <div className="between" style={{ marginBottom: 10 }}>
          <code>POST /v1/agents/{agent.id}/invoke</code>
          <button className="btn primary sm" onClick={download} disabled={busy}>⤓ Download Postman collection</button>
        </div>
        <div className="row" style={{ gap: 6, marginBottom: 6 }}>
          <button className={`btn sm${shell === "powershell" ? " primary" : ""}`} onClick={() => setShell("powershell")}>PowerShell</button>
          <button className={`btn sm${shell === "bash" ? " primary" : ""}`} onClick={() => setShell("bash")}>Bash / macOS / Linux</button>
          <button className="btn sm" onClick={() => navigator.clipboard?.writeText(curl)}>Copy</button>
        </div>
        <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{curl}</pre>
        <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>
          {fresh ? "Your new token is filled in above." : <>Click <b>Mint a token</b> below and the command fills it in, ready to paste.</>}
        </p>
        <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>
          The collection carries a token minted for you at download time. Import it, open <b>Invoke</b>,
          hit Send. If the agent reaches a write tool the response is <code>awaiting_approval</code>; answer
          it with <b>Resume</b> (or in the Playground).
        </p>
      </Card>

      <SectionTitle>Also available</SectionTitle>
      <Card style={{ padding: 0 }}>
        <table className="t">
          <tbody>
            <tr><td className="mono">POST /v1/agents/{agent.id}/stream</td><td className="muted">Same, streamed over SSE — one event per step</td></tr>
            <tr><td className="mono">POST /v1/agents/{agent.id}/runs/{"{run_id}"}/resume</td><td className="muted">Answer a pending approval: approve | reject</td></tr>
            <tr><td className="mono">GET  /v1/agents/{agent.id}/readiness</td><td className="muted">Which connections this run needs, and which are missing</td></tr>
            <tr><td className="mono">GET  /v1/agents/{agent.id}/runs</td><td className="muted">Run history</td></tr>
            <tr><td className="mono">GET  /v1/agents/{agent.id}/postman</td><td className="muted">This collection, as JSON</td></tr>
          </tbody>
        </table>
      </Card>

      <SectionTitle>Your API tokens</SectionTitle>
      <Card>
        {fresh && (
          <div className="note" style={{ marginBottom: 12 }}>
            <b>Copy it now — it is shown once.</b>
            <pre style={{ margin: "6px 0 0", fontSize: 12, userSelect: "all" }}>{fresh.token}</pre>
          </div>
        )}
        {tokens.length === 0
          ? <div className="muted" style={{ fontSize: 13 }}>No tokens yet. Downloading the collection mints one.</div>
          : (
            <table className="t">
              <thead><tr><th>Name</th><th>Token</th><th>Created</th><th>Last used</th><th></th></tr></thead>
              <tbody>
                {tokens.map((t) => (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td className="mono">{t.prefix}…</td>
                    <td className="muted">{ago(t.created_at)}</td>
                    <td className="muted">{t.last_used_at ? ago(t.last_used_at) : "never"}</td>
                    <td><button className="btn sm" onClick={() => revoke(t.id)}>Revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        <hr className="sep" />
        <div className="row">
          <button className="btn sm" onClick={mint} disabled={busy}>Mint a token</button>
          <span className="faint" style={{ fontSize: 12 }}>
            A token is yours: it runs agents as you, with your connections. Another company's token
            asking for this agent gets <Badge tone="danger">404</Badge>, not 403 — a 403 would confirm it exists.
          </span>
        </div>
      </Card>
    </div>
  );
}
