import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Memory } from "../api";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Input, Select } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

/** Global memories: one table across all agents, newest first, filterable by
 * agent and searchable across every namespace. A row opens the full memory in
 * its agent's Memories tab. */
export default function Memories() {
  // "[]"/"{}"/blank are agents' empty-state writes — show them as such.
const emptyish = (t: string) => !t.trim() || ["[]", "{}"].includes(t.trim());
const navigate = useNavigate();
  const [rows, setRows] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function load(query = q) {
    setLoading(true);
    setError(null);
    const qs = query.trim() ? `?q=${encodeURIComponent(query.trim())}&limit=500` : "?limit=500";
    api<Memory[]>(`/api/memories${qs}`)          // no agent → all namespaces
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load memories."))
      .finally(() => setLoading(false));
  }
  useEffect(() => { load(""); /* eslint-disable-next-line */ }, []);

  const agents = useMemo(
    () => [...new Set(rows.map((m) => m.agent))].sort(), [rows]);
  const shown = agentFilter ? rows.filter((m) => m.agent === agentFilter) : rows;

  const open = (m: Memory) =>
    navigate(`/agents/${encodeURIComponent(m.agent)}?tab=memories&memory=${encodeURIComponent(m.id)}`);
  const stamp = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

  return (
    <div className="page page-wide">
      <h1>Memories</h1>
      <p className="muted">
        What every agent has chosen to remember, newest first. Search runs across all agents; click a
        memory to open it in its agent.
      </p>

      <div className="row-actions" style={{ marginBottom: 12 }}>
        <Input placeholder="Search all memories…" value={q} style={{ flex: 1 }}
               aria-label="Search all memories"
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") load(); }} />
        <Button onClick={() => load()}>Search</Button>
        <Select aria-label="Filter memories by agent" value={agentFilter} onChange={(e) => setAgentFilter(e.target.value)}>
          <option value="">All agents</option>
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
      </div>

      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && shown.length === 0 && <p className="muted">No memories.</p>}
      {!loading && shown.length > 0 && (
        <Table className="mem-table">
          <thead>
            <tr><TH style={{ width: 140 }}>Agent</TH><TH>Memory</TH><TH style={{ width: 170 }}>Updated</TH></tr>
          </thead>
          <tbody>
            {shown.map((m) => (
              <tr key={m.id} className="clickable-row" onClick={() => open(m)}>
                <TD>{m.agent}</TD>
                <TD>
                  <div className="mem-cell">
                    {m.key && <Chip className="memory-key">{m.key}</Chip>}
                    <span className="memory-content one-line">{emptyish(m.content) ? "(empty)" : m.content}</span>
                  </div>
                </TD>
                <TD className="text-muted">{stamp(m.updated_at)}</TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
