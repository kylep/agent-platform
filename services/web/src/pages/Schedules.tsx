import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type AgentSummary, type ScheduleEntry } from "../api";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

function RequestSchedule() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agent, setAgent] = useState("");
  const [cron, setCron] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AgentSummary[]>("/api/agents")
      .then((a) => { setAgents(a); if (a[0]) setAgent(a[0].name); })
      .catch(() => {});
  }, []);

  async function request() {
    setBusy(true);
    setError(null);
    try {
      const run = await api<{ id: string }>(`/api/agents/${encodeURIComponent(agent)}/edit`, {
        method: "POST",
        body: JSON.stringify({
          instruction: `Set the \`schedule\` field in this agent's manifest.yaml to the 5-field cron \`${cron}\`. Add the field if it doesn't exist; change only that field.`,
        }),
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to request schedule.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>Request a schedule</h2>
      <p className="muted">
        Sets an agent's cron via platform-coder. It opens a pull request you review under Changes;
        the schedule goes live when you merge.
      </p>
      <div className="form-row">
        <select value={agent} onChange={(e) => setAgent(e.target.value)}>
          {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
        </select>
        <input placeholder="cron, e.g. */5 * * * *" value={cron} onChange={(e) => setCron(e.target.value)} />
        <button onClick={request} disabled={busy || !agent || cron.trim() === ""}>
          {busy ? "Requesting…" : "Request via PR"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
    </section>
  );
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
      <RequestSchedule />
      <h2>Active schedules</h2>
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
