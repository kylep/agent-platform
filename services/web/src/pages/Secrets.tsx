import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type SecretStatus } from "../api";

type SaveState = "idle" | "saving" | "saved" | "error";

function StatusChip({ status }: { status: string }) {
  return <span className={`chip chip-${status}`}>{status}</span>;
}

// Build the secret's key/value. An explicit key wins; otherwise use the
// heuristic (pasted JSON → credentials.json file, anything else → `token`).
function toData(value: string, key: string): Record<string, string> {
  const trimmed = value.trim();
  const k = key.trim() || (trimmed.startsWith("{") ? "credentials.json" : "token");
  return { [k]: trimmed };
}

function SecretRow({ secret, onSaved }: { secret: SecretStatus; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [keyName, setKeyName] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  async function save() {
    setSaveState("saving");
    try {
      await api(`/api/secrets/${encodeURIComponent(secret.name)}`, {
        method: "PUT",
        body: JSON.stringify({ data: toData(value, keyName) }),
      });
      setSaveState("saved");
      onSaved();
    } catch {
      setSaveState("error");
    }
  }

  return (
    <div className="secret-row">
      <div className="secret-row-header">
        <span className="secret-name">{secret.name}</span>
        {secret.required && <span className="chip chip-required">required</span>}
        <StatusChip status={secret.status} />
      </div>
      <textarea
        placeholder="Paste the secret value…"
        value={value}
        onChange={(e) => { setValue(e.target.value); setSaveState("idle"); }}
        rows={3}
      />
      <div className="secret-row-footer">
        <input
          className="secret-key"
          placeholder="key (default: token)"
          value={keyName}
          onChange={(e) => setKeyName(e.target.value)}
        />
        <button onClick={save} disabled={saveState === "saving" || value === ""}>
          {saveState === "saving" ? "Saving…" : "Save"}
        </button>
        {saveState === "saved" && <span className="muted">Saved.</span>}
        {saveState === "error" && <span className="error">Save failed.</span>}
      </div>
    </div>
  );
}

function AddSecret({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState("");
  const [keyName, setKeyName] = useState("");
  const [value, setValue] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  async function save() {
    if (!name.trim() || !value.trim()) return;
    setSaveState("saving");
    try {
      await api(`/api/secrets/${encodeURIComponent(name.trim())}`, {
        method: "PUT",
        body: JSON.stringify({ data: toData(value, keyName) }),
      });
      setSaveState("saved");
      setName(""); setKeyName(""); setValue("");
      onSaved();
    } catch {
      setSaveState("error");
    }
  }

  return (
    <div className="secret-row">
      <div className="secret-row-header"><span className="secret-name">Add a secret</span></div>
      <div className="row-actions" style={{ marginBottom: 6 }}>
        <input placeholder="name (e.g. discord-bot)" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="secret-key" placeholder="key (default: token)" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
      </div>
      <textarea placeholder="value…" value={value} onChange={(e) => setValue(e.target.value)} rows={2} />
      <div className="secret-row-footer">
        <button onClick={save} disabled={saveState === "saving" || !name.trim() || !value.trim()}>
          {saveState === "saving" ? "Saving…" : "Add secret"}
        </button>
        {saveState === "saved" && <span className="muted">Saved.</span>}
        {saveState === "error" && <span className="error">Save failed.</span>}
      </div>
    </div>
  );
}

export default function Secrets() {
  const location = useLocation();
  const banner = (location.state as { banner?: string } | null)?.banner;
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [loading, setLoading] = useState(true);

  function load() {
    setLoading(true);
    api<SecretStatus[]>("/api/secrets")
      .then(setSecrets)
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  return (
    <div className="page">
      <h1>Secrets</h1>
      <p className="muted">
        Rows include the platform's required secrets plus any declared by skills and connectors
        (e.g. <code>discord-bot</code> for the Discord connector). Leave the key blank to use the
        default (<code>token</code>).
      </p>
      {banner && <div className="banner">{banner}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && secrets.map((s) => (
        <SecretRow key={s.name} secret={s} onSaved={load} />
      ))}
      {!loading && <AddSecret onSaved={load} />}
    </div>
  );
}
