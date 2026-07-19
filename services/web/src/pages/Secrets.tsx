import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, type SecretStatus } from "../api";

type SaveState = "idle" | "saving" | "saved" | "error";

function StatusChip({ status }: { status: string }) {
  return <span className={`chip chip-${status}`}>{status}</span>;
}

function SecretRow({ secret, onSaved }: { secret: SecretStatus; onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  async function save() {
    setSaveState("saving");
    // Heuristic: pasted JSON is a session credentials file; anything else is
    // a long-lived `claude setup-token` value (the preferred credential).
    const trimmed = value.trim();
    const key = trimmed.startsWith("{") ? "credentials.json" : "token";
    setSaveState("saving");
    try {
      await api(`/api/secrets/${encodeURIComponent(secret.name)}`, {
        method: "PUT",
        body: JSON.stringify({ data: { [key]: trimmed } }),
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
        placeholder="Paste a `claude setup-token` value (or credentials.json contents)…"
        value={value}
        onChange={(e) => { setValue(e.target.value); setSaveState("idle"); }}
        rows={4}
      />
      <div className="secret-row-footer">
        <button onClick={save} disabled={saveState === "saving" || value === ""}>
          {saveState === "saving" ? "Saving…" : "Save"}
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
      {banner && <div className="banner">{banner}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && secrets.map((s) => (
        <SecretRow key={s.name} secret={s} onSaved={load} />
      ))}
    </div>
  );
}
