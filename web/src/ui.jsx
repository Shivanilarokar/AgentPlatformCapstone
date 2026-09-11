/* The design system, as components.
 *
 * Every class name here comes from style.css, which is the stylesheet the
 * published mockups use. Building these once means the ten screens still to
 * come are assembled rather than hand-written.
 */

const RISK_CLASS = { read: "", write: "warn", destructive: "danger" };

const STATUS = {
  live:           { cls: "ok",     label: "live" },
  draft:          { cls: "",       label: "draft" },
  pending_review: { cls: "warn",   label: "in review" },
  published:      { cls: "accent", label: "published" },
  degraded:       { cls: "danger", label: "degraded" },
};

export function Badge({ tone = "", dot = false, children }) {
  return (
    <span className={`badge ${tone}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export const RiskBadge = ({ risk }) => <Badge tone={RISK_CLASS[risk] ?? ""}>{risk}</Badge>;

export function StatusBadge({ status }) {
  const s = STATUS[status] ?? STATUS.draft;
  return <Badge tone={s.cls} dot>{s.label}</Badge>;
}

export const Chip = ({ children }) => <span className="chip">{children}</span>;

export function Card({ children, style, className = "" }) {
  return <div className={`card ${className}`} style={style}>{children}</div>;
}

export function TopBar({ title, sub, actions }) {
  return (
    <div className="topbar">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div className="row">{actions}</div>
    </div>
  );
}

export const SectionTitle = ({ children, style }) => (
  <div className="section-title" style={style}>{children}</div>
);

export function Note({ tone = "", children, style }) {
  return <div className={`note ${tone}`} style={style}>{children}</div>;
}

export function Check({ ok, mark, children }) {
  return (
    <div className={`check ${ok ? "pass" : "fail"}`}>
      <span className="m">{mark ?? (ok ? "✓" : "✕")}</span>
      <span>{children}</span>
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <div className="fld">
      <label>
        {label} {hint && <span className="faint">{hint}</span>}
      </label>
      {children}
    </div>
  );
}

export function Button({ variant = "", size = "", disabled, children, ...rest }) {
  return (
    <button className={`btn ${variant} ${size} ${disabled ? "disabled" : ""}`} disabled={disabled} {...rest}>
      {children}
    </button>
  );
}

/** What a screen shows while its first fetch is in flight. */
export const Loading = ({ what = "…" }) => (
  <div className="content"><div className="muted">Loading {what}</div></div>
);

/** A screen with nothing in it yet - and a nudge towards the thing to do next. */
export function Empty({ title, children }) {
  return (
    <Card style={{ textAlign: "center", padding: "46px 20px" }}>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{title}</div>
      <div className="muted" style={{ marginTop: 6 }}>{children}</div>
    </Card>
  );
}
