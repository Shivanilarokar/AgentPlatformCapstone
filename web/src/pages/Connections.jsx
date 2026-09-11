/* Connections.
 *
 * "Credentials go in once and vanish."
 *
 * The Secret column shows dots because that is genuinely all the API will ever
 * return. There is no endpoint that hands a secret back - not to this screen,
 * not to an agent, not to a log.
 */

import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ago, del, get, post } from "../api";
import { Badge, Card, Check, Field, Loading, SectionTitle, TopBar } from "../ui";

function statusBadge(status) {
  if (status === "active") return <Badge tone="ok" dot>active</Badge>;
  if (status === "revoked") return <Badge tone="danger" dot>revoked</Badge>;
  return <Badge tone="warn" dot>{status}</Badge>;
}

function AddForm({ catalogue, connections, onAdded, onCancel, initialSlug }) {
  const [name, setSlug] = useState(initialSlug ?? "");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const needsAuth = catalogue.filter((c) => c.auth_type !== "none");
  const chosen = catalogue.find((c) => c.name === name);
  const existing = connections.find((c) => c.server_name === name && c.status === "active");

  useEffect(() => {
    if (!name && needsAuth.length) setSlug(needsAuth[0].name);
  }, [catalogue]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await post("/v1/connections", { server_name: name, secret });
      setSecret(""); // gone from this component too
      await onAdded();
      onCancel();
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }

  return (
    <div className="modal" style={{ marginBottom: 22 }}>
      <div className="mh">
        <div style={{ fontWeight: 650, fontSize: 14.5 }}>Add a connection</div>
        <div className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
          Encrypted the moment you save it. You will not see it again.
        </div>
      </div>
      <div className="mb">
        <Field label="Server">
          <select value={name} onChange={(e) => setSlug(e.target.value)}>
            {catalogue.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
                {c.auth_type === "none" ? " — needs no credential" : ""}
              </option>
            ))}
          </select>
        </Field>

        {catalogue.length === 0 && (
          <div className="note warn" style={{ marginBottom: 12 }}>
            No servers registered yet. A credential belongs to a server, so{" "}
            <Link to="/registry">register one</Link> first.
          </div>
        )}

        {chosen?.auth_type === "none" && (
          <div className="note" style={{ marginBottom: 12 }}>
            <b>{chosen.name}</b> needs no credential — its tools are usable already.
          </div>
        )}

        {existing && (
          <div className="note warn" style={{ marginBottom: 12 }}>
            You already have a credential for <b>{name}</b>. Saving replaces it.
          </div>
        )}

        <Field label="Credential" hint="(stored encrypted, never shown again)">
          <input type="password" className="mono" value={secret} autoComplete="off"
                 onChange={(e) => setSecret(e.target.value)}
                 placeholder="xoxb-…" />
        </Field>

        {error && <div className="note warn" style={{ marginBottom: 12 }}>{error}</div>}

        <div className="row">
          <button className="btn primary sm" onClick={save} disabled={busy || !secret || !name}>
            {busy ? "Encrypting…" : "Save connection"}
          </button>
          <button className="btn sm" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

export default function Connections() {
  const [connections, setConnections] = useState(null);
  const [catalogue, setCatalogue] = useState([]);
  const [error, setError] = useState(null);

  // Arriving from the registry's Connect button: /connections?add=github
  // opens the form already pointed at that server.
  const [params, setParams] = useSearchParams();
  const requested = params.get("add");
  const [adding, setAdding] = useState(Boolean(requested));

  const load = useCallback(async () => {
    try {
      const [c, cat] = await Promise.all([get("/v1/connections"), get("/v1/servers")]);
      setConnections(c);
      setCatalogue(cat);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function closeForm() {
    setAdding(false);
    if (requested) setParams({}, { replace: true });  // drop ?add= from the URL
  }

  async function revoke(name) {
    await del(`/v1/connections/${name}`);
    await load();
  }

  if (error) return <div className="content"><div className="note warn">{error}</div></div>;
  if (!connections) return <Loading what="your connections" />;

  return (
    <>
      <TopBar
        title="Connections"
        sub="Credentials you have given the platform. Added once, reused by every agent you build."
        actions={
          !adding && (
            <button className="btn primary" onClick={() => setAdding(true)}>
              + Add a connection
            </button>
          )
        }
      />

      <div className="content narrow">
        {adding && (
          <AddForm catalogue={catalogue} connections={connections}
                   initialSlug={requested ?? undefined}
                   onAdded={load} onCancel={closeForm} />
        )}

        <Card style={{ padding: 0, marginBottom: 22 }}>
          <table className="t">
            <thead>
              <tr>
                <th>Server</th><th>Status</th><th>Secret</th>
                <th>Added</th><th>Last used</th><th />
              </tr>
            </thead>
            <tbody>
              {connections.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted" style={{ padding: "28px 14px", textAlign: "center" }}>
                    No credentials yet. Add one and every agent you build can use it.
                  </td>
                </tr>
              )}
              {connections.map((c) => (
                <tr key={c.server_name}>
                  <td><b>{c.server_name}</b></td>
                  <td>{statusBadge(c.status)}</td>
                  <td className="mono faint">{c.secret}</td>
                  <td className="muted">{ago(c.created_at)}</td>
                  <td className="muted">{ago(c.last_used_at)}</td>
                  <td>
                    {c.status === "active" && (
                      <button className="btn sm" onClick={() => revoke(c.server_name)}>Revoke</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <SectionTitle>Three things that must be true</SectionTitle>
        <Card>
          <Check ok>
            <b>Encrypted at rest.</b> Each secret has its own data key; that key is
            encrypted under a master key held outside the database. Nobody reading the
            database gets a usable token.
          </Check>
          <Check ok>
            <b>Never in an agent's configuration.</b> An agent's configuration says
            <span className="mono"> requires_connection: "{catalogue[0]?.name ?? "slack"}"</span> —
            a kind of connection, never which one, never what it contains.
          </Check>
          <Check ok>
            <b>Never in a prompt, log or saved conversation.</b> The token is decrypted
            inside one function, used for one tool call, and dropped in the same function.
          </Check>
          <hr className="sep" />
          <div className="faint" style={{ fontSize: 12 }}>
            Ciphertext is bound to this workspace and this server. Copy a row into
            another company's schema and it will not decrypt.
          </div>
        </Card>

        <SectionTitle>Revoking</SectionTitle>
        <Card>
          <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
            Revoking wipes the ciphertext but keeps the row. An agent that needed it goes{" "}
            <Badge tone="danger" dot>degraded</Badge> — it still answers, still uses its other
            tools, and reports the one that is broken. It must not crash.
          </p>
          <Link className="btn sm" to="/registry">Back to the registry →</Link>
        </Card>
      </div>
    </>
  );
}
