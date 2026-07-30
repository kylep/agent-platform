import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Conversation, type ConversationDetail } from "../api";

const ACTIVE = new Set(["queued", "dispatched", "running"]);

/** ChatGPT-style conversation view scoped to one agent: a rail of this agent's
 * conversations plus a chat pane. The selected conversation lives in the
 * `?conversation=` search param so /conversations rows can deep-link here. */
export default function AgentChat({ agent }: { agent: string }) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("conversation");
  const [list, setList] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    if (selected) {
      loadDetail(selected);
      // keep polling while a turn is in flight (cleared once idle)
      poll.current = setInterval(() => loadDetail(selected), 2500);
    }
    return () => { if (poll.current) clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // pin the transcript to the bottom as turns stream in
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

  async function close(id: string) {
    await api(`/api/conversations/${id}`, { method: "DELETE" }).catch(() => {});
    loadList();
    if (selected === id) loadDetail(id);
  }

  const thinking = detail?.turns.some((t) => ACTIVE.has(t.state)) ?? false;

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
              <div className="muted">{c.connector} · {c.status}</div>
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
                <strong>{detail.title}</strong> <span className="muted">({detail.status})</span>
                {detail.status === "active" && (
                  <button className="secondary" style={{ float: "right" }} onClick={() => close(detail.id)}>Close</button>
                )}
              </div>
              <div className="convo-turns" ref={scroller}>
                {detail.turns.map((t) => (
                  <div key={t.run_id} className="convo-turn">
                    {t.user_message && <div className="convo-user">{t.user_message}</div>}
                    <div className="convo-agent">
                      {t.result ?? (ACTIVE.has(t.state) ? <span className="muted">…thinking</span> : <span className="muted">({t.state})</span>)}
                    </div>
                  </div>
                ))}
                {detail.turns.length === 0 && <p className="muted">No messages yet — say something.</p>}
              </div>
              {detail.status === "active" && (
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
    </>
  );
}
