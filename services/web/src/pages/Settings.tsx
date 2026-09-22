import { useEffect, useState } from "react";
import { api, type ApiKey, type ApiKeyMinted, type RelayStats } from "../api";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Select } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";
import { API_KEY_ROLES, API_KEY_ROLE_DESC } from "../lib/roles";

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
      <form className="form-col" onSubmit={(e) => e.preventDefault()}>
        <Input type="password" autoComplete="current-password" placeholder="Current password"
               aria-label="Current password" value={oldPw}
               onChange={(e) => { setOldPw(e.target.value); setState("idle"); }} />
        <Input type="password" autoComplete="new-password" placeholder="New password (min 8 chars)"
               aria-label="New password" value={newPw}
               onChange={(e) => { setNewPw(e.target.value); setState("idle"); }} />
      </form>
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

function KeyRoleCell({ apiKey, onChanged }: { apiKey: ApiKey; onChanged: () => void }) {
  const [role, setRole] = useState(apiKey.role);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api(`/api/api-keys/${apiKey.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      });
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change role.");
    } finally {
      setSaving(false);
    }
  }

  if (apiKey.revoked_at) return <>{apiKey.role}</>;
  return (
    <div>
      <div className="row-actions">
        <Select aria-label={`Role for ${apiKey.name}`} value={role} disabled={saving}
                onChange={(e) => setRole(e.target.value)}>
          {!API_KEY_ROLES.some((r) => r === apiKey.role) &&
            <option value={apiKey.role}>{apiKey.role} (legacy)</option>}
          {API_KEY_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </Select>
        {role !== apiKey.role &&
          <Button size="sm" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState<(typeof API_KEY_ROLES)[number]>("operator");
  const [minted, setMinted] = useState<ApiKeyMinted | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);
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
        body: JSON.stringify({ name, role }),
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
        <Select aria-label="API key role" value={role}
                onChange={(e) => setRole(e.target.value as (typeof API_KEY_ROLES)[number])}>
          {API_KEY_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </Select>
        <Button onClick={mint} disabled={name.trim() === ""}>Create key</Button>
      </div>
      <p className="muted"><strong>{role}</strong> — {API_KEY_ROLE_DESC[role]}</p>
      {error && <div className="error">{error}</div>}
      <Table>
        <thead>
          <tr><TH>Name</TH><TH>Role</TH><TH>Prefix</TH><TH>Created</TH><TH>Status</TH><TH></TH></tr>
        </thead>
        <tbody>
          {keys.filter((k) => showRevoked || !k.revoked_at).map((k) => (
            <tr key={k.id}>
              <TD>{k.name}</TD>
              <TD><KeyRoleCell apiKey={k} onChanged={load} /></TD>
              <TD className="text-muted">{k.prefix}…</TD>
              <TD className="text-muted whitespace-nowrap">{k.created_at ? new Date(k.created_at).toLocaleString() : "—"}</TD>
              <TD>{k.revoked_at
                ? <Chip variant="danger">revoked</Chip>
                : <Chip variant="ok">active</Chip>}</TD>
              <TD>{!k.revoked_at &&
                <Button variant="secondary" size="sm" onClick={() => revoke(k.id)}>Revoke</Button>}</TD>
            </tr>
          ))}
          {keys.filter((k) => !k.revoked_at).length === 0 && (
            <tr><TD colSpan={6} className="text-muted">No active keys.</TD></tr>
          )}
        </tbody>
      </Table>
      {keys.some((k) => k.revoked_at) && (
        <Button variant="link" className="text-muted mt-2" onClick={() => setShowRevoked(!showRevoked)}>
          {showRevoked ? "Hide" : "Show"} {keys.filter((k) => k.revoked_at).length} revoked keys
        </Button>
      )}
    </section>
  );
}

// The Relay guards, in the order an operator asks about them, each with the
// reason it exists — lifted from the comments beside the settings themselves in
// `services/backend/agentplatform/config.py` so the two cannot drift into two
// different explanations of the same number.
const RELAY_GUARDS: { env: string; label: string;
                      value: (s: RelayStats["settings"]) => string; why: string }[] = [
  { env: "AP_RELAY_MAX_HOPS", label: "Max hops",
    value: (s) => String(s.max_hops),
    why: "A run triggered by a message at hop h posts its reply at h+1; an agent-authored " +
         "message at this hop can summon nobody, which is where a two-agent ping-pong stops. " +
         "A human mention starts a fresh chain at hop 0." },
  { env: "AP_RELAY_CHANNEL_INVOCATIONS_PER_HOUR", label: "Channel invocations / hour",
    value: (s) => String(s.channel_per_hour),
    why: "Spend cap on mention-triggered runs in one room, counted from the invocation log " +
         "so it survives a restart. Over budget the router suppresses and says so once per " +
         "channel per hour, rather than once per suppressed message." },
  { env: "AP_RELAY_GLOBAL_INVOCATIONS_PER_HOUR", label: "Global invocations / hour",
    value: (s) => String(s.global_per_hour),
    why: "The same cap across every room at once — the ceiling on what an hour of Relay can " +
         "cost, whichever channel the mentions land in." },
  { env: "AP_RELAY_AGENT_COOLDOWN_SECONDS", label: "Agent cooldown",
    value: (s) => `${s.cooldown_seconds}s`,
    why: "An agent mentioned by another agent within this long of its own last reply in that " +
         "channel gets a coalesced wake instead of a second run: three mentions during one " +
         "reply become one follow-up, never three." },
  { env: "AP_RELAY_CONTEXT_MESSAGES", label: "Context messages",
    value: (s) => String(s.context_messages),
    why: "How many recent channel messages a mention run is shown as context." },
];

function RelaySection() {
  const [stats, setStats] = useState<RelayStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<RelayStats>("/api/relay/stats").then(setStats)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load Relay settings."));
  }, []);

  return (
    <section>
      <h2>Relay</h2>
      <p className="muted">
        What the running platform is enforcing for the agent messenger — the default grant and the
        loop guards behind every <code>@mention</code>. Read-only here.
      </p>
      {error && <div className="error">{error}</div>}
      {!error && !stats && <p className="muted">Loading…</p>}
      {stats && (
        <>
          <p>
            Default grant:{" "}
            {stats.settings.default_grant
              ? <Chip variant="ok">on</Chip>
              : <Chip variant="danger">off</Chip>}{" "}
            <span className="muted">
              Whether creating an agent grants it <code>mcp__platform__relay</code>. The grant is a
              real row either way, so an admin can remove it per agent like any other.
            </span>
          </p>
          <Table>
            <thead>
              <tr><TH>Guard</TH><TH>Value</TH><TH>Env</TH><TH>Why</TH></tr>
            </thead>
            <tbody>
              {RELAY_GUARDS.map((g) => (
                <tr key={g.env}>
                  <TD>{g.label}</TD>
                  <TD>{g.value(stats.settings)}</TD>
                  <TD className="text-muted"><code>{g.env}</code></TD>
                  <TD className="text-muted">{g.why}</TD>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="muted mt-2">
            Set in the Helm chart / env (<code>AP_RELAY_*</code>), applied on restart.
          </p>
        </>
      )}
    </section>
  );
}

export default function Settings() {
  return (
    <div className="page">
      <h1>Settings</h1>
      <PasswordSection />
      <ApiKeysSection />
      <RelaySection />
    </div>
  );
}
