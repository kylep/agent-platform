import { useEffect, useState, type ReactNode } from "react";
import "./sidenav.css";
import { Button } from "./button";

// The platform sidebar, shared by the console and every app frontend
// (docs/design/11): apps keep the platform chrome, and deployed apps appear
// as children under the Apps entry. Presentational — consumers supply the
// entries (buildPlatformNav) and, optionally, a LinkComponent (the console
// passes a react-router adapter; apps use plain anchors, since crossing an
// app boundary is a full page load by design).

export type NavItem = {
  to: string;
  label: string;
  end?: boolean;
  // Rendered as a plain <a> even when a LinkComponent is provided — for
  // targets OUTSIDE the current SPA (e.g. /apps/news/ from the console).
  external?: boolean;
};
export type NavEntry = NavItem & { children?: NavItem[] };

export type AppNavInfo = { name: string; icon: string };

// The platform's information architecture — single source, so the console
// and app shells never drift. Deployed apps slot in under Apps.
export function buildPlatformNav(apps: AppNavInfo[] = []): NavEntry[] {
  return [
    { to: "/", label: "Dashboard", end: true },
    { to: "/reporting", label: "Reporting", children: [
      { to: "/runs", label: "Runs" },
      { to: "/reports", label: "Reports" },
    ] },
    { to: "/agents", label: "Agents", children: [
      { to: "/conversations", label: "Conversations" },
      { to: "/memories", label: "Memories" },
      { to: "/changes", label: "Changes" },
      { to: "/schedules", label: "Schedules" },
    ] },
    { to: "/apps", label: "Apps", children: apps.map((a) => (
      { to: `/apps/${a.name}/`, label: `${a.icon || "🧩"} ${a.name}`, external: true }
    )) },
    { to: "/skills", label: "Skills" },
    { to: "/settings", label: "Settings", children: [
      { to: "/secrets", label: "Secrets" },
      { to: "/dlq", label: "DLQ" },
    ] },
    { to: "/help", label: "Help" },
  ];
}

export type LinkComponent = (props: {
  to: string; end?: boolean; className: (active: boolean) => string;
  children: ReactNode;
}) => ReactNode;

const anchorLink: LinkComponent = ({ to, className, children }) => (
  <a key={to} href={to} className={className(false)}>{children}</a>
);

function groupPaths(e: NavEntry): string[] {
  return [e.to, ...(e.children ?? []).map((c) => c.to)];
}

export function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">(
    () => (localStorage.getItem("theme") === "light" ? "light" : "dark"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <Button variant="secondary" size="sm" onClick={toggle}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
      {theme === "dark" ? "☀ light" : "◐ dark"}
    </Button>
  );
}

export function SideNav({ entries, activePath, badges = {}, LinkComponent, footer }: {
  entries: NavEntry[];
  // Current location.pathname — drives active state + group auto-expand.
  // (Apps pass their own base, e.g. "/apps/news/".)
  activePath: string;
  badges?: Record<string, number>;
  LinkComponent?: LinkComponent;
  footer?: ReactNode;
}) {
  const Link = LinkComponent ?? anchorLink;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setOpen((prev) => {
      const next = { ...prev };
      for (const e of entries) {
        if (e.children?.length && groupPaths(e).some((p) =>
          p === "/" ? activePath === "/" : activePath.startsWith(p))) {
          next[e.to] = true;
        }
      }
      return next;
    });
  }, [activePath, entries]);

  function renderLink(l: NavItem, child = false) {
    const body = (
      <>
        <span>{l.label}</span>
        {badges[l.to] > 0 && (
          <span className="nav-badge">{badges[l.to] >= 10 ? "!" : badges[l.to]}</span>
        )}
      </>
    );
    const cls = (active: boolean) =>
      `nav-link${child ? " nav-child" : ""}${active ? " active" : ""}`;
    if (l.external || !LinkComponent) {
      const active = activePath.startsWith(l.to) && l.to !== "/";
      return <a key={l.to} href={l.to} className={cls(active)}>{body}</a>;
    }
    return <Link key={l.to} to={l.to} end={l.end} className={cls}>{body}</Link>;
  }

  return (
    <nav className="nav">
      <div className="nav-brand">Agent Platform</div>
      {entries.map((e) => {
        if (!e.children?.length) return renderLink(e);
        const expanded = open[e.to] ?? false;
        return (
          <div key={e.to} className="nav-group">
            <div className="nav-group-head">
              <span className="nav-parent">{renderLink(e)}</span>
              <button type="button" className={`nav-chevron${expanded ? " open" : ""}`}
                      aria-label={expanded ? `Collapse ${e.label}` : `Expand ${e.label}`}
                      onClick={() => setOpen((o) => ({ ...o, [e.to]: !expanded }))}>
                ›
              </button>
            </div>
            {expanded && e.children.map((c) => renderLink(c, true))}
          </div>
        );
      })}
      <div className="nav-foot">{footer ?? <ThemeToggle />}</div>
    </nav>
  );
}
