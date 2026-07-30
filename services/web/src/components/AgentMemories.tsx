import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Memory } from "../api";

/** Agent-scoped memory browser (Memories tab on the agent page): search within
 * this agent, add/edit/delete, and a selected memory (?memory=id) deep-links
 * from the global Memories table. */
export default function AgentMemories({ agent }: { agent: string }) {
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
        <input placeholder={`Search ${agent}'s memory…`} value={q}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") load(); }} style={{ flex: 1 }} />
        <button onClick={() => load()}>Search</button>
        <button className="secondary" onClick={startNew}>+ New memory</button>
      </div>
      <p className="muted" style={{ marginTop: -4 }}>
        What this agent remembers. A memory with a <em>key</em> is overwritten in place when the agent
        saves that key again; keyless memories are plain notes.
      </p>

      {editing === "new" && (
        <div className="secret-editor">
          <label className="field-label">Key <span className="muted">(optional — for overwrite-in-place state)</span></label>
          <input placeholder="e.g. alert-state (leave blank for a note)" value={draftKey}
                 onChange={(e) => setDraftKey(e.target.value)} />
          <label className="field-label">Content</label>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={4} autoFocus />
          {error && <div className="error">{error}</div>}
          <div className="row-actions" style={{ marginTop: 8 }}>
            <button onClick={save} disabled={busy === "new" || !draft.trim()}>
              {busy === "new" ? "Saving…" : "Add memory"}
            </button>
            <button className="secondary" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </div>
      )}

      {loading && <p className="muted">Loading…</p>}
      {error && editing !== "new" && <div className="error">{error}</div>}
      {!loading && rows.length === 0 && editing !== "new" && <p className="muted">No memories.</p>}
      {!loading && rows.length > 0 && (
        <table className="table mem-table">
          <thead><tr><th>Memory</th><th style={{ width: 150 }}>Updated</th><th style={{ width: 150 }}></th></tr></thead>
          <tbody>
            {rows.map((m) => {
              const open = selected === m.id;
              const isEditing = editing === m.id;
              return (
                <tr key={m.id} className={open ? "row-open" : ""}>
                  <td onClick={() => !isEditing && select(open ? null : m.id)} style={{ cursor: isEditing ? "default" : "pointer" }}>
                    {isEditing ? (
                      <textarea className="memory-edit" value={draft} onChange={(e) => setDraft(e.target.value)}
                                rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))} autoFocus />
                    ) : open ? (
                      <>
                        {m.key && <span className="chip memory-key">{m.key}</span>}
                        <div className="memory-content">{m.content}</div>
                      </>
                    ) : (
                      <div className="mem-cell">
                        {m.key && <span className="chip memory-key">{m.key}</span>}
                        <span className="memory-content one-line">{m.content}</span>
                      </div>
                    )}
                  </td>
                  <td className="muted">{stamp(m.updated_at)}</td>
                  <td>
                    {isEditing ? (
                      <div className="row-actions">
                        <button onClick={save} disabled={busy === m.id}>{busy === m.id ? "Saving…" : "Save"}</button>
                        <button className="secondary" onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    ) : (
                      <div className="row-actions">
                        <button className="secondary" onClick={() => startEdit(m)}>Edit</button>
                        <button className="secondary" onClick={() => remove(m.id)} disabled={busy === m.id}>
                          {busy === m.id ? "…" : "Delete"}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
