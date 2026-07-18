import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api, type SetupState } from "./api";

const AUTH_PATHS = ["/setup", "/login", "/secrets"];

// Secret statuses that do NOT block navigation. "unprobed" means a value
// was saved but the platform hasn't smoke-tested it yet -- that happens
// later via a run, not at gate time. Only "missing"/"invalid" block.
const PASSING_STATUSES = new Set(["ok", "unprobed"]);

export default function Gate() {
  const location = useLocation();
  const [state, setState] = useState<SetupState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<SetupState>("/api/setup-state")
      .then((s) => { if (!cancelled) { setState(s); setError(false); } })
      .catch(() => { if (!cancelled) setError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [location.pathname]);

  if (loading) return <div className="page-loading">Loading…</div>;
  if (error || !state) return <div className="page-loading">Unable to reach the API.</div>;

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
