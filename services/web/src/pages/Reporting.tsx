import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentMetrics, type KafkaHealth, type MetricsOverview } from "../api";

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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<MetricsOverview>("/api/metrics/overview"),
      api<AgentMetrics[]>("/api/metrics/agents"),
    ])
      .then(([o, a]) => { setOv(o); setAgents(a); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load metrics."));
    api<KafkaHealth>("/api/health/kafka").then(setKafka).catch(() => setKafka(null));
  }, []);

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
    </div>
  );
}
