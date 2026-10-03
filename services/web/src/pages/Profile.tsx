import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { apiErrorMessage, changeMyPassword, getMe, logout, type Me } from "../api";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

// Standalone page (no sidebar): the only screen a `user` role can reach
// (docs/design/40). Handles its own identity, so Gate does not fetch /api/me.
export default function Profile() {
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getMe().then(setMe).catch(() => navigate("/login", { replace: true }));
  }, [navigate]);

  async function signOut() {
    try { await logout(); } catch { /* the cookie is gone or the server is; either way leave */ }
    navigate("/login", { replace: true });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    if (next !== confirm) {
      setError("New passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await changeMyPassword(current, next, confirm);
      setSaved(true);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(apiErrorMessage(err, "Could not change password."));
    } finally {
      setBusy(false);
    }
  }

  if (!me) return <div className="auth-page"><div className="muted">Loading…</div></div>;

  const isAdmin = me.role === "admin";
  return (
    <div className="auth-page">
      <div className="auth-form" style={{ maxWidth: 420 }}>
        <h1>Profile</h1>
        <dl className="profile-facts">
          <dt className="muted">Username</dt><dd>{me.username}</dd>
          <dt className="muted">Group</dt><dd>{me.group?.name ?? "None"}</dd>
          <dt className="muted">Member since</dt>
          <dd>{me.created_at ? new Date(me.created_at).toLocaleDateString() : "—"}</dd>
        </dl>
        {isAdmin ? (
          <p className="muted">Change the admin password in <Link to="/settings">Settings</Link>.</p>
        ) : (
          <form className="form-col" onSubmit={onSubmit}>
            <h2>Change password</h2>
            <label htmlFor="pw-current">Current password</label>
            <Input id="pw-current" type="password" autoComplete="current-password"
                   value={current} onChange={(e) => { setCurrent(e.target.value); setSaved(false); }} />
            <label htmlFor="pw-new">New password</label>
            <Input id="pw-new" type="password" autoComplete="new-password"
                   value={next} onChange={(e) => { setNext(e.target.value); setSaved(false); }} />
            <label htmlFor="pw-confirm">Confirm new password</label>
            <Input id="pw-confirm" type="password" autoComplete="new-password"
                   value={confirm} onChange={(e) => { setConfirm(e.target.value); setSaved(false); }} />
            {error && <div className="error">{error}</div>}
            {saved && <div className="muted">Password changed.</div>}
            <Button type="submit" disabled={busy || !current || !next}>
              {busy ? "Saving…" : "Change password"}
            </Button>
          </form>
        )}
        <Button variant="secondary" onClick={signOut}>Sign out</Button>
      </div>
    </div>
  );
}
