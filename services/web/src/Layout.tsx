import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, type AppView, type PullRequest } from "./api";
import { buildPlatformNav, SideNav, type LinkComponent } from "@ap/ui/sidenav";

// The console shell: the shared platform sidebar (from @ap/ui — app
// frontends render the same one) around the routed pages. Console-specific
// bits live here: react-router links, the pending-changes badge, and the
// deployed-apps accordion data.

const routerLink: LinkComponent = ({ to, end, className, children }) => (
  <NavLink key={to} to={to} end={end}
           className={({ isActive }) => className(isActive)}>
    {children}
  </NavLink>
);

export default function Layout() {
  const [pendingChanges, setPendingChanges] = useState(0);
  const [apps, setApps] = useState<AppView[]>([]);
  const location = useLocation();

  function refreshBadges() {
    api<PullRequest[]>("/api/pull-requests")
      .then((prs) => setPendingChanges(prs.length))
      .catch(() => {});
  }
  useEffect(() => {
    refreshBadges();
    api<AppView[]>("/api/apps").then(setApps).catch(() => {});
    const id = setInterval(refreshBadges, 20000);
    return () => clearInterval(id);
  }, []);
  useEffect(refreshBadges, [location.pathname]);

  const entries = useMemo(() =>
    buildPlatformNav(apps.filter((a) => a.ui && a.ready)
      .map((a) => ({ name: a.name, icon: a.icon }))), [apps]);

  return (
    <div className="layout">
      <SideNav entries={entries} activePath={location.pathname}
               badges={{ "/changes": pendingChanges }} LinkComponent={routerLink} />
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
