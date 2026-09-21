import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary, type Job } from "../api";
import { AgentCard, AgentStatusChip, CronCell, scheduleOf, webhooksOf } from "../components/agents/AgentCard";
import { cn } from "@ap/ui/cn";
import { Button, buttonVariants } from "@ap/ui/button";
import { Table, TD, TH } from "@ap/ui/table";
import { useTitle } from "../lib/title";

// Which way the listing is laid out. Remembered per browser: an operator who
// wants the table wants it every time, and the storage may be refused (a
// private window, a locked-down profile) without costing the page anything.
type View = "grid" | "table";
const VIEW_KEY = "agents.view";

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === "table" ? "table" : "grid";
  } catch {
    return "grid";
  }
}

function storeView(v: View): void {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    // Nothing to do: the choice holds for this page and is asked again next time.
  }
}

// A segmented control: two real buttons, so it is keyboard-operable for free,
// and `aria-pressed` says which one holds rather than a colour alone.
function ViewToggle({ view, onChange }: { view: View; onChange: (v: View) => void }) {
  const seg = (v: View, label: string) => (
    <Button variant="secondary" size="sm" aria-pressed={view === v} onClick={() => onChange(v)}>
      {label}
    </Button>
  );
  return (
    <div role="group" aria-label="View" className="view-toggle">
      {seg("grid", "Grid")}
      {seg("table", "Table")}
    </div>
  );
}

function AgentGrid({ agents, jobs }: { agents: AgentSummary[]; jobs: Map<string, number> }) {
  if (agents.length === 0) return <p className="muted">No agents yet.</p>;
  return (
    <div className="agent-grid">
      {agents.map((a) => <AgentCard key={a.name} agent={a} jobs={jobs.get(a.name) ?? 0} />)}
    </div>
  );
}

function AgentTable({ agents, jobs }: { agents: AgentSummary[]; jobs: Map<string, number> }) {
  return (
    <Table>
      <thead>
        <tr><TH>Name</TH><TH>Description</TH><TH>Model</TH><TH>Schedule</TH><TH>Webhook</TH><TH>Status</TH></tr>
      </thead>
      <tbody>
        {agents.map((a) => {
          const schedule = scheduleOf(a);
          const hooks = webhooksOf(a);
          return (
            <tr key={a.name}>
              <TD><Link to={`/agents/${encodeURIComponent(a.name)}`}>{a.name}</Link></TD>
              <TD className="text-muted"><span className="line-clamp-1" title={a.description}>{a.description}</span></TD>
              <TD className="text-muted whitespace-nowrap">
                <code title={`${a.runtime === "codex" ? "OpenAI Codex" : "Claude Code"} runtime`}>
                  {a.model?.trim() || "platform default"}
                </code>
              </TD>
              <TD className="text-muted whitespace-nowrap">
                {schedule
                  ? <CronCell schedule={schedule} zone={a.entrypoints?.timezone} />
                  : jobs.get(a.name)
                  ? <Link to={`/agents/${encodeURIComponent(a.name)}?tab=schedules`}>{jobs.get(a.name)} job{jobs.get(a.name)! > 1 ? "s" : ""}</Link>
                  : "—"}
              </TD>
              <TD className="text-muted"
                  title={hooks.length ? hooks.map((p) => `POST /api/webhooks/${p}`).join("\n") : "No webhook entrypoint."}>
                {hooks.length ? "✓" : "—"}
              </TD>
              <TD><AgentStatusChip agent={a} /></TD>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

export default function Agents() {
  useTitle("Agents");
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [jobs, setJobs] = useState<Map<string, number>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(readView);

  function changeView(v: View) {
    setView(v);
    storeView(v);
  }

  useEffect(() => {
    api<Job[]>("/api/jobs")
      .then((js) => {
        const m = new Map<string, number>();
        // Relay jobs are skipped: they fire into a room, so no agent's card owes
        // the reader a "1 job" badge for them.
        for (const j of js) if (j.enabled && j.agent) m.set(j.agent, (m.get(j.agent) ?? 0) + 1);
        setJobs(m);
      })
      .catch(() => {});
    api<AgentSummary[]>("/api/agents")
      .then(setAgents)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load agents."))
      .finally(() => setLoading(false));
  }, []);

  const system = agents.filter((a) => a.system);
  const regular = agents.filter((a) => !a.system);

  return (
    <div className="page page-agents">
      <div className="page-header">
        <h1>Agents</h1>
        <div className="row-actions">
          <ViewToggle view={view} onChange={changeView} />
          <Link to="/agents/new"
                className={cn(buttonVariants({ variant: "primary", size: "sm" }), "no-underline hover:no-underline")}>
            + New Agent
          </Link>
        </div>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <>
          {view === "grid"
            ? <AgentGrid agents={regular} jobs={jobs} />
            : <AgentTable agents={regular} jobs={jobs} />}
          {system.length > 0 && (
            <>
              <h2>System agents</h2>
              <p className="muted">
                Platform-managed workers. Skipped by @all, available by name, and not deletable.
              </p>
              {view === "grid"
                ? <AgentGrid agents={system} jobs={jobs} />
                : <AgentTable agents={system} jobs={jobs} />}
            </>
          )}
        </>
      )}
    </div>
  );
}
