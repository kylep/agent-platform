import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type Binding = { kind: string; name: string; reason: string };
type App = { app_id: string; name: string; status: string; disabled_bindings: Binding[] };
type Watermark = { agent: string; last_fired_at: string | null; overdue_scheduled: number };
type Report = {
  maintenance: { mode: string; reason: string; entered_at: string | null; resumed_by: string | null };
  generated_at: string;
  disabled_count: number;
  apps: App[];
  task_watermarks: Watermark[];
  outbox: { status: string; message: string };
};

export default function RestoreReport() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  function refresh() {
    api<Report>("/api/maintenance/restore-report").then(setReport)
      .catch((e) => setError(String(e)));
  }
  useEffect(refresh, []);

  async function resume() {
    setWorking(true); setError("");
    try {
      await api("/api/maintenance/resume", { method: "POST" });
      refresh();
    } catch (e) { setError(String(e)); }
    finally { setWorking(false); }
  }

  return <div className="page">
    <p><Link to="/settings">Settings</Link> / Restore report</p>
    <h1>Restore report</h1>
    {error && <p role="alert" className="error">{error}</p>}
    {!report && !error && <p>Checking restored state…</p>}
    {report && <>
      <p>Automation: <strong>{report.maintenance.mode === "restore" ? "Paused for restore" : "Running"}</strong>
        {report.maintenance.reason && <> — {report.maintenance.reason}</>}</p>
      <p>{report.apps.length} Apps checked · {report.disabled_count} disabled bindings.
        Definitions were kept as restored.</p>
      {report.apps.map((app) => <section key={app.app_id}>
        <h2>{app.name} <small>({app.status})</small></h2>
        {app.disabled_bindings.length === 0 ? <p>No disabled bindings.</p> :
          <ul>{app.disabled_bindings.map((item) => <li key={`${item.kind}:${item.name}`}>
            {item.kind} {item.name}: {item.reason}
          </li>)}</ul>}
      </section>)}
      <section><h2>Task watermarks</h2>
        {report.task_watermarks.length === 0 ? <p>No Tasks recorded.</p> :
          <table><thead><tr><th>Agent</th><th>Last fired</th><th>Overdue scheduled</th></tr></thead>
            <tbody>{report.task_watermarks.map((task) => <tr key={task.agent}>
              <td>{task.agent}</td><td>{task.last_fired_at || "Never"}</td>
              <td>{task.overdue_scheduled}</td>
            </tr>)}</tbody></table>}
      </section>
      <p>{report.outbox.message}</p>
      {report.maintenance.mode === "restore" &&
        <button type="button" disabled={working} onClick={resume}>Resume automation</button>}
    </>}
  </div>;
}
