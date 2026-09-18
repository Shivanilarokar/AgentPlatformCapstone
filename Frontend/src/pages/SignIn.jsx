/* "Email and password is enough. What matters is what it keeps separate."
 *
 * Creating a workspace is the moment a company gets its own Postgres schema, so
 * the right-hand panel says so - it is the one idea a visitor should leave with.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, post } from "../api";
import { Badge } from "../ui";

const MESSAGES = {
  bad_credentials: "That email and password do not match.",
  email_taken: "That email already has an account. Sign in instead.",
  bad_company_name: "Use letters and numbers for the company name.",
  no_such_company: "No company with that name. Check the spelling with your admin, or create it.",
  company_exists: "That company already exists. Choose Join to become one of its users.",
};

export default function SignIn() {
  const [mode, setMode] = useState("signin");
  const [form, setForm] = useState({ email: "", password: "", name: "", company: "" });
  const [intent, setIntent] = useState("create"); // create a company (admin) | join one (user)
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const signup = mode === "signup";
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (signup) {
        await post("/auth/register", {
          email: form.email.trim(),
          password: form.password,
          name: form.name.trim() || form.email.split("@")[0],
          company: form.company.trim(),
          intent,
        });
      } else {
        await post("/auth/login", { email: form.email.trim(), password: form.password });
      }
      navigate("/registry", { replace: true });
    } catch (err) {
      setError(
        (err instanceof ApiError && MESSAGES[err.code]) || err.message || "Something went wrong.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="split">
      <div className="left">
        <div className="box">
          <div className="row" style={{ gap: 9, marginBottom: 22 }}>
            <span className="mark signin-mark">F</span>
            <span style={{ fontWeight: 650, fontSize: 17, letterSpacing: "-.01em" }}>Forge</span>
          </div>

          <div className="tabs-mini">
            <button className={!signup ? "on" : ""} onClick={() => setMode("signin")} type="button">
              Sign in
            </button>
            <button className={signup ? "on" : ""} onClick={() => setMode("signup")} type="button">
              Sign up
            </button>
          </div>

          <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 640 }}>
            {signup ? "Sign up" : "Sign in"}
          </h2>
          <p className="muted" style={{ margin: "0 0 18px", fontSize: 13 }}>
            {signup
              ? "A company is a workspace: its own Postgres schema. The first person creates it and is its admin; everyone after joins it."
              : "Build, test and publish agents."}
          </p>

          {signup && (
            <div className="row" style={{ gap: 6, marginBottom: 14 }}>
              <button type="button" className={`btn sm ${intent === "create" ? "primary" : ""}`}
                      onClick={() => setIntent("create")} style={{ flex: 1, justifyContent: "center" }}>
                Create a new company
              </button>
              <button type="button" className={`btn sm ${intent === "join" ? "primary" : ""}`}
                      onClick={() => setIntent("join")} style={{ flex: 1, justifyContent: "center" }}>
                Join an existing one
              </button>
            </div>
          )}

          <form onSubmit={submit}>
            {signup && (
              <>
                <div className="fld">
                  <label htmlFor="company">{intent === "create" ? "Company name (new)" : "Company name (exactly as your admin created it)"}</label>
                  <input id="company" value={form.company} onChange={set("company")}
                         placeholder="Northwind Labs" autoComplete="organization" required />
                  <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>
                    {intent === "create"
                      ? "You become this company's admin."
                      : "You become one of its users - your own agents and credentials, nobody else's."}
                  </div>
                </div>
                <div className="fld">
                  <label htmlFor="name">Your name</label>
                  <input id="name" value={form.name} onChange={set("name")}
                         placeholder="Priya Raman" autoComplete="name" />
                </div>
              </>
            )}

            <div className="fld">
              <label htmlFor="email">Email</label>
              <input id="email" type="email" value={form.email} onChange={set("email")}
                     placeholder="priya@northwind.example" autoComplete="email" required />
            </div>

            <div className="fld">
              <label htmlFor="password">Password</label>
              <input id="password" type="password" value={form.password} onChange={set("password")}
                     placeholder="at least 8 characters" minLength={8}
                     autoComplete={signup ? "new-password" : "current-password"} required />
            </div>

            {error && <div className="note warn" style={{ margin: "10px 0" }}>{error}</div>}

            <button className="btn primary" type="submit" disabled={busy}
                    style={{ width: "100%", justifyContent: "center", marginTop: 6 }}>
              {busy ? "Working…" : !signup ? "Sign in" : intent === "create" ? "Create company" : "Join company"}
            </button>
          </form>

          <p className="faint" style={{ fontSize: 12, marginTop: 16, textAlign: "center" }}>
            Email and password. That is all this needs to be.
          </p>
        </div>
      </div>

      <div className="right">
        <div className="wall">
          <div className="section-title" style={{ marginTop: 0 }}>What signing in decides</div>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Two things travel with you for the rest of the session: which company you belong to,
            and whether you are an admin. Everything else follows from those.
          </p>

          <div className="stack" style={{ marginTop: 18 }}>
            <div className="lane">
              <div className="row between" style={{ marginBottom: 6 }}>
                <b style={{ fontSize: 13 }}>Your workspace</b>
                <Badge>you</Badge>
              </div>
              <div className="muted" style={{ fontSize: 12.5 }}>
                Creating one gives your company its own Postgres schema. Your agents, connections
                and runs are physically stored apart from everyone else's.
              </div>
            </div>

            <div className="lane" style={{ opacity: 0.5 }}>
              <div className="row between" style={{ marginBottom: 6 }}>
                <b style={{ fontSize: 13 }}>Every other company</b>
                <Badge tone="danger">not visible to you</Badge>
              </div>
              <div className="row wrap" style={{ gap: 5 }}>
                <span className="chip">••••••••</span>
                <span className="chip">••••••••</span>
                <span className="chip">••••••••</span>
              </div>
            </div>

            <div className="lane" style={{ borderColor: "var(--accent)" }}>
              <div className="row between" style={{ marginBottom: 6 }}>
                <b style={{ fontSize: 13 }}>Marketplace</b>
                <Badge tone="accent">shared, on purpose</Badge>
              </div>
              <div className="muted" style={{ fontSize: 12.5 }}>
                The one deliberate exception, which is exactly why an admin guards it.
              </div>
            </div>
          </div>

          <div className="req" style={{ marginTop: 20 }}>
            <div className="rh">The whole security requirement</div>
            Isolation is not a filter somebody remembers to write. Each company is a separate
            Postgres schema, chosen per request.
            <div style={{ marginTop: 8, color: "var(--req)", fontWeight: 600, fontSize: 12.2 }}>
              Sign up twice and compare the two registries.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
