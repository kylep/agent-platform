export type SecretStatus = { name: string; status: string; required: boolean };
export type SetupState = { needs_admin: boolean; secrets: SecretStatus[] };

export type AgentSummary = {
  name: string;
  description: string;
  quarantined: boolean;
  error: string | null;
  system: boolean;
  schedule: string;
};

export type AgentManifest = {
  role: string;
  concurrency: number;
  timeout_seconds: number;
  skills: string[];
  secrets: string[];
  description: string;
};

export type AgentDetail = {
  name: string;
  manifest: AgentManifest;
  agent_md: string;
  error: string | null;
};

export type RunSummary = {
  id: string;
  agent: string;
  state: string;
  trigger: string;
  created_at: string;
  summary: string | null;
  tags: string[];
};

export type RunDetailData = RunSummary & {
  prompt: string;
  exit_code: number | null;
  error: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tool_calls: number | null;
  started_at: string | null;
  finished_at: string | null;
  parent_run_id: string | null;
  depth: number;
  requested_by: string;
};

export type DlqEntry = {
  id: string;
  agent: string;
  trigger: string;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
};

export type KafkaHealth = {
  reachable: boolean;
  topics: string[];
  missing_topics: string[];
  lag: number | null;
  error: string | null;
  backlog: { queued: number; active: number; dlq: number };
};

export type RunEvent = Record<string, unknown> & { type?: string; terminal?: boolean };

export type PullRequest = {
  number: number;
  title: string;
  url: string;
  branch: string;
  author: string;
  created_at: string;
};

export type PullRequestFile = {
  filename: string;
  status: string;
  additions: number;
  deletions: number;
  patch: string;
};

export type ApiKey = {
  id: string;
  name: string;
  role: string;
  agent: string | null;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
};

export type ApiKeyMinted = ApiKey & { token: string };

export type ScheduleEntry = {
  agent: string;
  cron: string;
  enabled: boolean;
  last_fire: string | null;
  next_fire: string | null;
};

export type Memory = {
  id: string;
  agent: string;
  key: string | null;
  content: string;
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
};

export type Skill = {
  name: string;
  description: string;
  secrets: string[];
  error: string | null;
  used_by: string[];
};

export type SkillDetail = Skill & { body: string };

export type MetricsOverview = {
  total: number;
  by_state: Record<string, number>;
  active: number;
  succeeded: number;
  success_rate: number | null;
  tokens_in: number;
  tokens_out: number;
  tool_calls: number;
  avg_duration_seconds: number | null;
  max_duration_seconds: number | null;
  last_run_at: string | null;
  runs_24h: number;
  runs_7d: number;
  dlq: number;
  window: number;
};

export type AgentMetrics = {
  agent: string;
  total: number;
  succeeded: number;
  success_rate: number | null;
  failure_streak: number;
  tokens_in: number;
  tokens_out: number;
  tool_calls: number;
  avg_duration_seconds: number | null;
  last_run_at: string | null;
};

export type Retention = {
  default_days: number;
  per_agent_days: Record<string, number>;
};

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const isAuthCall = path.startsWith("/api/login") || path.startsWith("/api/setup");
  if (res.status === 401) {
    if (!isAuthCall) window.location.href = "/login";
    throw new Error("401");
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
