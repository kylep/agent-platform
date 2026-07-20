import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";

const REFRESH_MS = 5000;

const ACTIVE_STATES = new Set(["queued", "dispatched", "running"]);
const OK_STATES = new Set(["succeeded"]);

export function stateChipClass(state: string): string {
  if (OK_STATES.has(state)) return "chip-ok";
  if (ACTIVE_STATES.has(state)) return "chip-unprobed";
  return "chip-invalid";
}

export function isActiveState(state: string): boolean {
  return ACTIVE_STATES.has(state);
}

export default function Runs() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [tag, setTag] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api<string[]>("/api/tags").then(setTags).catch(() => {}); }, [runs.length]);

  useEffect(() => {
    let cancelled = false;
    function load() {
      const q = tag ? `/api/runs?limit=50&tag=${encodeURIComponent(tag)}` : "/api/runs?limit=50";
      api<RunSummary[]>(q)
        .then((data) => { if (!cancelled) { setRuns(data); setError(null); } })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load runs."); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [tag]);

  return (
    <div className="page">
      <h1>Runs</h1>
      <div className="form-row">
        <label className="muted">Filter by tag:{" "}
          <select value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">all</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <table className="table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Agent</th>
              <th>State</th>
              <th>Summary</th>
              <th>Tags</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></td>
                <td>{r.agent}</td>
                <td><span className={`chip ${stateChipClass(r.state)}`}>{r.state}</span></td>
                <td className="muted" title={r.summary ?? ""}>
                  {r.summary ? (r.summary.length > 70 ? r.summary.slice(0, 70) + "…" : r.summary) : "—"}
                </td>
                <td>{(r.tags ?? []).map((t) => (
                  <button key={t} className="tag" onClick={() => setTag(t)}>{t}</button>
                ))}</td>
                <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
