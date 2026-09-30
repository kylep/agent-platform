import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AgentSummary } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Select } from "@ap/ui/field";
import { useTitle } from "../lib/title";

type Task = { id: string; title: string; agent: string; prompt: string; runtime: string;
  model: string; run_at: string; expires_at: string; timezone: string; creator: string;
  creator_agent: string | null; status: string; outcome: string; run_id: string | null;
  run_state: string | null; reason: string | null; version: number; delivery: string;
  created_at: string; fired_at: string | null };
type Event = { kind: string; actor: string; reason: string | null; run_id: string | null;
  created_at: string };
type Models = { runtime: string; default: string; models: { id: string; label: string }[] };
type Grant = { creator: string; target: string };
const when = (s: string) => new Date(s).toLocaleString();
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
function wallCandidates(local: string): string[] {
  if (!local) return [];
  const first = new Date(local).getTime();
  if (!Number.isFinite(first)) return [];
  const format = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const matches: string[] = [];
  for (let delta = -120; delta <= 120; delta += 30) {
    const d = new Date(first + delta * 60000);
    const fields = Object.fromEntries(format.formatToParts(d).map((p) => [p.type, p.value]));
    const wall = `${fields.year}-${fields.month}-${fields.day}T${fields.hour}:${fields.minute}`;
    if (wall === local && !matches.includes(d.toISOString())) matches.push(d.toISOString());
  }
  return matches.sort();
}
const label: Record<string, string> = { upcoming: "Upcoming", starting: "Starting", running: "Running",
  done: "Done", failed: "Failed", didnt_run: "Didn't run" };

export default function Tasks() {
  useTitle("Tasks");
  const [params, setParams] = useSearchParams();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [selected, setSelected] = useState<Task | null>(null);
  const [creating, setCreating] = useState(false);
  const [agent, setAgent] = useState("");
  const [models, setModels] = useState<Models | null>(null);
  const [model, setModel] = useState("");
  const [title, setTitle] = useState("One-time run");
  const [prompt, setPrompt] = useState("");
  const [localTime, setLocalTime] = useState("");
  const [timeChoice, setTimeChoice] = useState(0);
  const [editing, setEditing] = useState<Task | null>(null);
  const [lateMinutes, setLateMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [grantCreator, setGrantCreator] = useState("");
  const [grantTarget, setGrantTarget] = useState("");
  const filter = params.get("view") || "upcoming";
  const agentFilter = params.get("agent") || "";

  function load() {
    api<Task[]>("/api/tasks?limit=200").then((rows) => { setTasks(rows);
      setSelected((old) => old ? rows.find((t) => t.id === old.id) || old : null);
    }).catch((e) => setError(String(e)));
  }
  useEffect(() => {
    load();
    api<AgentSummary[]>("/api/agents").then(setAgents).catch(() => {});
    api<Grant[]>("/api/tasks/grants").then(setGrants).catch(() => {});
    const id = window.setInterval(load, 15000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (!agent) { setModels(null); return; }
    api<Models>(`/api/tasks/models?agent=${encodeURIComponent(agent)}`)
      .then((x) => { setModels(x); setModel(x.default); }).catch((e) => setError(String(e)));
  }, [agent]);
  useEffect(() => {
    const id = params.get("task");
    if (id && tasks.length && selected?.id !== id) setSelected(tasks.find((t) => t.id === id) || null);
  }, [tasks, params, selected?.id]);
  useEffect(() => {
    if (!selected) { setEvents([]); return; }
    api<Event[]>(`/api/tasks/${selected.id}/events`).then(setEvents).catch(() => {});
  }, [selected?.id, selected?.version, selected?.run_state]);

  const candidateTimes = useMemo(() => wallCandidates(localTime), [localTime]);
  const chosenTime = candidateTimes[timeChoice] || candidateTimes[0];

  const shown = useMemo(() => tasks.filter((t) => {
    if (agentFilter && t.agent !== agentFilter && t.creator_agent !== agentFilter) return false;
    if (filter === "upcoming") return t.outcome === "upcoming" || t.outcome === "starting" || t.outcome === "running";
    if (filter === "attention") return t.outcome === "failed" || t.outcome === "didnt_run";
    return t.outcome === "done" || t.outcome === "failed" || t.outcome === "didnt_run";
  }).sort((a, b) => filter === "upcoming" ? a.run_at.localeCompare(b.run_at) : b.run_at.localeCompare(a.run_at)), [tasks, filter, agentFilter]);

  async function create() {
    if (!agent || !chosenTime || !prompt.trim()) return;
    setBusy(true); setError(null);
    try {
      if (editing) {
        await api<Task>(`/api/tasks/${editing.id}`, { method: "PATCH", body: JSON.stringify({
          version: editing.version, model, title, prompt, run_at: chosenTime,
          timezone: zone, late_minutes: lateMinutes }) });
      } else {
        await api<Task>("/api/tasks", { method: "POST", body: JSON.stringify({ agent, model, title,
          prompt, run_at: chosenTime, timezone: zone, late_minutes: lateMinutes }) });
      }
      setCreating(false); setEditing(null); setPrompt(""); setLocalTime(""); load();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function cancel(t: Task) {
    setBusy(true); setError(null);
    try { await api<Task>(`/api/tasks/${t.id}/cancel`, { method: "POST" }); setSelected(null); load(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function addGrant() {
    try { await api<Grant>("/api/tasks/grants", { method: "POST", body: JSON.stringify({
      creator: grantCreator, target: grantTarget }) });
      setGrants(await api<Grant[]>("/api/tasks/grants"));
    } catch (e) { setError(String(e)); }
  }
  return <div className="page page-wide">
    <div className="row-actions" style={{ justifyContent: "space-between", alignItems: "center" }}>
      <div><h1>Tasks</h1><p className="muted">One-time scheduled agent runs. For recurring work, use <Link to="/schedules">Schedules</Link>.</p></div>
      <Button onClick={() => { setCreating(true); setEditing(null); setSelected(null); setError(null); }}>+ New Task</Button>
    </div>
    {error && <div className="error" role="alert">{error}</div>}
    {creating ? <section className="task-panel task-form" style={{ maxWidth: 760 }}>
      <div className="row-actions" style={{ justifyContent: "space-between" }}><h2>{editing ? "Edit Task" : "Create a one-time run"}</h2><Button variant="secondary" onClick={() => setCreating(false)}>Close</Button></div>
      <label>Agent</label><Select value={agent} disabled={!!editing} onChange={(e) => setAgent(e.target.value)}><option value="">Choose an agent</option>{agents.filter((a) => !a.system && a.enabled !== false).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}</Select>
      <label>Model</label><Select value={model} onChange={(e) => setModel(e.target.value)} disabled={!models}>{models?.models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</Select>
      {models && <p className="muted">{models.runtime} runtime · default {models.default}</p>}
      <label>Title</label><input value={title} maxLength={128} onChange={(e) => setTitle(e.target.value)} />
      <label>Prompt</label><textarea rows={6} maxLength={20000} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <p className="muted">{prompt.length}/20,000 characters</p>
      <label>Run once at</label><input type="datetime-local" value={localTime} onChange={(e) => { setLocalTime(e.target.value); setTimeChoice(0); }} />
      {localTime && candidateTimes.length === 0 && <p className="error">This wall-clock time does not exist in {zone}; choose another time.</p>}
      {candidateTimes.length > 1 && <><label>Which occurrence of this daylight-saving time?</label><Select value={timeChoice} onChange={(e) => setTimeChoice(Number(e.target.value))}>{candidateTimes.map((c, i) => <option value={i} key={c}>{new Date(c).toLocaleString(undefined, { timeZoneName: "short" })} · {c}</option>)}</Select></>}
      <p className="muted">{chosenTime ? `${when(chosenTime)} · ${zone} · ${chosenTime}` : "Choose a local date and time."}</p>
      <label>Run if late by up to (minutes)</label><input type="number" min={1} max={1440} value={lateMinutes} onChange={(e) => setLateMinutes(Number(e.target.value))} />
      <p className="muted">Results appear in this Task's log. External chats do not receive an automatic reply.</p>
      <Button disabled={busy || !agent || !chosenTime || !prompt.trim()} onClick={create}>{editing ? "Save Task" : "Schedule Task"}</Button>
    </section> : null}
    <div className="row-actions" style={{ margin: "18px 0" }}>
      {([ ["upcoming", "Upcoming"], ["past", "Past"], ["attention", "Needs attention"] ] as const).map(([key, text]) => <Button key={key} variant={filter === key ? "primary" : "secondary"} onClick={() => { const n = new URLSearchParams(params); n.set("view", key); setParams(n); }}>{text}</Button>)}
      <Select aria-label="Filter by agent" value={agentFilter} onChange={(e) => { const n = new URLSearchParams(params); if (e.target.value) n.set("agent", e.target.value); else n.delete("agent"); setParams(n); }}><option value="">All agents</option>{agents.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}</Select>
    </div>
    {shown.length === 0 ? <div className="task-panel"><p>No {filter === "attention" ? "Tasks need attention" : "Tasks here yet"}.</p><Button onClick={() => setCreating(true)}>Create a one-time run</Button></div> :
      <div className="task-grid">{shown.map((t) => <button type="button" className="task-card" key={t.id} onClick={() => { setSelected(t); setCreating(false); }}>
        <span className="task-card-top"><strong>{t.title}</strong><Chip variant={t.outcome === "failed" || t.outcome === "didnt_run" ? "danger" : "ok"}>{label[t.outcome]}</Chip></span>
        <span>{t.agent} · {t.model}</span><span className="muted">{when(t.run_at)}</span>
        <span className="muted">Requested by {t.creator}</span>
      </button>)}</div>}
    {selected && <section className="panel task-detail">
      <div className="row-actions" style={{ justifyContent: "space-between" }}><h2>{selected.title}</h2><Button variant="secondary" onClick={() => setSelected(null)}>Close</Button></div>
      <p><Chip variant={selected.outcome === "failed" || selected.outcome === "didnt_run" ? "danger" : "ok"}>{label[selected.outcome]}</Chip> {selected.reason && <span className="error">{selected.reason}</span>}</p>
      <p>Runs once at <strong>{when(selected.run_at)}</strong> ({selected.timezone}); latest start {when(selected.expires_at)}.</p>
      <p>{selected.agent} · {selected.runtime} · {selected.model} · requested by {selected.creator}</p>
      <pre className="task-prompt">{selected.prompt}</pre>
      {selected.run_id ? <p><Link to={`/runs/${selected.run_id}`}>View Run {selected.run_id.slice(0, 8)}</Link> · {selected.run_state}</p> : <p className="muted">No run yet.</p>}
      {selected.status === "scheduled" && <Button variant="secondary" onClick={() => { setEditing(selected); setAgent(selected.agent); setModel(selected.model);
        setTitle(selected.title); setPrompt(selected.prompt); setLocalTime(new Date(selected.run_at).toLocaleString("sv-SE", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).replace(" ", "T"));
        setLateMinutes(Math.round((Date.parse(selected.expires_at) - Date.parse(selected.run_at)) / 60000)); setCreating(true); setSelected(null);
      }}>Edit</Button>}
      {(selected.status === "scheduled" || (selected.status === "launched" && selected.run_state === "queued")) && <Button variant="secondary" disabled={busy} onClick={() => cancel(selected)}>Cancel Task</Button>}
      {(selected.status === "blocked" || selected.status === "expired") && <Button variant="secondary" onClick={() => { setAgent(selected.agent); setModel(selected.model); setTitle(selected.title); setPrompt(selected.prompt); setLocalTime(""); setCreating(true); setSelected(null); }}>Create replacement</Button>}
      <h3>Task log</h3><ol className="task-events">{events.map((e, i) => <li key={i}><strong>{e.kind}</strong> · {when(e.created_at)} · {e.actor}{e.reason && <div>{e.reason}</div>}</li>)}</ol>
    </section>}
    <details style={{ marginTop: 32 }}><summary>Cross-agent scheduling permissions</summary><p className="muted">Agents can schedule themselves. Add explicit links for one agent to schedule another.</p>
      <div className="row-actions"><Select aria-label="Creator agent" value={grantCreator} onChange={(e) => setGrantCreator(e.target.value)}><option value="">Creator</option>{agents.filter((a) => !a.system).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}</Select>
      <Select aria-label="Target agent" value={grantTarget} onChange={(e) => setGrantTarget(e.target.value)}><option value="">Target</option>{agents.filter((a) => !a.system).map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}</Select>
      <Button disabled={!grantCreator || !grantTarget} onClick={addGrant}>Allow</Button></div>
      <ul>{grants.map((g) => <li key={`${g.creator}-${g.target}`}>{g.creator} → {g.target} <Button variant="link" onClick={async () => {
        try { await api<{ok: boolean}>(`/api/tasks/grants/${encodeURIComponent(g.creator)}/${encodeURIComponent(g.target)}`, { method: "DELETE" });
          setGrants(await api<Grant[]>("/api/tasks/grants")); }
        catch (e) { setError(String(e)); }
      }}>Remove</Button></li>)}</ul>
    </details>
  </div>;
}
