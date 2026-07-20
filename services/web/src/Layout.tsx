import { NavLink, Outlet } from "react-router-dom";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/agents", label: "Agents" },
  { to: "/runs", label: "Runs" },
  { to: "/schedules", label: "Schedules" },
  { to: "/changes", label: "Changes" },
  { to: "/secrets", label: "Secrets" },
  { to: "/settings", label: "Settings" },
];

export default function Layout() {
  return (
    <div className="layout">
      <nav className="nav">
        <div className="nav-brand">Agent Platform</div>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            {l.label}
          </NavLink>
        ))}
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
