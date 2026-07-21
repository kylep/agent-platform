import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, type PullRequest } from "./api";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/reporting", label: "Reporting" },
  { to: "/agents", label: "Agents" },
  { to: "/skills", label: "Skills" },
  { to: "/runs", label: "Runs" },
  { to: "/conversations", label: "Conversations" },
  { to: "/memories", label: "Memories" },
  { to: "/schedules", label: "Schedules" },
  { to: "/changes", label: "Changes" },
  { to: "/dlq", label: "DLQ" },
  { to: "/secrets", label: "Secrets" },
  { to: "/settings", label: "Settings" },
];

export default function Layout() {
  const [pendingChanges, setPendingChanges] = useState(0);
  const location = useLocation();

  function refreshChanges() {
    api<PullRequest[]>("/api/pull-requests")
      .then((prs) => setPendingChanges(prs.length))
      .catch(() => {});
  }

  // Poll for pending changes, and refresh on navigation (so merging one on the
  // Changes page clears the badge promptly).
  useEffect(() => {
    refreshChanges();
    const id = setInterval(refreshChanges, 20000);
    return () => clearInterval(id);
  }, []);
  useEffect(refreshChanges, [location.pathname]);

  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand">Agent Platform</div>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end}
                   className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            <span>{l.label}</span>
            {l.to === "/changes" && pendingChanges > 0 && (
              <span className="nav-badge">{pendingChanges >= 10 ? "!" : pendingChanges}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
