import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Connector, type Conversation } from "../api";

/** Index of every conversation across agents. Clicking a row opens it in the
 * agent's Conversations tab (the chat view), which is also where new
 * conversations are started. */
export default function Conversations() {
  const navigate = useNavigate();
  const [list, setList] = useState<Conversation[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api<Conversation[]>("/api/conversations")
      .then((all) => setList(all.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))))
      .catch(() => {})
      .finally(() => setLoaded(true));
    api<Connector[]>("/api/connectors").then(setConnectors).catch(() => {});
  }, []);

  const when = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");
  const open = (c: Conversation) =>
    navigate(`/agents/${encodeURIComponent(c.agent)}?tab=conversations&conversation=${encodeURIComponent(c.id)}`);

  return (
    <div className="page">
      <h1>Conversations</h1>
      <p className="muted">
        Multi-turn threads with an agent; each turn is a tracked run. To start a new one, open an
        agent's <em>Conversations</em> tab (or mention the bot on a connected channel like Discord).
      </p>

      <table className="table">
        <thead>
          <tr><th>Title</th><th>Agent</th><th>Type</th><th>Updated</th></tr>
        </thead>
        <tbody>
          {list.map((c) => (
            <tr key={c.id} className="clickable-row" onClick={() => open(c)}>
              <td>{c.title}</td>
              <td>
                <Link to={`/agents/${encodeURIComponent(c.agent)}`} onClick={(e) => e.stopPropagation()}>
                  {c.agent}
                </Link>
              </td>
              <td><span className={`convo-type convo-type-${c.connector}`}>{c.connector}</span></td>
              <td className="muted">{when(c.updated_at)}</td>
            </tr>
          ))}
          {loaded && list.length === 0 && (
            <tr><td colSpan={4} className="muted">No conversations yet — open an agent and start one.</td></tr>
          )}
        </tbody>
      </table>

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
