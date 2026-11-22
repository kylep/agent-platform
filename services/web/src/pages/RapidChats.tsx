import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiErrorMessage, type AgentSummary } from "../api";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { FormDialog } from "@ap/ui/dialog";
import { Select } from "@ap/ui/field";
import { useTitle } from "../lib/title";

// Shapes follow docs/design/45-rapid-chat.md §2 and §7.1.
type Area = { key: string; title: string; status: string; turns: number; summary?: string | null;
  author?: string | null; critic?: string | null };
type Quota = Record<string, unknown>;
type Chat = { id: string; title: string; goal: string; participants: string[];
  sink: { kind: string; slug: string; mode: string }; areas: Area[]; state: string;
  end_reason: string | null; started_at: string | null; ended_at: string | null;
  duration_minutes: number; max_turns: number; turns_total: number; last_quota: Quota | null;
  output_ref: { kind?: string; slug: string; version: number } | null; summary: string | null;
  last_error: string | null; created_at: string;
  limits?: { claude_5h_max_pct: number; claude_7d_max_pct: number; codex_7d_max_pct: number };
  participant_counts?: Record<string, number> };
type Turn = { id: number; seq: number; agent: string; area_key: string | null; role: string;
  outcome: string | null; state: string; run_id: string | null; prompt: string; result: string | null;
  started_at: string | null; finished_at: string | null; reason: string | null };

const when = (s: string | null) => s ? new Date(s).toLocaleString() : "-";
const finishing = (c: Chat) => c.state === "running" && !!c.end_reason;
const stateLabel = (c: Chat) => finishing(c) ? "finishing" : c.state;
const active = (c: Chat) => c.state === "running" || c.state === "stopping";
function mins(ms: number) { const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`; }
function dur(t: Turn) {
  if (!t.started_at || !t.finished_at) return "-";
  return mins(Date.parse(t.finished_at) - Date.parse(t.started_at));
}

export default function RapidChats() {
  useTitle("Rapid Chats");
  const [chats, setChats] = useState<Chat[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [selId, setSelId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [areasText, setAreasText] = useState("");
  const [slug, setSlug] = useState("");
  const [mode, setMode] = useState("replace");
  const [sinkKind, setSinkKind] = useState("wiki");
  const [duration, setDuration] = useState(60);
  const [maxTurns, setMaxTurns] = useState(200);
  const [turnTimeout, setTurnTimeout] = useState(240);
  const [lim5h, setLim5h] = useState(95);
  const [lim7d, setLim7d] = useState(40);
  const [limCodex, setLimCodex] = useState(40);

  const sel = chats.find((c) => c.id === selId) || null;
  const anyActive = chats.some(active);

  function load() {
    api<Chat[]>("/api/rapid-chats?limit=50").then(setChats).catch((e) => setError(String(e)));
  }
  function loadTurns(id: string) {
    api<Turn[]>(`/api/rapid-chats/${id}/turns?limit=500`).then(setTurns).catch(() => {});
  }
  useEffect(() => {
    load();
    api<AgentSummary[]>("/api/agents").then(setAgents).catch(() => {});
  }, []);
  useEffect(() => {
    if (!anyActive) return;
    const id = window.setInterval(() => { load(); setNow(Date.now()); if (selId) loadTurns(selId); }, 5000);
    return () => window.clearInterval(id);
  }, [anyActive, selId]);
  useEffect(() => {
    setOpen(null);
    if (selId) loadTurns(selId); else setTurns([]);
  }, [selId]);

  const candidates = useMemo(() => agents.filter((a) => !a.system && a.enabled !== false
    && (showAll || a.agent_type === "persona")), [agents, showAll]);

  function parseAreas(): (string | { title: string; seed: string })[] {
    return areasText.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const i = l.indexOf("::");
      if (i < 0) return l;
      return { title: l.slice(0, i).trim(), seed: l.slice(i + 2).trim() };
    });
  }
  const valid = title.trim() && goal.trim() && picked.length >= 1 && picked.length <= 5 && slug.trim();

  async function launch() {
    if (!valid) return;
    setBusy(true); setError(null);
    try {
      const c = await api<Chat>("/api/rapid-chats", { method: "POST", body: JSON.stringify({
        title: title.trim(), goal, participants: picked, areas: parseAreas(),
        sink: { kind: sinkKind, slug: slug.trim(), mode: sinkKind === "ui_designer" ? "replace" : mode }, duration_minutes: duration,
        max_turns: maxTurns, turn_timeout_seconds: turnTimeout,
        limits: { claude_5h_max_pct: lim5h, claude_7d_max_pct: lim7d, codex_7d_max_pct: limCodex } }) });
      setSelId(c.id); setLaunchOpen(false); load();
    } catch (e) { setError(apiErrorMessage(e, "Could not launch rapid-chat.")); }
    finally { setBusy(false); }
  }
  async function stop(c: Chat) {
    setBusy(true); setError(null);
    try { await api<Chat>(`/api/rapid-chats/${c.id}/stop`, { method: "POST" }); load(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  function timing(c: Chat) {
    if (!c.started_at) return null;
    const end = c.ended_at ? Date.parse(c.ended_at) : now;
    const elapsed = end - Date.parse(c.started_at);
    const remaining = c.duration_minutes * 60000 - elapsed;
    return <p>Elapsed {mins(elapsed)}{active(c) && <> · remaining {mins(remaining)}</>} · {c.turns_total}/{c.max_turns} turns</p>;
  }

  return <div className="page page-wide">
    <div className="row-actions" style={{ justifyContent: "space-between" }}>
      <h1>Rapid Chats</h1>
      <Button onClick={() => { setError(null); setLaunchOpen(true); }}>New rapid-chat</Button></div>
    <p className="muted">Time-boxed multi-agent working sessions that produce a wiki spec or a UI design.</p>
    {error && !launchOpen && <div className="error" role="alert">{error}</div>}

    <FormDialog open={launchOpen} title="New rapid-chat" submitLabel={busy ? "Launching..." : "Launch"}
      disabled={busy || !valid} onSubmit={launch} onCancel={() => setLaunchOpen(false)}>
      <div className="task-form" style={{ maxHeight: "60vh", overflowY: "auto" }}>
      <label htmlFor="rapid-title">Title</label><input id="rapid-title" value={title} maxLength={128} onChange={(e) => setTitle(e.target.value)} />
      <label htmlFor="rapid-goal">Goal</label><textarea id="rapid-goal" rows={5} value={goal} onChange={(e) => setGoal(e.target.value)} />
      <label>Participants (1-5)</label>
      <div>{candidates.map((a) => <label key={a.name} style={{ display: "inline-block", marginRight: 16 }}>
        <input type="checkbox" checked={picked.includes(a.name)}
          onChange={(e) => setPicked((p) => e.target.checked ? [...p, a.name] : p.filter((n) => n !== a.name))} /> {a.name}
      </label>)}</div>
      <label><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show all agents</label>
      <label>Areas (one per line; "Title :: seed text" seeds an area)</label>
      <textarea rows={5} value={areasText} onChange={(e) => setAreasText(e.target.value)} />
      <label htmlFor="rapid-sink">Output kind</label><Select id="rapid-sink" value={sinkKind} onChange={(e) => { setSinkKind(e.target.value); if (e.target.value === "ui_designer") setMode("replace"); }}><option value="wiki">Wiki specification</option><option value="ui_designer">UI Designer document</option></Select>
      {sinkKind === "ui_designer" && <p className="muted">Creates a new design document. Existing document slugs cannot be overwritten.</p>}
      <label htmlFor="rapid-slug">{sinkKind === "ui_designer" ? "UI Designer output slug" : "Wiki output slug"}</label><input id="rapid-slug" value={slug} onChange={(e) => setSlug(e.target.value)} />
      <label htmlFor="rapid-mode">Mode</label>
      <Select id="rapid-mode" disabled={sinkKind === "ui_designer"} value={sinkKind === "ui_designer" ? "replace" : mode} onChange={(e) => setMode(e.target.value)}>
        <option value="replace">replace</option>{sinkKind === "wiki" && <option value="append">append</option>}</Select>
      <label>Duration (minutes, 5-240)</label>
      <input type="number" min={5} max={240} value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
      <label>Max turns (1-500)</label>
      <input type="number" min={1} max={500} value={maxTurns} onChange={(e) => setMaxTurns(Number(e.target.value))} />
      <label>Turn timeout (seconds, 60-900)</label>
      <input type="number" min={60} max={900} value={turnTimeout} onChange={(e) => setTurnTimeout(Number(e.target.value))} />
      <label>Claude 5h max %</label><input type="number" value={lim5h} onChange={(e) => setLim5h(Number(e.target.value))} />
      <label>Claude 7d max %</label><input type="number" value={lim7d} onChange={(e) => setLim7d(Number(e.target.value))} />
      <label>Codex 7d max %</label><input type="number" value={limCodex} onChange={(e) => setLimCodex(Number(e.target.value))} />
      </div>
      {error && <div className="error" role="alert">{error}</div>}
    </FormDialog>

    <h2 style={{ marginTop: 24 }}>Chats</h2>
    {chats.length === 0 ? <p className="muted">No Rapid Chats yet.</p> :
      <div className="task-grid">{chats.map((c) => <button type="button" className="task-card" key={c.id} onClick={() => setSelId(c.id)}>
        <span className="task-card-top"><strong>{c.title}</strong>
          <Chip variant={c.end_reason === "error" ? "danger" : "ok"}>{stateLabel(c)}</Chip></span>
        <span>{c.participants.join(", ")}</span>
        <span className="muted">{when(c.started_at)}{c.end_reason ? ` · ${c.end_reason}` : ""}</span>
      </button>)}</div>}

    {sel && <section className="panel task-detail">
      <div className="row-actions" style={{ justifyContent: "space-between" }}>
        <h2>{sel.title}</h2><Button variant="secondary" onClick={() => setSelId(null)}>Close</Button></div>
      <p><Chip variant={sel.end_reason === "error" ? "danger" : "ok"}>{finishing(sel) ? `finishing · ${sel.end_reason}` : sel.state}</Chip>{" "}
        {sel.end_reason && !finishing(sel) && <span className="muted">{sel.end_reason}</span>}
        {sel.last_error && <span className="error"> {sel.last_error}</span>}</p>
      {timing(sel)}
      {sel.last_quota && <p className="muted">Last quota reading: {Object.entries(sel.last_quota).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</p>}
      <p>{sel.participants.join(", ")}</p>
      <pre className="task-prompt">{sel.goal}</pre>
      {active(sel) && <Button variant="secondary" disabled={busy || sel.state === "stopping"} onClick={() => stop(sel)}>
        {sel.state === "stopping" ? "Stopping..." : "Stop"}</Button>}
      {sel.output_ref && <p>Output: <Link to={sel.output_ref.kind === "ui_designer" || sel.sink.kind === "ui_designer" ? `/ui-designer/${encodeURIComponent(sel.output_ref.slug)}?version=${sel.output_ref.version}` : `/wiki/${encodeURIComponent(sel.output_ref.slug)}`}>{sel.output_ref.slug}</Link> (v{sel.output_ref.version})</p>}
      {!sel.output_ref && <p className="muted">Output slug: <Link to={sel.sink.kind === "ui_designer" ? `/ui-designer/${encodeURIComponent(sel.sink.slug)}` : `/wiki/${encodeURIComponent(sel.sink.slug)}`}>{sel.sink.slug}</Link></p>}
      {sel.summary && <><h3>Summary</h3><pre className="task-prompt">{sel.summary}</pre></>}
      <h3>Areas</h3>
      {sel.areas.length === 0 ? <p className="muted">No areas yet.</p> :
        <div className="task-grid">{sel.areas.map((a) => <div className="task-card" key={a.key}>
          <span className="task-card-top"><strong>{a.title}</strong><Chip variant="ok">{a.status}</Chip></span>
          <span className="muted">{a.turns} turns · author {a.author || "-"} · critic {a.critic || "-"}</span>
          {a.summary && <span>{a.summary}</span>}
        </div>)}</div>}
      <h3>Turns</h3>
      <table><thead><tr><th>#</th><th>Agent</th><th>Area</th><th>Role</th><th>Outcome</th><th>Duration</th><th>Run</th><th></th></tr></thead>
        <tbody>{turns.map((t) => <Fragment key={t.id}>
          <tr><td>{t.seq}</td><td>{t.agent}</td><td>{t.area_key || "-"}</td><td>{t.role}</td>
            <td>{t.outcome || t.state}{t.reason ? ` (${t.reason})` : ""}</td><td>{dur(t)}</td>
            <td>{t.run_id ? <Link to={`/runs/${t.run_id}`}>{t.run_id.slice(0, 8)}</Link> : "-"}</td>
            <td><Button variant="link" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? "Hide" : "Show"}</Button></td></tr>
          {open === t.id && <tr><td colSpan={8}>
            <strong>Prompt</strong><pre className="task-prompt">{t.prompt}</pre>
            <strong>Result</strong><pre className="task-prompt">{t.result || "(none)"}</pre></td></tr>}
        </Fragment>)}</tbody></table>
    </section>}
  </div>;
}
