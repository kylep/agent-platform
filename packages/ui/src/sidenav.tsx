import { useEffect, useMemo, useState, type ReactNode } from "react";
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
    { to: "/relay", label: "Relay" },
    { to: "/tickets", label: "Tickets" },
    { to: "/wiki", label: "Wiki" },
    { to: "/reporting", label: "Reporting", children: [
      { to: "/runs", label: "Runs" },
      { to: "/reports", label: "Reports" },
    ] },
    { to: "/agents", label: "Agents", children: [
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

function pathOf(to: string): string {
  const i = to.indexOf("?");
  return i < 0 ? to : to.slice(0, i);
}

function searchOf(to: string): URLSearchParams {
  const i = to.indexOf("?");
  return new URLSearchParams(i < 0 ? "" : to.slice(i + 1));
}

function groupPaths(e: NavEntry): string[] {
  return [e.to, ...(e.children ?? []).map((c) => pathOf(c.to))];
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

export function SideNav({ entries, activePath, activeSearch = "", badges = {},
                         LinkComponent, footer, belowBrand }: {
  entries: NavEntry[];
  // Current location.pathname — drives active state + group auto-expand.
  // (Apps pass their own base, e.g. "/apps/news/".)
  activePath: string;
  // Current location.search. Only needed where two links share a pathname and
  // differ by a query param (Relay: /relay is Channels, /relay?kind=dm is
  // DMs) — a router NavLink matches on pathname alone and would light both.
  activeSearch?: string;
  badges?: Record<string, number>;
  LinkComponent?: LinkComponent;
  footer?: ReactNode;
  // Rendered directly under the brand, at the brand's own horizontal padding
  // — the console puts the usage bars here (docs/design/22). The wrapper
  // collapses when what it holds renders nothing, so a consumer that has no
  // data to show costs the nav no height.
  belowBrand?: ReactNode;
}) {
  const Link = LinkComponent ?? anchorLink;
  // The query keys any link declares at a given pathname. An unparameterised
  // link there is the selected one exactly when NONE of them is in the URL,
  // which is what makes "Channels" go quiet on /relay?kind=dm.
  const paramKeys = useMemo(() => {
    const keys: Record<string, Set<string>> = {};
    for (const e of entries) {
      for (const l of [e, ...(e.children ?? [])]) {
        const set = keys[pathOf(l.to)] ?? (keys[pathOf(l.to)] = new Set());
        for (const k of searchOf(l.to).keys()) set.add(k);
      }
    }
    return keys;
  }, [entries]);
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

  // Whether a query-discriminated link is the current one: its own params
  // must match, and a link that declares none must find none in the URL.
  function paramsMatch(to: string): boolean {
    const own = searchOf(to);
    const current = new URLSearchParams(activeSearch);
    for (const key of paramKeys[pathOf(to)] ?? []) {
      if ((own.get(key) ?? "") !== (current.get(key) ?? "")) return false;
    }
    return true;
  }

  function renderLink(l: NavItem, child = false, groupHead = false) {
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
    const path = pathOf(l.to);
    // A group's own header link is the WHOLE group — prefix semantics, never
    // discriminated by the params its children use. Otherwise "Relay" goes
    // dark the moment you are on "/relay?kind=dm", which is still Relay.
    const discriminated = !groupHead && (paramKeys[path]?.size ?? 0) > 0;
    if (l.external || !LinkComponent) {
      const active = activePath.startsWith(path) && path !== "/" && paramsMatch(l.to);
      return <a key={l.to} href={l.to} className={cls(active)}>{body}</a>;
    }
    if (discriminated) {
      // The router cannot answer this one, so answer it here and hand the
      // link a verdict instead of a predicate.
      const active = activePath === path && paramsMatch(l.to);
      return <Link key={l.to} to={l.to} className={() => cls(active)}>{body}</Link>;
    }
    return <Link key={l.to} to={l.to} end={l.end} className={cls}>{body}</Link>;
  }

  return (
    <nav className="nav">
      <div className="nav-brand">Agent Platform</div>
      <div className="nav-below-brand">{belowBrand}</div>
      {entries.map((e) => {
        if (!e.children?.length) return renderLink(e);
        const expanded = open[e.to] ?? false;
        return (
          <div key={e.to} className="nav-group">
            <div className="nav-group-head">
              <span className="nav-parent">{renderLink(e, false, true)}</span>
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
