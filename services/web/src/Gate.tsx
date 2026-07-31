import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api, type SetupState } from "./api";

const AUTH_PATHS = ["/setup", "/login", "/secrets"];

// Secret statuses that do NOT block navigation. "unprobed" = saved but not yet
// smoke-tested; "valid" = a run authenticated with it. Only "missing"/"invalid"
// block.
const PASSING_STATUSES = new Set(["ok", "unprobed", "valid"]);

export default function Gate() {
  const location = useLocation();
  const [state, setState] = useState<SetupState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<SetupState>("/api/setup-state")
      .then((s) => { if (!cancelled) setState(s); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [location.pathname]);

  // Block only on the FIRST load — later route changes revalidate in the
  // background against the stale state, so navigation never blanks the app.
  if (loading && !state) {
    return (
      <div className="auth-page">
        <div className="text-center">
          <div className="mb-2 text-lg font-semibold">Agent Platform</div>
          <div className="muted">connecting…</div>
        </div>
      </div>
    );
  }
  if (!state) return <div className="page-loading">Unable to reach the API.</div>;

  if (state.needs_admin && location.pathname !== "/setup") {
    return <Navigate to="/setup" replace />;
  }

  const blockingSecret = state.secrets.find(
    (s) => s.required && !PASSING_STATUSES.has(s.status)
  );

  if (blockingSecret && !AUTH_PATHS.includes(location.pathname)) {
    return <Navigate to="/secrets" replace state={{ banner: `Required secret "${blockingSecret.name}" is ${blockingSecret.status}.` }} />;
  }

  return <Outlet context={{ setupState: state }} />;
}
