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

  function refreshBadges() {
    api<PullRequest[]>("/api/pull-requests")
      .then((prs) => setPendingChanges(prs.length))
      .catch(() => {});
  }

  // Poll for pending changes, and refresh on navigation (so acting on one
  // clears its badge promptly).
  useEffect(() => {
    refreshBadges();
    const id = setInterval(refreshBadges, 20000);
    return () => clearInterval(id);
  }, []);
  useEffect(refreshBadges, [location.pathname]);

  const badge: Record<string, number> = { "/changes": pendingChanges };

  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand">Agent Platform</div>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end}
                   className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            <span>{l.label}</span>
            {badge[l.to] > 0 && (
              <span className="nav-badge">{badge[l.to] >= 10 ? "!" : badge[l.to]}</span>
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
