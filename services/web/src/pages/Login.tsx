import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Button } from "../ui/button";
import { Input } from "../ui/field";

export default function Login() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
      navigate("/", { replace: true });
    } catch {
      setError("Invalid password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-form" onSubmit={onSubmit}>
        <h1>Log in</h1>
        <label htmlFor="password">Password</label>
        <Input
          id="password"
          type="password"
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
