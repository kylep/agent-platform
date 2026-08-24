import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, type AgentMetrics, type AgentSummary, type Job, type KafkaHealth,
  type MetricsOverview, type PullRequest, type RunSummary, type ScheduleEntry,
  type SecretStatus,
} from "../api";
import { Chip, StatusChip } from "@ap/ui/chip";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";
import { cronTitle, isSingleExpression, useCronPreview } from "../lib/cron";

// One actionable item in the "Needs attention" panel.
type Attn = { key: string; text: string; to: string; sev: "warn" | "bad" };

// What's next. A Job has a name it was given; an agent's entrypoint cron has
// none, so the cell says what the cron means instead — asked of the platform,
// which is the only thing that renders a cron into English.
function UpcomingCell({ cron, name }: { cron: string; name: string | null }) {
  const preview = useCronPreview(isSingleExpression(cron) ? cron : "", "", 0);
  return (
    <TD className="cron" title={cronTitle(preview) ?? cron}>
      {name ?? (preview?.english || cron)}
    </TD>
  );
}

function pct(x: number | null): string { return x === null ? "—" : `${(x * 100).toFixed(0)}%`; }

export default function Dashboard() {
  const [ov, setOv] = useState<MetricsOverview | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [kafka, setKafka] = useState<KafkaHealth | null>(null);
  const [agentMetrics, setAgentMetrics] = useState<AgentMetrics[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  // `name` is null for an entrypoint cron — it has no name of its own.
  const [upcoming, setUpcoming] = useState<
    { agent: string; name: string | null; next: string | null; cron: string }[]>([]);
  const [loaded, setLoaded] = useState(false);

  function refresh() {
    api<MetricsOverview>("/api/metrics/overview").then(setOv).catch(() => {});
    api<RunSummary[]>("/api/runs?limit=8").then(setRuns).catch(() => {});
    api<KafkaHealth>("/api/health/kafka").then(setKafka).catch(() => setKafka(null));
    api<AgentMetrics[]>("/api/metrics/agents").then(setAgentMetrics).catch(() => {});
    api<AgentSummary[]>("/api/agents").then(setAgents).catch(() => {});
    api<SecretStatus[]>("/api/secrets").then(setSecrets).catch(() => {});
    api<PullRequest[]>("/api/pull-requests").then(setPrs).catch(() => setPrs([]));  // 409 if no GH app
    Promise.all([
      api<Job[]>("/api/jobs").catch(() => [] as Job[]),
      api<ScheduleEntry[]>("/api/schedules").catch(() => [] as ScheduleEntry[]),
    ]).then(([jobs, scheds]) => {
      const rows = [
        ...jobs.filter((j) => j.enabled).map((j) => ({ agent: j.agent, name: j.name, next: j.next_fire, cron: j.cron })),
        ...scheds.filter((s) => s.enabled).map((s) => ({ agent: s.agent, name: null, next: s.next_fire, cron: s.cron })),
      ].filter((r) => r.next).sort((a, b) => (a.next ?? "").localeCompare(b.next ?? "")).slice(0, 5);
      setUpcoming(rows);
    }).finally(() => setLoaded(true));
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000);   // keep the landing view live
    return () => clearInterval(id);
  }, []);

  // --- Needs attention: the actionable queue -------------------------------
  const attention: Attn[] = [];
  if (prs.length) attention.push({ key: "prs", sev: "warn", to: "/changes",
    text: `${prs.length} pending change${prs.length === 1 ? "" : "s"} to review` });
  const dlq = ov?.dlq ?? kafka?.backlog.dlq ?? 0;
  if (dlq) attention.push({ key: "dlq", sev: "bad", to: "/dlq",
    text: `${dlq} run${dlq === 1 ? "" : "s"} in the dead-letter queue` });
  // Failing = a CURRENT agent (metrics include deleted ones) with 2+ consecutive
  // failures — a single failure is noise, it shows up in Recent runs / DLQ.
  const live = new Set(agents.map((a) => a.name));
  for (const a of agentMetrics
    .filter((m) => m.failure_streak >= 2 && live.has(m.agent))
    .sort((a, b) => b.failure_streak - a.failure_streak)) {
    attention.push({ key: `fail-${a.agent}`, sev: "bad", to: `/agents/${encodeURIComponent(a.agent)}?tab=report`,
      text: `${a.agent} failing — ${a.failure_streak} in a row` });
  }
  for (const a of agents.filter((x) => x.quarantined)) {
    attention.push({ key: `quar-${a.name}`, sev: "bad", to: `/agents/${encodeURIComponent(a.name)}`,
      text: `${a.name} is quarantined${a.error ? ` (${a.error})` : ""}` });
  }
  for (const a of agents.filter((x) => x.blocked)) {
    attention.push({ key: `blocked-${a.name}`, sev: "bad", to: `/agents/${encodeURIComponent(a.name)}`,
      text: `${a.name} ${a.blocked_reason ?? "blocked — unmet secret requirement"}` });
  }
  // A failing verification matters even on an "optional" secret — some skill
  // declared it; missing only alarms when the platform requires the secret.
  for (const s of secrets.filter((x) => x.status === "invalid" || (x.required && x.status === "missing"))) {
    attention.push({ key: `sec-${s.name}`, sev: "bad", to: "/secrets",
      text: `${s.name}: ${s.status}` });
  }
  if (kafka && !kafka.reachable) attention.push({ key: "broker", sev: "bad", to: "/reporting",
    text: "Kafka broker unreachable" });

  const claude = secrets.find((s) => s.name === "claude-credentials");

  return (
    <div className="page page-wide">
      <h1>Dashboard</h1>

      {/* System status: is the platform able to work right now? */}
      <div className="chip-row mb-2">
        <Chip variant={kafka && !kafka.reachable ? "danger" : "ok"}>
          broker: {kafka ? (kafka.reachable ? "up" : "down") : "…"}
        </Chip>
        <Chip variant={claude?.status === "valid" ? "ok" : "danger"}>
          claude token: {claude?.status ?? "…"}
        </Chip>
        <Chip>active runs: {ov?.active ?? "…"}</Chip>
        {(ov?.dlq ?? 0) > 0 && (
          <Link to="/dlq" className="no-underline"><Chip variant="danger">dlq: {ov?.dlq}</Chip></Link>
        )}
      </div>

      {/* Needs attention: the triage queue */}
      <h2>Needs attention</h2>
      {!loaded && <p className="muted">Loading…</p>}
      {loaded && attention.length === 0 && (
        <div className="rounded-md border border-success/40 px-3 py-2 text-sm text-success">
          ✓ All clear — nothing needs your attention.
        </div>
      )}
      {attention.length > 0 && (
        <ul className="attention-list">
          {attention.map((a) => (
            <li key={a.key} className={`attention-item attention-${a.sev}`}>
              <Link to={a.to}>{a.text} →</Link>
            </li>
          ))}
        </ul>
      )}

      {/* Activity glance */}
      <h2>Activity</h2>
      <StatRow>
        <Stat label="active" value={ov?.active ?? "—"} to="/runs" />
        <Stat label="runs · 24h" value={ov?.runs_24h ?? "—"} to="/runs" />
        <Stat label="success rate" value={pct(ov?.success_rate ?? null)}
              warn={ov?.success_rate != null && ov.success_rate < 0.8} to="/reporting" />
        <Stat label={ov ? `tokens in/out (uncached) · last ${ov.window} runs` : "tokens in/out"} value={ov ? `${ov.tokens_in.toLocaleString()} / ${ov.tokens_out.toLocaleString()}` : "—"} to="/reporting" />
      </StatRow>

      <div className="dash-cols">
        <section>
          <h2>Recent runs</h2>
          <Table>
            <thead><tr><TH>ID</TH><TH>Agent</TH><TH>State</TH><TH>Created</TH></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <TD><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></TD>
                  <TD><Link to={`/agents/${encodeURIComponent(r.agent)}`}>{r.agent}</Link></TD>
                  <TD><StatusChip status={r.state} /></TD>
                  <TD className="text-muted">{new Date(r.created_at).toLocaleString()}</TD>
                </tr>
              ))}
              {loaded && runs.length === 0 && <tr><TD colSpan={4} className="text-muted">No runs yet.</TD></tr>}
            </tbody>
          </Table>
        </section>

        <section>
          <h2>Upcoming</h2>
          <Table>
            <thead><tr><TH>Agent</TH><TH>Job</TH><TH>Next fire</TH></tr></thead>
            <tbody>
              {upcoming.map((u, i) => (
                <tr key={`${u.agent}-${u.name}-${i}`}>
                  <TD><Link to={`/agents/${encodeURIComponent(u.agent)}?tab=schedules`}>{u.agent}</Link></TD>
                  <UpcomingCell cron={u.cron} name={u.name} />
                  <TD className="text-muted" title={u.cron}>{u.next ? new Date(u.next).toLocaleString() : "—"}</TD>
                </tr>
              ))}
              {loaded && upcoming.length === 0 && <tr><TD colSpan={3} className="text-muted">Nothing scheduled.</TD></tr>}
            </tbody>
          </Table>
        </section>
      </div>
    </div>
  );
}
