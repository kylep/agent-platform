import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type KafkaHealth, type RunSummary, type SetupState } from "../api";
import { stateChipClass } from "./Runs";

export default function Dashboard() {
  const [setupState, setSetupState] = useState<SetupState | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [kafka, setKafka] = useState<KafkaHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<SetupState>("/api/setup-state"),
      api<RunSummary[]>("/api/runs?limit=10"),
    ])
      .then(([s, r]) => { setSetupState(s); setRuns(r); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load dashboard."))
      .finally(() => setLoading(false));
    // Kafka health is a separate, slower probe; don't block the rest of the page on it.
    api<KafkaHealth>("/api/health/kafka").then(setKafka).catch(() => setKafka(null));
  }, []);

  return (
    <div className="page">
      <h1>Dashboard</h1>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}

      {!loading && !error && (
        <>
          <div className="tile-row">
            <Link className="tile" to="/agents">Agents</Link>
            <Link className="tile" to="/runs">Runs</Link>
            <Link className="tile" to="/secrets">Secrets</Link>
          </div>

          <h2>Kafka health</h2>
          {!kafka && <p className="muted">Probing…</p>}
          {kafka && (
            <div className="chip-row">
              <span className={`chip ${kafka.reachable ? "chip-ok" : "chip-invalid"}`}>
                broker: {kafka.reachable ? "reachable" : "unreachable"}
              </span>
              <span className="chip">queued: {kafka.backlog.queued}</span>
              <span className="chip">active: {kafka.backlog.active}</span>
              <Link to="/dlq" className={`chip ${kafka.backlog.dlq > 0 ? "chip-invalid" : ""}`}>
                dlq: {kafka.backlog.dlq}
              </Link>
              {kafka.lag !== null && <span className="chip">dispatcher lag: {kafka.lag}</span>}
              {kafka.missing_topics.length > 0 && (
                <span className="chip chip-invalid">missing: {kafka.missing_topics.join(", ")}</span>
              )}
              {kafka.error && <span className="chip chip-invalid">{kafka.error}</span>}
            </div>
          )}

          <h2>Secrets</h2>
          <div className="chip-row">
            {setupState?.secrets.map((s) => (
              <span key={s.name} className={`chip chip-${s.status}`}>
                {s.name}: {s.status}
              </span>
            ))}
          </div>

          <h2>Recent runs</h2>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Agent</th>
                <th>State</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link>
                  </td>
                  <td>{r.agent}</td>
                  <td>
                    <span className={`chip ${stateChipClass(r.state)}`}>{r.state}</span>
                  </td>
                  <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
