import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import cronstrue from "cronstrue";
import { api, type Job, type ScheduleEntry } from "../api";

const when = (ts: string | null) => (ts ? new Date(ts).toLocaleString() : "—");

function cronText(cron: string): string | null {
  try { return cronstrue.toString(cron, { throwExceptionOnParseError: true }); }
  catch { return null; }
}

function Cron({ cron }: { cron: string }) {
  const text = cronText(cron);
  return <code className="cron" title={text ?? "unrecognized cron expression"}>{cron}{!text && " ⚠"}</code>;
}

type Row = {
  agent: string; name: string; kind: "Job" | "Manifest";
  cron: string; next_fire: string | null; enabled: boolean;
};

/** Global schedules: every cron job and manifest schedule across all agents,
 * filterable by agent. New jobs are created from the agent's Schedules tab;
 * clicking a row opens it there. */
export default function Schedules() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<Row[]>([]);
  const [agentFilter, setAgentFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<Job[]>("/api/jobs"),
      api<ScheduleEntry[]>("/api/schedules"),
    ]).then(([jobs, scheds]) => {
      const j: Row[] = jobs.map((x) => ({
        agent: x.agent, name: x.name, kind: "Job", cron: x.cron,
        next_fire: x.next_fire, enabled: x.enabled }));
      const s: Row[] = scheds.map((x) => ({
        agent: x.agent, name: "(manifest schedule)", kind: "Manifest", cron: x.cron,
        next_fire: x.next_fire, enabled: x.enabled }));
      const all = [...j, ...s].sort((a, b) =>
        (a.next_fire ?? "9999").localeCompare(b.next_fire ?? "9999"));
      setRows(all);
    }).catch((err) => setError(err instanceof Error ? err.message : "Failed to load schedules."))
      .finally(() => setLoading(false));
  }, []);

  const agents = useMemo(() => [...new Set(rows.map((r) => r.agent))].sort(), [rows]);
  const shown = agentFilter ? rows.filter((r) => r.agent === agentFilter) : rows;

  return (
    <div className="page page-wide">
      <h1>Schedules</h1>
      <p className="muted">
        Recurring work across all agents — cron jobs and manifest schedules. Hover a cron to read it in
        plain English. Create or edit from an agent's <em>Schedules</em> tab; click a row to open it there.
      </p>

      <div className="row-actions" style={{ marginBottom: 12 }}>
        <select value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
          <option value="">All agents</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && shown.length === 0 && <p className="muted">No schedules yet.</p>}
      {!loading && shown.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Agent</th><th>Name</th><th>Type</th><th>Cron</th><th>Next fire</th><th>Status</th></tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={`${r.agent}-${r.name}-${i}`} className="clickable-row"
                  onClick={() => navigate(`/agents/${encodeURIComponent(r.agent)}?tab=schedules`)}>
                <td>{r.agent}</td>
                <td>{r.name}</td>
                <td className="muted">{r.kind}</td>
                <td><Cron cron={r.cron} /></td>
                <td className="muted">{when(r.next_fire)}</td>
                <td>{r.enabled ? <span className="chip chip-ok">enabled</span> : <span className="chip chip-invalid">disabled</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
