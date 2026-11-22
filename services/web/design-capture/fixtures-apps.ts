import { readFileSync } from "node:fs";
import type { Activity, Prs, Summary as RunningSummary } from "../../../apps/running/frontend/src/api.ts";
import type { Summary as StockSummary, SymbolView } from "../../../apps/stockmarket/frontend/src/api.ts";
import type { Overview, CaseDetail, RunDetail } from "../../../apps/tcms/frontend/src/api.ts";
import type { BeliefDetail, BeliefRow, Feedback, PredictionDetail, PredictionRow, Review } from "../../../apps/judgment/frontend/src/api.ts";

const timestamp = "2026-11-16T14:00:00Z";
const day = (ago: number) => new Date(Date.UTC(2026, 10, 16 - ago)).toISOString().slice(0, 10);
const fixture = JSON.parse(readFileSync(new URL(
  "../../../apps/stockmarket/frontend/src/fixtures/backtests.json", import.meta.url,
), "utf8"));

const calendarDays = Array.from({ length: 182 }, (_, i) => {
  const active = i % 4 < 2 || i % 7 === 1;
  return { day: day(181 - i), distance_km: active ? (i % 8) + 4.2 : 0, count: active ? 1 : 0 };
});
const activities: Activity[] = ["Easy riverside loop", "Long run", "Morning tempo", "Recovery jog"].map((name, i) => ({
  id: 21 - i, day: day(i * 2), name, type: "Run", distance_km: [6.4, 15.2, 8.1, 4.3][i],
  moving_time_s: [2112, 5280, 2580, 1560][i], pace: "5:30/km",
  elevation_m: 44 + i * 12, avg_hr: 146 + i,
}));
const runningSummary: RunningSummary = {
  totals: { total_km: 712.5, total_elevation_m: 8240, total_moving_time_s: 239261,
    activities: 106, runs: 101, everests: 0.93, comparison: "About 17 marathons" },
  latest_day: day(0), latest_brief_week: day(7), sync_after: day(0), today: day(0), tags: ["base training"],
};
const personalRecords: Prs = {
  longest_run: { km: 24.3, day: day(43) }, fastest_5k: { pace: "4:39/km", day: day(18) },
  fastest_10k: { pace: "4:56/km", day: day(26) }, biggest_week: { km: 47.8, week_start: day(35) },
  longest_streak: 5, current_streak: 2,
};
const running: Record<string, unknown> = {
  "/summary": runningSummary,
  "/calendar": { weeks: 26, days: calendarDays },
  "/weekly": { weeks: Array.from({ length: 12 }, (_, i) => ({
    week_start: day((12 - i) * 7), distance_km: 20 + (i * 17 % 28), runs: 3 + (i % 3), moving_time_s: 6000 + i * 190,
  })) },
  "/prs": personalRecords,
  "/briefs": [{ week_start: day(7), body: "Steady aerobic volume this week. Keep the next long run easy and leave a recovery day after the tempo session.",
    highlights: ["4 runs · 36.8 km", "Longest run: 15.2 km"], tags: ["base training"], distance_km: 36.8, runs: 4, run_id: null }],
  "/activities": activities,
  "/health": { ok: true, activity_count: 106, brief_count: 19, latest_activity_day: day(0),
    pipeline: { consumer_started: true, consumer_assigned: true, last_message_at: timestamp,
      last_activity_sync_at: timestamp, last_error: null } },
};

const symbol = (symbol: string, label: string, kind: SymbolView["kind"], latest_close: number, change_pct: number): SymbolView =>
  ({ symbol, label, kind, status: "ok", error: "", latest_day: day(0), latest_close, change_pct });
const stockSummary: StockSummary = {
  indexes: [symbol("SPY", "S&P 500", "index", 604.52, 0.86), symbol("QQQ", "Nasdaq 100", "index", 533.71, 1.24)],
  watchlist: [symbol("MSFT", "Microsoft", "watch", 427.91, -0.32), symbol("VTI", "Total US stock market", "watch", 296.83, 0.64)],
  latest_day: day(0), latest_brief_day: day(0), tags: ["closing brief"],
};
function stockmarket(path: string, query: URLSearchParams): unknown {
  if (path === "/summary") return stockSummary;
  if (path === "/briefs") return [{ day: day(0), tags: ["closing brief"], run_id: null,
    body: "Large-cap technology led the session while the broader market rose modestly.",
    indexes: [{ symbol: "SPY", return_pct: 0.86, note: "Broad gains across sectors." }], movers: [] }];
  if (path === "/series") {
    return [...stockSummary.indexes, ...stockSummary.watchlist].map(({ symbol, latest_close }) => ({
      symbol, downsampled: false, points: Array.from({ length: 45 }, (_, i) =>
        [day(44 - i), (latest_close ?? 100) * (0.9 + i * 0.002 + Math.sin(i / 2) * 0.012)]),
    }));
  }
  if (path === "/backtests") return fixture.list;
  const [, id, sub] = /^\/backtests\/([^/]+)(?:\/([^/]+))?$/.exec(path) ?? [];
  const detail = fixture.detail[id];
  if (!detail || !sub) return detail;
  const strategyId = query.get("strategy") ?? "s1";
  if (sub === "spec") return { id, spec: detail.spec };
  if (sub === "series") return fixture.series[id]?.[strategyId];
  if (sub === "events") {
    const events = fixture.events[id]?.[strategyId] ?? [];
    const page = Number(query.get("page") ?? 1);
    return { strategy_id: strategyId, events: events.slice((page - 1) * 100, page * 100),
      page, page_size: 100, has_more: events.length > page * 100 };
  }
  if (sub === "metrics") return { strategy_id: strategyId,
    metrics: detail.strategies.find((s: { id: string }) => s.id === strategyId)?.metrics };
  return undefined;
}

const result = {
  id: 1, ref: "smoke-dashboard", path: "console/smoke.spec.ts", name: "Dashboard loads",
  case_key: "WEB-1", status: "pass" as const, duration_ms: 820, message: "", layer: "e2e" as const,
};
const tcmsRun: RunDetail = {
  id: 42, commit_sha: "bcb8f19e3a4db0c2", branch: "console-smoke", run_id: null, agent: "health-monitor",
  started_at: timestamp, finished_at: timestamp, verify_ok: true,
  suites: [{ name: "Console smoke", seconds: 12.4, exit: 0 }], published_at: timestamp,
  totals: { pass: 46, fail: 0, skip: 1, flaky: 1, error: 0 }, n: 48, unlinked: 2, seconds: 12.4,
  groups: [{ file: result.path, failures: 0, results: [
    result, { ...result, id: 2, ref: "smoke-relay", name: "Relay opens a thread", case_key: "WEB-2", duration_ms: 1100 },
  ] }],
};
const tcmsOverview: Overview = {
  latest_run: tcmsRun,
  pyramid: [{ layer: "unit", count: 36, seconds: 3 }, { layer: "integration", count: 8, seconds: 4 }, { layer: "e2e", count: 4, seconds: 5.4 }],
  pass_rate: Array.from({ length: 7 }, (_, i) => ({
    test_run_id: 36 + i, commit_sha: tcmsRun.commit_sha, started_at: day(6 - i), pass: 46 - i % 3,
    fail: i % 3, total: 48, rate: (46 - i % 3) / 48,
  })),
  coverage: { test_run_id: 42, lines_covered: 840, lines_total: 1000, pct: 84,
    trend: Array.from({ length: 7 }, (_, i) => ({
      test_run_id: 36 + i, commit_sha: tcmsRun.commit_sha, started_at: day(6 - i),
      lines_covered: 810 + i * 5, lines_total: 1000, pct: 81 + i * 0.5,
    })) },
  attention: { failing: 0, flaky: 1, unlinked: 2, prune_candidates: 1 },
};
const caseDetail: CaseDetail = {
  key: "WEB-1", suite: "Console smoke", area: "Dashboard", title: "Dashboard loads", layer: "e2e",
  priority: "high", status: "active", automation: [result.ref], tags: ["console", "smoke"], last_status: "pass",
  preconditions: ["A fixture administrator session is active."],
  steps: ["Open the dashboard.", "Wait for the latest run activity."],
  expected: "The dashboard shows the recent runs and platform health.",
  tickets: ["OPS-1"], synced_at: timestamp, source_sha: tcmsRun.commit_sha, file: "cases/WEB-1.yaml",
  refs: [{ ref: result.ref, path: result.path, name: result.name,
    results: [{ status: "pass", duration_ms: 820, message: "", commit_sha: tcmsRun.commit_sha, started_at: timestamp }] }],
};
const tcms: Record<string, unknown> = {
  "/overview": tcmsOverview,
  "/runs": [tcmsRun, { ...tcmsRun, id: 41, branch: "main", started_at: day(1) }],
  "/runs/42": tcmsRun,
  "/cases": { total: 2, cases: [
    caseDetail, { ...caseDetail, key: "WEB-2", title: "Relay opens a thread", area: "Relay", last_status: "flaky" },
  ] },
  "/cases/WEB-1": caseDetail,
  "/slowest": [{ ...result, avg_ms: 820, max_ms: 1121, n: 30, history: caseDetail.refs[0].results }],
  "/flaky": [{ ...result, name: "Relay opens a thread", fails: 2, passes: 25, flaky: 1, failing_commits: 1, same_commit_flips: 1 }],
  "/prune-candidates": [{ ...result, avg_ms: 820, n: 30, fails: 0, reason: "Covered by the navigation sweep." }],
};

const belief: BeliefRow = {
  id: "b1", status: "active", current_version: 2, created_at: timestamp, confirmed: null,
  current: { id: "v2", belief_id: "b1", version: 2, claim: "Fixture quality rises with small deterministic datasets",
    scope: "Browser capture and smoke tests.", evidence: "The sampled routes load consistently in isolation.",
    provenance: "observed", source_ref: null, confidence: "high", reason: "Reviewed the capture harness.",
    feedback_id: null, author: "kai", created_at: timestamp },
};
const prediction: PredictionRow = {
  id: "p1", created_at: timestamp, scenario: "Nightly test run completes in ten minutes",
  alternatives: ["Finishes in ten minutes", "Finishes later"],
  predicted_choice: "Finishes in ten minutes", rationale: "Fixture setup is local and deterministic.",
  confidence: "medium", timing: "prospective", question_ref: null, author: "kai",
  resolution: null, flags: [], feedback_count: 1,
};
const feedback: Feedback = {
  id: "f1", created_at: timestamp, prediction_id: prediction.id, belief_id: belief.id,
  belief_version: 2, kyle_words: "Keep the setup fast and preserve the checks that catch failures.",
  source_ref: null, source_at: timestamp, outcome: "supported", interpretation: "Keep the route checks focused.",
  author: "kai", confirmed_at: null,
};
const beliefDetail: BeliefDetail = {
  belief, versions: [belief.current!, { ...belief.current!, id: "v1", version: 1, confidence: "medium" }],
  predictions: [prediction], feedback: [feedback],
};
const predictionDetail: PredictionDetail = { prediction, resolution: "supported", flags: [],
  links: [{ belief_id: belief.id, belief_version: 2, claim: belief.current!.claim }], feedback: [feedback] };
const review: Review = {
  counts: { prospective_resolved: { supported: 1, contradicted: 0, mixed: 0, context_changed: 0 },
    prospective_resolved_confirmed: 0, prospective_resolved_relayed_only: 1, prospective_pending: 1,
    retrospective: 0, flagged: 0, feedback_unconfirmed: 1 },
  items: [{ prediction, feedback: [feedback], resolution: "supported", flags: [] }],
};
const judgment: Record<string, unknown> = {
  "/me": { principal: "kyle" },
  "/beliefs": [belief],
  "/beliefs/b1": beliefDetail,
  "/predictions": [prediction],
  "/predictions/p1": predictionDetail,
  "/feedback": [feedback],
  "/review": review,
};

export function appFixtures(source: string, url: URL): unknown {
  const base = `/apps/${source}/api`;
  if (!url.pathname.startsWith(`${base}/`)) return undefined;
  const path = url.pathname.slice(base.length);
  if (source === "running") return running[path];
  if (source === "stockmarket") return stockmarket(path, url.searchParams);
  if (source === "tcms") return tcms[path];
  if (source === "judgment") return judgment[path];
  return undefined;
}
