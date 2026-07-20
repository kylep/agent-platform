import { useEffect, useState } from "react";
import { api, type ApiKey, type ApiKeyMinted } from "../api";

const ROLES = ["reader", "operator", "coder", "admin"];

function PasswordSection() {
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  async function change() {
    setState("saving");
    setError(null);
    try {
      await api("/api/change-password", {
        method: "POST",
        body: JSON.stringify({ old_password: oldPw, new_password: newPw }),
      });
      setState("saved");
      setOldPw(""); setNewPw("");
    } catch (err) {
      setState("idle");
      setError(err instanceof Error ? err.message : "Failed to change password.");
    }
  }

  return (
    <section>
      <h2>Change admin password</h2>
      <div className="form-col">
        <input type="password" placeholder="Current password" value={oldPw}
               onChange={(e) => { setOldPw(e.target.value); setState("idle"); }} />
        <input type="password" placeholder="New password (min 8 chars)" value={newPw}
               onChange={(e) => { setNewPw(e.target.value); setState("idle"); }} />
      </div>
      {error && <div className="error">{error}</div>}
      <div className="secret-row-footer">
        <button onClick={change} disabled={state === "saving" || oldPw === "" || newPw.length < 8}>
          {state === "saving" ? "Saving…" : "Change password"}
        </button>
        {state === "saved" && <span className="muted">Password changed.</span>}
      </div>
    </section>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState("operator");
  const [minted, setMinted] = useState<ApiKeyMinted | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api<ApiKey[]>("/api/api-keys").then(setKeys).catch(() => {});
  }
  useEffect(load, []);

  async function mint() {
    setError(null);
    try {
      const k = await api<ApiKeyMinted>("/api/api-keys", {
        method: "POST",
        body: JSON.stringify({ name, role, agent: null }),
      });
      setMinted(k);
      setName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to mint key.");
    }
  }

  async function revoke(id: string) {
    await api(`/api/api-keys/${id}`, { method: "DELETE" });
    load();
  }

  return (
    <section>
      <h2>API keys</h2>
      <p className="muted">Bearer tokens for non-interactive access. The token is shown once, at creation.</p>
      {minted && (
        <div className="banner">
          New key <strong>{minted.name}</strong> ({minted.role}) — copy it now, it won't be shown again:
          <pre className="agent-md">{minted.token}</pre>
        </div>
      )}
      <div className="form-row">
        <input placeholder="Key name" value={name} onChange={(e) => setName(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <button onClick={mint} disabled={name.trim() === ""}>Create key</button>
      </div>
      {error && <div className="error">{error}</div>}
      <table className="table">
        <thead>
          <tr><th>Name</th><th>Role</th><th>Prefix</th><th>Status</th><th></th></tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id}>
              <td>{k.name}</td>
              <td>{k.role}</td>
              <td className="muted">{k.prefix}…</td>
              <td>{k.revoked_at
                ? <span className="chip chip-invalid">revoked</span>
                : <span className="chip chip-ok">active</span>}</td>
              <td>{!k.revoked_at &&
                <button className="secondary" onClick={() => revoke(k.id)}>Revoke</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function Settings() {
  return (
    <div className="page">
      <h1>Settings</h1>
      <PasswordSection />
      <ApiKeysSection />
    </div>
  );
}
