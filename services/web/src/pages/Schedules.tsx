import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import cronstrue from "cronstrue";
import { api, type AgentSummary, type Job, type ScheduleEntry } from "../api";

function when(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString() : "—";
}

// Plain-English cron, or null if it can't be parsed.
function cronText(cron: string): string | null {
  try {
    return cronstrue.toString(cron, { throwExceptionOnParseError: true });
  } catch {
    return null;
  }
}

// A cron cell with a plain-English tooltip on hover.
function Cron({ cron }: { cron: string }) {
  const text = cronText(cron);
  return (
    <code className="cron" title={text ?? "unrecognized cron expression"}>
      {cron}{!text && " ⚠"}
    </code>
  );
}

function useAgents() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  useEffect(() => { api<AgentSummary[]>("/api/agents").then(setAgents).catch(() => {}); }, []);
  return agents;
}

// Create or edit a Scheduled Job.
function JobForm({ agents, job, onDone, onCancel }: {
  agents: AgentSummary[]; job?: Job; onDone: () => void; onCancel: () => void;
}) {
  const [name, setName] = useState(job?.name ?? "");
  const [agent, setAgent] = useState(job?.agent ?? "");
  const [cron, setCron] = useState(job?.cron ?? "");
  const [prompt, setPrompt] = useState(job?.prompt ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preview = cron.trim() ? cronText(cron) : null;
  const cronOk = cron.trim() !== "" && preview !== null;
  const effectiveAgent = agent || agents[0]?.name || "";

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = { name, agent: effectiveAgent, cron, prompt };
      if (job) await api(`/api/jobs/${job.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save job.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="secret-editor">
      <label className="field-label">Name</label>
      <input placeholder="e.g. morning-news" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="field-label">Agent</label>
      <select value={effectiveAgent} onChange={(e) => setAgent(e.target.value)}>
        {agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
      </select>
      <label className="field-label">Cron (UTC)</label>
      <input placeholder="e.g. 0 11 * * *" value={cron} onChange={(e) => setCron(e.target.value)} />
      <div className={cron.trim() === "" || cronOk ? "muted check-note" : "error"}>
        {cron.trim() === "" ? "5-field cron, evaluated in UTC." : cronOk ? `→ ${preview}` : "Unrecognized cron expression."}
      </div>
      <label className="field-label">Prompt</label>
      <textarea placeholder="What the agent should do each run…" value={prompt} rows={4}
                onChange={(e) => setPrompt(e.target.value)} />
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <button onClick={save} disabled={busy || !name.trim() || !cronOk || !prompt.trim()}>
          {busy ? "Saving…" : job ? "Save" : "Create job"}
        </button>
        <button className="secondary" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function Jobs({ agents }: { agents: AgentSummary[] }) {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);   // job id, or "new"
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    api<Job[]>("/api/jobs").then(setJobs)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load jobs."))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);
  function done() { setEditing(null); load(); }

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusy(id); setError(null);
    try { await fn(); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Action failed."); }
    finally { setBusy(null); }
  }

  async function runNow(job: Job) {
    setBusy(job.id); setError(null);
    try {
      const run = await api<{ id: string }>(`/api/jobs/${job.id}/run`, { method: "POST" });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run job.");
      setBusy(null);
    }
  }

  return (
    <section>
      <div className="page-header">
        <h2>Jobs</h2>
        {editing !== "new" && <button onClick={() => setEditing("new")}>+ New Job</button>}
      </div>
      <p className="muted">
        A job runs an agent on a cron with its own prompt. One agent can back many jobs (1:many).
      </p>
      {editing === "new" && (
        <JobForm agents={agents} onDone={done} onCancel={() => setEditing(null)} />
      )}
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && jobs.length === 0 && editing !== "new" && <p className="muted">No jobs yet.</p>}
      {!loading && jobs.length > 0 && (
        <table className="table">
          <thead>
            <tr><th>Name</th><th>Agent</th><th>Cron</th><th>Next fire</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.name}</td>
                <td><Link to={`/agents/${encodeURIComponent(j.agent)}`}>{j.agent}</Link></td>
                <td><Cron cron={j.cron} /></td>
                <td className="muted">{when(j.next_fire)}</td>
                <td>{j.enabled
                  ? <span className="chip chip-ok">enabled</span>
                  : <span className="chip chip-invalid">disabled</span>}</td>
                <td>
                  <div className="row-actions">
                    <button className="secondary" onClick={() => runNow(j)} disabled={busy === j.id}>Run now</button>
                    <button className="secondary" onClick={() => setEditing(editing === j.id ? null : j.id)}>
                      {editing === j.id ? "Close" : "Edit"}
                    </button>
                    <button className="secondary" onClick={() =>
                      act(j.id, () => api(`/api/jobs/${j.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !j.enabled }) }))
                    } disabled={busy === j.id}>{j.enabled ? "Disable" : "Enable"}</button>
                    <button className="secondary" onClick={() => {
                      if (confirm(`Delete job "${j.name}"?`)) act(j.id, () => api(`/api/jobs/${j.id}`, { method: "DELETE" }));
                    }} disabled={busy === j.id}>Delete</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editing && editing !== "new" && (() => {
        const j = jobs.find((x) => x.id === editing);
        return j ? <JobForm agents={agents} job={j} onDone={done} onCancel={() => setEditing(null)} /> : null;
      })()}
    </section>
  );
}

// Manifest-declared schedules (1:1, e.g. system agents). Read-only cron here;
// change it in the agent's manifest. Enable/disable + Run now are available.
function ManifestSchedules() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<ScheduleEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    api<ScheduleEntry[]>("/api/schedules").then(setRows)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load schedules."))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function toggle(agent: string, enabled: boolean) {
    setBusy(agent); setError(null);
    try {
      await api(`/api/schedules/${encodeURIComponent(agent)}/${enabled ? "disable" : "enable"}`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update schedule.");
    } finally { setBusy(null); }
  }

  async function runNow(agent: string) {
    setBusy(agent); setError(null);
    try {
      const run = await api<{ id: string }>("/api/runs", {
        method: "POST", body: JSON.stringify({ agent, prompt: "Scheduled run." }),
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run.");
      setBusy(null);
    }
  }

  if (!loading && rows.length === 0) return null;
  return (
    <section>
      <h2>Manifest schedules</h2>
      <p className="muted">Declared in each agent's manifest (<code>schedule:</code>). Edit the cron in the manifest.</p>
      {error && <div className="error">{error}</div>}
      {!loading && (
        <table className="table">
          <thead>
            <tr><th>Agent</th><th>Cron</th><th>Next fire</th><th>Last fire</th><th>Status</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent}>
                <td><Link to={`/agents/${encodeURIComponent(r.agent)}`}>{r.agent}</Link></td>
                <td><Cron cron={r.cron} /></td>
                <td className="muted">{when(r.next_fire)}</td>
                <td className="muted">{when(r.last_fire)}</td>
                <td>{r.enabled
                  ? <span className="chip chip-ok">enabled</span>
                  : <span className="chip chip-invalid">disabled</span>}</td>
                <td>
                  <div className="row-actions">
                    <button className="secondary" onClick={() => runNow(r.agent)} disabled={busy === r.agent}>Run now</button>
                    <button className="secondary" onClick={() => toggle(r.agent, r.enabled)} disabled={busy === r.agent}>
                      {r.enabled ? "Disable" : "Enable"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function Schedules() {
  const agents = useAgents();
  return (
    <div className="page">
      <h1>Schedules</h1>
      <p className="muted">Recurring work. Hover a cron to read it in plain English.</p>
      <Jobs agents={agents} />
      <ManifestSchedules />
    </div>
  );
}
