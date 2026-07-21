import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary } from "../api";

function AgentTable({ agents }: { agents: AgentSummary[] }) {
  return (
    <table className="table">
      <thead>
        <tr><th>Name</th><th>Description</th><th>Schedule</th><th>Status</th></tr>
      </thead>
      <tbody>
        {agents.map((a) => (
          <tr key={a.name}>
            <td><Link to={`/agents/${encodeURIComponent(a.name)}`}>{a.name}</Link></td>
            <td className="muted">{a.description}</td>
            <td className="muted">{a.schedule ? <code>{a.schedule}</code> : "—"}</td>
            <td>
              {a.quarantined
                ? <span className="chip chip-invalid" title={a.error ?? "Quarantined"}>quarantined</span>
                : <span className="chip chip-ok">ok</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

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

  const system = agents.filter((a) => a.system);
  const regular = agents.filter((a) => !a.system);

  return (
    <div className="page">
      <div className="page-header">
        <h1>Agents</h1>
        <Link to="/agents/new" className="button-link">+ New Agent</Link>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <>
          <AgentTable agents={regular} />
          {system.length > 0 && (
            <>
              <h2>System agents</h2>
              <p className="muted">Platform-internal agents. Managed by the platform; not deletable.</p>
              <AgentTable agents={system} />
            </>
          )}
        </>
      )}
    </div>
  );
}
