import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { ChipButton, StatusChip } from "../ui/chip";
import { Select } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

const REFRESH_MS = 5000;

const ACTIVE_STATES = new Set(["queued", "dispatched", "running"]);

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
          <Select aria-label="Filter runs by tag" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">all</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </label>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <Table>
          <thead>
            <tr>
              <TH>ID</TH>
              <TH>Agent</TH>
              <TH>State</TH>
              <TH>Summary</TH>
              <TH>Tags</TH>
              <TH>Created</TH>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <TD><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></TD>
                <TD>{r.agent}</TD>
                <TD><StatusChip status={r.state} /></TD>
                <TD className="text-muted" title={r.summary ?? ""}>
                  {r.summary ? (r.summary.length > 70 ? r.summary.slice(0, 70) + "…" : r.summary) : "—"}
                </TD>
                <TD>
                  <span className="flex flex-wrap gap-1">
                    {(r.tags ?? []).map((t) => (
                      <ChipButton key={t} className="normal-case" onClick={() => setTag(t)}>{t}</ChipButton>
                    ))}
                  </span>
                </TD>
                <TD className="text-muted">{new Date(r.created_at).toLocaleString()}</TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
