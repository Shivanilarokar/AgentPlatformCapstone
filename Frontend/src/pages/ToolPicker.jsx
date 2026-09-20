/* The admin's allow-list, as a checklist.
 *
 * Used twice: after "Connect" on the register form (what the server just
 * reported), and on the Edit page (what is stored). `selected` is a Set of tool
 * names; the parent owns it. Ticking a tool means agents in this company may
 * use it - it does NOT remove the approval step: write and destructive tools
 * still ask a human on every call.
 *
 * 45 tools is too many to scan, so: grouped by risk (safest first), a search
 * box, and "read-only / all / none" shortcuts.
 *
 * A tool the API marks `selectable: false` (a destructive tool, for an ordinary
 * user) is shown but greyed out and can never be ticked, not even by "all".
 * The API refuses it too; this is so nobody is offered a box that will fail.
 */

import { useState } from "react";

import { RiskBadge } from "../ui";

const ORDER = ["read", "write", "destructive"];

export function ToolPicker({ tools, selected, onChange }) {
  const [q, setQ] = useState("");

  const needle = q.trim().toLowerCase();
  const visible = tools.filter(
    (t) => !needle || t.name.toLowerCase().includes(needle) || (t.description ?? "").toLowerCase().includes(needle),
  );
  const groups = ORDER.map((r) => [r, visible.filter((t) => t.risk === r)]).filter(([, l]) => l.length);

  const canPick = (t) => t.selectable !== false;
  const locked = tools.filter((t) => !canPick(t)).length;

  const set = (names) => onChange(new Set(names));
  const toggle = (name) => {
    const next = new Set(selected);
    next.has(name) ? next.delete(name) : next.add(name);
    onChange(next);
  };

  return (
    <div className="picker">
      <div className="ph">
        <span><b>{selected.size}</b> of {tools.length} selected</span>
        <span className="acts">
          <button type="button" className="linkish" onClick={() => set(tools.filter((t) => t.risk === "read").map((t) => t.name))}>
            read-only
          </button>
          <button type="button" className="linkish" onClick={() => set(tools.filter(canPick).map((t) => t.name))}>all</button>
          <button type="button" className="linkish" onClick={() => set([])}>none</button>
        </span>
      </div>
      {locked > 0 && (
        <div className="faint" style={{ fontSize: 12, padding: "2px 2px 6px" }}>
          {locked} destructive tool{locked === 1 ? "" : "s"} can only be enabled by an admin.
        </div>
      )}
      {tools.length > 8 && (
        <input type="search" placeholder="Filter tools…" value={q} onChange={(e) => setQ(e.target.value)} />
      )}
      <div className="list">
        {groups.length === 0 && <div className="none">No tool matches “{q}”.</div>}
        {groups.map(([risk, list]) => (
          <div key={risk}>
            <div className="grp">{risk} · {list.filter((t) => selected.has(t.name)).length}/{list.length}</div>
            {list.map((t) => (
              <label className="prow" key={t.name}
                     title={canPick(t) ? t.description : "Only an admin can enable destructive tools"}
                     style={canPick(t) ? undefined : { opacity: 0.5, cursor: "not-allowed" }}>
                <input type="checkbox" checked={canPick(t) && selected.has(t.name)} disabled={!canPick(t)}
                       onChange={() => toggle(t.name)} />
                <span className="nm mono">{t.name}</span>
                <RiskBadge risk={t.risk} />
              </label>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
