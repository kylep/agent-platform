import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type AgentDef } from "../api";
import { useGrantCatalog } from "../components/CapabilityPickers";
import { emptyDef, EntrypointsFields, GrantsFields, IdentityFields, PromptField } from "../components/AgentForm";
import {
  invalidSecretPaths, pendingSecretWrites, useWebhookSecrets,
  WEBHOOK_SECRET_MAX, WEBHOOK_SECRET_MIN, writeWebhookSecrets,
} from "../lib/webhook-secrets";
import { Button } from "@ap/ui/button";
import { Input } from "@ap/ui/field";

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;

export default function NewAgent() {
  const navigate = useNavigate();
  const catalog = useGrantCatalog();
  const [draft, setDraft] = useState<AgentDef>(emptyDef());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Typed webhook secrets — held apart from the draft on purpose (design/16).
  const secrets = useWebhookSecrets();

  const patch = (p: Partial<AgentDef>) => setDraft((d) => ({ ...d, ...p }));
  const nameOk = NAME_RE.test(draft.name);
  const badSecrets = invalidSecretPaths(draft.entrypoints.webhooks, secrets.values);

  async function create() {
    setSaving(true);
    setError(null);
    const detail = `/agents/${encodeURIComponent(draft.name)}`;
    try {
      await api<AgentDef>("/api/agents", { method: "POST", body: JSON.stringify(draft) });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent.");
      setSaving(false);
      return;
    }
    // Past this line the agent EXISTS, so a failed secret write must not leave
    // the operator on a form whose next click collides with the row it just
    // made. Hand them the editor instead: the secret field, the fail-closed
    // state and the retry all live there. The failure text deliberately does
    // not travel — it is API error text, and the request that produced it
    // carried the secret.
    try {
      // Second and alone: the secret endpoint 404s until the path is declared,
      // which it only is once the agent exists.
      await writeWebhookSecrets(draft.name, pendingSecretWrites(draft.entrypoints.webhooks, secrets.values));
      navigate(detail);
    } catch {
      navigate(detail, { state: { notice:
        `${draft.name} was created, but its webhook secret could not be set. A webhook in Secret ` +
        "mode rejects every caller until one is stored — set it below and save." } });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page agent-form">
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
      <EntrypointsFields draft={draft} patch={patch} secrets={secrets} />
      <GrantsFields draft={draft} patch={patch} catalog={catalog} />

      {error && <div className="error">{error}</div>}
      {badSecrets.length > 0 && (
        <div className="error">
          A webhook secret must be {WEBHOOK_SECRET_MIN}–{WEBHOOK_SECRET_MAX} characters.
        </div>
      )}
      <div className="row-actions" style={{ marginTop: 12 }}>
        <Button onClick={create} disabled={saving || !nameOk || badSecrets.length > 0}>
          {saving ? "Creating…" : "Create agent"}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/agents")}>Cancel</Button>
      </div>
    </div>
  );
}
