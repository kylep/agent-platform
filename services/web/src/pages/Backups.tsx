import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Table, TD, TH } from "@ap/ui/table";

type Backup = { name: string; bytes: number; created_at: number; uploaded: boolean;
  gcs_uri: string | null; sha256?: string; secret_count?: number };
type Connection = { bucket: string; prefix: string; age_recipient: string;
  credential_set: boolean; ready: boolean };
type Inspection = { version: number; created_at: string; namespace: string;
  database_bytes: number; secret_count: number; secret_names: string[] };
type Job = { job: string; succeeded: number; failed: number; active: number };

export default function Backups() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<Job | null>(null);
  const [archive, setArchive] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function load() {
    Promise.all([api<Backup[]>("/api/backups"), api<Connection>("/api/backups/connection")])
      .then(([list, configured]) => { setBackups(list); setConnection(configured); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not load backups."));
  }
  useEffect(load, []);
  useEffect(() => {
    if (!job) return;
    const timer = window.setInterval(() => {
      api<Job>(`/api/backups/jobs/${job}`).then((status) => {
        setJobStatus(status);
        if (status.succeeded || status.failed) { window.clearInterval(timer); load(); }
      }).catch(() => {});
    }, 4000);
    return () => window.clearInterval(timer);
  }, [job]);

  async function run() {
    setBusy(true); setError(""); setJobStatus(null);
    try {
      const result = await api<{ job: string }>("/api/backups/run", { method: "POST" });
      setJob(result.job);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not start backup."); }
    finally { setBusy(false); }
  }

  async function inspect() {
    if (!archive || !keyFile) return;
    setBusy(true); setError(""); setInspection(null);
    try {
      const form = new FormData();
      form.append("file", archive);
      form.append("identity", await keyFile.text());
      const response = await fetch("/api/backups/import/inspect", {
        method: "POST", credentials: "include", body: form,
      });
      if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
      setInspection(await response.json() as Inspection);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not inspect backup."); }
    finally { setBusy(false); }
  }

  return <div className="page">
    <h1>Backups</h1>
    <p className="muted">Recovery archives contain the database and platform Secrets, encrypted before upload. Only an administrator can create, download, or inspect them.</p>
    {error && <Banner variant="danger">{error}</Banner>}
    <section>
      <h2>Cloud destination</h2>
      {connection ? <p>
        {connection.ready ? <Chip variant="ok">Configured</Chip> : <Chip variant="warn">Needs setup</Chip>}{" "}
        {connection.bucket ? <code>gs://{connection.bucket}/{connection.prefix}/</code> : "No bucket selected"}.{" "}
        <Link to="/secrets?connection=backup-gcs">Edit connection</Link>
      </p> : <p className="muted">Loading…</p>}
      <div className="row-actions">
        <Button disabled={busy || !connection?.ready} onClick={run}>{busy ? "Starting…" : "Back up now"}</Button>
        {job && <span role="status">Job <code>{job}</code>: {jobStatus?.succeeded ? "complete" : jobStatus?.failed ? "failed" : "running or queued"}</span>}
      </div>
    </section>
    <section>
      <h2>Encrypted exports</h2>
      <p className="muted">These are the copies retained on pai. Completed exports also show their cloud path. Downloaded files remain encrypted.</p>
      <Table><thead><tr><TH>Created</TH><TH>Size</TH><TH>Cloud copy</TH><TH></TH></tr></thead>
        <tbody>{backups.map((backup) => <tr key={backup.name}>
          <TD>{new Date(backup.created_at * 1000).toLocaleString()}<br /><code>{backup.name}</code></TD>
          <TD>{(backup.bytes / 1048576).toFixed(1)} MiB</TD>
          <TD>{backup.uploaded ? <span title={backup.gcs_uri ?? ""}><Chip variant="ok">Uploaded</Chip></span> : <Chip variant="warn">Local only</Chip>}</TD>
          <TD><a href={`/api/backups/file/${encodeURIComponent(backup.name)}`} download={backup.name}>Download</a></TD>
        </tr>)}
        {backups.length === 0 && <tr><TD colSpan={4} className="muted">No encrypted exports on pai yet.</TD></tr>}</tbody></Table>
    </section>
    <section>
      <h2>Inspect an import</h2>
      <p className="muted">Select an encrypted archive and its recovery identity. Inspection checks the archive and shows what it contains without displaying secret values. The identity is used for this request and is not saved.</p>
      <div className="form-col">
        <label htmlFor="backup-import-file">Encrypted backup file</label>
        <input id="backup-import-file" type="file" accept=".age" onChange={(event) => setArchive(event.target.files?.[0] ?? null)} />
        <label htmlFor="backup-import-key">Private age identity file</label>
        <input id="backup-import-key" type="file" onChange={(event) => setKeyFile(event.target.files?.[0] ?? null)} />
        <Button disabled={busy || !archive || !keyFile} onClick={inspect}>{busy ? "Inspecting…" : "Inspect import"}</Button>
      </div>
      {inspection && <div role="status">
        <p><strong>Archive verified.</strong> Created {new Date(inspection.created_at).toLocaleString()} in namespace <code>{inspection.namespace}</code>. Database dump: {(inspection.database_bytes / 1048576).toFixed(1)} MiB; {inspection.secret_count} Secrets.</p>
        <details><summary>Secret names</summary><p>{inspection.secret_names.join(", ")}</p></details>
        <p>To apply this archive to a fresh cluster, follow the <Link to="/help/backups">restore guide</Link>. Restoring replaces platform state and must run while application writers are stopped.</p>
      </div>}
    </section>
  </div>;
}
