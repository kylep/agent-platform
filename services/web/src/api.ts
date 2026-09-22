export type SecretKeyField = { name: string; hint?: string };
export type SecretStatus = { name: string; status: string; declared: boolean; required: boolean; hint?: string; key?: string; probeable?: boolean; keys?: SecretKeyField[] };
export type SetupState = { needs_admin: boolean; secrets: SecretStatus[] };

// --- Agents (DB-first — docs/design/15) -------------------------------------
// An agent IS its row: prompt, config, grants and entrypoints all live in
// `agent_defs` and are edited directly (no PR round-trip). Every write appends
// a snapshot to the change log below.

export type CronEntry = { schedule: string; prompt: string; model: string };

// How a declared webhook path authenticates callers (docs/design/16).
// `none` = a platform operator key, as before; `secret` additionally accepts
// the shared secret in the `X-AP-Webhook-Secret` header.
export type WebhookAuth = "none" | "secret";

// The entry carries the MODE only — the secret VALUE lives in its own
// write-only endpoint and never on the definition, which is snapshotted into
// the change log on every write. `secret_set` is derived by the API on GET;
// the editor echoes it back on PUT, where the server accepts and drops it.
export type WebhookEntry = { path: string; auth: WebhookAuth; model: string; secret_set?: boolean };

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
  runtime: "claude" | "codex";
  model: string;                // "" = platform default
  role: string;
  system: boolean;
  responds_to_all: boolean;
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
  // The Workbench (docs/design/24). Two grants: the fnmatch globs a dev run
  // may land without review (empty = every publish is a PR) and whether a
  // publish may delete a test file. Two edit fields: the usage percentages
  // above which a dev run declines to start.
  push_path_globs: string[];
  may_delete_tests: boolean;
  quota_5h_max_pct: number;
  quota_7d_max_pct: number;
};

// The listing carries each agent's full definition plus server-derived
// readiness. Definition fields are optional so the list keeps rendering if the
// API trims the payload; readiness fields are never part of the row.
export type AgentSummary = Partial<AgentDef> & {
  name: string;
  // The agent's picture (docs/design/23): an image artifact's id, and the face
  // the API derived from it — `image_url` set when there is one to wear.
  image_artifact_id?: string | null;
  face?: RelayFace | null;
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
  sensitive: boolean;           // runner permits it only in Workbench runs
  display_name?: string | null;
  dev_only?: boolean;           // only a `role: dev` run gets it (docs/design/25)
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
  // The ticket this run was summoned from (docs/design/20); null for every
  // other trigger.
  ticket_id?: string | null;
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
  runtime: string;
  requested_model: string;
  model: string;
  agent_version: number | null;
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
  // The Workbench's chips (docs/design/24): the ticket parsed off a `coder/`
  // or `qa/` head, the agent from the PR body's platform header, and whether
  // GitHub holds an auto-merge request (null when the PR object did not say).
  ticket_key?: string | null;
  agent?: string | null;
  auto_merge?: boolean | null;
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
  // Exactly one of these is set. An agent job runs `agent` with `prompt`; a
  // relay job posts `prompt` into the named Relay channel as the platform
  // (docs/design/19) — the #standup summons is one, and it has no agent because
  // an agent-authored `@all` never reaches the room.
  agent: string | null;
  relay_channel: string | null;
  cron: string;
  timezone: string;       // IANA zone the cron is read in; "" = UTC
  prompt: string;
  model: string;
  enabled: boolean;
  last_fire: string | null;
  next_fire: string | null;
};

/** How a job's target reads in a table. A relay job names its room, because
 * "—" in the Agent column tells a reader nothing about what fires at 09:00. */
export const jobTarget = (j: Pick<Job, "agent" | "relay_channel">) =>
  j.agent ?? `Relay → #${j.relay_channel ?? "?"}`;

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

// `image_url` is the agent's picture (docs/design/23) — the thumb route of
// its image artifact — which every face consumer shows over the emoji when it
// is set. Optional because a face derived client-side (lib/face) has none.
export type RelayFace = { emoji: string; hue: number; image_url?: string | null };

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
  home?: string;             // relay | external
  reply_mode?: string;       // linear | threaded
  dispatch_mode?: string;    // facade | mentions | default
  default_agent?: string | null;
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

export type RelayBinding = {
  id: string;
  connector: string;
  external_ref: string;
  external_kind: string;
  parent_external_ref?: string | null;
  display_name?: string;
  external_url?: string;
  status?: string;
  config: Record<string, unknown>;
};

export type RelayChannelDetail = RelayChannel & {
  faces: Record<string, RelayFace>;
  bindings?: RelayBinding[];
  display_names?: Record<string, string>;
};

/** An event row's card. `type` names the shape the rest is in: the platform's
 * own `ticket` cards (docs/design/20) carry the board fields below, and
 * anything else is a title and a markdown body. Every field is optional —
 * whatever posted the event chose them, so nothing here is guaranteed. */
export type RelayCard = {
  title?: string;
  body?: string;
  type?: string;
  key?: string;
  state?: string;
  priority?: string;
  assignee?: string | null;
  url?: string;
  // The `#art` card (docs/design/23): the generated image, who asked for it,
  // on which model, with the prompt already flattened by the API.
  artifact_id?: string;
  owner?: string;
  model?: string;
  prompt?: string;
  // The publish card (docs/design/24): what a dev run landed, or why it was
  // refused — `pr`/`url` null on a refusal, `verify_ok` null when nothing ran.
  pr?: number | null;
  branch?: string;
  files?: number;
  tests_removed?: string[];
  verify_ok?: boolean | null;
  refused_reason?: string | null;
  run_id?: string | null;
  agent?: string;
  warnings?: string[];
};

export type RelayMessage = {
  id: string;
  channel_id: string;
  author: string;
  kind: string;             // text | system | event
  body: string;
  // `event` rows carry a rendered card instead of prose. Unvalidated JSON from
  // whatever posted it, so the pane reads every field defensively.
  card: RelayCard | null;
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
    context_messages: number;
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

// --- Artifacts (docs/design/23) ---------------------------------------------
// A picture or a file the platform keeps: a row with bytes behind it. The
// view is metadata only — the thumb and the content are the two byte routes
// below, never a JSON field. `[[artifact:<id>]]` in a message is its card.

export type ArtifactKind = "image" | "file";
export type ArtifactSource = "upload" | "generated" | "derived" | "tool";

export type Artifact = {
  id: string;                  // 32 lowercase hex chars
  name: string;
  mime: string;                // what the bytes are, never what was claimed
  size: number;
  sha256: string;
  kind: ArtifactKind;
  width: number | null;
  height: number | null;
  owner: string;               // a participant string: agent:<name> | user:<principal>
  run_id: string | null;
  source: ArtifactSource;
  // A generated image's provenance lives here (provider, model, prompt,
  // params, seed, cost_usd, duration_ms, reference_ids); a derived one names
  // its `parent_id`. Whatever produced the row chose the keys, so a reader
  // checks before it trusts one.
  meta: Record<string, unknown>;
  tags: string[];
  created_at: string | null;
  deleted_at: string | null;
  thumb_url: string | null;    // images only
  content_url: string;
};

export type ArtifactStats = {
  count: number;
  bytes: number;
  total_cap: number;
  generated_this_month: number;
  spend_this_month_usd: number;
  spend_today_usd: number;
  daily_cap_usd: number;
};

/** A registry entry × whether its provider's key is set. `sizes` or `aspects`,
 * never both — which one says what geometry the model takes. */
export type ImageModel = {
  id: string;
  provider: string;
  label: string;
  price_usd: number;
  sizes: string[] | null;
  aspects: string[] | null;
  custom_size: boolean;
  qualities: string[] | null;
  edits: boolean;
  configured: boolean;
  default: boolean;
  billing?: "api" | "codex";
  seeded?: boolean;
};

export type GenerateIn = {
  prompt: string;
  model?: string | null;
  size?: string | null;
  aspect?: string | null;
  quality?: string | null;
  seed?: number | null;
  reference_ids?: string[] | null;
  name?: string | null;
  tags?: string[] | null;
};

/** One frame of `/api/artifacts/events`: `artifact` is null only on an
 * `agent_image` clear, whose whole meaning is that there is no picture. */
export type ArtifactEvent = {
  event: "created" | "deleted" | "agent_image";
  artifact: Artifact | null;
  agent?: string | null;
};

export type ArtifactQuery = {
  kind?: string; owner?: string; source?: string; q?: string; tag?: string;
  limit?: number; before?: string;
};

export function listArtifacts(query: ArtifactQuery = {}): Promise<Artifact[]> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return api<Artifact[]>(`/api/artifacts${qs ? `?${qs}` : ""}`);
}

export function getArtifact(id: string): Promise<Artifact> {
  return api<Artifact>(`/api/artifacts/${encodeURIComponent(id)}`);
}

/** Multipart, so the browser sets the boundary itself: the wrapper's JSON
 * header is deliberately overridden with none. */
export function uploadArtifact(form: FormData): Promise<Artifact> {
  return api<Artifact>("/api/artifacts", { method: "POST", body: form, headers: {} });
}

export function patchArtifact(id: string, body: { name?: string; tags?: string[] }):
  Promise<Artifact> {
  return api<Artifact>(`/api/artifacts/${encodeURIComponent(id)}`,
                       { method: "PATCH", body: JSON.stringify(body) });
}

/** A soft delete; the API answers with the row as it now is. */
export function deleteArtifact(id: string): Promise<Artifact> {
  return api<Artifact>(`/api/artifacts/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function artifactStats(): Promise<ArtifactStats> {
  return api<ArtifactStats>("/api/artifacts/stats");
}

export function artifactModels(): Promise<ImageModel[]> {
  return api<ImageModel[]>("/api/artifacts/models");
}

/** Synchronous on the API's side — a caller waits for the picture. */
export function generateArtifact(body: GenerateIn): Promise<Artifact> {
  return api<Artifact>("/api/artifacts/generate", { method: "POST", body: JSON.stringify(body) });
}

/** Set (or with null, clear) an agent's picture. */
export function setAgentImage(name: string, artifactId: string | null): Promise<AgentSummary> {
  return api<AgentSummary>(`/api/agents/${encodeURIComponent(name)}/image`,
                           { method: "PUT", body: JSON.stringify({ artifact_id: artifactId }) });
}

/** Sign in as a named principal (docs/design/25); the API answers a plain 401
 *  for a bad name and a bad password alike. */
export function login(principal: string, password: string): Promise<{ ok: boolean }> {
  return api("/api/login", { method: "POST", body: JSON.stringify({ principal, password }) });
}

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
