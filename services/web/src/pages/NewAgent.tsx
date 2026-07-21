import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EditResult } from "../api";
import { SkillPicker, ToolPicker, useCapabilities } from "../components/CapabilityPickers";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;

export default function NewAgent() {
  const navigate = useNavigate();
  const { skills, tools, ready } = useCapabilities();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [pickedSkills, setPickedSkills] = useState<Set<string>>(new Set());
  // Default: all tools on (unrestricted) — matches the backend default.
  const [pickedTools, setPickedTools] = useState<Set<string>>(new Set());
  const [seededTools, setSeededTools] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<EditResult | null>(null);

  // Seed tools to "all on" once the catalog loads.
  if (ready && !seededTools) {
    setPickedTools(new Set(tools));
    setSeededTools(true);
  }

  const nameOk = NAME_RE.test(name);

  async function create() {
    setSaving(true);
    setError(null);
    try {
      const r = await api<EditResult>("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          name, description, model,
          skills: [...pickedSkills], tools: [...pickedTools],
          prompt,
        }),
      });
      setResult(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent.");
    } finally {
      setSaving(false);
    }
  }

  if (result) {
    return (
      <div className="page">
        <h1>Agent “{name}” proposed</h1>
        <div className="banner">
          Created on branch <code>{result.branch}</code> as a pull request for review.
          {result.pr && <> — <a href={result.pr.url} target="_blank" rel="noreferrer">PR #{result.pr.number}</a></>}
        </div>
        <p className="muted">
          The agent goes live once you merge it under <Link to="/changes">Changes</Link>.
        </p>
        <div className="row-actions">
          <button onClick={() => navigate("/changes")}>Go to Changes</button>
          <button className="secondary" onClick={() => navigate("/agents")}>Back to Agents</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page">
      <h1>New Agent</h1>
      <p className="muted">
        Define an agent and pick its skills and tools. Saving opens a pull request under{" "}
        <Link to="/changes">Changes</Link> — nothing runs until you merge it.
      </p>

      <label className="field-label">Name</label>
      <input placeholder="lowercase-with-hyphens" value={name}
             onChange={(e) => setName(e.target.value.trim())} />
      {name && !nameOk && <div className="error">Lowercase letters, digits and hyphens only (1–63 chars).</div>}

      <label className="field-label">Description</label>
      <input placeholder="What does this agent do?" value={description}
             onChange={(e) => setDescription(e.target.value)} />

      <label className="field-label">Model <span className="muted">(optional)</span></label>
      <input placeholder="e.g. sonnet — blank uses the platform default" value={model}
             onChange={(e) => setModel(e.target.value.trim())} />

      <h2>Skills</h2>
      <p className="muted">Skills mount into the agent's pod and bind their required secrets.</p>
      <SkillPicker skills={skills} selected={pickedSkills} onChange={setPickedSkills} />

      <h2>Tools</h2>
      <ToolPicker tools={tools} selected={pickedTools} onChange={setPickedTools} />

      <label className="field-label">System prompt</label>
      <textarea placeholder="You are…" value={prompt} rows={6}
                onChange={(e) => setPrompt(e.target.value)} />

      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 12 }}>
        <button onClick={create} disabled={saving || !nameOk}>
          {saving ? "Creating…" : "Create agent (opens PR)"}
        </button>
        <button className="secondary" onClick={() => navigate("/agents")}>Cancel</button>
      </div>
    </div>
  );
}
