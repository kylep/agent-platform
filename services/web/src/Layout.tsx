import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, type PullRequest } from "./api";
import { Button } from "./ui/button";

// A nav entry is either a plain link or a group: a parent link with children
// that collapse/expand (GCP-style). Children are indented under the parent.
type Item = { to: string; label: string; end?: boolean };
type Group = { to: string; label: string; children: Item[] };
type Entry = Item | Group;

const isGroup = (e: Entry): e is Group => "children" in e;

const nav: Entry[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/reporting", label: "Reporting", children: [
    { to: "/runs", label: "Runs" },
  ] },
  { to: "/agents", label: "Agents", children: [
    { to: "/conversations", label: "Conversations" },
    { to: "/memories", label: "Memories" },
    { to: "/changes", label: "Changes" },
    { to: "/schedules", label: "Schedules" },
  ] },
  { to: "/skills", label: "Skills" },
  { to: "/settings", label: "Settings", children: [
    { to: "/secrets", label: "Secrets" },
    { to: "/dlq", label: "DLQ" },
  ] },
];

// Paths whose active state should keep a group open (parent + its children).
function groupPaths(g: Group): string[] {
  return [g.to, ...g.children.map((c) => c.to)];
}

// Theme: dark is the default (Terminal); the choice persists per browser.
function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (localStorage.getItem("theme") === "light" ? "light" : "dark"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

export default function Layout() {
  const [pendingChanges, setPendingChanges] = useState(0);
  const location = useLocation();
  const { theme, toggle } = useTheme();

  // Which groups are expanded. A group opens automatically when the current
  // route is the parent or one of its children; the user can also toggle it.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setOpen((prev) => {
      const next = { ...prev };
      for (const e of nav) {
        if (isGroup(e) && groupPaths(e).some((p) =>
          p === "/" ? location.pathname === "/" : location.pathname.startsWith(p))) {
          next[e.to] = true;
        }
      }
      return next;
    });
  }, [location.pathname]);

  function refreshBadges() {
    api<PullRequest[]>("/api/pull-requests")
      .then((prs) => setPendingChanges(prs.length))
      .catch(() => {});
  }
  useEffect(() => {
    refreshBadges();
    const id = setInterval(refreshBadges, 20000);
    return () => clearInterval(id);
  }, []);
  useEffect(refreshBadges, [location.pathname]);

  const badge: Record<string, number> = { "/changes": pendingChanges };

  function link(l: Item, child = false) {
    return (
      <NavLink key={l.to} to={l.to} end={l.end}
               className={({ isActive }) =>
                 `nav-link${child ? " nav-child" : ""}${isActive ? " active" : ""}`}>
        <span>{l.label}</span>
        {badge[l.to] > 0 && <span className="nav-badge">{badge[l.to] >= 10 ? "!" : badge[l.to]}</span>}
      </NavLink>
    );
  }

  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand">Agent Platform</div>
        {nav.map((e) => {
          if (!isGroup(e)) return link(e);
          const expanded = open[e.to] ?? false;
          return (
            <div key={e.to} className="nav-group">
              <div className="nav-group-head">
                <NavLink to={e.to} end={e.to === "/"}
                         className={({ isActive }) => `nav-link nav-parent${isActive ? " active" : ""}`}>
                  <span>{e.label}</span>
                </NavLink>
                <button className={`nav-chevron${expanded ? " open" : ""}`}
                        aria-label={expanded ? `Collapse ${e.label}` : `Expand ${e.label}`}
                        onClick={() => setOpen((o) => ({ ...o, [e.to]: !expanded }))}>
                  ›
                </button>
              </div>
              {expanded && e.children.map((c) => link(c, true))}
            </div>
          );
        })}
        <div className="nav-foot">
          <Button variant="secondary" size="sm" onClick={toggle}
                  aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? "☀ light" : "◐ dark"}
          </Button>
        </div>
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
