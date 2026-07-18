import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary } from "../api";

export default function Agents() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AgentSummary[]>("/api/agents")
      .then(setAgents)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load agents."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="page">
      <h1>Agents</h1>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Description</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.name}>
                <td>
                  <Link to={`/agents/${encodeURIComponent(a.name)}`}>{a.name}</Link>
                </td>
                <td className="muted">{a.description}</td>
                <td>
                  {a.quarantined ? (
                    <span className="chip chip-invalid" title={a.error ?? "Quarantined"}>
                      quarantined
                    </span>
                  ) : (
                    <span className="chip chip-ok">ok</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
