import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, asList, type AgentSummary, type CronEntry, type Job, type WebhookEntry } from "../api";
import { cronTitle, isSingleExpression, useCronPreview } from "../lib/cron";
import { cn } from "@ap/ui/cn";
import { buttonVariants } from "@ap/ui/button";
import { Chip } from "@ap/ui/chip";
import { Table, TD, TH } from "@ap/ui/table";

// The cron summary: the API may pre-render one, else it's the agent's own
// cron entrypoints (its row is the source of truth — docs/design/15).
// `asList` because the entrypoints blob comes back unvalidated — a warped row
// must cost this agent its schedule cell, not the whole listing.
function scheduleOf(a: AgentSummary): string {
  if (a.schedule) return a.schedule;
  return asList<CronEntry>(a.entrypoints?.crons)
    .map((c) => c?.schedule).filter(Boolean).join(", ");
}

// The declared webhook paths, same defensive read as the crons above.
function webhooksOf(a: AgentSummary): string[] {
  return asList<WebhookEntry>(a.entrypoints?.webhooks)
    .map((w) => w?.path).filter((p): p is string => typeof p === "string" && p !== "");
}

// The schedule cell. A hook per row, so each cell asks the platform what its
// own cron means — the descriptions are cached by expression, so a listing of
// agents on the same schedule costs one request, not one per row.
function CronCell({ schedule, zone }: { schedule: string; zone?: string }) {
  const preview = useCronPreview(isSingleExpression(schedule) ? schedule : "", zone, 0);
  return <code className="cron" title={cronTitle(preview, zone)}>{schedule}</code>;
}

function AgentTable({ agents, jobs }: { agents: AgentSummary[]; jobs: Map<string, number> }) {
  return (
    <Table>
      <thead>
        <tr><TH>Name</TH><TH>Description</TH><TH>Schedule</TH><TH>Webhook</TH><TH>Status</TH></tr>
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
              <TD>
                {a.quarantined
                  ? <Chip variant="danger" title={a.error ?? "Quarantined"}>quarantined</Chip>
                  : a.blocked
                  ? <Chip variant="danger" title={a.blocked_reason ?? "Blocked"}>blocked</Chip>
                  : a.enabled === false
                  ? <Chip variant="warn" title="Disabled — the definition stays, runs are rejected.">disabled</Chip>
                  : <Chip variant="ok">ok</Chip>}
              </TD>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [jobs, setJobs] = useState<Map<string, number>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Job[]>("/api/jobs")
      .then((js) => {
        const m = new Map<string, number>();
        for (const j of js) if (j.enabled) m.set(j.agent, (m.get(j.agent) ?? 0) + 1);
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
    <div className="page">
      <div className="page-header">
        <h1>Agents</h1>
        <Link to="/agents/new"
              className={cn(buttonVariants({ variant: "primary", size: "sm" }), "no-underline hover:no-underline")}>
          + New Agent
        </Link>
      </div>
      {loading && <p className="muted">Loading…</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && (
        <>
          <AgentTable agents={regular} jobs={jobs} />
          {system.length > 0 && (
            <>
              <h2>System agents</h2>
              <p className="muted">Platform-internal agents. Managed by the platform; not deletable.</p>
              <AgentTable agents={system} jobs={jobs} />
            </>
          )}
        </>
      )}
    </div>
  );
}
