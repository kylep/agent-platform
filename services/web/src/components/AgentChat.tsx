import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Conversation, type ConversationDetail } from "../api";
import { Button } from "../ui/button";
import { ConfirmDialog } from "../ui/dialog";
import { Input, Textarea } from "../ui/field";

const ACTIVE = new Set(["queued", "dispatched", "running"]);

// Only conversations the platform originated (web) can be continued or deleted
// from the UI. Connector conversations (Discord, …) are owned by their channel.
const isWeb = (c: { connector: string }) => c.connector === "web";

function TypeBadge({ connector }: { connector: string }) {
  return <span className={`convo-type convo-type-${connector}`}>{connector}</span>;
}

// Last-activity stamp as local yyyy-mm-dd hh:mm.
function stamp(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** ChatGPT-style conversation view scoped to one agent: a rail of this agent's
 * conversations plus a chat pane. Web conversations are interactive; connector
 * conversations (Discord) are read-only transcripts with per-message senders.
 * The selected conversation lives in the `?conversation=` search param so the
 * /conversations table can deep-link here. */
export default function AgentChat({ agent }: { agent: string }) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("conversation");
  const [list, setList] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);

  function select(id: string | null) {
    const p = new URLSearchParams(params);
    p.set("tab", "conversations");
    if (id) p.set("conversation", id);
    else p.delete("conversation");
    setParams(p);
  }

  function loadList() {
    api<Conversation[]>("/api/conversations")
      .then((all) => setList(all
        .filter((c) => c.agent === agent)
        .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))))
      .catch(() => {});
  }
  useEffect(() => { loadList(); }, [agent]);

  function loadDetail(id: string) {
    api<ConversationDetail>(`/api/conversations/${id}`).then((d) => {
      setDetail(d);
      const active = d.turns.some((t) => ACTIVE.has(t.state));
      if (!active && poll.current) { clearInterval(poll.current); poll.current = null; loadList(); }
    }).catch(() => {});
  }
  useEffect(() => {
    if (poll.current) { clearInterval(poll.current); poll.current = null; }
    setDetail(null);
    setConfirmDelete(false);
    setRenaming(false);
    if (selected) {
      loadDetail(selected);
      poll.current = setInterval(() => loadDetail(selected), 2500);
    }
    return () => { if (poll.current) clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [detail?.turns.length, detail?.turns.map((t) => t.state).join(",")]);

  async function create() {
    setError(null);
    try {
      const c = await api<Conversation>("/api/conversations", {
        method: "POST", body: JSON.stringify({ connector: "web", agent }),
      });
      loadList();
      select(c.id);
    } catch (err) { setError(err instanceof Error ? err.message : "Create failed."); }
  }

  async function send() {
    if (!selected || !text.trim()) return;
    setBusy(true); setError(null);
    const msg = text;
    setText("");
    try {
      await api(`/api/conversations/${selected}/messages`, { method: "POST", body: JSON.stringify({ text: msg }) });
      loadDetail(selected);
      if (!poll.current) poll.current = setInterval(() => loadDetail(selected), 2500);
    } catch (err) { setError(err instanceof Error ? err.message : "Send failed."); setText(msg); }
    finally { setBusy(false); }
  }

  async function rename(id: string) {
    const title = nameDraft.trim();
    if (!title) return;
    try {
      const c = await api<Conversation>(`/api/conversations/${id}`, {
        method: "PATCH", body: JSON.stringify({ title }),
      });
      setRenaming(false);
      setDetail((d) => (d ? { ...d, title: c.title } : d));
      loadList();
    } catch (err) { setError(err instanceof Error ? err.message : "Rename failed."); }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
      setConfirmDelete(false);
      select(null);
      loadList();
    } catch (err) { setError(err instanceof Error ? err.message : "Delete failed."); }
    finally { setBusy(false); }
  }

  const thinking = detail?.turns.some((t) => ACTIVE.has(t.state)) ?? false;
  const web = detail ? isWeb(detail) : false;

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="chat-layout">
        <div className="chat-rail">
          <Button variant="secondary" className="mb-1.5 justify-start border-dashed text-accent"
                  onClick={create}>+ New conversation</Button>
          {list.map((c) => (
            <button
              key={c.id}
              className={selected === c.id ? "convo-item active" : "convo-item"}
              onClick={() => select(c.id)}
            >
              <div className="convo-item-title">{c.title}</div>
              <div className="convo-item-meta">
                <TypeBadge connector={c.connector} />
                <span className="convo-item-ts">{stamp(c.updated_at)}</span>
              </div>
            </button>
          ))}
          {list.length === 0 && <p className="muted" style={{ padding: "4px 6px" }}>No conversations yet.</p>}
        </div>

        <div className="convo-main">
          {!detail && (
            <div className="chat-empty muted">
              {selected ? "Loading…" : "Pick a conversation, or start a new one. Each turn is a tracked run."}
            </div>
          )}
          {detail && (
            <>
              <div className="convo-head">
                {renaming ? (
                  <span className="convo-rename">
                    <Input
                      value={nameDraft}
                      aria-label="Conversation title"
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.preventDefault(); rename(detail.id); }
                        if (e.key === "Escape") setRenaming(false);
                      }}
                      autoFocus
                    />
                    <Button size="sm" onClick={() => rename(detail.id)} disabled={!nameDraft.trim()}>Save</Button>
                    <Button size="sm" variant="secondary" onClick={() => setRenaming(false)}>Cancel</Button>
                  </span>
                ) : (
                  <>
                    <strong>{detail.title}</strong> <TypeBadge connector={detail.connector} />
                    <Button variant="link"
                            className="px-1.5 text-default no-underline opacity-60 hover:text-accent hover:no-underline hover:opacity-100"
                            onClick={() => { setNameDraft(detail.title); setRenaming(true); }}
                            title="Rename conversation">✎</Button>
                  </>
                )}
                {web && !renaming && (
                  <span className="convo-head-actions">
                    <Button variant="link" className="text-danger no-underline hover:underline"
                            onClick={() => setConfirmDelete(true)}>Delete</Button>
                  </span>
                )}
              </div>
              {!web && (
                <div className="convo-note muted">
                  This is a {detail.connector} conversation — reply from {detail.connector} to continue it.
                </div>
              )}
              <div className="convo-turns" ref={scroller}>
                {detail.turns.map((t) => (
                  <div key={t.run_id} className="convo-turn">
                    {t.user_message && (
                      <div className="convo-user">
                        {!web && <div className="convo-sender">{t.sender}</div>}
                        {t.user_message}
                      </div>
                    )}
                    <div className="convo-agent">
                      {t.result ?? (ACTIVE.has(t.state) ? <span className="muted">…thinking</span> : <span className="muted">({t.state})</span>)}
                    </div>
                  </div>
                ))}
                {detail.turns.length === 0 && <p className="muted">No messages yet{web ? " — say something." : "."}</p>}
              </div>
              {web && (
                <div className="convo-compose">
                  <Textarea
                    value={text}
                    aria-label="Message"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    placeholder={thinking ? "Waiting for the agent…" : "Message… (Enter to send, Shift+Enter for a new line)"}
                    rows={2}
                  />
                  <Button onClick={send} disabled={busy || thinking || !text.trim()}>Send</Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {detail && (
        <ConfirmDialog
          open={confirmDelete}
          title="Delete this conversation?"
          confirmLabel={busy ? "Deleting…" : "Delete permanently"}
          onConfirm={() => remove(detail.id)}
          onCancel={() => setConfirmDelete(false)}
        >
          “{detail.title}” and its {detail.turns.length} turn{detail.turns.length === 1 ? "" : "s"} will be
          permanently removed. The underlying run history is kept. This can't be undone.
        </ConfirmDialog>
      )}
    </>
  );
}
