import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type AgentDef, type AgentMetrics, type AgentSummary, type ModelUsage,
         type RelayChannelDetail } from "../api";
import { useGrantCatalog } from "../components/CapabilityPickers";
import { EntrypointsFields, GrantsFields, IdentityFields, PromptField, toDraft } from "../components/AgentForm";
import {
  invalidSecretPaths, markSecretsSet, pendingSecretWrites, useWebhookSecrets,
  WEBHOOK_SECRET_MAX, WEBHOOK_SECRET_MIN, writeWebhookSecrets,
} from "../lib/webhook-secrets";
import AgentVersions from "../components/AgentVersions";
import { ProfileImage, type AgentImage } from "../components/agents/ProfileImage";
import { Face } from "../components/relay/Face";
import MessagePane from "../components/relay/MessagePane";
import AgentMemories from "../components/AgentMemories";
import AgentSchedules from "../components/AgentSchedules";
import { isClosed, stateLabel, type Ticket } from "../lib/tickets";
import { Banner } from "@ap/ui/banner";
import { Button } from "@ap/ui/button";
import { Chip, StatusChip } from "@ap/ui/chip";
import { ConfirmDialog } from "@ap/ui/dialog";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";
import { useTitle } from "../lib/title";

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
/** This agent's DM with you, rendered by the same pane Relay uses. The DM is
 * an identity, not a new thread: `POST /api/relay/dm` is get-or-create, so
 * opening this tab twice lands in the same room. */
function AgentDm({ agent }: { agent: string }) {
  const [channel, setChannel] = useState<RelayChannelDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setChannel(null); setError(null);
    api<RelayChannelDetail>("/api/relay/dm", {
      method: "POST", body: JSON.stringify({ with: `agent:${agent}` }),
    })
      .then(setChannel)
      .catch((err) => setError(err instanceof Error ? err.message : "Could not open the dm."));
  }, [agent]);

  if (error) return <div className="error">{error}</div>;
  if (!channel) return <p className="muted">Opening…</p>;
  return (
    <>
      <p className="muted">
        Your direct messages with {agent}. Every channel it is in lives in{" "}
        <Link to="/relay">Relay</Link>.
      </p>
      <MessagePane channelId={channel.id} />
    </>
  );
}

/** What this agent owes the platform and what it has asked of it: the tickets
 * assigned to it and the ones it opened. Both come out of one unfiltered read
 * of the board — the list route filters by assignee but has no `reporter`
 * filter, and two halves of one page should not be two different truths. */
function AgentTickets({ agent }: { agent: string }) {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const me = `agent:${agent}`;
  useEffect(() => {
    setTickets(null);
    api<Ticket[]>("/api/tickets").then(setTickets).catch(() => setTickets([]));
  }, [agent]);

  if (!tickets) return <p className="muted">Loading…</p>;

  const section = (label: string, rows: Ticket[], empty: string) => (
    <section aria-label={label} className="agent-tickets">
      <h2>{label}</h2>
      {rows.length === 0
        ? <p className="muted">{empty}</p>
        : (
          <Table>
            <thead><tr><TH>Ticket</TH><TH>State</TH><TH>Title</TH></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}>
                  <TD><Link to={`/tickets/${t.key}`}>{t.key}</Link></TD>
                  <TD><StatusChip status={stateLabel(t.state)} /></TD>
                  <TD>{t.title}</TD>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
    </section>
  );

  // Open work first, and finished work only where it still says something —
  // an agent's page is a queue, not an archive (that is the board's closed
  // column and the ticket's own page).
  const order = (rows: Ticket[]) => [...rows].sort((a, b) =>
    Number(isClosed(a.state)) - Number(isClosed(b.state))
    || (b.last_activity_at ?? "").localeCompare(a.last_activity_at ?? ""));

  return (
    <>
      <p className="muted">
        The work this agent is on. Assigning it a ticket also summons it — the ask and
        the assignment are one act (<Link to="/help/tickets">Tickets</Link>).
      </p>
      {section(`Assigned to ${agent}`, order(tickets.filter((t) => t.assignee === me)),
               "Nothing is assigned to this agent.")}
      {section(`Reported by ${agent}`, order(tickets.filter((t) => t.reporter === me)),
               "This agent has not opened any tickets.")}
    </>
  );
}

// The row as every GET answers it: the definition plus the picture the image
// route owns (docs/design/23), which the editor must not treat as an edit.
type AgentRow = AgentDef & AgentImage;

// The definition alone. The picture rides on the row but is not the editor's
// to save: kept out of the draft, a refetch that changes the face does not
// read as unsaved changes, and the PUT never carries it.
function defOf(row: AgentRow): AgentDef {
  const def: Record<string, unknown> = { ...row };
  delete def.face;
  delete def.image_artifact_id;
  return def as AgentDef;
}

function AgentConfig({ agent, onSaved }: { agent: AgentRow; onSaved: (next: AgentDef) => void }) {
  const navigate = useNavigate();
  const catalog = useGrantCatalog();
  const original = toDraft(defOf(agent));
  const [draft, setDraft] = useState<AgentDef>(original);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Typed webhook secrets — held apart from the draft on purpose (design/16).
  const secrets = useWebhookSecrets();

  const webhooks = draft.entrypoints.webhooks;
  const pendingSecrets = pendingSecretWrites(webhooks, secrets.values);
  const badSecrets = invalidSecretPaths(webhooks, secrets.values);
  const dirty = JSON.stringify(draft) !== JSON.stringify(original);
  // A rotated secret is a save with no change to the row: it has to enable the
  // button on its own, or the only way to send it would be to dirty the def.
  const savable = (dirty || pendingSecrets.length > 0) && badSecrets.length === 0;
  const patch = (p: Partial<AgentDef>) => { setDraft((d) => ({ ...d, ...p })); setSaved(false); };

  async function save() {
    setSaving(true); setError(null); setSaved(false);
    try {
      // The full definition goes on the wire: the server's field-level guard
      // decides what a caller may change, and an admin session may change all
      // of it. Sending everything also survives a replace-style PUT. Skipped
      // when nothing in the row moved, so rotating a secret doesn't append a
      // no-op snapshot to the change log.
      const next = dirty
        ? await api<AgentDef>(`/api/agents/${encodeURIComponent(agent.name)}`, {
            method: "PUT",
            body: JSON.stringify(draft),
          })
        : null;
      // Adopt the row the server actually stored (it may normalize fields), so
      // the editor stops claiming unsaved changes it no longer has.
      let canonical = next && next.name ? toDraft(defOf(next)) : draft;
      // Secrets go second and alone: the endpoint 404s until the path is
      // declared, and the value never rides along with the definition.
      await writeWebhookSecrets(agent.name, pendingSecrets);
      canonical = markSecretsSet(canonical, pendingSecrets.map((p) => p.path));
      secrets.reset();
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
      {badSecrets.length > 0 && (
        <div className="error">
          A webhook secret must be {WEBHOOK_SECRET_MIN}–{WEBHOOK_SECRET_MAX} characters.
        </div>
      )}
      <div className="row-actions" style={{ marginTop: 10 }}>
        <Button onClick={save} disabled={saving || !savable}>{saving ? "Saving…" : "Save changes"}</Button>
        {(dirty || pendingSecrets.length > 0) && (
          <Button variant="secondary"
                  onClick={() => { setDraft(original); secrets.reset(); }}>Discard edits</Button>
        )}
        <span className="muted check-note">
          {dirty || pendingSecrets.length > 0
            ? "Unsaved changes."
            : saved ? "Saved — live now." : "Saved changes apply to the next run."}
        </span>
      </div>
    </>
  );

  return (
    // `agent-form` gives the section headings their divider rhythm (app.css):
    // this form scrolls for pages, and the h2s are its only landmarks.
    <div className="agent-form">
      <IdentityFields draft={draft} patch={patch} catalog={catalog} />
      <PromptField draft={draft} patch={patch} />
      {actions}

      <EntrypointsFields draft={draft} patch={patch} secrets={secrets} />
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
    </div>
  );
}

type Tab = "config" | "history" | "conversations" | "tickets" | "memories" | "schedules" | "report";

export default function AgentDetail() {
  const { name } = useParams<{ name: string }>();
  useTitle(name, "Agents");
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) ?? "config";
  const [agent, setAgent] = useState<AgentRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [summary, setSummary] = useState<AgentSummary | null>(null);
  // Handed over by whoever navigated here — the New-Agent wizard says so when
  // it created the agent but couldn't store its webhook secret, which is a
  // fail-closed webhook the operator has to finish here.
  const notice = (useLocation().state as { notice?: string } | null)?.notice ?? null;
  // Remounts the editor so its field-local state re-seeds from a fresh load
  // (after a save or a rollback) instead of holding stale text.
  const [formKey, setFormKey] = useState(0);

  function setTab(t: Tab) {
    const p = new URLSearchParams(params);
    p.set("tab", t);
    if (t !== "memories") p.delete("memory");
    setParams(p);
  }

  function loadContent() {
    if (!name) return;
    api<AgentRow>(`/api/agents/${encodeURIComponent(name)}`)
      .then((a) => { setAgent(a); setFormKey((k) => k + 1); })
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load agent."))
      .finally(() => setLoading(false));
    // The listing carries readiness (blocked + reason) — the row itself doesn't.
    api<AgentSummary[]>("/api/agents")
      .then((all) => setSummary(all.find((a) => a.name === name) ?? null))
      .catch(() => setSummary(null));
  }

  // The row again, and only the row: what a changed picture needs. The editor
  // is NOT remounted, so an edit in progress survives choosing a face.
  async function reloadRow() {
    if (!name) return;
    setAgent(await api<AgentRow>(`/api/agents/${encodeURIComponent(name)}`));
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
        <div className="agent-head">
          <Face participant={`agent:${agent.name}`} face={agent.face} size={64} />
          <h1>{agent.name}</h1>
        </div>
        <div className="row-actions">
          {agent.system && <Chip>system</Chip>}
          {agent.enabled === false && <Chip variant="warn">disabled</Chip>}
          {summary?.quarantined && <Chip variant="danger">quarantined</Chip>}
        </div>
      </div>
      {notice && <Banner variant="danger">{notice}</Banner>}
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
        <button className={tab === "tickets" ? "tab active" : "tab"} onClick={() => setTab("tickets")}>Tickets</button>
        <button className={tab === "memories" ? "tab active" : "tab"} onClick={() => setTab("memories")}>Memories</button>
        <button className={tab === "schedules" ? "tab active" : "tab"} onClick={() => setTab("schedules")}>Schedules</button>
        <button className={tab === "report" ? "tab active" : "tab"} onClick={() => setTab("report")}>Report</button>
      </div>

      {tab === "report" && <AgentReport name={agent.name} />}
      {tab === "conversations" && <AgentDm agent={agent.name} />}
      {tab === "tickets" && <AgentTickets agent={agent.name} />}
      {tab === "memories" && <AgentMemories agent={agent.name} />}
      {tab === "schedules" && <AgentSchedules agent={agent.name} />}
      {tab === "history" && <AgentVersions agent={agent.name} onRolledBack={loadContent} />}
      {tab === "config" && (
        <>
          <div className="profile-image-section">
            <h2>Profile image</h2>
            <ProfileImage name={agent.name} description={agent.description}
                          image={agent} onChanged={reloadRow} />
          </div>
          {/* A save answers with the definition only; the picture on the row
              stays what the image route last made it. */}
          <AgentConfig key={formKey} agent={agent}
                       onSaved={(next) => setAgent((a) => ({ ...a, ...next }))} />
        </>
      )}
    </div>
  );
}
