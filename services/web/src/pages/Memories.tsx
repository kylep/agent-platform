import { useEffect, useState } from "react";
import { api, type AgentSummary, type Memory } from "../api";

export default function Memories() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [agent, setAgent] = useState<string>("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api<AgentSummary[]>("/api/agents")
      .then((a) => { setAgents(a); if (a.length && !agent) setAgent(a[0].name); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load agents."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function load(a = agent, query = q) {
    if (!a) return;
    setLoading(true);
    setError(null);
    const qs = query.trim() ? `&q=${encodeURIComponent(query.trim())}` : "";
    api<Memory[]>(`/api/memories?agent=${encodeURIComponent(a)}${qs}`)
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load memories."))
      .finally(() => setLoading(false));
  }

  useEffect(() => { if (agent) load(agent, q); // reload when the agent changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent]);

  async function remove(id: string) {
    setBusy(id);
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <h1>Memories</h1>
      <p className="muted">
        What each agent has chosen to remember. Memories are private to an agent's namespace; pick an
        agent to browse or search its memory.
      </p>

      <div className="row-actions" style={{ marginBottom: 12 }}>
        <select value={agent} onChange={(e) => setAgent(e.target.value)}>
          {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
        </select>
        <input
          placeholder="Search…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") load(); }}
        />
        <button onClick={() => load()}>Search</button>
      </div>

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && rows.length === 0 && <p className="muted">No memories.</p>}
      {!loading && rows.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Key</th><th>Content</th><th>Updated</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id}>
                <td className="muted">{m.key || "—"}</td>
                <td>{m.content}</td>
                <td className="muted">{m.updated_at ? new Date(m.updated_at).toLocaleString() : "—"}</td>
                <td>
                  <button className="secondary" onClick={() => remove(m.id)} disabled={busy === m.id}>
                    {busy === m.id ? "Deleting…" : "Delete"}
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
