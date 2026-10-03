import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api, getMe, type Me, type SetupState } from "./api";

const AUTH_PATHS = ["/setup", "/login", "/secrets"];
// Pages that resolve their own identity; Gate does not fetch /api/me for them
// (so /login can never redirect to itself).
const SELF_AUTH_PATHS = ["/setup", "/login", "/profile"];

// Secret statuses that do NOT block navigation. "unprobed" = saved but not yet
// smoke-tested; "valid" = a run authenticated with it. Only "missing"/"invalid"
// block.
const PASSING_STATUSES = new Set(["ok", "unprobed", "valid"]);

export default function Gate() {
  const location = useLocation();
  const [state, setState] = useState<SetupState | null>(null);
  const [loading, setLoading] = useState(true);
  // undefined = not asked for this stretch of navigation, null = anonymous.
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const selfAuth = SELF_AUTH_PATHS.includes(location.pathname);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    // Visiting a self-auth page forgets the old answer, so a fresh sign-in is
    // never judged by the anonymous result from before it.
    if (selfAuth) setMe(undefined);
    const setup = api<SetupState>("/api/setup-state")
      .then((s) => { if (!cancelled) setState(s); })
      .catch(() => {});
    // 401 = anonymous. Any other failure renders as today: the page's own
    // calls surface it, and a hiccup here must not log anybody out.
    const who = selfAuth ? Promise.resolve() : getMe()
      .then((m) => { if (!cancelled) setMe(m); })
      .catch((err) => {
        if (!cancelled && err instanceof Error && err.message === "401") setMe(null);
      });
    Promise.all([setup, who]).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [location.pathname]);

  // Block only on the FIRST load — later route changes revalidate in the
  // background against the stale state, so navigation never blanks the app.
  if ((loading && !state) || (loading && !selfAuth && me === undefined)) {
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

  if (!selfAuth) {
    if (me === null) return <Navigate to="/login" replace />;
    if (me?.role === "user") return <Navigate to="/profile" replace />;
  }

  const blockingSecret = state.secrets.find(
    (s) => s.required && !PASSING_STATUSES.has(s.status)
  );

  if (blockingSecret && !AUTH_PATHS.includes(location.pathname)) {
    return <Navigate to="/secrets" replace state={{ banner: `Required secret "${blockingSecret.name}" is ${blockingSecret.status}.` }} />;
  }

  return <Outlet context={{ setupState: state, me }} />;
}
