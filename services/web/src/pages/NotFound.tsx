import { Link, useLocation } from "react-router-dom";

// The catch-all route: a styled dead end instead of a blank page.
export default function NotFound() {
  const { pathname } = useLocation();
  return (
    <div className="page">
      <h1>Not found</h1>
      <p className="muted">
        Nothing lives at <code>{pathname}</code>. It may have been removed, or the link is stale.
      </p>
      <p><Link to="/">Back to the Dashboard</Link></p>
    </div>
  );
}
