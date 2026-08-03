import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AgentMetrics, type AgentSummary, type Integration, type KafkaHealth, type MetricsOverview, type ModelUsage, type Retention } from "../api";
import DurationChart from "../components/DurationChart";
import { Button } from "@ap/ui/button";
import { Chip, chipStatusVariant } from "@ap/ui/chip";
import { Select } from "@ap/ui/field";
import { Stat, StatRow } from "@ap/ui/stat";
import { Table, TD, TH } from "@ap/ui/table";

function IntegrationChip({ status }: { status: string }) {
  return <Chip variant={status === "missing" ? "danger" : chipStatusVariant(status)}>{status}</Chip>;
}

function pct(x: number | null): string {
  return x === null ? "—" : `${(x * 100).toFixed(0)}%`;
}
function dur(x: number | null): string {
  return x === null ? "—" : x >= 60 ? `${(x / 60).toFixed(1)}m` : `${x.toFixed(1)}s`;
}

export default function Reporting() {
  const [ov, setOv] = useState<MetricsOverview | null>(null);
  const [agents, setAgents] = useState<AgentMetrics[]>([]);
  const [liveAgents, setLiveAgents] = useState<Set<string>>(new Set());
  const [kafka, setKafka] = useState<KafkaHealth | null>(null);
  const [retention, setRetention] = useState<Retention | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [models, setModels] = useState<ModelUsage[]>([]);
  const [modelAgent, setModelAgent] = useState<string>("");   // "" = all agents
  const [error, setError] = useState<string | null>(null);
  const [pruning, setPruning] = useState(false);
  const [pruneMsg, setPruneMsg] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<MetricsOverview>("/api/metrics/overview"),
      api<AgentMetrics[]>("/api/metrics/agents"),
    ])
      .then(([o, a]) => { setOv(o); setAgents(a); })
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load metrics."));
    api<KafkaHealth>("/api/health/kafka").then(setKafka).catch(() => setKafka(null));
    api<Retention>("/api/maintenance/retention").then(setRetention).catch(() => setRetention(null));
    api<Integration[]>("/api/integrations").then(setIntegrations).catch(() => setIntegrations([]));
    api<AgentSummary[]>("/api/agents")
      .then((a) => setLiveAgents(new Set(a.map((x) => x.name))))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const qs = modelAgent ? `?agent=${encodeURIComponent(modelAgent)}` : "";
    api<ModelUsage[]>(`/api/metrics/models${qs}`).then(setModels).catch(() => setModels([]));
  }, [modelAgent]);

  async function prune() {
    setPruning(true);
    setPruneMsg(null);
    try {
      const r = await api<{ deleted: number }>("/api/maintenance/prune-transcripts", { method: "POST" });
      setPruneMsg(`Pruned ${r.deleted} transcript events.`);
    } catch (err) {
      setPruneMsg(err instanceof Error ? err.message : "Prune failed.");
    } finally {
      setPruning(false);
    }
  }

  return (
    <div className="page">
      <h1>Reporting</h1>
      <p className="muted">Platform health at a glance, and per-agent run metrics (last {ov?.window ?? 5000} runs).</p>
      {error && <div className="error">{error}</div>}

      <h2>Health</h2>
      <StatRow>
        <Stat label="broker" value={kafka ? (kafka.reachable ? "up" : "down") : "…"} warn={kafka ? !kafka.reachable : false} />
        <Stat label="dispatcher lag" value={kafka?.lag ?? "—"} warn={(kafka?.lag ?? 0) > 50} />
        <Stat label="active runs" value={ov?.active ?? "—"} />
        <Stat label="dlq depth" value={ov?.dlq ?? "—"} warn={(ov?.dlq ?? 0) > 0} />
      </StatRow>

      <h2>Integrations</h2>
      <Table>
        <thead><tr><TH>Integration</TH><TH>Status</TH><TH>Secret</TH><TH>Detail</TH></tr></thead>
        <tbody>
          {integrations.map((i) => (
            <tr key={i.name}>
              <TD>{i.name}</TD>
              <TD><IntegrationChip status={i.status} /></TD>
              <TD className="text-muted">{i.secrets.join(", ") || "—"}</TD>
              <TD className="text-muted">{i.detail}</TD>
            </tr>
          ))}
          {integrations.length === 0 && <tr><TD colSpan={4} className="text-muted">No integrations.</TD></tr>}
        </tbody>
      </Table>

      <h2>Runs</h2>
      {ov && (
        <StatRow>
          <Stat label="success rate" value={pct(ov.success_rate)} warn={ov.success_rate !== null && ov.success_rate < 0.8} />
          <Stat label="runs · 24h" value={ov.runs_24h} />
          <Stat label="runs · 7d" value={ov.runs_7d} />
          <Stat label="total" value={ov.total} />
          <Stat label="avg duration" value={dur(ov.avg_duration_seconds)} />
          <Stat label={`tokens in/out (uncached) · last ${ov.window} runs`} value={`${ov.tokens_in.toLocaleString()} / ${ov.tokens_out.toLocaleString()}`} />
        </StatRow>
      )}

      <h2>Seconds per run</h2>
      <DurationChart />

      <h2>Per agent</h2>
      <Table>
        <thead>
          <tr><TH>Agent</TH><TH>Runs</TH><TH>Success</TH><TH>Fail streak</TH><TH>Avg dur</TH><TH>Tokens (in/out)</TH><TH>Last run</TH></tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.agent}>
              <TD className="whitespace-nowrap">
                {liveAgents.has(a.agent)
                  ? <Link to={`/agents/${a.agent}`}>{a.agent}</Link>
                  : <span className="text-muted" title="This agent no longer exists; its history remains.">{a.agent} <Chip>deleted</Chip></span>}
              </TD>
              <TD>{a.total}</TD>
              <TD>{pct(a.success_rate)}</TD>
              <TD>{a.failure_streak > 0 ? <Chip variant="danger">{a.failure_streak}</Chip> : "0"}</TD>
              <TD>{dur(a.avg_duration_seconds)}</TD>
              <TD className="text-muted">{a.tokens_in.toLocaleString()}/{a.tokens_out.toLocaleString()}</TD>
              <TD className="text-muted">{a.last_run_at ? new Date(a.last_run_at).toLocaleString() : "—"}</TD>
            </tr>
          ))}
          {agents.length === 0 && <tr><TD colSpan={7} className="text-muted">No runs yet.</TD></tr>}
        </tbody>
      </Table>

      <h2>Tokens by model <span className="muted text-sm font-normal">(all time, incl. cache reads)</span></h2>
      <div className="row-actions" style={{ marginBottom: 8 }}>
        <label className="muted" htmlFor="model-agent-filter">Agent:</label>
        <Select id="model-agent-filter" aria-label="Filter models by agent" value={modelAgent}
                onChange={(e) => setModelAgent(e.target.value)}>
          <option value="">All agents</option>
          {agents.map((a) => <option key={a.agent} value={a.agent}>{a.agent}</option>)}
        </Select>
      </div>
      <Table>
        <thead><tr><TH>Model</TH><TH>Runs</TH><TH>Tokens in</TH><TH>Tokens out</TH></tr></thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.model}>
              <TD>{m.model}</TD>
              <TD>{m.runs}</TD>
              <TD className="text-muted">{m.tokens_in.toLocaleString()}</TD>
              <TD className="text-muted">{m.tokens_out.toLocaleString()}</TD>
            </tr>
          ))}
          {models.length === 0 && <tr><TD colSpan={4} className="text-muted">No model usage recorded yet.</TD></tr>}
        </tbody>
      </Table>

      <h2>Transcript retention</h2>
      <p className="muted">
        Run metadata is kept forever; bulky transcript events are pruned after their agent's
        retention (default {retention?.default_days ?? "—"} days, 0 = keep forever). Pruning runs
        daily; you can also run it now.
      </p>
      <div className="row-actions">
        <Button onClick={prune} disabled={pruning}>{pruning ? "Pruning…" : "Prune transcripts now"}</Button>
        {pruneMsg && <span className="muted">{pruneMsg}</span>}
      </div>
    </div>
  );
}
