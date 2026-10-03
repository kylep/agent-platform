import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { apiErrorMessage, getMe, login, register, registrationOpen } from "../api";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

// `admin` is prefilled (docs/design/25): the one-user install types only its
// password, and a named principal (the QA, a second person) overwrites it.
const DEFAULT_PRINCIPAL = "admin";

type Tab = "signin" | "register";

export default function Login() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("signin");
  const [canRegister, setCanRegister] = useState(false);
  const [principal, setPrincipal] = useState(DEFAULT_PRINCIPAL);
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The Register tab only exists while registration is open.
  useEffect(() => {
    registrationOpen().then((r) => setCanRegister(r.open)).catch(() => setCanRegister(false));
  }, []);

  function pick(next: Tab) {
    setTab(next);
    setError(null);
  }

  // The admin lands on the console, a user on their profile.
  async function landed() {
    let role = "admin";
    try { role = (await getMe()).role; } catch { /* fall back to the console */ }
    navigate(role === "user" ? "/profile" : "/", { replace: true });
  }

  async function onSignIn(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(principal.trim() || DEFAULT_PRINCIPAL, password);
      await landed();
    } catch {
      // One message for a bad name and a bad password — the API's 401 does
      // not say which, and neither does the form.
      setError("Invalid username or password.");
    } finally {
      setBusy(false);
    }
  }

  async function onRegister(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await register(username.trim().toLowerCase(), newPassword, confirm);
      await landed();
    } catch (err) {
      setError(apiErrorMessage(err, "Could not register."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-form">
        <h1>Agent Platform</h1>
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "signin"}
                  className={`tab${tab === "signin" ? " active" : ""}`}
                  onClick={() => pick("signin")}>Sign in</button>
          {canRegister && (
            <button type="button" role="tab" aria-selected={tab === "register"}
                    className={`tab${tab === "register" ? " active" : ""}`}
                    onClick={() => pick("register")}>Register</button>
          )}
        </div>
        {tab === "signin" || !canRegister ? (
          <form className="form-col" onSubmit={onSignIn}>
            <label htmlFor="principal">Username</label>
            <Input
              id="principal"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={principal}
              onChange={(e) => setPrincipal(e.target.value)}
            />
            <label htmlFor="password">Password</label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && <div className="error">{error}</div>}
            <Button type="submit" disabled={busy}>{busy ? "Logging in…" : "Log in"}</Button>
          </form>
        ) : (
          <form className="form-col" onSubmit={onRegister}>
            <label htmlFor="reg-username">Choose a username</label>
            <Input
              id="reg-username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <label htmlFor="reg-password">Choose a password</label>
            <Input
              id="reg-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
            <label htmlFor="reg-confirm">Confirm password</label>
            <Input
              id="reg-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {error && <div className="error">{error}</div>}
            <Button type="submit" disabled={busy || !username || !newPassword}>
              {busy ? "Registering…" : "Create account"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
