/* Build - describe an agent, and the platform builds it.
 *
 * The two places this screen stops and waits are the centre of the project.
 * Both are LangGraph interrupts: while one is showing, nothing is running on
 * the server - the build is a row in t_<tenant>.checkpoints.
 *
 * The thread id is kept in the URL, so you can close the tab, restart the API,
 * come back tomorrow, and the same question is still on screen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { get, post } from "../api";
import { Badge, Card, RiskBadge, TopBar } from "../ui";

const STEPS = [
  ["understand", "Understand", "Work out what it should do"],
  ["select", "Pick tools", "Search the registry, ask you"],
  ["connections", "Check connections", "Stop if a credential is missing"],
  ["done", "Build and deploy", "Write the config, create the agent"],
];

function Steps({ at }) {
  const order = STEPS.map(([k]) => k);
  const now = order.indexOf(at);
  return (
    <div className="steps">
      <div className="section-title" style={{ marginTop: 0 }}>Builder graph</div>
      {STEPS.map(([key, label, sub], i) => {
        const state = i < now ? "done" : i === now ? "now" : "todo";
        return (
          <div className={`step ${state}`} key={key}>
            <div className="n">{state === "done" ? "✓" : i + 1}</div>
            <div>
              <div className="lb">{label}</div>
              <div className="sd">{sub}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------- interrupt 1 */

function SelectTools({ payload, onAnswer, busy }) {
  const [picked, setPicked] = useState(new Set(payload.suggested ?? []));

  const byServer = payload.catalogue.reduce((acc, t) => {
    (acc[t.server] ??= []).push(t);
    return acc;
  }, {});

  const toggle = (ref) => {
    const next = new Set(picked);
    next.has(ref) ? next.delete(ref) : next.add(ref);
    setPicked(next);
  };

  return (
    <div className="interrupt">
      <div className="ihead">
        <span>Paused · waiting for you</span>
        <span>interrupt: select_tools</span>
      </div>
      <div className="ibody">
        <p style={{ marginTop: 0 }}>
          I searched your registry. These tools cover it — tick the ones you want.
        </p>

        {Object.entries(byServer).map(([server, tools]) => (
          <div key={server} style={{ marginBottom: 10 }}>
            <div className="faint" style={{ fontSize: 11.5, textTransform: "uppercase",
                                            letterSpacing: ".05em", marginBottom: 4 }}>
              {server}
            </div>
            {tools.map((t) => (
              <label className={`pick ${picked.has(t.ref) ? "on" : ""}`} key={t.ref}>
                <div className="box">{picked.has(t.ref) ? "✓" : ""}</div>
                <input type="checkbox" checked={picked.has(t.ref)}
                       onChange={() => toggle(t.ref)} style={{ display: "none" }} />
                <div>
                  <div className="t">
                    <span className="mono">{t.ref}</span> <RiskBadge risk={t.risk} />
                  </div>
                  <div className="d">{t.description || "—"}</div>
                </div>
              </label>
            ))}
          </div>
        ))}

        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn primary sm" disabled={busy || picked.size === 0}
                  onClick={() => onAnswer({ selected: [...picked] })}>
            Use these {picked.size}
          </button>
          <span className="faint" style={{ fontSize: 12 }}>
            The build waits here until you answer.
          </span>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- interrupt 2 */

function MissingConnection({ payload, onAnswer, busy }) {
  return (
    <div className="interrupt">
      <div className="ihead">
        <span>Paused · missing credential</span>
        <span>interrupt: missing_connection</span>
      </div>
      <div className="ibody">
        <p style={{ marginTop: 0 }}>Checked your connections:</p>

        {payload.required.map((name) => {
          const missing = payload.missing.includes(name);
          return (
            <div className="row" style={{ gap: 10, padding: "6px 0" }} key={name}>
              {missing ? <Badge tone="danger">missing</Badge> : <Badge tone="ok" dot>ok</Badge>}
              <b>{name}</b>
              <span className="faint">
                {missing ? "no credential in this workspace" : "connected"}
              </span>
            </div>
          );
        })}

        <hr className="sep" style={{ margin: "8px 0 12px" }} />
        <p className="muted" style={{ margin: "0 0 10px" }}>
          Add one to carry on. It is encrypted when you save it and is never written into
          the agent's configuration.
        </p>

        <div className="row">
          <Link className="btn primary sm" to={`/connections?add=${payload.missing[0]}`}>
            Connect {payload.missing[0]}
          </Link>
          <button className="btn sm" disabled={busy}
                  onClick={() => onAnswer({ action: "connected" })}>
            I have connected it
          </button>
          <button className="btn sm" disabled={busy}
                  onClick={() => onAnswer({ action: "skip" })}>
            Skip — build without it
          </button>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- screen */

export default function Build() {
  const [params, setParams] = useSearchParams();
  const thread = params.get("thread");

  const [prompt, setPrompt] = useState("");
  const [state, setState] = useState(null);   // the BuildOut from the API
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const bottom = useRef(null);

  // Reload a build that is still paused - this is what makes "come back
  // tomorrow" work. The browser only ever held a thread id.
  useEffect(() => {
    if (!thread) return;
    get(`/v1/builds/${thread}`).then(setState).catch(() => setError("That build is gone."));
  }, [thread]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [state]);

  const start = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r = await post("/v1/builds", { prompt });
      setState(r);
      setParams({ thread: r.thread_id }, { replace: true });
    } catch (e) { setError(e.message); }
    setBusy(false);
  }, [prompt, setParams]);

  async function answer(body) {
    setBusy(true); setError(null);
    try {
      setState(await post(`/v1/builds/${state.thread_id}/resume`, body));
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  const kind = state?.interrupt?.type;
  const at = !state ? "understand"
    : kind === "select_tools" ? "select"
    : kind === "missing_connection" ? "connections"
    : "done";

  return (
    <>
      <TopBar title="Build an agent"
              sub="Describe what you want. The platform picks the tools and checks your connections." />

      <div className="content">
        <div className="buildwrap">
          <div>
            <div className="chat">
              {!state && (
                <div className="msg bot">
                  <div className="who-av">F</div>
                  <div className="body">
                    <p style={{ marginTop: 0 }}>
                      Tell me what you want an agent to do. I will search your registry, show you
                      the tools I think it needs, and check you are connected to each one.
                    </p>
                    <p className="muted" style={{ marginBottom: 0, fontSize: 12.5 }}>
                      For example: <em>"read my open GitHub issues every morning and post a
                      summary to Slack"</em>
                    </p>
                  </div>
                </div>
              )}

              {state && (
                <>
                  <div className="msg user"><div className="body">{prompt || "(earlier request)"}</div></div>
                  {state.log?.map((line, i) => (
                    <div className="msg bot" key={i}>
                      <div className="who-av">F</div>
                      <div className="body">{line}</div>
                    </div>
                  ))}
                </>
              )}

              {kind === "select_tools" && (
                <div className="msg bot">
                  <div className="who-av">F</div>
                  <div className="body" style={{ width: "100%" }}>
                    <SelectTools payload={state.interrupt} onAnswer={answer} busy={busy} />
                  </div>
                </div>
              )}

              {kind === "missing_connection" && (
                <div className="msg bot">
                  <div className="who-av">F</div>
                  <div className="body" style={{ width: "100%" }}>
                    <MissingConnection payload={state.interrupt} onAnswer={answer} busy={busy} />
                  </div>
                </div>
              )}

              {state?.status === "done" && (
                <div className="msg bot">
                  <div className="who-av">F</div>
                  <div className="body" style={{ width: "100%" }}>
                    <p style={{ marginTop: 0 }}>Built and saved. Here it is:</p>
                    <Card style={{ maxWidth: 520 }}>
                      <div className="between" style={{ marginBottom: 8 }}>
                        <div style={{ fontWeight: 650, fontSize: 15 }}>{state.config?.name}</div>
                        <Badge dot>draft</Badge>
                      </div>
                      <div className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
                        {state.config?.description}
                      </div>
                      <dl className="kv" style={{ gridTemplateColumns: "118px 1fr", fontSize: 12.5 }}>
                        <dt>Tools</dt>
                        <dd>{state.config?.tools.map((t) => (
                          <span className="chip" key={t.ref}>{t.ref}</span>
                        ))}</dd>
                        <dt>Approvals</dt>
                        <dd>{state.config?.tools.filter((t) => t.approval === "ask")
                              .map((t) => t.ref).join(", ") || "none needed"}</dd>
                        <dt>Needs</dt>
                        <dd>{state.config?.requires_connections.join(", ")}</dd>
                      </dl>
                      <hr className="sep" />
                      <Link className="btn primary sm" to="/agents">Go to My Agents</Link>
                    </Card>
                  </div>
                </div>
              )}

              {error && <div className="note warn">{error}</div>}
              <div ref={bottom} />
            </div>

            {!state && (
              <div className="composer">
                <input value={prompt} onChange={(e) => setPrompt(e.target.value)}
                       onKeyDown={(e) => e.key === "Enter" && prompt.length > 3 && start()}
                       placeholder="I want an agent that…" />
                <button className="btn primary sm" onClick={start} disabled={busy || prompt.length < 4}>
                  {busy ? "Thinking…" : "Send"}
                </button>
              </div>
            )}

            {state && (
              <div className="composer">
                <span className="faint" style={{ fontSize: 12 }}>
                  thread <span className="mono">{state.thread_id}</span> — bookmark this URL, the
                  build survives a restart
                </span>
                <button className="btn sm" onClick={() => { setState(null); setPrompt(""); setParams({}); }}>
                  Start over
                </button>
              </div>
            )}
          </div>

          <Steps at={at} />
        </div>
      </div>
    </>
  );
}
