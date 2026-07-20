import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type AgentDetail as AgentDetailData } from "../api";

// The Claude Code tools an agent may be granted (frontmatter `tools:`).
const AVAILABLE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep",
  "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit"];

function parseTools(md: string): Set<string> {
  const fm = md.split("---")[1] ?? "";  // frontmatter is between the first pair of ---
  const line = fm.split("\n").find((l) => /^\s*tools:/i.test(l));
  if (!line) return new Set();
  return new Set(line.replace(/^\s*tools:/i, "").split(/[,\s]+/).map((s) => s.trim()).filter(Boolean));
}

export default function AgentDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<AgentDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [editing, setEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    api<AgentDetailData>(`/api/agents/${encodeURIComponent(name)}`)
      .then(setAgent)
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load agent."))
      .finally(() => setLoading(false));
  }, [name]);

  async function runNow() {
    if (!name) return;
    setRunning(true);
    setRunError(null);
    try {
      const run = await api<{ id: string; state: string }>("/api/runs", {
        method: "POST",
        body: JSON.stringify({ agent: name, prompt }),
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Failed to start run.");
    } finally {
      setRunning(false);
    }
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

  const agentTools = parseTools(agent.agent_md);

  return (
    <div className="page">
      <h1>{agent.name}</h1>
      {agent.error && <div className="banner">{agent.error}</div>}

      <dl className="def-list">
        <dt>Role</dt>
        <dd>{agent.manifest.role}</dd>
        <dt>Description</dt>
        <dd>{agent.manifest.description}</dd>
        <dt>Concurrency</dt>
        <dd>{agent.manifest.concurrency}</dd>
        <dt>Timeout (s)</dt>
        <dd>{agent.manifest.timeout_seconds}</dd>
        <dt>Skills</dt>
        <dd>{agent.manifest.skills.length ? agent.manifest.skills.join(", ") : "—"}</dd>
        <dt>Secrets</dt>
        <dd>{agent.manifest.secrets.length ? agent.manifest.secrets.join(", ") : "—"}</dd>
      </dl>

      <h2>Tools</h2>
      <p className="muted">Tools this agent may use, declared in its definition. Change them by editing the agent below.</p>
      <div className="tool-list">
        {AVAILABLE_TOOLS.map((t) => {
          const on = agentTools.has(t);
          return <span key={t} className={on ? "tool tool-on" : "tool"}>{on ? "☑" : "☐"} {t}</span>;
        })}
      </div>

      <h2>Agent definition</h2>
      <p className="muted">The live definition, synced from <code>main</code> — this is exactly what runs.</p>
      <pre className="agent-md">{agent.agent_md}</pre>

      <h2>Edit this agent</h2>
      <p className="muted">
        Describe a change in plain language. This edits the definition above — platform-coder
        makes the change in the repo and opens a pull request (one per agent) that you review and
        merge under <Link to="/changes">Changes</Link>. It does not change anything until you merge.
      </p>
      <textarea
        placeholder="e.g. Add a line telling the agent to always reply in English."
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        rows={3}
      />
      {editError && <div className="error">{editError}</div>}
      <div className="secret-row-footer">
        <button onClick={editAgent} disabled={editing || instruction.trim() === ""}>
          {editing ? "Dispatching…" : "Edit with platform-coder"}
        </button>
      </div>

      <h2>Run now</h2>
      <textarea
        placeholder="Prompt…"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={5}
      />
      {runError && <div className="error">{runError}</div>}
      <div className="secret-row-footer">
        <button onClick={runNow} disabled={running || prompt.trim() === ""}>
          {running ? "Starting…" : "Run now"}
        </button>
      </div>
    </div>
  );
}
