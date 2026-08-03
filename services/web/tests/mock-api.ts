import type { Page, Route } from "@playwright/test";

// A deterministic mock of the platform API, rich enough for every page to
// render its real states (blocked agents, pending changes, invalid secrets…).
// The UI layer is what these tests gate; the API's own behavior is covered by
// the backend suite. Unmatched GETs 404 loudly so a new page dependency shows
// up as a test failure, not silent emptiness.

const agents = [
  { name: "health-monitor", description: "Watches platform health.", quarantined: false,
    error: null, blocked: false, blocked_reason: null, system: true, schedule: "*/15 * * * *" },
  { name: "news", description: "Gathers the day's notable news.", quarantined: false,
    error: null, blocked: true,
    blocked_reason: "blocked: skill `discord` disabled — secret `discord-webhook` is not set",
    system: false, schedule: "" },
  { name: "pai", description: "Conversational assistant.", quarantined: false,
    error: null, blocked: false, blocked_reason: null, system: false, schedule: "" },
];

const manifest = {
  role: "operator", concurrency: 1, timeout_seconds: 600, skills: [], secrets: [],
  description: "Watches platform health.", schedule: "", model: "sonnet", system: true,
  can_invoke: false, memory: true, transcript_retention_days: null,
};

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

const prs = [
  { number: 12, title: "Edit news: agent definition", url: "https://github.com/x/y/pull/12",
    branch: "coder/agent-news", author: "pericakai[bot]", created_at: new Date().toISOString() },
];

const durations = agents.flatMap((a, i) =>
  [0, 1, 2].map((d) => ({
    run_id: `${i}${d}`.padEnd(32, "0"), agent: a.name, state: d === 1 ? "failed" : "succeeded",
    finished_at: new Date(Date.now() - d * 86400000).toISOString(), seconds: 10 + i * 20 + d,
  })));

const agg = {
  total: 42, by_state: { succeeded: 40, failed: 2 }, active: 1, succeeded: 40,
  success_rate: 0.95, tokens_in: 1000, tokens_out: 5000, tool_calls: 12,
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
  "/api/agents/health-monitor": {
    name: "health-monitor", manifest, agent_md: "# health-monitor\nYou watch health.",
    entrypoints: { cron: ["*/15 * * * *"], webhooks: [], kafka: [] },
    entrypoints_raw: 'cron: ["*/15 * * * *"]\n', error: null,
  },
  "/api/agent-tools": { tools: ["Bash", "Read", "WebFetch", "WebSearch"] },
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
    { filename: "agents/news/agent.md", status: "modified", additions: 2, deletions: 1,
      patch: "@@ -1,2 +1,3 @@\n-old line\n+new line\n+another" },
  ],
  "/api/pull-requests/12/summary": {
    state: "ready", sha: "abc123",
    summary: "Changes the news agent's definition: adds one instruction line. Low risk — no secrets, triggers, or permissions change.",
  },
  "/api/pull-requests/12/impact": {
    items: [{ file: "agents/news/agent.md", block: "agent: news", area: "definition",
              status: "modified", additions: 2, deletions: 1, notable: [] }],
    warnings: [],
  },
  "/api/sync-status": { sha: "abc123" },
  "/api/dlq": [],
  "/api/skills": [
    { name: "discord", description: "Post to Discord.", icon: "💬",
      secrets: ["discord-webhook"], error: null, used_by: [] },
  ],
  "/api/skills/discord": {
    name: "discord", description: "Post to Discord.", icon: "💬",
    secrets: ["discord-webhook"], error: null, used_by: [],
    body: "Post a message.", raw: "---\nname: discord\n---\nPost a message.",
  },
  "/api/schedules": [
    { agent: "health-monitor", cron: "*/15 * * * *", enabled: true,
      last_fire: new Date().toISOString(), next_fire: new Date(Date.now() + 600000).toISOString() },
  ],
  "/api/jobs": [],
  "/api/secrets": secrets,
  "/api/metrics/overview": { ...agg, runs_24h: 10, runs_7d: 42, dlq: 0, window: 5000 },
  "/api/metrics/agents": agents.map((a) => ({ ...agg, agent: a.name, failure_streak: a.name === "news" ? 2 : 0 })),
  "/api/metrics/models": [{ model: "claude-sonnet-5", runs: 40, tokens_in: 900, tokens_out: 4500 }],
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
