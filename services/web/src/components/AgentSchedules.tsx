import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cronTitle, isSingleExpression, useCronPreview, zoneOptions } from "../lib/cron";
import { CronBuilder, DEFAULT_CRON } from "./CronBuilder";
import { api, type Job, type ScheduleEntry } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Input, Textarea } from "@ap/ui/field";
import { Table, TD, TH } from "@ap/ui/table";

const when = (ts: string | null) => (ts ? new Date(ts).toLocaleString() : "—");

function Cron({ cron, timezone }: { cron: string; timezone?: string }) {
  // An agent's entrypoint crons arrive comma-joined into one cell; only a lone
  // expression can be described, so the rest are shown as written.
  const preview = useCronPreview(isSingleExpression(cron) ? cron : "", timezone, 0);
  return (
    <code className="cron" title={cronTitle(preview, timezone)}>
      {cron}{preview?.error && " ⚠"}
      {timezone && <span className="text-muted"> {timezone}</span>}
    </code>
  );
}

// Create/edit a job. The agent is fixed to this page's agent.
function JobForm({ agent, job, onDone, onCancel }: {
  agent: string; job?: Job; onDone: () => void; onCancel: () => void;
}) {
  const [name, setName] = useState(job?.name ?? "");
  const [cron, setCron] = useState(job?.cron || DEFAULT_CRON);
  const [timezone, setTimezone] = useState(job?.timezone ?? "");
  const [prompt, setPrompt] = useState(job?.prompt ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zones = zoneOptions();
  const zone = timezone.trim();
  const zoneOk = zone === "" || zones.length === 0 || zones.includes(zone);
  // A bad ZONE is reported at the timezone field, so the preview is asked in
  // UTC rather than answering a question about the schedule with a complaint
  // about a different field. Trimmed once, here: the builder and this hook must
  // agree on the cache key or the same schedule is fetched twice.
  const previewZone = zoneOk ? zone : "";
  // The builder's own preview line already says WHY an expression is bad; this
  // only decides whether Save is offered. An answer still in flight counts as
  // fine — a button that waits on a round-trip reads as broken, and the API
  // validates the write regardless.
  const cronPreview = useCronPreview(cron, previewZone);
  const cronOk = cron.trim() !== "" && !cronPreview?.error;

  async function save() {
    setBusy(true); setError(null);
    try {
      const body = { name, agent, cron, timezone: zone, prompt };
      if (job) await api(`/api/jobs/${job.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
      onDone();
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to save job."); }
    finally { setBusy(false); }
  }

  return (
    <div className="secret-editor">
      <label className="field-label">Name</label>
      <Input placeholder="e.g. morning-news" aria-label="Job name" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="field-label">Schedule</label>
      <CronBuilder value={cron} timezone={previewZone} onChange={setCron} label="Job schedule" />
      <label className="field-label">Timezone</label>
      <Input placeholder="UTC" aria-label="Timezone" list="tz-options" value={timezone}
             onChange={(e) => setTimezone(e.target.value)} />
      <datalist id="tz-options">{zones.map((z) => <option key={z} value={z} />)}</datalist>
      <div className={zoneOk ? "muted check-note" : "error"}>
        {zoneOk
          ? "Blank means UTC. Set an IANA zone (e.g. America/Toronto) to pin a job to wall-clock time across daylight saving."
          : "Unknown timezone — use an IANA name like America/Toronto."}
      </div>
      <label className="field-label">Prompt</label>
      <Textarea placeholder="What the agent should do each run…" aria-label="Job prompt" value={prompt} rows={4}
                onChange={(e) => setPrompt(e.target.value)} />
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <Button onClick={save} disabled={busy || !name.trim() || !cronOk || !zoneOk || !prompt.trim()}>
          {busy ? "Saving…" : job ? "Save" : "Create job"}
        </Button>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** Agent-scoped Schedules tab: this agent's cron jobs (1:many) plus its
 * cron entrypoints (declared in its definition). */
export default function AgentSchedules({ agent }: { agent: string }) {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [schedule, setSchedule] = useState<ScheduleEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);  // job id or "new"
  const [busy, setBusy] = useState<string | null>(null);

  function load() {
    setLoading(true);
    Promise.all([
      api<Job[]>("/api/jobs").then((all) => setJobs(all.filter((j) => j.agent === agent))),
      api<ScheduleEntry[]>("/api/schedules").then((all) => setSchedule(all.find((s) => s.agent === agent) ?? null)),
    ]).catch((err) => setError(err instanceof Error ? err.message : "Failed to load."))
      .finally(() => setLoading(false));
  }
  useEffect(() => { load(); setEditing(null); /* eslint-disable-next-line */ }, [agent]);
  function done() { setEditing(null); load(); }

  async function act(id: string, fn: () => Promise<unknown>) {
    setBusy(id); setError(null);
    try { await fn(); load(); }
    catch (err) { setError(err instanceof Error ? err.message : "Action failed."); }
    finally { setBusy(null); }
  }

  async function runJob(job: Job) {
    setBusy(job.id); setError(null);
    try {
      const run = await api<{ id: string }>(`/api/jobs/${job.id}/run`, { method: "POST" });
      navigate(`/runs/${run.id}`);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to run job."); setBusy(null); }
  }

  async function runSchedule() {
    setBusy("schedule"); setError(null);
    try {
      const run = await api<{ id: string }>("/api/runs", {
        method: "POST", body: JSON.stringify({ agent, prompt: "Scheduled run." }) });
      navigate(`/runs/${run.id}`);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to run."); setBusy(null); }
  }

  return (
    <>
      <div className="page-header">
        <h2>Jobs</h2>
        {editing !== "new" && <Button onClick={() => setEditing("new")}>+ New Job</Button>}
      </div>
      <p className="muted">A job runs {agent} on a cron with its own prompt. One agent can back many jobs.</p>
      {editing === "new" && <JobForm agent={agent} onDone={done} onCancel={() => setEditing(null)} />}
      {error && <div className="error">{error}</div>}
      {loading && <p className="muted">Loading…</p>}
      {!loading && jobs.length === 0 && editing !== "new" && <p className="muted">No jobs for this agent.</p>}
      {!loading && jobs.length > 0 && (
        <Table>
          <thead><tr><TH>Name</TH><TH>Cron</TH><TH>Next fire</TH><TH>Status</TH><TH></TH></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <TD>{j.name}</TD>
                <TD><Cron cron={j.cron} timezone={j.timezone} /></TD>
                <TD className="text-muted">{when(j.next_fire)}</TD>
                <TD>{j.enabled ? <Chip variant="ok">enabled</Chip> : <Chip variant="danger">disabled</Chip>}</TD>
                <TD>
                  <div className="row-actions">
                    <Button size="sm" variant="secondary" onClick={() => runJob(j)} disabled={busy === j.id}>Run now</Button>
                    <Button size="sm" variant="secondary" onClick={() => setEditing(editing === j.id ? null : j.id)}>
                      {editing === j.id ? "Close" : "Edit"}
                    </Button>
                    <Button size="sm" variant="secondary" disabled={busy === j.id} onClick={() =>
                      act(j.id, () => api(`/api/jobs/${j.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !j.enabled }) }))
                    }>{j.enabled ? "Disable" : "Enable"}</Button>
                    <Button size="sm" variant="secondary" disabled={busy === j.id} onClick={() => {
                      if (confirm(`Delete job "${j.name}"?`)) act(j.id, () => api(`/api/jobs/${j.id}`, { method: "DELETE" }));
                    }}>Delete</Button>
                  </div>
                </TD>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {editing && editing !== "new" && (() => {
        const j = jobs.find((x) => x.id === editing);
        return j ? <JobForm agent={agent} job={j} onDone={done} onCancel={() => setEditing(null)} /> : null;
      })()}

      {schedule && (
        <section style={{ marginTop: 20 }}>
          <h2>Entrypoint cron</h2>
          <p className="muted">Part of the agent's definition — edit it under <em>Config → Entrypoints</em>.</p>
          <Table>
            <thead><tr><TH>Cron</TH><TH>Next fire</TH><TH>Last fire</TH><TH>Status</TH><TH></TH></tr></thead>
            <tbody>
              <tr>
                <TD><Cron cron={schedule.cron} /></TD>
                <TD className="text-muted">{when(schedule.next_fire)}</TD>
                <TD className="text-muted">{when(schedule.last_fire)}</TD>
                <TD>{schedule.enabled ? <Chip variant="ok">enabled</Chip> : <Chip variant="danger">disabled</Chip>}</TD>
                <TD>
                  <div className="row-actions">
                    <Button size="sm" variant="secondary" onClick={runSchedule} disabled={busy === "schedule"}>Run now</Button>
                    <Button size="sm" variant="secondary" disabled={busy === "schedule"} onClick={() =>
                      act("schedule", () => api(`/api/schedules/${encodeURIComponent(agent)}/${schedule.enabled ? "disable" : "enable"}`, { method: "POST" }))
                    }>{schedule.enabled ? "Disable" : "Enable"}</Button>
                  </div>
                </TD>
              </tr>
            </tbody>
          </Table>
        </section>
      )}
    </>
  );
}
