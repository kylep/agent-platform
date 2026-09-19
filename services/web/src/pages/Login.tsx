import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

// `admin` is prefilled (docs/design/25): the one-user install types only its
// password, and a named principal (the QA, a second person) overwrites it.
const DEFAULT_PRINCIPAL = "admin";

export default function Login() {
  const navigate = useNavigate();
  const [principal, setPrincipal] = useState(DEFAULT_PRINCIPAL);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(principal.trim() || DEFAULT_PRINCIPAL, password);
      navigate("/", { replace: true });
    } catch {
      // One message for a bad name and a bad password — the API's 401 does
      // not say which, and neither does the form.
      setError("Invalid username or password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-form" onSubmit={onSubmit}>
        <h1>Log in</h1>
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
    </div>
  );
}
