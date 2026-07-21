import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentMetrics, type KafkaHealth, type MetricsOverview, type Retention } from "../api";

function pct(x: number | null): string {
  return x === null ? "—" : `${(x * 100).toFixed(0)}%`;
}
function dur(x: number | null): string {
  return x === null ? "—" : x >= 60 ? `${(x / 60).toFixed(1)}m` : `${x.toFixed(1)}s`;
}

function Stat({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className={warn ? "stat stat-warn" : "stat"}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export default function Reporting() {
  const [ov, setOv] = useState<MetricsOverview | null>(null);
  const [agents, setAgents] = useState<AgentMetrics[]>([]);
  const [kafka, setKafka] = useState<KafkaHealth | null>(null);
  const [retention, setRetention] = useState<Retention | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pruning, setPruning] = useState(false);
  const [pruneMsg, setPruneMsg] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<MetricsOverview>("/api/metrics/overview"),
      api<AgentMetrics[]>("/api/metrics/agents"),
    ])
      .then(([o, a]) => { setOv(o); setAgents(a); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load metrics."));
    api<KafkaHealth>("/api/health/kafka").then(setKafka).catch(() => setKafka(null));
    api<Retention>("/api/maintenance/retention").then(setRetention).catch(() => setRetention(null));
  }, []);

  async function prune() {
    setPruning(true);
    setPruneMsg(null);
    try {
      const r = await api<{ deleted: number }>("/api/maintenance/prune-transcripts", { method: "POST" });
      setPruneMsg(`Pruned ${r.deleted} transcript events.`);
    } catch (err) {
      setPruneMsg(err instanceof Error ? err.message : "Prune failed.");
    } finally {
      setPruning(false);
    }
  }

  return (
    <div className="page">
      <h1>Reporting</h1>
      <p className="muted">Platform health at a glance, and per-agent run metrics (last {ov?.window ?? 5000} runs).</p>
      {error && <div className="error">{error}</div>}

      <h2>Health</h2>
      <div className="stat-row">
        <Stat label="broker" value={kafka ? (kafka.reachable ? "up" : "down") : "…"} warn={kafka ? !kafka.reachable : false} />
        <Stat label="dispatcher lag" value={kafka?.lag ?? "—"} warn={(kafka?.lag ?? 0) > 50} />
        <Stat label="active runs" value={ov?.active ?? "—"} />
        <Stat label="dlq depth" value={ov?.dlq ?? "—"} warn={(ov?.dlq ?? 0) > 0} />
      </div>

      <h2>Runs</h2>
      {ov && (
        <div className="stat-row">
          <Stat label="success rate" value={pct(ov.success_rate)} warn={ov.success_rate !== null && ov.success_rate < 0.8} />
          <Stat label="runs · 24h" value={ov.runs_24h} />
          <Stat label="runs · 7d" value={ov.runs_7d} />
          <Stat label="total" value={ov.total} />
          <Stat label="avg duration" value={dur(ov.avg_duration_seconds)} />
          <Stat label="tokens in/out" value={`${ov.tokens_in}/${ov.tokens_out}`} />
        </div>
      )}

      <h2>Per agent</h2>
      <table className="table">
        <thead>
          <tr><th>Agent</th><th>Runs</th><th>Success</th><th>Fail streak</th><th>Avg dur</th><th>Tokens (in/out)</th><th>Last run</th></tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.agent}>
              <td><Link to={`/agents/${a.agent}`}>{a.agent}</Link></td>
              <td>{a.total}</td>
              <td>{pct(a.success_rate)}</td>
              <td>{a.failure_streak > 0 ? <span className="chip chip-invalid">{a.failure_streak}</span> : "0"}</td>
              <td>{dur(a.avg_duration_seconds)}</td>
              <td className="muted">{a.tokens_in}/{a.tokens_out}</td>
              <td className="muted">{a.last_run_at ? new Date(a.last_run_at).toLocaleString() : "—"}</td>
            </tr>
          ))}
          {agents.length === 0 && <tr><td colSpan={7} className="muted">No runs yet.</td></tr>}
        </tbody>
      </table>

      <h2>Transcript retention</h2>
      <p className="muted">
        Run metadata is kept forever; bulky transcript events are pruned after their agent's
        retention (default {retention?.default_days ?? "—"} days, 0 = keep forever). Pruning runs
        daily; you can also run it now.
      </p>
      <div className="row-actions">
        <button onClick={prune} disabled={pruning}>{pruning ? "Pruning…" : "Prune transcripts now"}</button>
        {pruneMsg && <span className="muted">{pruneMsg}</span>}
      </div>
    </div>
  );
}
