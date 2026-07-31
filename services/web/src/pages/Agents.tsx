import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary, type Job } from "../api";
import { cronEnglish } from "../lib/cron";
import { cn } from "../lib/cn";
import { buttonVariants } from "../ui/button";
import { Chip } from "../ui/chip";
import { Table, TD, TH } from "../ui/table";

function AgentTable({ agents, jobs }: { agents: AgentSummary[]; jobs: Map<string, number> }) {
  return (
    <Table>
      <thead>
        <tr><TH>Name</TH><TH>Description</TH><TH>Schedule</TH><TH>Status</TH></tr>
      </thead>
      <tbody>
        {agents.map((a) => (
          <tr key={a.name}>
            <TD><Link to={`/agents/${encodeURIComponent(a.name)}`}>{a.name}</Link></TD>
            <TD className="text-muted"><span className="line-clamp-1" title={a.description}>{a.description}</span></TD>
            <TD className="text-muted whitespace-nowrap">
              {a.schedule
                ? <code className="cron" title={cronEnglish(a.schedule)}>{a.schedule}</code>
                : jobs.get(a.name)
                ? <Link to={`/agents/${encodeURIComponent(a.name)}?tab=schedules`}>{jobs.get(a.name)} job{jobs.get(a.name)! > 1 ? "s" : ""}</Link>
                : "—"}
            </TD>
            <TD>
              {a.quarantined
                ? <Chip variant="danger" title={a.error ?? "Quarantined"}>quarantined</Chip>
                : a.blocked
                ? <Chip variant="danger" title={a.blocked_reason ?? "Blocked"}>blocked</Chip>
                : <Chip variant="ok">ok</Chip>}
            </TD>
          </tr>
        ))}
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
