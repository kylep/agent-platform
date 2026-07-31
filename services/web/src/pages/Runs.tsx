import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AgentSummary, type RunSummary } from "../api";
import { Button } from "../ui/button";
import { ChipButton, StatusChip } from "../ui/chip";
import { Select } from "../ui/field";
import { Table, TD, TH } from "../ui/table";

const REFRESH_MS = 5000;
const PAGE = 50;

const ACTIVE_STATES = new Set(["queued", "dispatched", "running"]);
const ALL_STATES = ["queued", "dispatched", "running", "succeeded", "failed",
                    "rejected", "killed", "timeout", "dlq"];

export function isActiveState(state: string): boolean {
  return ACTIVE_STATES.has(state);
}

export default function Runs() {
  const [params, setParams] = useSearchParams();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [count, setCount] = useState(PAGE);       // how many rows are shown
  const [atEnd, setAtEnd] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const tag = params.get("tag") ?? "";
  const agent = params.get("agent") ?? "";
  const state = params.get("state") ?? "";

  function setFilter(key: string, value: string) {
    const p = new URLSearchParams(params);
    if (value) p.set(key, value); else p.delete(key);
    setParams(p);
    setCount(PAGE);
    setAtEnd(false);
  }

  useEffect(() => { api<string[]>("/api/tags").then(setTags).catch(() => {}); }, [runs.length]);
  useEffect(() => {
    api<AgentSummary[]>("/api/agents")
      .then((a) => setAgents(a.map((x) => x.name)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    function load() {
      const q = new URLSearchParams({ limit: String(count) });
      if (tag) q.set("tag", tag);
      if (agent) q.set("agent", agent);
      if (state) q.set("state", state);
      api<RunSummary[]>(`/api/runs?${q}`)
        .then((data) => {
          if (cancelled) return;
          setRuns(data);
          setAtEnd(data.length < count);
          setError(null);
        })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load runs."); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(interval); };
  }, [tag, agent, state, count]);

  // A run's agent may be deleted; keep it selectable so its history is reachable.
  const agentOptions = [...new Set([...agents, ...(agent ? [agent] : [])])].sort();

  return (
    <div className="page page-wide">
      <h1>Runs</h1>
      <div className="form-row">
        <label className="muted">Agent{" "}
          <Select aria-label="Filter runs by agent" value={agent}
                  onChange={(e) => setFilter("agent", e.target.value)}>
            <option value="">all</option>
            {agentOptions.map((a) => <option key={a} value={a}>{a}</option>)}
          </Select>
        </label>
        <label className="muted">State{" "}
          <Select aria-label="Filter runs by state" value={state}
                  onChange={(e) => setFilter("state", e.target.value)}>
            <option value="">all</option>
            {ALL_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </label>
        <label className="muted">Tag{" "}
          <Select aria-label="Filter runs by tag" value={tag}
                  onChange={(e) => setFilter("tag", e.target.value)}>
            <option value="">all</option>
            {tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </label>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <>
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
                  <TD className="whitespace-nowrap">{r.agent}</TD>
                  <TD><StatusChip status={r.state} /></TD>
                  <TD className="text-muted" title={r.summary ?? ""}>
                    {r.summary ? (r.summary.length > 90 ? r.summary.slice(0, 90) + "…" : r.summary) : "—"}
                  </TD>
                  <TD>
                    <span className="flex flex-wrap gap-1">
                      {(r.tags ?? []).map((t) => (
                        <ChipButton key={t} className="normal-case" onClick={() => setFilter("tag", t)}>{t}</ChipButton>
                      ))}
                    </span>
                  </TD>
                  <TD className="whitespace-nowrap text-muted">{new Date(r.created_at).toLocaleString()}</TD>
                </tr>
              ))}
              {runs.length === 0 && <tr><TD colSpan={6} className="text-muted">No runs match.</TD></tr>}
            </tbody>
          </Table>
          {!atEnd && (
            <div className="row-actions" style={{ marginTop: 10 }}>
              <Button variant="secondary" onClick={() => setCount((c) => c + PAGE)}>
                Load {PAGE} more
              </Button>
              <span className="muted">showing {runs.length}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
