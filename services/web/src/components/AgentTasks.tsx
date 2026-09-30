import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type TaskRow = { id: string; title: string; agent: string; creator_agent: string | null;
  run_at: string; outcome: string; model: string };

export default function AgentTasks({ agent }: { agent: string }) {
  const [rows, setRows] = useState<TaskRow[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api<TaskRow[]>(`/api/tasks?limit=200`).then((all) => setRows(all.filter((t) =>
      t.agent === agent || t.creator_agent === agent))).catch((e) => setError(String(e)));
  }, [agent]);
  const assigned = rows.filter((t) => t.agent === agent);
  const created = rows.filter((t) => t.creator_agent === agent);
  const list = (items: TaskRow[]) => items.length ? <ul>{items.map((t) =>
    <li key={t.id}><Link to={`/tasks?task=${t.id}`}>{t.title}</Link> · {t.outcome.replaceAll("_", " ")} · {new Date(t.run_at).toLocaleString()} · {t.model}</li>
  )}</ul> : <p className="muted">None yet.</p>;
  return <section><p><Link to={`/tasks?agent=${encodeURIComponent(agent)}`}>Open all Tasks for {agent}</Link></p>
    {error && <div className="error">{error}</div>}
    <h2>Assigned to {agent}</h2>{list(assigned)}
    <h2>Created by {agent}</h2>{list(created)}</section>;
}
