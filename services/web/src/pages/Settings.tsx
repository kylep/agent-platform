import { useEffect, useState } from "react";
import { api, type ApiKey, type ApiKeyMinted } from "../api";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Input, Select } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

const ROLE_DESC: Record<string, string> = {
  reader: "Read-only: view agents, runs, schedules, and changes.",
  operator: "Reader + trigger runs and fire webhooks.",
  coder: "Operator + edit agents (self-edit / open PRs).",
  admin: "Full control: secrets, API keys, merges, and settings.",
};
const ROLES = Object.keys(ROLE_DESC);

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
        <Input type="password" placeholder="Current password" value={oldPw}
               onChange={(e) => { setOldPw(e.target.value); setState("idle"); }} />
        <Input type="password" placeholder="New password (min 8 chars)" value={newPw}
               onChange={(e) => { setNewPw(e.target.value); setState("idle"); }} />
      </div>
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <Button onClick={change} disabled={state === "saving" || oldPw === "" || newPw.length < 8}>
          {state === "saving" ? "Saving…" : "Change password"}
        </Button>
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
        <Banner>
          New key <strong>{minted.name}</strong> ({minted.role}) — copy it now, it won't be shown again:
          <pre className="agent-md">{minted.token}</pre>
        </Banner>
      )}
      <div className="form-row">
        <Input placeholder="Key name" value={name} onChange={(e) => setName(e.target.value)} />
        <Select aria-label="API key role" value={role} onChange={(e) => setRole(e.target.value)}>
          {ROLES.map((r) => <option key={r} value={r} title={ROLE_DESC[r]}>{r}</option>)}
        </Select>
        <Button onClick={mint} disabled={name.trim() === ""}>Create key</Button>
      </div>
      <p className="muted"><strong>{role}</strong> — {ROLE_DESC[role]}</p>
      {error && <div className="error">{error}</div>}
      <Table>
        <thead>
          <tr><TH>Name</TH><TH>Role</TH><TH>Prefix</TH><TH>Status</TH><TH></TH></tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k.id}>
              <TD>{k.name}</TD>
              <TD>{k.role}</TD>
              <TD className="text-muted">{k.prefix}…</TD>
              <TD>{k.revoked_at
                ? <Chip variant="danger">revoked</Chip>
                : <Chip variant="ok">active</Chip>}</TD>
              <TD>{!k.revoked_at &&
                <Button variant="secondary" size="sm" onClick={() => revoke(k.id)}>Revoke</Button>}</TD>
            </tr>
          ))}
        </tbody>
      </Table>
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
