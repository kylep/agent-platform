import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, logout, type AppView, type PullRequest } from "./api";
import { buildPlatformNav, SideNav, ThemeToggle, type LinkComponent } from "@ap/ui/sidenav";
import { QuotaBars } from "@ap/ui/quota";
import { useQuota } from "./components/quota/useQuota";
import { listStateApps, type StateAppSummary } from "./lib/appData";

// The console shell: the shared platform sidebar (from @ap/ui — app
// frontends render the same one) around the routed pages. Console-specific
// bits live here: react-router links, the pending-changes badge, the
// deployed-apps accordion data, and the usage snapshot the bars under the
// brand are drawn from (docs/design/22 — the shell itself fetches nothing).

const routerLink: LinkComponent = ({ to, end, className, children }) => (
  <NavLink key={to} to={to} end={end}
           className={({ isActive }) => className(isActive)}>
    {children}
  </NavLink>
);

export default function Layout() {
  const location = useLocation();
  if (location.pathname === "/relay" && new URLSearchParams(location.search).get("popout") === "1") {
    return <main className="relay-popout-main"><Outlet /></main>;
  }
  return <PlatformLayout />;
}

function PlatformLayout() {
  const [pendingChanges, setPendingChanges] = useState(0);
  const [apps, setApps] = useState<AppView[]>([]);
  const [stateApps, setStateApps] = useState<StateAppSummary[]>([]);
  const [restoreMode, setRestoreMode] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const quota = useQuota();

  async function signOut() {
    try { await logout(); } catch { /* leave either way */ }
    navigate("/login", { replace: true });
  }

  function refreshBadges() {
    api<PullRequest[]>("/api/pull-requests")
      .then((prs) => setPendingChanges(prs.length))
      .catch(() => {});
  }
  useEffect(() => {
    refreshBadges();
    api<AppView[]>("/api/apps").then(setApps).catch(() => {});
    listStateApps().then(setStateApps).catch(() => {});
    const id = setInterval(refreshBadges, 20000);
    return () => clearInterval(id);
  }, []);
  useEffect(refreshBadges, [location.pathname]);
  useEffect(() => {
    api<{ mode: string }>("/api/maintenance/status")
      .then((status) => setRestoreMode(status.mode === "restore"))
      .catch(() => {});
  }, [location.pathname]);

  const entries = useMemo(() =>
    buildPlatformNav([
      ...stateApps.filter((a) => a.status === "active" && a.approved_version !== null)
        .map((a) => ({ name: a.name, icon: a.name === "running" ? "🏃" : "🧩",
          display_name: a.name === "running" ? "Running Coach" : a.name,
          to: `/apps/state/${encodeURIComponent(a.id)}/pages/home` })),
      ...apps.filter((a) => a.ui && a.ready && !stateApps.some((s) =>
        s.name === a.name && s.status === "active" && s.approved_version !== null))
        .map((a) => ({ name: a.name, icon: a.icon, display_name: a.display_name })),
    ]), [apps, stateApps]);

  return (
    <div className="layout">
      <SideNav entries={entries} activePath={location.pathname}
               activeSearch={location.search}
               badges={{ "/changes": pendingChanges }} LinkComponent={routerLink}
               belowBrand={<QuotaBars {...quota} />}
               footer={<div className="nav-account">
                 <NavLink to="/profile" className="nav-link">Profile</NavLink>
                 <button type="button" className="nav-link nav-signout" onClick={signOut}>Sign out</button>
                 <ThemeToggle />
               </div>} />
      <main className="main">
        {restoreMode && <div role="status" className="notice">
          Automation is paused after a restore. <Link to="/settings/restore">Review restored state</Link>.
        </div>}
        <Outlet />
      </main>
    </div>
  );
}
