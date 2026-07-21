import { useEffect, useRef, useState } from "react";
import { api, type AgentSummary, type Connector, type Conversation, type ConversationDetail } from "../api";

const ACTIVE = new Set(["queued", "dispatched", "running"]);

export default function Conversations() {
  const [list, setList] = useState<Conversation[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [newAgent, setNewAgent] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  function loadList() {
    api<Conversation[]>("/api/conversations").then(setList).catch(() => {});
  }
  useEffect(() => {
    loadList();
    api<AgentSummary[]>("/api/agents").then((a) => { setAgents(a); if (a.length) setNewAgent(a[0].name); }).catch(() => {});
    api<Connector[]>("/api/connectors").then(setConnectors).catch(() => {});
  }, []);

  function loadDetail(id: string) {
    api<ConversationDetail>(`/api/conversations/${id}`).then((d) => {
      setDetail(d);
      // keep polling while the latest turn is still running
      const active = d.turns.some((t) => ACTIVE.has(t.state));
      if (!active && poll.current) { clearInterval(poll.current); poll.current = null; }
    }).catch(() => {});
  }
  useEffect(() => {
    if (poll.current) { clearInterval(poll.current); poll.current = null; }
    if (selected) loadDetail(selected);
    return () => { if (poll.current) clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  async function create() {
    setError(null);
    try {
      const c = await api<Conversation>("/api/conversations", {
        method: "POST", body: JSON.stringify({ connector: "web", agent: newAgent }),
      });
      loadList();
      setSelected(c.id);
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
      if (!poll.current) poll.current = setInterval(() => selected && loadDetail(selected), 2500);
    } catch (err) { setError(err instanceof Error ? err.message : "Send failed."); setText(msg); }
    finally { setBusy(false); }
  }

  async function close(id: string) {
    await api(`/api/conversations/${id}`, { method: "DELETE" }).catch(() => {});
    loadList();
    if (selected === id) loadDetail(id);
  }

  const thinking = detail?.turns.some((t) => ACTIVE.has(t.state));

  return (
    <div className="page">
      <h1>Conversations</h1>
      <p className="muted">
        Multi-turn threads with an agent. Create one here (web connector), or connect Discord. Each
        turn is a tracked run.
      </p>
      {error && <div className="error">{error}</div>}

      <div className="convo-layout">
        <div className="convo-list">
          <div className="row-actions" style={{ marginBottom: 8 }}>
            <select value={newAgent} onChange={(e) => setNewAgent(e.target.value)}>
              {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
            </select>
            <button onClick={create}>New</button>
          </div>
          {list.map((c) => (
            <button
              key={c.id}
              className={selected === c.id ? "convo-item active" : "convo-item"}
              onClick={() => setSelected(c.id)}
            >
              <div>{c.title}</div>
              <div className="muted">{c.connector} · {c.agent} · {c.status}</div>
            </button>
          ))}
          {list.length === 0 && <p className="muted">No conversations yet.</p>}
        </div>

        <div className="convo-main">
          {!detail && <p className="muted">Select or create a conversation.</p>}
          {detail && (
            <>
              <div className="convo-head">
                <strong>{detail.title}</strong> <span className="muted">({detail.agent} · {detail.status})</span>
                {detail.status === "active" && (
                  <button className="secondary" style={{ float: "right" }} onClick={() => close(detail.id)}>Close</button>
                )}
              </div>
              <div className="convo-turns">
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
                    placeholder={thinking ? "Waiting for the agent…" : "Message… (Enter to send)"}
                    rows={2}
                  />
                  <button onClick={send} disabled={busy || thinking || !text.trim()}>Send</button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <h2>Connectors</h2>
      <div className="chip-row">
        {connectors.map((c) => (
          <span key={c.name} className={c.implemented ? "chip chip-ok" : "chip"} title={c.description}>
            {c.name}{c.implemented ? "" : " — NYI"}
          </span>
        ))}
      </div>
    </div>
  );
}
