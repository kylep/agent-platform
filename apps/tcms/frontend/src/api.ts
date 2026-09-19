// Typed client for the tcms app's browse API (apps/tcms/backend/tcmsapp/api.py).
// Same shape as the running app's api.ts: credentials included (the session
// cookie the nginx auth_request checks), JSON in and out. The types mirror
// the route views one for one — the UI is built on those, not on prose.

export const RESULT_STATUSES = ["pass", "fail", "skip", "flaky", "error"] as const;
export type ResultStatus = (typeof RESULT_STATUSES)[number];
export type Totals = Record<ResultStatus, number>;

export const LAYERS = ["unit", "integration", "e2e", "manual"] as const;
export type Layer = (typeof LAYERS)[number];

export type Suite = { name?: string; seconds?: number; exit?: number | null };

export type Run = {
  id: number;
  commit_sha: string;
  branch: string | null;
  run_id: string | null;
  agent: string | null;
  started_at: string | null;
  finished_at: string | null;
  verify_ok: boolean | null;
  suites: Suite[];
  published_at: string | null;
  totals: Totals;
  n: number;
  unlinked: number;
  seconds: number;
};

export type Result = {
  id: number;
  ref: string;
  path: string;
  name: string;
  case_key: string | null;
  status: ResultStatus;
  duration_ms: number;
  message: string;
  layer: Layer | null;
};

export type RunGroup = { file: string; failures: number; results: Result[] };
export type RunDetail = Run & { groups: RunGroup[] };

export type PassRatePoint = {
  test_run_id: number;
  commit_sha: string;
  started_at: string | null;
  pass: number;
  fail: number;
  total: number;
  rate: number | null;
};

export type CoveragePoint = {
  test_run_id: number;
  commit_sha: string;
  started_at: string | null;
  lines_covered: number;
  lines_total: number;
  pct: number;
};

export type CoverageSummary = {
  test_run_id: number | null;
  lines_covered: number;
  lines_total: number;
  pct: number;
  trend: CoveragePoint[];
};

export type PyramidLayer = { layer: "e2e" | "integration" | "unit"; count: number; seconds: number };

export type Overview = {
  latest_run: Run | null;
  pyramid: PyramidLayer[];
  pass_rate: PassRatePoint[];
  coverage: CoverageSummary;
  attention: { failing: number; flaky: number; unlinked: number; prune_candidates: number };
};

export type CaseRow = {
  key: string;
  suite: string;
  area: string;
  title: string;
  layer: Layer;
  priority: string;
  status: "active" | "retired";
  automation: string[];
  tags: string[];
  last_status: ResultStatus | null;
};

export type CaseList = { total: number; cases: CaseRow[] };

export type RefResult = {
  status: ResultStatus;
  duration_ms: number;
  message: string;
  commit_sha: string;
  started_at: string | null;
};

export type CaseRef = { ref: string; path: string; name: string; results: RefResult[] };

export type CaseDetail = CaseRow & {
  preconditions: string[];
  steps: string[];
  expected: string;
  tickets: string[];
  synced_at: string | null;
  source_sha: string | null;
  refs: CaseRef[];
  file: string;
};

export type FlakyRef = {
  ref: string;
  path: string;
  name: string;
  fails: number;
  passes: number;
  flaky: number;
  failing_commits: number;
  same_commit_flips: number;
};

export type HistoryPoint = {
  status: ResultStatus;
  duration_ms: number;
  commit_sha: string;
  started_at: string | null;
};

export type SlowRef = {
  ref: string;
  path: string;
  name: string;
  layer: Layer | null;
  avg_ms: number;
  max_ms: number;
  n: number;
  history: HistoryPoint[];
};

export type PruneCandidate = {
  ref: string;
  path: string;
  name: string;
  case_key: string | null;
  avg_ms: number;
  n: number;
  fails: number;
  reason: string;
};

// The face the platform draws for an agent (docs/design/23): what /api/agents
// sends back for each one. The app reads it, never derives it.
export type Face = { emoji: string; hue: number; image_url?: string | null };
export type AgentFaces = Record<string, Face>;

const BASE = "/apps/tcms/api";

export async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

/** name → face for every agent the platform lists; empty when the console's
 * API is out of reach, so a missing face is a plain disc and never an error. */
export async function agentFaces(): Promise<AgentFaces> {
  try {
    const r = await fetch("/api/agents", { credentials: "include" });
    if (!r.ok) return {};
    const rows = (await r.json()) as { name: string; face?: Face | null }[];
    const out: AgentFaces = {};
    for (const a of rows) if (a.face) out[a.name] = a.face;
    return out;
  } catch {
    return {};
  }
}
