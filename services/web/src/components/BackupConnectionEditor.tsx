import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Button } from "@ap/ui/button";
import { Input, Textarea } from "@ap/ui/field";

type BackupConnection = {
  bucket: string; prefix: string; age_recipient: string;
  credential_set: boolean; ready: boolean;
};

export function BackupConnectionEditor({ onChanged }: { onChanged: () => void }) {
  const [connection, setConnection] = useState<BackupConnection | null>(null);
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function load() {
    api<BackupConnection>("/api/backups/connection").then(setConnection)
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not load connection."));
  }
  useEffect(load, []);

  async function save() {
    if (!connection) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await api("/api/backups/connection", { method: "PUT", body: JSON.stringify({
        bucket: connection.bucket, prefix: connection.prefix,
        age_recipient: connection.age_recipient,
        ...(credential.trim() ? { service_account_json: credential.trim() } : {}),
      }) });
      setCredential(""); setMessage("Connection saved. Verify its upload permission next.");
      load(); onChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save connection."); }
    finally { setBusy(false); }
  }

  async function verify() {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<{ detail: string }>("/api/backups/connection/verify", { method: "POST" });
      setMessage(result.detail); onChanged();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Upload verification failed."); }
    finally { setBusy(false); }
  }

  if (!connection) return <p className="muted">Loading connection…</p>;
  return <div className="form-col">
    <label htmlFor="backup-bucket">Cloud Storage bucket</label>
    <Input id="backup-bucket" placeholder="e.g. kp-pai-memory-backups" value={connection.bucket}
      onChange={(event) => setConnection({ ...connection, bucket: event.target.value })} />
    <label htmlFor="backup-prefix">Object prefix</label>
    <Input id="backup-prefix" placeholder="agent-platform" value={connection.prefix}
      onChange={(event) => setConnection({ ...connection, prefix: event.target.value })} />
    <label htmlFor="backup-recipient">Public age recipient</label>
    <Input id="backup-recipient" placeholder="age1…" spellCheck={false} value={connection.age_recipient}
      onChange={(event) => setConnection({ ...connection, age_recipient: event.target.value })} />
    <p className="muted">Paste the public recipient from your recovery key. Keep the private key outside the platform and the cloud bucket.</p>
    <label htmlFor="backup-credential">Google service-account JSON key</label>
    <Textarea id="backup-credential" rows={3} autoComplete="off" spellCheck={false}
      placeholder={connection.credential_set ? "Credential set · leave blank to keep it" : "Paste the JSON key"}
      value={credential} onChange={(event) => setCredential(event.target.value)} />
    <p className="muted">Give this account Storage Object Creator on this bucket. It needs upload permission only. The JSON key is never shown again.</p>
    <div className="row-actions">
      <Button disabled={busy || !connection.bucket || !connection.age_recipient} onClick={save}>{busy ? "Working…" : "Save connection"}</Button>
      <Button variant="secondary" disabled={busy || !connection.ready} onClick={verify}>Verify upload</Button>
      <Link to="/backups">View backups</Link>
    </div>
    {message && <p role="status">{message}</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}
