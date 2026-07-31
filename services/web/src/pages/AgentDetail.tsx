import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type AgentDetail as AgentDetailData, type AgentMetrics, type AgentSummary, type EditResult, type ModelUsage } from "../api";
import { SkillPicker, ToolPicker, useCapabilities } from "../components/CapabilityPickers";
import { ChangePhaseBanner, PendingChangeBanner, useChangeLoop } from "../components/ChangeFlow";
import AgentChat from "../components/AgentChat";
import AgentMemories from "../components/AgentMemories";
import AgentSchedules from "../components/AgentSchedules";

function AgentReport({ name }: { name: string }) {
  const [m, setM] = useState<AgentMetrics | null>(null);
  const [models, setModels] = useState<ModelUsage[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    api<AgentMetrics[]>("/api/metrics/agents")
      .then((rows) => setM(rows.find((r) => r.agent === name) ?? null))
      .finally(() => setLoaded(true));
    api<ModelUsage[]>(`/api/metrics/models?agent=${encodeURIComponent(name)}`).then(setModels).catch(() => setModels([]));
  }, [name]);

  const pct = (x: number | null) => (x === null ? "—" : `${(x * 100).toFixed(0)}%`);
  const dur = (x: number | null) => (x === null ? "—" : x >= 60 ? `${(x / 60).toFixed(1)}m` : `${x.toFixed(1)}s`);

  if (loaded && !m) return <p className="muted">No runs recorded for this agent yet.</p>;
  if (!m) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="stat-row">
        <div className="stat"><div className="stat-value">{m.total}</div><div className="stat-label">runs</div></div>
        <div className={m.success_rate !== null && m.success_rate < 0.8 ? "stat stat-warn" : "stat"}>
          <div className="stat-value">{pct(m.success_rate)}</div><div className="stat-label">success</div></div>
        <div className={m.failure_streak > 0 ? "stat stat-warn" : "stat"}>
          <div className="stat-value">{m.failure_streak}</div><div className="stat-label">fail streak</div></div>
        <div className="stat"><div className="stat-value">{dur(m.avg_duration_seconds)}</div><div className="stat-label">avg duration</div></div>
        <div className="stat"><div className="stat-value">{m.tokens_in}/{m.tokens_out}</div><div className="stat-label">tokens in/out</div></div>
      </div>
      <h2>Tokens by model</h2>
      <table className="table">
        <thead><tr><th>Model</th><th>Runs</th><th>Tokens in</th><th>Tokens out</th></tr></thead>
        <tbody>
          {models.map((mu) => (
            <tr key={mu.model}>
              <td>{mu.model}</td><td>{mu.runs}</td>
              <td className="muted">{mu.tokens_in.toLocaleString()}</td>
              <td className="muted">{mu.tokens_out.toLocaleString()}</td>
            </tr>
          ))}
          {models.length === 0 && <tr><td colSpan={4} className="muted">No model usage recorded yet.</td></tr>}
        </tbody>
      </table>
      <p className="muted">Last run: {m.last_run_at ? new Date(m.last_run_at).toLocaleString() : "—"}</p>
    </>
  );
}

// The tools an agent.md declares, or null when it has no `tools:` line (which
// the CLI reads as "all tools"). Mirrors the backend parse_agent_tools.
function parseTools(md: string): string[] | null {
  const fm = md.split("---")[1] ?? "";
  const line = fm.split("\n").find((l) => /^\s*tools:/i.test(l));
  if (!line) return null;
  return line.replace(/^\s*tools:/i, "").split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
}

// The editable Skills + Tools panel. Saving opens a PR via PATCH …/config.
function CapabilityEditor({ agent, locked, onSaved }: {
  agent: AgentDetailData; locked: boolean; onSaved: (r: EditResult) => void;
}) {
  const { skills, tools, ready } = useCapabilities();
  const [pickedSkills, setPickedSkills] = useState<Set<string>>(new Set(agent.manifest.skills));
  const [pickedTools, setPickedTools] = useState<Set<string>>(new Set());
  const [seeded, setSeeded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noop, setNoop] = useState(false);

  // Once the tool catalog loads, seed the selection from the agent.md (no
  // tools: line → all tools on).
  if (ready && !seeded) {
    const declared = parseTools(agent.agent_md);
    setPickedTools(new Set(declared ?? tools));
    setSeeded(true);
  }

  async function save() {
    setSaving(true);
    setError(null);
    setNoop(false);
    try {
      const r = await api<EditResult>(`/api/agents/${encodeURIComponent(agent.name)}/config`, {
        method: "PATCH",
        body: JSON.stringify({ skills: [...pickedSkills], tools: [...pickedTools] }),
      });
      if (r.tier === 0) setNoop(true);
      else onSaved(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2>Skills</h2>
      <p className="muted">Skills mount into the agent's pod and bind their required secrets.</p>
      <SkillPicker skills={skills} selected={pickedSkills} onChange={setPickedSkills} />

      <h2>Tools</h2>
      <ToolPicker tools={tools} selected={pickedTools} onChange={setPickedTools} />

      {noop && <div className="banner">No changes — the agent already matches this configuration.</div>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 12 }}>
        <button onClick={save} disabled={saving || !ready || locked}>
          {saving ? "Saving…" : "Save skills & tools (opens PR)"}
        </button>
      </div>
      <p className="muted check-note">
        Changing skills or tools is review-gated: it always opens a pull request rather than editing <code>main</code> directly.
      </p>
    </>
  );
}

// The raw agent.md editor: deterministic save — exactly what you type becomes
// the pending change (a PR on the agent's branch), no agent in the loop.
function DefinitionEditor({ agent, locked, onSaved }: {
  agent: AgentDetailData; locked: boolean; onSaved: (r: EditResult) => void;
}) {
  const [md, setMd] = useState(agent.agent_md);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noop, setNoop] = useState(false);
  const dirty = md !== agent.agent_md;

  async function save() {
    setSaving(true);
    setError(null);
    setNoop(false);
    try {
      const r = await api<EditResult>(`/api/agents/${encodeURIComponent(agent.name)}/quick-edit`, {
        method: "POST",
        body: JSON.stringify({ field: "prompt", value: md }),
      });
      if (r.tier === 0) setNoop(true);
      else onSaved(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2>Agent definition</h2>
      <p className="muted">
        The live definition, synced from <code>main</code> — this is exactly what runs. Edit it and
        save: your exact text becomes a pending change to review under <Link to="/changes">Changes</Link>.
      </p>
      <textarea
        className="agent-md-editor"
        value={md}
        onChange={(e) => setMd(e.target.value)}
        readOnly={locked}
        spellCheck={false}
        rows={Math.min(30, Math.max(12, md.split("\n").length + 2))}
      />
      {noop && <div className="banner">No changes — the definition already matches.</div>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <button onClick={save} disabled={saving || locked || !dirty}>
          {saving ? "Saving…" : "Save definition (opens PR)"}
        </button>
        {dirty && !locked && (
          <button className="secondary" onClick={() => setMd(agent.agent_md)}>Discard edits</button>
        )}
      </div>
    </>
  );
}

// The agent's durable triggers (entrypoints.yaml) — same save→PR contract as
// the definition editor, on the same coder/agent-<name> branch and lock.
function EntrypointsEditor({ agent, locked, onSaved }: {
  agent: AgentDetailData; locked: boolean; onSaved: (r: EditResult) => void;
}) {
  const [yamlText, setYamlText] = useState(agent.entrypoints_raw);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noop, setNoop] = useState(false);
  useEffect(() => setYamlText(agent.entrypoints_raw), [agent.entrypoints_raw]);
  const dirty = yamlText !== agent.entrypoints_raw;

  async function save() {
    setSaving(true); setError(null); setNoop(false);
    try {
      const r = await api<EditResult>(`/api/agents/${encodeURIComponent(agent.name)}/quick-edit`, {
        method: "POST",
        body: JSON.stringify({ field: "entrypoints", value: yamlText }),
      });
      if (r.tier === 0) setNoop(true);
      else onSaved(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2>Entrypoints</h2>
      <p className="muted">
        The agent's durable triggers (<code>entrypoints.yaml</code>): <code>cron</code> list,
        declared <code>webhooks</code> paths, <code>kafka</code> (reserved). Invalid YAML is
        rejected at save time. Emptying the editor removes the file. Ad-hoc schedules belong
        in <Link to={`/agents/${encodeURIComponent(agent.name)}?tab=schedules`}>Jobs</Link> instead.
      </p>
      <textarea
        className="agent-md-editor"
        value={yamlText}
        onChange={(e) => setYamlText(e.target.value)}
        readOnly={locked}
        spellCheck={false}
        placeholder={'cron: ["0 9 * * *"]\nwebhooks:\n  - path: my-hook\n'}
        rows={Math.min(14, Math.max(5, yamlText.split("\n").length + 2))}
      />
      {noop && <div className="banner">No changes — the entrypoints already match.</div>}
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 8 }}>
        <button onClick={save} disabled={saving || locked || !dirty}>
          {saving ? "Saving…" : "Save entrypoints (opens PR)"}
        </button>
        {dirty && !locked && (
          <button className="secondary" onClick={() => setYamlText(agent.entrypoints_raw)}>Discard edits</button>
        )}
      </div>
    </>
  );
}

type Tab = "config" | "conversations" | "memories" | "schedules" | "report";

export default function AgentDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) ?? "config";
  const [agent, setAgent] = useState<AgentDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AgentSummary | null>(null);

  function setTab(t: Tab) {
    const p = new URLSearchParams(params);
    p.set("tab", t);
    if (t !== "conversations") p.delete("conversation");
    if (t !== "memories") p.delete("memory");
    setParams(p);
  }

  function loadContent() {
    if (!name) return;
    api<AgentDetailData>(`/api/agents/${encodeURIComponent(name)}`)
      .then(setAgent)
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load agent."))
      .finally(() => setLoading(false));
    // The listing carries readiness (blocked + reason) — the detail view doesn't.
    api<AgentSummary[]>("/api/agents")
      .then((all) => setSummary(all.find((a) => a.name === name) ?? null))
      .catch(() => setSummary(null));
  }

  // The change loop: while a PR is open on this agent's branch every editor is
  // locked; when it resolves we wait for the cluster sync, refetch, and flash.
  const { pr: pending, phase, adopt } = useChangeLoop(`coder/agent-${name}`, loadContent);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    loadContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  function onSaved(r: EditResult) {
    // A save opened (or updated) the PR — reflect the lock immediately.
    adopt({
      number: r.pr?.number ?? 0,
      title: `Pending change for ${name}`,
      url: r.pr?.url ?? "",
      branch: r.branch ?? `coder/agent-${name}`,
      author: "you",
      created_at: new Date().toISOString(),
    });
  }

  async function editAgent() {
    if (!name) return;
    setEditing(true);
    setEditError(null);
    try {
      const run = await api<{ id: string }>(`/api/agents/${encodeURIComponent(name)}/edit`, {
        method: "POST",
        body: JSON.stringify({ instruction }),
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to start edit.");
    } finally {
      setEditing(false);
    }
  }

  if (loading) return <div className="page"><p className="muted">Loading…</p></div>;
  if (loadError) return <div className="page"><div className="error">{loadError}</div></div>;
  if (!agent) return null;

  return (
    <div className={tab === "conversations" ? "page page-chat" : "page"}>
      <h1>{agent.name}</h1>
      {agent.error && <div className="banner">{agent.error}</div>}
      {summary?.blocked && (
        <div className="banner">
          {summary.blocked_reason} — fix it under <Link to="/secrets">Settings → Secrets</Link>.
          Runs are rejected until the secret is healthy.
        </div>
      )}
      {pending && <PendingChangeBanner pr={pending} what="agent" />}
      <ChangePhaseBanner phase={phase} what="definition" />

      <div className="tabs">
        <button className={tab === "config" ? "tab active" : "tab"} onClick={() => setTab("config")}>Config</button>
        <button className={tab === "conversations" ? "tab active" : "tab"} onClick={() => setTab("conversations")}>Conversations</button>
        <button className={tab === "memories" ? "tab active" : "tab"} onClick={() => setTab("memories")}>Memories</button>
        <button className={tab === "schedules" ? "tab active" : "tab"} onClick={() => setTab("schedules")}>Schedules</button>
        <button className={tab === "report" ? "tab active" : "tab"} onClick={() => setTab("report")}>Report</button>
      </div>

      {tab === "report" && <AgentReport name={agent.name} />}
      {tab === "conversations" && <AgentChat agent={agent.name} />}
      {tab === "memories" && <AgentMemories agent={agent.name} />}
      {tab === "schedules" && <AgentSchedules agent={agent.name} />}
      {tab === "config" && (<>
      <dl className="def-list">
        <dt>Role</dt>
        <dd>{agent.manifest.role}</dd>
        <dt>Description</dt>
        <dd>{agent.manifest.description}</dd>
        <dt>Concurrency</dt>
        <dd>{agent.manifest.concurrency}</dd>
        <dt>Timeout (s)</dt>
        <dd>{agent.manifest.timeout_seconds}</dd>
        <dt>Secrets</dt>
        <dd>{agent.manifest.secrets.length ? agent.manifest.secrets.join(", ") : "—"}</dd>
      </dl>

      <DefinitionEditor agent={agent} locked={pending !== null} onSaved={onSaved} />

      <EntrypointsEditor agent={agent} locked={pending !== null} onSaved={onSaved} />

      <CapabilityEditor agent={agent} locked={pending !== null} onSaved={onSaved} />

      <h2>Edit the prompt with platform-coder</h2>
      <p className="muted">
        Or describe a change in plain language. platform-coder makes the change in the repo and opens a
        pull request (one per agent) that you review and merge under <Link to="/changes">Changes</Link>.
        It does not change anything until you merge.
      </p>
      <textarea
        placeholder="e.g. Add a line telling the agent to always reply in English."
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        rows={3}
        readOnly={pending !== null}
      />
      {editError && <div className="error">{editError}</div>}
      <div className="secret-row-footer">
        <button onClick={editAgent} disabled={editing || pending !== null || instruction.trim() === ""}>
          {editing ? "Dispatching…" : "Edit with platform-coder"}
        </button>
      </div>
      </>)}
    </div>
  );
}
