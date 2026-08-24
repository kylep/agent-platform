import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type AgentDef, type AgentMetrics, type AgentSummary, type ModelUsage } from "../api";
import { useGrantCatalog } from "../components/CapabilityPickers";
import { EntrypointsFields, GrantsFields, IdentityFields, PromptField, toDraft } from "../components/AgentForm";
import AgentVersions from "../components/AgentVersions";
import AgentChat from "../components/AgentChat";
import AgentMemories from "../components/AgentMemories";
import AgentSchedules from "../components/AgentSchedules";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";

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
      <StatRow>
        <Stat label="runs" value={m.total} />
        <Stat label="success" value={pct(m.success_rate)} warn={m.success_rate !== null && m.success_rate < 0.8} />
        <Stat label="fail streak" value={m.failure_streak} warn={m.failure_streak > 0} />
        <Stat label="avg duration" value={dur(m.avg_duration_seconds)} />
        <Stat label="tokens in/out (uncached) · last 5000 runs" value={`${m.tokens_in.toLocaleString()} / ${m.tokens_out.toLocaleString()}`} />
      </StatRow>
      <h2>Tokens by model <span className="muted text-sm font-normal">(all time, incl. cache reads)</span></h2>
      <Table>
        <thead><tr><TH>Model</TH><TH>Runs</TH><TH>Tokens in</TH><TH>Tokens out</TH></tr></thead>
        <tbody>
          {models.map((mu) => (
            <tr key={mu.model}>
              <TD>{mu.model}</TD><TD>{mu.runs}</TD>
              <TD className="text-muted">{mu.tokens_in.toLocaleString()}</TD>
              <TD className="text-muted">{mu.tokens_out.toLocaleString()}</TD>
            </tr>
          ))}
          {models.length === 0 && <tr><TD colSpan={4} className="text-muted">No model usage recorded yet.</TD></tr>}
        </tbody>
      </Table>
      <p className="muted">Last run: {m.last_run_at ? new Date(m.last_run_at).toLocaleString() : "—"}</p>
    </>
  );
}

// The editor. An agent is a row (docs/design/15): the whole definition — prompt,
// config, entrypoints and grants — is one draft, and Save writes it straight to
// the live agent. The change log (History tab) is what makes that safe.
function AgentConfig({ agent, onSaved }: { agent: AgentDef; onSaved: (next: AgentDef) => void }) {
  const navigate = useNavigate();
  const catalog = useGrantCatalog();
  const original = toDraft(agent);
  const [draft, setDraft] = useState<AgentDef>(original);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const dirty = JSON.stringify(draft) !== JSON.stringify(original);
  const patch = (p: Partial<AgentDef>) => { setDraft((d) => ({ ...d, ...p })); setSaved(false); };

  async function save() {
    setSaving(true); setError(null); setSaved(false);
    try {
      // The full definition goes on the wire: the server's field-level guard
      // decides what a caller may change, and an admin session may change all
      // of it. Sending everything also survives a replace-style PUT.
      const next = await api<AgentDef>(`/api/agents/${encodeURIComponent(agent.name)}`, {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      // Adopt the row the server actually stored (it may normalize fields), so
      // the editor stops claiming unsaved changes it no longer has.
      const canonical = next && next.name ? toDraft(next) : draft;
      setDraft(canonical);
      setSaved(true);
      onSaved(canonical);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    setDeleting(true); setError(null);
    try {
      await api(`/api/agents/${encodeURIComponent(agent.name)}`, { method: "DELETE" });
      navigate("/agents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete.");
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  }

  const actions = (
    <>
      {error && <div className="error">{error}</div>}
      <div className="row-actions" style={{ marginTop: 10 }}>
        <Button onClick={save} disabled={saving || !dirty}>{saving ? "Saving…" : "Save changes"}</Button>
        {dirty && <Button variant="secondary" onClick={() => setDraft(original)}>Discard edits</Button>}
        <span className="muted check-note">
          {dirty ? "Unsaved changes." : saved ? "Saved — live now." : "Saved changes apply to the next run."}
        </span>
      </div>
    </>
  );

  return (
    <>
      <IdentityFields draft={draft} patch={patch} catalog={catalog} />
      <PromptField draft={draft} patch={patch} />
      {actions}

      <EntrypointsFields draft={draft} patch={patch} />
      <GrantsFields draft={draft} patch={patch} catalog={catalog} />
      {actions}

      {!draft.system && (
        <>
          <h2>Delete</h2>
          <p className="muted">
            Removes the definition. Run history, memories and reports stay — they belong to the
            platform, not the row.
          </p>
          <div className="row-actions">
            <Button variant="danger" onClick={() => setConfirmDelete(true)} disabled={deleting}>
              Delete agent
            </Button>
          </div>
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${agent.name}?`}
        confirmLabel={deleting ? "Deleting…" : "Delete agent"}
        onConfirm={remove}
        onCancel={() => setConfirmDelete(false)}
      >
        The agent stops existing immediately: schedules and webhooks pointing at it stop firing.
        Its change log goes with it — this is not undoable from the UI.
      </ConfirmDialog>
    </>
  );
}

type Tab = "config" | "history" | "conversations" | "memories" | "schedules" | "report";

export default function AgentDetail() {
  const { name } = useParams<{ name: string }>();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) ?? "config";
  const [agent, setAgent] = useState<AgentDef | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AgentSummary | null>(null);
  // Remounts the editor so its field-local state re-seeds from a fresh load
  // (after a save or a rollback) instead of holding stale text.
  const [formKey, setFormKey] = useState(0);

  function setTab(t: Tab) {
    const p = new URLSearchParams(params);
    p.set("tab", t);
    if (t !== "conversations") p.delete("conversation");
    if (t !== "memories") p.delete("memory");
    setParams(p);
  }

  function loadContent() {
    if (!name) return;
    api<AgentDef>(`/api/agents/${encodeURIComponent(name)}`)
      .then((a) => { setAgent(a); setFormKey((k) => k + 1); })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load agent."))
      .finally(() => setLoading(false));
    // The listing carries readiness (blocked + reason) — the row itself doesn't.
    api<AgentSummary[]>("/api/agents")
      .then((all) => setSummary(all.find((a) => a.name === name) ?? null))
      .catch(() => setSummary(null));
  }

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    loadContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  if (loading) return <div className="page"><p className="muted">Loading…</p></div>;
  if (loadError) {
    const gone = loadError.startsWith("404");
    return (
      <div className="page">
        <h1>{name}</h1>
        {gone ? (
          <p className="muted">
            No agent named <code>{name}</code> exists (it may have been deleted). Its run
            history is still in <Link to={`/runs?agent=${encodeURIComponent(name ?? "")}`}>Runs</Link> and{" "}
            <Link to="/reporting">Reporting</Link>.
          </p>
        ) : (
          <div className="error">{loadError}</div>
        )}
      </div>
    );
  }
  if (!agent) return null;

  return (
    <div className={tab === "conversations" ? "page page-chat" : "page"}>
      <div className="page-header">
        <h1>{agent.name}</h1>
        <div className="row-actions">
          {agent.system && <Chip>system</Chip>}
          {agent.enabled === false && <Chip variant="warn">disabled</Chip>}
          {summary?.quarantined && <Chip variant="danger">quarantined</Chip>}
        </div>
      </div>
      {summary?.error && <Banner variant="danger">{summary.error}</Banner>}
      {summary?.blocked && (
        <Banner variant="danger">
          {summary.blocked_reason} — fix it under <Link to="/secrets">Settings → Secrets</Link>.
          Runs are rejected until the secret is healthy.
        </Banner>
      )}

      <div className="tabs">
        <button className={tab === "config" ? "tab active" : "tab"} onClick={() => setTab("config")}>Config</button>
        <button className={tab === "history" ? "tab active" : "tab"} onClick={() => setTab("history")}>History</button>
        <button className={tab === "conversations" ? "tab active" : "tab"} onClick={() => setTab("conversations")}>Conversations</button>
        <button className={tab === "memories" ? "tab active" : "tab"} onClick={() => setTab("memories")}>Memories</button>
        <button className={tab === "schedules" ? "tab active" : "tab"} onClick={() => setTab("schedules")}>Schedules</button>
        <button className={tab === "report" ? "tab active" : "tab"} onClick={() => setTab("report")}>Report</button>
      </div>

      {tab === "report" && <AgentReport name={agent.name} />}
      {tab === "conversations" && <AgentChat agent={agent.name} />}
      {tab === "memories" && <AgentMemories agent={agent.name} />}
      {tab === "schedules" && <AgentSchedules agent={agent.name} />}
      {tab === "history" && <AgentVersions agent={agent.name} onRolledBack={loadContent} />}
      {tab === "config" && (
        <AgentConfig key={formKey} agent={agent} onSaved={setAgent} />
      )}
    </div>
  );
}
