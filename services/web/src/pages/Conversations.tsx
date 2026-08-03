import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { convoTitle } from "../lib/convo";
import { api, type Connector, type Conversation } from "../api";
import { Chip } from "@ap/ui/chip";
import { Table, TD, TH } from "@ap/ui/table";

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

      <Table>
        <thead>
          <tr><TH>Title</TH><TH>Agent</TH><TH>Type</TH><TH>Updated</TH></tr>
        </thead>
        <tbody>
          {list.map((c) => (
            <tr key={c.id} className="clickable-row" onClick={() => open(c)}>
              <TD>
                <Link to={`/agents/${encodeURIComponent(c.agent)}?tab=conversations&conversation=${encodeURIComponent(c.id)}`}
                      className="text-default" onClick={(e) => e.stopPropagation()}>
                  {convoTitle(c)}
                </Link>
              </TD>
              <TD>
                <Link to={`/agents/${encodeURIComponent(c.agent)}`} onClick={(e) => e.stopPropagation()}>
                  {c.agent}
                </Link>
              </TD>
              <TD><span className={`convo-type convo-type-${c.connector}`}>{c.connector}</span></TD>
              <TD className="text-muted">{when(c.updated_at)}</TD>
            </tr>
          ))}
          {loaded && list.length === 0 && (
            <tr><TD colSpan={4} className="text-muted">No conversations yet — open an agent and start one.</TD></tr>
          )}
        </tbody>
      </Table>

      <h2>Connectors</h2>
      <div className="chip-row">
        {connectors.map((c) => (
          <Chip key={c.name} variant={c.implemented ? "ok" : "neutral"} title={c.description}>
            {c.name}{c.implemented ? "" : " — not yet implemented"}
          </Chip>
        ))}
      </div>
    </div>
  );
}
