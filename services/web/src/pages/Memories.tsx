import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Memory } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Select } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";
import { PromoteDialog } from "../components/wiki/PromoteDialog";
import { forgetWikiPages, useWikiPages } from "../components/wiki/Prose";
import type { WikiPage } from "../lib/wiki";

/** Global memories: one table across all agents, newest first, filterable by
 * agent and searchable across every namespace. A row opens the full memory in
 * its agent's Memories tab.
 *
 * The last column is the wiki (docs/design/21). A memory is one agent's
 * private note; promoting it is somebody deciding the note has hardened into a
 * fact everybody should be able to cite. One that already has a page wears the
 * badge and links to it. */
export default function Memories() {
  // "[]"/"{}"/blank are agents' empty-state writes — show them as such.
const emptyish = (t: string) => !t.trim() || ["[]", "{}"].includes(t.trim());
const navigate = useNavigate();
  const [rows, setRows] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promoting, setPromoting] = useState<Memory | null>(null);
  // The pages promoted in THIS session, on top of the listing the page loaded
  // with. A write answers with the page it stored, which is the only thing
  // that knows the slug the server settled on.
  const [written, setWritten] = useState<WikiPage[]>([]);
  // Which memories the wiki already has a page for. ONE request for the whole
  // table, indexed by the provenance the pages carry: asking per row would be
  // the same answer fetched once per memory.
  const pages = useWikiPages();
  const promoted = useMemo(() => {
    const by = new Map<string, WikiPage>();
    for (const p of [...(pages ?? []), ...written]) {
      if (p.source_memory_id) by.set(p.source_memory_id, p);
    }
    return by;
  }, [pages, written]);

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
        <Table className="mem-table mem-table-all">
          <thead>
            <tr><TH>Agent</TH><TH>Memory</TH><TH>Updated</TH><TH>Wiki</TH></tr>
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
                {/* The cell swallows the click: what is in it goes somewhere
                    of its own, and the row's own destination is the memory
                    rather than the page it became. A memory that has already
                    graduated offers the page instead of the verb — promoting
                    it again is an edit, and the place to edit a page is the
                    page. */}
                <TD onClick={(e) => e.stopPropagation()}>
                  <div className="mem-wiki">
                    {promoted.has(m.id)
                      ? (
                        <Link to={`/wiki/${promoted.get(m.id)!.slug}`} className="mem-promoted"
                              title={`promoted to ${promoted.get(m.id)!.title}`}>
                          📖 promoted
                        </Link>
                      )
                      : (
                        <Button variant="secondary" size="sm" onClick={() => setPromoting(m)}>
                          Promote to wiki
                        </Button>
                      )}
                  </div>
                </TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <PromoteDialog memory={promoting} onClose={() => setPromoting(null)}
                     onPromoted={(page) => {
                       setWritten((prev) => [...prev, page]);
                       // A slug that exists as of a moment ago must not still
                       // read as wanted on the next page somebody opens.
                       forgetWikiPages();
                     }} />
    </div>
  );
}
