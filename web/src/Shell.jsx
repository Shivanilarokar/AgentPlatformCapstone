/* The signed-in frame: sidebar, nav, who you are.
 *
 * Also the auth boundary. It asks /auth/me once; if that 401s you are sent to
 * sign-in, so no screen inside ever has to think about it.
 */

import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Outlet, useNavigate } from "react-router-dom";

import { get, initials, post } from "./api";

const WORKSPACE = [
  { to: "/build", icon: "✦", label: "Build" },
  { to: "/agents", icon: "◧", label: "My Agents" },
  { to: "/registry", icon: "⛁", label: "MCP Registry" },
  { to: "/connections", icon: "⚿", label: "Connections" },
];

const SHARED = [
  { to: "/marketplace", icon: "⬡", label: "Marketplace" },
  { to: "/review", icon: "☑", label: "Admin Review", admin: true },
];

export default function Shell() {
  const [me, setMe] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | anon
  const navigate = useNavigate();

  useEffect(() => {
    get("/auth/me")
      .then((user) => { setMe(user); setState("ready"); })
      .catch(() => setState("anon"));
  }, []);

  if (state === "loading") return <div className="app" />;
  if (state === "anon") return <Navigate to="/signin" replace />;

  const item = (n) => (
    <NavLink
      key={n.to}
      to={n.to}
      className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}
    >
      <span className="ic">{n.icon}</span>
      {n.label}
    </NavLink>
  );

  const signOut = async (e) => {
    e.preventDefault();
    await post("/auth/logout");
    navigate("/signin", { replace: true });
  };

  return (
    <div className="app">
      <nav className="sidebar">
        <Link className="brand" to="/registry">
          <span className="mark">F</span>Forge
        </Link>

        <div className="nav-label">Workspace</div>
        {WORKSPACE.map(item)}

        <div className="nav-label">Shared</div>
        {SHARED.filter((n) => !n.admin || me.role === "admin").map(item)}

        <div className="sidebar-foot">
          <div className="who">
            <div className="avatar">{initials(me.name)}</div>
            <div>
              <div style={{ fontWeight: 550 }}>{me.name}</div>
              <div className="org">
                {me.company}
                {me.role === "admin" ? " · admin" : " · member"}
              </div>
            </div>
          </div>
          {me.invite_code && (
            <div className="faint" style={{ fontSize: 11.5, padding: "4px 12px 6px" }}
                 title="Give this to a colleague: they sign up with your company name and this code, and join as a member.">
              invite code <span className="mono">{me.invite_code}</span>
            </div>
          )}
          <a className="nav-item" href="#signout" onClick={signOut}>
            <span className="ic">↩</span>Sign out
          </a>
        </div>
      </nav>

      <div className="main">
        <Outlet context={me} />
      </div>
    </div>
  );
}
