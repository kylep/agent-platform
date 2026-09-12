export type SecretKeyField = { name: string; hint?: string };
export type SecretStatus = { name: string; status: string; declared: boolean; required: boolean; hint?: string; key?: string; probeable?: boolean; keys?: SecretKeyField[] };
export type SetupState = { needs_admin: boolean; secrets: SecretStatus[] };

// --- Agents (DB-first — docs/design/15) -------------------------------------
// An agent IS its row: prompt, config, grants and entrypoints all live in
// `agent_defs` and are edited directly (no PR round-trip). Every write appends
// a snapshot to the change log below.

export type CronEntry = { schedule: string; prompt: string };

// How a declared webhook path authenticates callers (docs/design/16).
// `none` = a platform operator key, as before; `secret` additionally accepts
// the shared secret in the `X-AP-Webhook-Secret` header.
export type WebhookAuth = "none" | "secret";

// The entry carries the MODE only — the secret VALUE lives in its own
// write-only endpoint and never on the definition, which is snapshotted into
// the change log on every write. `secret_set` is derived by the API on GET;
// the editor echoes it back on PUT, where the server accepts and drops it.
export type WebhookEntry = { path: string; auth: WebhookAuth; secret_set?: boolean };

export type AgentEntrypoints = {
  crons: CronEntry[];
  webhooks: WebhookEntry[];
  topics: string[];
  timezone: string;      // IANA zone the crons are read in; "" = UTC
};

// `entrypoints` is a JSON column the API returns VERBATIM: it deliberately
// stopped validating the blob on the way out so a row whose shape went wrong
// (raw SQL, a restore, a bad migration) can still be read and repaired. The
// type above is what a well-formed row holds, not a guarantee — so anything
// walking a list out of that blob checks first. A warped agent costs itself a
// schedule cell, never the whole page.
export function asList<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

export type AgentDef = {
  name: string;
  prompt: string;               // the agent's context/personality (former agent.md body)
  description: string;
  model: string;                // "" = platform default
  role: string;
  system: boolean;
  can_invoke: boolean;
  concurrency: number;
  timeout_seconds: number;
  result_topic: string;
  transcript_retention_days: number | null;   // null = platform default
  harness_tools: string[];      // Claude Code tools (Bash, WebFetch, …)
  platform_tools: string[];     // mcp__…__ tools via the broker
  skills: string[];
  secrets: string[];
  entrypoints: AgentEntrypoints;
  enabled: boolean;
};

// The listing carries each agent's full definition plus server-derived
// readiness. Definition fields are optional so the list keeps rendering if the
// API trims the payload; readiness fields are never part of the row.
export type AgentSummary = Partial<AgentDef> & {
  name: string;
  quarantined?: boolean;
  error?: string | null;
  // Blocked = unmet required secret dependency (fix the secret);
  // quarantined = broken definition (fix the agent).
  blocked?: boolean;
  blocked_reason?: string | null;
  schedule?: string;            // pre-rendered cron summary, when the API sends one
};

// One row of the append-only change log (no snapshot in the listing).
export type AgentVersion = {
  version: number;
  changed_by: string;           // verified principal — never self-reported
  changed_via: string;          // admin | tool:agents_edit | import | rollback | …
  created_at: string;
};

// GET …/versions/{n}. The snapshot may arrive nested (`{version, snapshot}`)
// or as the bare definition object; readers handle both.
export type AgentVersionDetail = Partial<AgentVersion> & {
  snapshot?: Record<string, unknown>;
} & Record<string, unknown>;

// A grantable tool with what enabling it actually does (/api/help/tools).
export type ToolHelp = {
  name: string;
  kind: string;                 // claude (harness) | platform (brokered)
  description: string;
  sensitive: boolean;           // runner denies it for non-self-edit agents
  display_name?: string | null;
};

export type EditResult = {
  tier: number;
  branch: string | null;
  changes: string[];
  pr: { number: number; url: string } | null;
};

export type SyncStatus = { sha: string | null };

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
  result: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  tool_calls: number | null;
  started_at: string | null;
  finished_at: string | null;
  parent_run_id: string | null;
  depth: number;
  requested_by: string;
  initiated_by?: string | null;
  secrets_granted: string[];
  permission_denials?: Array<Record<string, unknown>>;
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

export type RunEvent = Record<string, unknown> & { type?: string; terminal?: boolean; seq?: number };

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

export type Job = {
  id: string;
  name: string;
  agent: string;
  cron: string;
  timezone: string;       // IANA zone the cron is read in; "" = UTC
  prompt: string;
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
  icon: string;
  secrets: string[];
  error: string | null;
  used_by: string[];
};

export type SkillDetail = Skill & { body: string; raw: string };

export type Tool = {
  name: string;
  description: string;
  secrets: string[];
  database: boolean;
  has_requirements: boolean;
  timeout_seconds: number;
  error: string | null;
  used_by: string[];
};

export type ToolDetail = Tool & { params: Record<string, unknown>; files: Record<string, string> };

export type ToolMetrics = {
  tool: string;
  calls: number;
  denials: number;
  errors: number;
  avg_latency_ms: number;
};

export type MetricsOverview = {
  total: number;
  by_state: Record<string, number>;
  active: number;
  succeeded: number;
  success_rate: number | null;
  tokens_in: number;
  tokens_out: number;
  tokens_cache_read: number;
  tokens_cache_creation: number;
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
  last_failed_at: string | null;   // newest terminal non-success; null if none
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

export type Integration = {
  name: string;
  kind: string;
  secrets: string[];
  configured: boolean;
  status: "working" | "configured" | "missing";
  detail: string;
};

export type ModelUsage = {
  model: string;
  runs: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cache_read: number;
  tokens_cache_creation: number;
};

export type ModelOption = {
  id: string;
  label: string;
};

// --- Relay (docs/design/19) --------------------------------------------------
// The agent messenger. A participant is `<namespace>:<id>` — `agent:news`,
// `user:kyle`, `discord:1529…` — and never a self-reported name: the API
// attributes a message from the caller's token, so these strings are the one
// identity the UI can trust.

export type RelayFace = { emoji: string; hue: number };

export type RelayReaction = { emoji: string; count: number; mine: boolean };

export type RelayLastMessage = {
  id: string;
  author: string;
  body: string;             // truncated by the API — a rail preview, not the message
  created_at: string | null;
};

export type RelayChannel = {
  id: string;
  kind: string;             // dm | channel | group
  name: string | null;      // slug, channels only
  // A group's given name, when it has one. Absent on older API builds, which
  // is why the UI can still name a group by who is in it.
  title?: string | null;
  topic: string;
  // An open channel carries NO participant rows (every agent and human is in
  // it), which is why this travels rather than being inferred from the list.
  open: boolean;
  archived_at: string | null;
  agent: string | null;     // legacy single-agent column; set on DMs
  participants: string[];
  last_message: RelayLastMessage | null;
  message_count: number;
  unread: number;
};

export type RelayChannelDetail = RelayChannel & { faces: Record<string, RelayFace> };

export type RelayMessage = {
  id: string;
  channel_id: string;
  author: string;
  kind: string;             // text | system | event
  body: string;
  // `event` rows carry a rendered card instead of prose. Unvalidated JSON from
  // whatever posted it, so the pane reads title/body defensively.
  card: { title?: string; body?: string } | null;
  reply_to: string | null;
  thread_root: string | null;
  run_id: string | null;    // the run that wrote this, on agent messages
  hop: number;
  mentions: string[];
  created_at: string | null;
  edited_at: string | null;
  face: RelayFace | null;   // agents only — derive the rest with lib/face
  reactions: RelayReaction[];
};

export type RelayPresence = {
  agent: string;
  state: string;            // idle | thinking | quarantined | disabled
  thinking_in: string[];    // channel ids the agent has an active run in
  face: RelayFace;
};

export type RelayStats = {
  messages_24h: number;
  agent_messages_24h: number;
  invocations_24h: number;
  // REFUSED mentions only — hop_limit, budget, not_member. A coalesced wake is
  // a healthy suppression (the agent answers once instead of three times) and
  // is deliberately not counted here.
  suppressed_24h: number;
  // Every suppression reason of the last day, zero-filled — including the
  // ROUTINE ones (`coalesced`, `facade_owns_turn`) that the count above
  // deliberately leaves out, so a reader can tell the two apart.
  suppressed_by_reason: Record<string, number>;
  budget: { channel_per_hour: number; global_per_hour: number; global_used_last_hour: number };
  settings: {
    default_grant: boolean;
    max_hops: number;
    channel_per_hour: number;
    global_per_hour: number;
    cooldown_seconds: number;
  };
};

// Verified caller identity. `principal` is what a human's participant string
// is built from (`user:<principal>`), so it is how the pane knows "you".
export type WhoAmI = {
  principal: string;
  role: string;
  agent: string | null;
  run_id: string | null;
  initiated_by?: string | null;
  tools: string[] | null;
};

export type ReportType = {
  name: string;
  description: string;
  icon: string;
  generator: string;
  cadence: string;          // daily | intraday | adhoc
  retention_days: number;
  error: string | null;
  count: number;
  latest_date: string | null;
};

export type ReportMeta = {
  id: string;
  type: string;
  date: string;             // YYYY-MM-DD
  time: string;             // HH-MM, "" for daily
  title: string;
  meta: Record<string, unknown>;
  run_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type ReportDetail = ReportMeta & { html: string };

export type AppView = {
  name: string;
  description: string;
  icon: string;
  ui: boolean;
  api: boolean;
  postgres: boolean;
  kafka_topics: string[];
  redis: boolean;
  agent_key_role: string | null;
  error: string | null;
  ready: boolean | null;      // null = not deployed / unknown
  ready_replicas: number;
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
