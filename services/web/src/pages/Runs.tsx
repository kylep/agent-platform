import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";

const REFRESH_MS = 5000;

const ACTIVE_STATES = new Set(["queued", "dispatched", "running"]);
const OK_STATES = new Set(["succeeded"]);

export function stateChipClass(state: string): string {
  if (OK_STATES.has(state)) return "chip-ok";
  if (ACTIVE_STATES.has(state)) return "chip-unprobed";
  return "chip-invalid";
}

export function isActiveState(state: string): boolean {
  return ACTIVE_STATES.has(state);
}

export default function Runs() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function load() {
      api<RunSummary[]>("/api/runs?limit=50")
        .then((data) => { if (!cancelled) { setRuns(data); setError(null); } })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load runs."); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  return (
    <div className="page">
      <h1>Runs</h1>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Agent</th>
              <th>State</th>
              <th>Trigger</th>
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
                <td className="muted">{r.trigger}</td>
                <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
