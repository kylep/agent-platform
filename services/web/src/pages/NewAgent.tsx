import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type AgentDef } from "../api";
import { useGrantCatalog } from "../components/CapabilityPickers";
import { emptyDef, EntrypointsFields, GrantsFields, IdentityFields, PromptField } from "../components/AgentForm";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;

export default function NewAgent() {
  const navigate = useNavigate();
  const catalog = useGrantCatalog();
  const [draft, setDraft] = useState<AgentDef>(emptyDef());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch = (p: Partial<AgentDef>) => setDraft((d) => ({ ...d, ...p }));
  const nameOk = NAME_RE.test(draft.name);

  async function create() {
    setSaving(true);
    setError(null);
    try {
      await api<AgentDef>("/api/agents", { method: "POST", body: JSON.stringify(draft) });
      navigate(`/agents/${encodeURIComponent(draft.name)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page">
      <h1>New Agent</h1>
      <p className="muted">
        An agent is a row: this form writes it directly. It exists — and runs, if you give it an
        entrypoint — as soon as you create it, and every later change is recorded in its change log.
      </p>

      <label className="field-label">Name</label>
      <Input className="w-full sm:w-80" aria-label="Name" placeholder="lowercase-with-hyphens"
             value={draft.name} onChange={(e) => patch({ name: e.target.value.trim() })} />
      {draft.name && !nameOk && (
        <div className="error">Lowercase letters, digits and hyphens only (1–63 chars).</div>
      )}
      <p className="muted check-note">The slug is permanent — it identifies the agent everywhere.</p>

      <IdentityFields draft={draft} patch={patch} catalog={catalog} />
      <PromptField draft={draft} patch={patch} />
      <EntrypointsFields draft={draft} patch={patch} />
      <GrantsFields draft={draft} patch={patch} catalog={catalog} />

      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 12 }}>
        <Button onClick={create} disabled={saving || !nameOk}>
          {saving ? "Creating…" : "Create agent"}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/agents")}>Cancel</Button>
      </div>
    </div>
  );
}
