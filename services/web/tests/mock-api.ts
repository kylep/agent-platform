import type { Page, Route } from "@playwright/test";

// A deterministic mock of the platform API, rich enough for every page to
// render its real states (blocked agents, pending changes, invalid secrets…).
// The UI layer is what these tests gate; the API's own behavior is covered by
// the backend suite. Unmatched GETs 404 loudly so a new page dependency shows
// up as a test failure, not silent emptiness.

// An agent IS its row (docs/design/15): the listing carries the full
// definition plus server-derived readiness.
const def = (over: Record<string, unknown>) => ({
  prompt: "You are a platform agent.", description: "", model: "", role: "operator",
  system: false, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
  result_topic: "", transcript_retention_days: null,
  harness_tools: [], platform_tools: [], skills: [], secrets: [],
  entrypoints: { crons: [], webhooks: [], topics: [], timezone: "" }, enabled: true,
  ...over,
});

const healthMonitor = def({
  name: "health-monitor", description: "Watches platform health.", model: "sonnet",
  system: true, timeout_seconds: 600, harness_tools: ["WebSearch"],
  platform_tools: ["mcp__platform__metrics"], skills: [], secrets: [],
  prompt: "# health-monitor\nYou watch health.",
  entrypoints: { crons: [{ schedule: "*/15 * * * *", prompt: "Check platform health." }],
                 webhooks: [], topics: [], timezone: "" },
});

const agents = [
  { ...healthMonitor, quarantined: false, error: null, blocked: false, blocked_reason: null },
  { ...def({ name: "news", description: "Gathers the day's notable news.", skills: ["news-lookup"] }),
    quarantined: false, error: null, blocked: true,
    blocked_reason: "blocked: skill `discord` disabled — secret `discord-webhook` is not set" },
  // pai is the one with a webhook entrypoint — the listing's Webhook column
  // reads it out of the (unvalidated) entrypoints blob.
  { ...def({ name: "pai", description: "Conversational assistant.",
             entrypoints: { crons: [], topics: [], timezone: "",
                            webhooks: [{ path: "pai-inbox", auth: "secret", secret_set: true }] } }),
    quarantined: false, error: null, blocked: false, blocked_reason: null },
];

const versions = [
  { version: 2, changed_by: "kyle", changed_via: "admin", created_at: new Date().toISOString() },
  { version: 1, changed_by: "import", changed_via: "import",
    created_at: new Date(Date.now() - 86400000).toISOString() },
];

const run = (id: string, agent: string, state: string, mins: number) => ({
  id, agent, state, trigger: "schedule", created_at: new Date(Date.now() - mins * 60000).toISOString(),
  summary: state === "succeeded" ? "Did the thing." : null, tags: ["ok"],
});

const runs = [
  run("a1".repeat(16), "health-monitor", "succeeded", 5),
  run("b2".repeat(16), "news", "rejected", 15),
  run("c3".repeat(16), "pai", "running", 1),
];

const runDetail = {
  ...runs[0], prompt: "Scheduled run.", exit_code: 0, error: null, result: "All healthy.",
  tokens_in: 100, tokens_out: 50, tool_calls: 2,
  started_at: runs[0].created_at, finished_at: new Date().toISOString(),
  parent_run_id: null, depth: 0, requested_by: "scheduler",
  secrets_granted: [], permission_denials: [],
};

const secrets = [
  { name: "claude-credentials", status: "valid", declared: true, required: true,
    hint: "A `claude setup-token` value.", key: "", probeable: false },
  { name: "discord-webhook", status: "missing", declared: true, required: false,
    hint: "Discord incoming webhook URL", key: "DISCORD_WEBHOOK_URL", probeable: true },
  { name: "mystery-value", status: "unprobed", declared: false, required: false,
    hint: "", key: "", probeable: false },
];

// Pending changes are platform CODE only now — agent definitions are rows and
// save directly (docs/design/15).
const prs = [
  { number: 12, title: "Edit news-lookup: skill body", url: "https://github.com/x/y/pull/12",
    branch: "coder/skill-news-lookup", author: "pericakai[bot]", created_at: new Date().toISOString() },
];

const durations = agents.flatMap((a, i) =>
  [0, 1, 2].map((d) => ({
    run_id: `${i}${d}`.padEnd(32, "0"), agent: a.name, state: d === 1 ? "failed" : "succeeded",
    finished_at: new Date(Date.now() - d * 86400000).toISOString(), seconds: 10 + i * 20 + d,
  })));

const agg = {
  total: 42, by_state: { succeeded: 40, failed: 2 }, active: 1, succeeded: 40,
  success_rate: 0.95, tokens_in: 1000, tokens_out: 5000, tool_calls: 12,
  tokens_cache_read: 20000, tokens_cache_creation: 3000,
  avg_duration_seconds: 18.3, max_duration_seconds: 120, last_run_at: new Date().toISOString(),
};

const today = new Date().toISOString().slice(0, 10);
const reportHtml =
  '<header class="rk-header"><h1 class="rk-title">Daily news</h1>' +
  '<p class="rk-meta">23 items · 4 topics</p></header>' +
  '<section class="rk-section"><h2>AI</h2><div class="rk-item">' +
  '<span class="rk-item-title">Model X ships</span>' +
  '<p class="rk-item-sum">A release happened.</p></div></section>';
const reports = [
  { id: "r1".padEnd(32, "0"), type: "daily-news", date: today, time: "",
    title: "Daily news", meta: {}, run_id: runs[0].id,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

const FIXTURES: Record<string, unknown> = {
  "/api/setup-state": { needs_admin: false, secrets },
  "/api/agents": agents,
  "/api/agents/health-monitor": healthMonitor,
  "/api/agents/health-monitor/versions": versions,
  "/api/agents/health-monitor/versions/1": { ...versions[1], snapshot: def({ name: "health-monitor" }) },
  "/api/agents/health-monitor/versions/2": { ...versions[0], snapshot: healthMonitor },
  "/api/agent-models": { models: [{ id: "", label: "CLI default" }, { id: "sonnet", label: "Sonnet" }] },
  "/api/runs": runs,
  [`/api/runs/${runs[0].id}`]: runDetail,
  [`/api/runs/${runs[0].id}/transcript`]: [],
  "/api/conversations": [
    { id: "cv1", agent: "pai", title: "hello there", kind: "web", active: true,
      created_at: new Date().toISOString(), last_message_at: new Date().toISOString(), turns: 2 },
  ],
  "/api/memories": [
    { id: "m1", agent: "pai", key: null, content: "Kyle likes terminals.", tags: ["style"],
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
  ],
  "/api/tags": [],
  "/api/pull-requests": prs,
  "/api/pull-requests/12/files": [
    { filename: "skills/news-lookup/SKILL.md", status: "modified", additions: 2, deletions: 1,
      patch: "@@ -1,2 +1,3 @@\n-old line\n+new line\n+another" },
  ],
  "/api/pull-requests/12/summary": {
    state: "ready", sha: "abc123",
    summary: "Changes the news-lookup skill: adds one instruction line. Low risk — no secrets, triggers, or permissions change.",
  },
  "/api/pull-requests/12/impact": {
    items: [{ file: "skills/news-lookup/SKILL.md", block: "skill: news-lookup", area: "definition",
              status: "modified", additions: 2, deletions: 1, notable: [] }],
    warnings: [],
  },
  "/api/sync-status": { sha: "abc123" },
  "/api/dlq": [],
  "/api/skills": [
    { name: "news-lookup", description: "Query the news archive.", icon: "🗞️",
      secrets: [], error: null, used_by: ["news-librarian"] },
  ],
  "/api/skills/news-lookup": {
    name: "news-lookup", description: "Query the news archive.", icon: "🗞️",
    secrets: [], error: null, used_by: ["news-librarian"],
    body: "Query it.", raw: "---\nname: news-lookup\n---\nQuery it.",
  },
  "/api/tools": [
    { name: "stocks", description: "Yahoo Finance daily history + summary for a ticker.",
      secrets: [], database: false, has_requirements: true, timeout_seconds: 45,
      error: null, used_by: ["pai"] },
    { name: "memory", description: "Persistent namespaced agent memory (read/save).",
      secrets: [], database: true, has_requirements: true, timeout_seconds: 20,
      error: null, used_by: ["health-monitor"] },
  ],
  "/api/tools/stocks": {
    name: "stocks", description: "Yahoo Finance daily history + summary for a ticker.",
    secrets: [], database: false, has_requirements: true, timeout_seconds: 45,
    error: null, used_by: ["pai"], params: { type: "object" },
    files: { "tool.yaml": "name: stocks\n", "run.py": "print('hi')\n",
             "requirements.txt": "yfinance\n" },
  },
  "/api/tools/memory": {
    name: "memory", description: "Persistent namespaced agent memory (read/save).",
    secrets: [], database: true, has_requirements: true, timeout_seconds: 20,
    error: null, used_by: ["health-monitor"], params: { type: "object" },
    files: { "tool.yaml": "name: memory\n", "run.py": "print('hi')\n" },
  },
  "/api/schedules": [
    { agent: "health-monitor", cron: "*/15 * * * *", enabled: true,
      last_fire: new Date().toISOString(), next_fire: new Date(Date.now() + 600000).toISOString() },
  ],
  "/api/jobs": [],
  "/api/secrets": secrets,
  "/api/metrics/overview": { ...agg, runs_24h: 10, runs_7d: 42, dlq: 0, window: 5000 },
  "/api/metrics/agents": agents.map((a) => ({ ...agg, agent: a.name, failure_streak: a.name === "news" ? 2 : 0 })),
  "/api/metrics/models": [{ model: "claude-sonnet-5", runs: 40, tokens_in: 900, tokens_out: 4500,
                            tokens_cache_read: 18000, tokens_cache_creation: 2500 }],
  "/api/metrics/durations": durations,
  "/api/health/kafka": { reachable: true, backlog: { dlq: 0 }, lag: 0 },
  "/api/integrations": [
    { name: "Discord", status: "configured", secrets: ["discord-bot"], detail: "Token set." },
  ],
  "/api/maintenance/retention": { default_days: 30, overrides: [] },
  "/api/connectors": [
    { name: "web", kind: "web", implemented: true, secrets: [], description: "Web UI." },
  ],
  "/api/api-keys": [],
  "/api/apps": [
    { name: "news", description: "Browse gathered news by calendar and topic.", icon: "🗞️",
      ui: true, api: true, postgres: true, kafka_topics: ["app.news.item.ingested"],
      redis: false, agent_key_role: "operator", error: null, ready: true, ready_replicas: 1 },
    { name: "scratch", description: "A declared-but-undeployed app.", icon: "🧩",
      ui: false, api: true, postgres: false, kafka_topics: [], redis: false,
      agent_key_role: null, error: null, ready: null, ready_replicas: 0 },
  ],
  "/api/metrics/tools": [
    { tool: "stocks", calls: 12, denials: 0, errors: 1, avg_latency_ms: 900.5 },
  ],
  "/api/help/topics": [
    { slug: "agents", title: "Agents" },
    { slug: "changes", title: "Changes — the change loop" },
  ],
  "/api/help/topics/agents": {
    slug: "agents", title: "Agents",
    markdown: "# Agents\n\n**What:** who runs — one folder per agent.",
  },
  "/api/help/tools": [
    { name: "Bash", kind: "claude", sensitive: true,
      description: "Run shell commands inside the agent's pod." },
    { name: "WebSearch", kind: "claude", sensitive: false,
      description: "Search the public web." },
    { name: "TodoWrite", kind: "claude", sensitive: false, display_name: "Todo",
      description: "Keep an internal working task list during a run." },
    { name: "mcp__platform__query_app", kind: "platform", sensitive: false,
      description: "Call a read-only API endpoint of an installed platform app." },
  ],
  "/api/report-types": [
    { name: "daily-news", description: "Morning digest of gathered news.", icon: "📰",
      generator: "news", cadence: "daily", retention_days: 365, error: null,
      count: 1, latest_date: today },
  ],
  "/api/reports": reports,
  [`/api/reports/${reports[0].id}`]: { ...reports[0], html: reportHtml },
};

export async function mockApi(page: Page): Promise<string[]> {
  const unmatched: string[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const hit = FIXTURES[path];
    if (hit !== undefined) {
      await route.fulfill({ json: hit });
      return;
    }
    if (route.request().method() !== "GET") {
      await route.fulfill({ json: { ok: true } });
      return;
    }
    unmatched.push(path);
    await route.fulfill({ status: 404, json: { detail: `no fixture for ${path}` } });
  });
  return unmatched;
}
