import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RunSummary, type SetupState } from "../api";
import { stateChipClass } from "./Runs";

export default function Dashboard() {
  const [setupState, setSetupState] = useState<SetupState | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
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
