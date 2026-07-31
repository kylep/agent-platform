import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, type AgentMetrics, type AgentSummary, type Job, type KafkaHealth,
  type MetricsOverview, type PullRequest, type RunSummary, type ScheduleEntry,
  type SecretStatus,
} from "../api";
import { stateChipClass } from "./Runs";

// One actionable item in the "Needs attention" panel.
type Attn = { key: string; text: string; to: string; sev: "warn" | "bad" };

function pct(x: number | null): string { return x === null ? "—" : `${(x * 100).toFixed(0)}%`; }

function Stat({ label, value, warn, to }: { label: string; value: string | number; warn?: boolean; to?: string }) {
  const inner = (
    <div className={warn ? "stat stat-warn" : "stat"}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
  return to ? <Link to={to} className="stat-link">{inner}</Link> : inner;
}

function StatusPill({ label, value, bad }: { label: string; value: string; bad: boolean }) {
  return <span className={`chip ${bad ? "chip-invalid" : "chip-ok"}`}>{label}: {value}</span>;
}

export default function Dashboard() {
  const [ov, setOv] = useState<MetricsOverview | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [kafka, setKafka] = useState<KafkaHealth | null>(null);
  const [agentMetrics, setAgentMetrics] = useState<AgentMetrics[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [secrets, setSecrets] = useState<SecretStatus[]>([]);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [upcoming, setUpcoming] = useState<{ agent: string; name: string; next: string | null; cron: string }[]>([]);
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
        ...scheds.filter((s) => s.enabled).map((s) => ({ agent: s.agent, name: "(manifest)", next: s.next_fire, cron: s.cron })),
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
  for (const s of secrets.filter((x) => x.required && (x.status === "missing" || x.status === "invalid"))) {
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
      <div className="chip-row" style={{ marginBottom: 8 }}>
        <StatusPill label="broker" value={kafka ? (kafka.reachable ? "up" : "down") : "…"} bad={!!kafka && !kafka.reachable} />
        <StatusPill label="claude token" value={claude?.status ?? "…"} bad={claude?.status === "invalid" || claude?.status === "missing"} />
        <span className="chip">active runs: {ov?.active ?? "…"}</span>
        {(ov?.dlq ?? 0) > 0 && <Link to="/dlq" className="chip chip-invalid">dlq: {ov?.dlq}</Link>}
      </div>

      {/* Needs attention: the triage queue */}
      <h2>Needs attention</h2>
      {!loaded && <p className="muted">Loading…</p>}
      {loaded && attention.length === 0 && (
        <div className="banner banner-ok">✓ All clear — nothing needs your attention.</div>
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
      <div className="stat-row">
        <Stat label="active" value={ov?.active ?? "—"} to="/runs" />
        <Stat label="runs · 24h" value={ov?.runs_24h ?? "—"} to="/runs" />
        <Stat label="success rate" value={pct(ov?.success_rate ?? null)}
              warn={ov?.success_rate != null && ov.success_rate < 0.8} to="/reporting" />
        <Stat label="tokens in/out" value={ov ? `${ov.tokens_in}/${ov.tokens_out}` : "—"} to="/reporting" />
      </div>

      <div className="dash-cols">
        <section>
          <h2>Recent runs</h2>
          <table className="table">
            <thead><tr><th>ID</th><th>Agent</th><th>State</th><th>Created</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td><Link to={`/runs/${r.id}`}>{r.id.slice(0, 8)}</Link></td>
                  <td><Link to={`/agents/${encodeURIComponent(r.agent)}`}>{r.agent}</Link></td>
                  <td><span className={`chip ${stateChipClass(r.state)}`}>{r.state}</span></td>
                  <td className="muted">{new Date(r.created_at).toLocaleString()}</td>
                </tr>
              ))}
              {loaded && runs.length === 0 && <tr><td colSpan={4} className="muted">No runs yet.</td></tr>}
            </tbody>
          </table>
        </section>

        <section>
          <h2>Upcoming</h2>
          <table className="table">
            <thead><tr><th>Agent</th><th>Job</th><th>Next fire</th></tr></thead>
            <tbody>
              {upcoming.map((u, i) => (
                <tr key={`${u.agent}-${u.name}-${i}`}>
                  <td><Link to={`/agents/${encodeURIComponent(u.agent)}?tab=schedules`}>{u.agent}</Link></td>
                  <td>{u.name}</td>
                  <td className="muted" title={u.cron}>{u.next ? new Date(u.next).toLocaleString() : "—"}</td>
                </tr>
              ))}
              {loaded && upcoming.length === 0 && <tr><td colSpan={3} className="muted">Nothing scheduled.</td></tr>}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
