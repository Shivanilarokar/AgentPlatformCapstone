/* Edit an MCP server - the only place a registered server can be changed.
 *
 * Registering a name that is taken is refused (409); it never overwrites. So
 * this page is how you change a server's address, its credential, its
 * description, who can see it, and which of its tools agents may use.
 *
 * The name is fixed: agents refer to a server by name.
 *
 * Only what changed is sent. A description, visibility or tool-selection change needs no
 * network, so it still saves when the server is down or its token has expired;
 * changing the address (or pasting a new credential) makes the platform connect
 * and ask for the tool list again first, and nothing is saved if it does not
 * answer - the same rule as registering.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useOutletContext, useParams } from "react-router-dom";

import { get, patch } from "../api";
import { Field, Loading, TopBar } from "../ui";
import { ToolPicker } from "./ToolPicker";

export default function RegistryEdit() {
  const { name } = useParams();
  const me = useOutletContext();
  const navigate = useNavigate();
  const platformAdmin = me?.role === "platform_admin";

  const [server, setServer] = useState(undefined); // undefined = loading, null = not editable
  const [f, setF] = useState(null);
  const [picked, setPicked] = useState(new Set()); // the tools switched on
  const [needsToken, setNeedsToken] = useState(false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    get("/v1/servers")
      .then((all) => {
        const s = all.find((x) => x.name === name && x.editable);
        setServer(s ?? null);
        if (s) {
          setPicked(new Set(s.tools.filter((t) => t.enabled).map((t) => t.name)));
          setF({
            transport: s.transport,
            endpoint: s.endpoint,
            auth_type: s.auth_type,
            credential_env_var: s.credential_env_var ?? "",
            description: s.description ?? "",
            visibility: s.visibility,
            token: "",
          });
        }
      })
      .catch((e) => setError(e.message));
  }, [name]);

  if (error && server === undefined) {
    return <div className="content"><div className="note warn">{error}</div></div>;
  }
  if (server === undefined) return <Loading what="the server" />;
  if (server === null) {
    return (
      <>
        <TopBar title="Edit a server" />
        <div className="content">
          <div className="note warn">
            You can't edit <b>{name}</b>. Only an admin can, and only for a server they registered
            themselves.
          </div>
          <p><Link to="/registry">← Back to the registry</Link></p>
        </div>
      </>
    );
  }

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  // Only what differs from what is stored.
  const changes = {};
  const original = {
    transport: server.transport,
    endpoint: server.endpoint,
    auth_type: server.auth_type,
    credential_env_var: server.credential_env_var ?? "",
    description: server.description ?? "",
    visibility: server.visibility,
  };
  for (const k of Object.keys(original)) {
    if (f[k].trim() !== original[k]) changes[k] = f[k].trim();
  }
  if ("credential_env_var" in changes) changes.credential_env_var = changes.credential_env_var || null;
  if (f.token) changes.token = f.token;
  // the allow-list, sent only when it differs from what is stored
  const stored = server.tools.filter((t) => t.enabled).map((t) => t.name);
  const toolsChanged = stored.length !== picked.size || stored.some((n) => !picked.has(n));
  if (toolsChanged) changes.enabled_tools = [...picked];
  const dirty = Object.keys(changes).length > 0;
  const reconnects = ["transport", "endpoint", "auth_type", "credential_env_var", "token"]
    .some((k) => k in changes);

  const showEnv = f.transport === "stdio" && f.auth_type !== "none";
  const showToken = !platformAdmin && (needsToken || (f.auth_type !== "none" && f.transport !== "stdio"));

  async function save() {
    setError(null); setSaving(true);
    try {
      await patch(`/v1/servers/${name}`, changes);
      navigate("/registry");
    } catch (e) {
      setSaving(false);
      if (e.code === "auth_required") {
        setNeedsToken(true);
        setError(`${name} answered but will not list its tools without a credential. Paste one and save again.`);
      } else if (e.code === "credential_rejected") {
        setNeedsToken(true);
        setError(`${name} rejected that credential. ${e.message}`);
      } else {
        setError(e.message);
      }
    }
  }

  return (
    <>
      <TopBar
        title={`Edit ${name}`}
        sub="Change how this server is reached, or who can see it."
        actions={<Link className="btn" to="/registry">Cancel</Link>}
      />
      <div className="content">
        <div className="modal">
          <div className="mb">
            <Field label="Name" hint="(fixed - agents refer to it)">
              <input value={name} disabled />
            </Field>
            <Field label="Transport">
              <select value={f.transport} onChange={set("transport")}>
                <option value="http">http</option>
                <option value="sse">sse</option>
                <option value="stdio">stdio</option>
              </select>
            </Field>
            <Field label="Endpoint">
              <input className="mono" value={f.endpoint} onChange={set("endpoint")} autoComplete="off" />
            </Field>
            <Field label="Authentication">
              <select value={f.auth_type} onChange={set("auth_type")}>
                <option value="oauth">oauth</option>
                <option value="api_key">api_key</option>
                <option value="none">none</option>
              </select>
            </Field>
            {showEnv && (
              <Field label="Credential env var" hint="(what the subprocess reads)">
                <input className="mono" value={f.credential_env_var} onChange={set("credential_env_var")}
                       placeholder="GITHUB_PERSONAL_ACCESS_TOKEN" autoComplete="off" />
              </Field>
            )}
            {showToken && (
              <Field label="New credential" hint="(optional - leave empty to keep the one you saved)">
                <input type="password" className="mono" value={f.token} onChange={set("token")}
                       placeholder="paste a new one to replace it" autoComplete="new-password" />
              </Field>
            )}
            <Field label="Description">
              <input value={f.description} onChange={set("description")} autoComplete="off" />
            </Field>
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
                Tools agents may use{" "}
                <span className="faint" style={{ fontWeight: 400 }}>
                  ({server.tools.length} on this server)
                </span>
              </div>
              <ToolPicker tools={server.tools} selected={picked} onChange={setPicked} />
              <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>
                Unticked tools disappear from the builder and are refused at run time, even for agents
                already built. A tool the server adds later starts unticked. Write and destructive
                tools still ask for approval on every call.
              </div>
            </div>
            <Field label="Visible to">
              <select value={f.visibility} onChange={set("visibility")}
                      disabled={platformAdmin} style={{ marginBottom: 2 }}>
                {platformAdmin ? (
                  <option value="everyone">Everyone</option>
                ) : (
                  <>
                    <option value="private">Just me</option>
                    <option value="company">My whole company</option>
                  </>
                )}
              </select>
            </Field>

            {error && (
              <div className="note warn" style={{ marginBottom: 12 }}>
                <b>Nothing was saved.</b> {error}
              </div>
            )}
            {dirty && reconnects && !error && (
              <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>
                The platform will reconnect and re-read the tool list before saving.
              </div>
            )}

            <div className="row">
              <button className="btn primary sm" onClick={save} disabled={saving || !dirty || !f.endpoint.trim()}>
                {saving ? (reconnects ? "Connecting…" : "Saving…") : "Save changes"}
              </button>
              <Link className="btn sm" to="/registry">Cancel</Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
