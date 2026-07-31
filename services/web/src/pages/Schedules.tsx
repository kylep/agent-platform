import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cronEnglish } from "../lib/cron";
import { api, type Job, type ScheduleEntry } from "../api";
import { Chip } from "../ui/chip";
import { Select } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

const when = (ts: string | null) => (ts ? new Date(ts).toLocaleString() : "—");

function cronText(cron: string): string | null {
  return cronEnglish(cron);
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
        <Select aria-label="Filter schedules by agent" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
          <option value="">All agents</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
      </div>

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && shown.length === 0 && <p className="muted">No schedules yet.</p>}
      {!loading && shown.length > 0 && (
        <Table>
          <thead>
            <tr><TH>Agent</TH><TH>Name</TH><TH>Type</TH><TH>Cron</TH><TH>Next fire</TH><TH>Status</TH></tr>
          </thead>
          <tbody>
            {shown.map((r, i) => (
              <tr key={`${r.agent}-${r.name}-${i}`} className="clickable-row"
                  onClick={() => navigate(`/agents/${encodeURIComponent(r.agent)}?tab=schedules`)}>
                <TD>{r.agent}</TD>
                <TD>{r.name}</TD>
                <TD className="text-muted">{r.kind}</TD>
                <TD><Cron cron={r.cron} /></TD>
                <TD className="text-muted">{when(r.next_fire)}</TD>
                <TD>{r.enabled ? <Chip variant="ok">enabled</Chip> : <Chip variant="danger">disabled</Chip>}</TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
