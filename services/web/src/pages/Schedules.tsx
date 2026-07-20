import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ScheduleEntry } from "../api";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

export default function Schedules() {
  const [rows, setRows] = useState<ScheduleEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    setLoading(true);
    api<ScheduleEntry[]>("/api/schedules")
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load schedules."))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function toggle(agent: string, enabled: boolean) {
    setBusy(agent);
    setError(null);
    try {
      await api(`/api/schedules/${encodeURIComponent(agent)}/${enabled ? "disable" : "enable"}`, {
        method: "POST",
      });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update schedule.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <h1>Schedules</h1>
      <p className="muted">Agents that run on a cron schedule. Declared via each agent's manifest.</p>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && rows.length === 0 && <p className="muted">No scheduled agents.</p>}
      {!loading && rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Cron</th>
              <th>Next fire</th>
              <th>Last fire</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent}>
                <td><Link to={`/agents/${encodeURIComponent(r.agent)}`}>{r.agent}</Link></td>
                <td><code>{r.cron}</code></td>
                <td className="muted">{when(r.next_fire)}</td>
                <td className="muted">{when(r.last_fire)}</td>
                <td>{r.enabled
                  ? <span className="chip chip-ok">enabled</span>
                  : <span className="chip chip-invalid">disabled</span>}</td>
                <td>
                  <button className="secondary" onClick={() => toggle(r.agent, r.enabled)} disabled={busy === r.agent}>
                    {busy === r.agent ? "…" : r.enabled ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
