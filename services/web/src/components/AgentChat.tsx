import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Conversation, type ConversationDetail } from "../api";

const ACTIVE = new Set(["queued", "dispatched", "running"]);

// Only conversations the platform originated (web) can be continued or deleted
// from the UI. Connector conversations (Discord, …) are owned by their channel.
const isWeb = (c: { connector: string }) => c.connector === "web";

function TypeBadge({ connector }: { connector: string }) {
  return <span className={`convo-type convo-type-${connector}`}>{connector}</span>;
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
          <button className="chat-new" onClick={create}>+ New conversation</button>
          {list.map((c) => (
            <button
              key={c.id}
              className={selected === c.id ? "convo-item active" : "convo-item"}
              onClick={() => select(c.id)}
            >
              <div className="convo-item-title">{c.title}</div>
              <TypeBadge connector={c.connector} />
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
                <strong>{detail.title}</strong> <TypeBadge connector={detail.connector} />
                {web && (
                  <span className="convo-head-actions">
                    <button className="danger-ghost" onClick={() => setConfirmDelete(true)}>Delete</button>
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
                  <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    placeholder={thinking ? "Waiting for the agent…" : "Message… (Enter to send, Shift+Enter for a new line)"}
                    rows={2}
                  />
                  <button onClick={send} disabled={busy || thinking || !text.trim()}>Send</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {confirmDelete && detail && (
        <div className="modal-backdrop" onClick={() => setConfirmDelete(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Delete this conversation?</h2>
            <p className="muted">
              “{detail.title}” and its {detail.turns.length} turn{detail.turns.length === 1 ? "" : "s"} will be
              permanently removed. The underlying run history is kept. This can't be undone.
            </p>
            <div className="row-actions" style={{ justifyContent: "flex-end" }}>
              <button className="secondary" onClick={() => setConfirmDelete(false)}>Cancel</button>
              <button className="danger" onClick={() => remove(detail.id)} disabled={busy}>
                {busy ? "Deleting…" : "Delete permanently"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
