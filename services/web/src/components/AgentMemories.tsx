import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Memory } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Textarea } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";

/** Agent-scoped memory browser (Memories tab on the agent page): search within
 * this agent, add/edit/delete, and a selected memory (?memory=id) deep-links
 * from the global Memories table. */
export default function AgentMemories({ agent }: { agent: string }) {
  // "[]"/"{}"/blank are agents' empty-state writes — show them as such.
const emptyish = (t: string) => !t.trim() || ["[]", "{}"].includes(t.trim());
const [params, setParams] = useSearchParams();
  const selected = params.get("memory");
  const [rows, setRows] = useState<Memory[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);  // memory id or "new"
  const [draft, setDraft] = useState("");
  const [draftKey, setDraftKey] = useState("");

  function select(id: string | null) {
    const p = new URLSearchParams(params);
    p.set("tab", "memories");
    if (id) p.set("memory", id); else p.delete("memory");
    setParams(p);
  }

  function load(query = q) {
    setLoading(true);
    setError(null);
    const qs = query.trim() ? `&q=${encodeURIComponent(query.trim())}` : "";
    api<Memory[]>(`/api/memories?agent=${encodeURIComponent(agent)}${qs}`)
      .then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load memories."))
      .finally(() => setLoading(false));
  }
  useEffect(() => { load(""); setEditing(null); /* eslint-disable-next-line */ }, [agent]);

  async function remove(id: string) {
    setBusy(id);
    try { await api(`/api/memories/${id}`, { method: "DELETE" }); if (selected === id) select(null); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Delete failed."); }
    finally { setBusy(null); }
  }

  async function save() {
    setBusy(editing);
    setError(null);
    try {
      if (editing === "new") {
        await api("/api/memories", { method: "POST", body: JSON.stringify({
          agent, content: draft, key: draftKey.trim() || null }) });
      } else if (editing) {
        await api(`/api/memories/${editing}`, { method: "PATCH", body: JSON.stringify({ content: draft }) });
      }
      setEditing(null);
      load();
    } catch (err) { setError(err instanceof Error ? err.message : "Save failed."); }
    finally { setBusy(null); }
  }

  function startNew() { setEditing("new"); setDraft(""); setDraftKey(""); select(null); }
  function startEdit(m: Memory) { setEditing(m.id); setDraft(m.content); }

  const stamp = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

  return (
    <>
      <div className="row-actions" style={{ marginBottom: 12 }}>
        <Input placeholder={`Search ${agent}'s memory…`} aria-label="Search memories" value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") load(); }} className="flex-1" />
        <Button onClick={() => load()}>Search</Button>
        <Button variant="secondary" onClick={startNew}>+ New memory</Button>
      </div>
      <p className="muted" style={{ marginTop: -4 }}>
        What this agent remembers. A memory with a <em>key</em> is overwritten in place when the agent
        saves that key again; keyless memories are plain notes.
      </p>

      {editing === "new" && (
        <div className="secret-editor">
          <label className="field-label">Key <span className="muted">(optional — for overwrite-in-place state)</span></label>
          <Input placeholder="e.g. alert-state (leave blank for a note)" aria-label="Memory key" value={draftKey}
                 onChange={(e) => setDraftKey(e.target.value)} />
          <label className="field-label">Content</label>
          <Textarea value={draft} aria-label="Memory content" onChange={(e) => setDraft(e.target.value)} rows={4} autoFocus />
          {error && <div className="error">{error}</div>}
          <div className="row-actions" style={{ marginTop: 8 }}>
            <Button onClick={save} disabled={busy === "new" || !draft.trim()}>
              {busy === "new" ? "Saving…" : "Add memory"}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
          </div>
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && editing !== "new" && <div className="error">{error}</div>}
      {!loading && rows.length === 0 && editing !== "new" && <p className="muted">No memories.</p>}
      {!loading && rows.length > 0 && (
        <Table className="mem-table">
          <thead><tr><TH>Memory</TH><TH style={{ width: 150 }}>Updated</TH><TH style={{ width: 150 }}></TH></tr></thead>
          <tbody>
            {rows.map((m) => {
              const open = selected === m.id;
              const isEditing = editing === m.id;
              return (
                <tr key={m.id} className={open ? "row-open" : ""}>
                  <TD onClick={() => !isEditing && select(open ? null : m.id)} style={{ cursor: isEditing ? "default" : "pointer" }}>
                    {isEditing ? (
                      <Textarea className="memory-edit" aria-label="Memory content" value={draft} onChange={(e) => setDraft(e.target.value)}
                                rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))} autoFocus />
                    ) : open ? (
                      <>
                        {m.key && <Chip className="memory-key">{m.key}</Chip>}
                        <div className="memory-content">{emptyish(m.content) ? "(empty)" : m.content}</div>
                      </>
                    ) : (
                      <div className="mem-cell">
                        {m.key && <Chip className="memory-key">{m.key}</Chip>}
                        <span className="memory-content one-line">{emptyish(m.content) ? "(empty)" : m.content}</span>
                      </div>
                    )}
                  </TD>
                  <TD className="text-muted">{stamp(m.updated_at)}</TD>
                  <TD>
                    {isEditing ? (
                      <div className="row-actions">
                        <Button size="sm" onClick={save} disabled={busy === m.id}>{busy === m.id ? "Saving…" : "Save"}</Button>
                        <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
                      </div>
                    ) : (
                      <div className="row-actions">
                        <Button size="sm" variant="secondary" onClick={() => startEdit(m)}>Edit</Button>
                        <Button size="sm" variant="secondary" onClick={() => remove(m.id)} disabled={busy === m.id}>
                          {busy === m.id ? "…" : "Delete"}
                        </Button>
                      </div>
                    )}
                  </TD>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </>
  );
}
