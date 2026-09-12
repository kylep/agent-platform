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


// --- Relay (docs/design/19) --------------------------------------------------
// Two channels (one busy, one empty) and a dm, with a face on the agents and a
// message mix wide enough to exercise every row the pane can draw: grouped
// agent messages with a run link, a system notice, an event card, reactions, a
// threaded reply and a bridged Discord user.
const MINUTE = 60000;
const at = (minsAgo: number) => new Date(Date.now() - minsAgo * MINUTE).toISOString();

const face = (emoji: string, hue: number) => ({ emoji, hue });
// Derived by the backend (agentplatform.relay.face_for) — the frontend's
// lib/face.ts reproduces these exactly, and tests/faces.spec.ts guards that.
const FACES = {
  news: face("🎈", 9),
  "health-monitor": face("🧭", 109),
  pai: face("🐢", 145),
};

const relayMessage = (over: Record<string, unknown>) => ({
  id: "m0", channel_id: "rc1", author: "user:kyle", kind: "text", body: "",
  card: null, reply_to: null, thread_root: null, run_id: null, hop: 0,
  mentions: [], created_at: at(1), edited_at: null, face: null, reactions: [],
  ...over,
});

// Oldest first here — readable — and reversed on the way out, because the API
// answers a newest-first page.
const generalMessages = [
  relayMessage({ id: "m1", author: "user:kyle", body: "Morning — what's on fire?",
                 created_at: at(60) }),
  relayMessage({ id: "m2", author: "agent:news", body: "Nothing is on fire. Three stories worth reading.",
                 run_id: runs[1].id, face: FACES.news, created_at: at(58),
                 reactions: [{ emoji: "👍", count: 2, mine: false }] }),
  relayMessage({ id: "m3", author: "agent:news", body: "The third one is a duplicate; I rejected it.",
                 run_id: runs[1].id, face: FACES.news, created_at: at(57) }),
  relayMessage({ id: "m4", author: "user:kyle", kind: "system",
                 body: "kyle archived #old-standup.", created_at: at(40) }),
  relayMessage({ id: "m5", author: "agent:health-monitor", kind: "event",
                 face: FACES["health-monitor"], created_at: at(30),
                 card: { title: "Run failed", body: "`news` has failed twice in a row." } }),
  relayMessage({ id: "m6", author: "user:kyle", body: "@news dig into that one.",
                 mentions: ["news"], created_at: at(20) }),
  relayMessage({ id: "m7", author: "agent:news", body: "On it — reading the run now.",
                 run_id: runs[1].id, face: FACES.news, reply_to: "m6", thread_root: "m6",
                 created_at: at(15) }),
  relayMessage({ id: "m8", author: "discord:152911", body: "hello from discord",
                 created_at: at(5) }),
];

const dmMessages = [
  relayMessage({ id: "d1", channel_id: "rd1", author: "user:kyle",
                 body: "What's my day look like?", created_at: at(90) }),
  relayMessage({ id: "d2", channel_id: "rd1", author: "agent:pai", face: FACES.pai,
                 body: "Quiet. One deploy, one review.", run_id: runs[2].id, created_at: at(89) }),
];

const relayChannel = (over: Record<string, unknown>) => ({
  id: "rc1", kind: "channel", name: null, topic: "", open: true, archived_at: null,
  agent: null, participants: [], last_message: null, message_count: 0, unread: 0,
  ...over,
});

const preview = (m: Record<string, unknown>) =>
  ({ id: m.id, author: m.author, body: m.body, created_at: m.created_at });

const relayChannels = [
  relayChannel({ id: "rc1", name: "general", topic: "everyone", message_count: 8, unread: 2,
                 last_message: preview(generalMessages[generalMessages.length - 1]) }),
  relayChannel({ id: "rc2", name: "quiet", topic: "nothing has happened here yet" }),
  relayChannel({ id: "rd1", kind: "dm", topic: "", open: false, agent: "pai",
                 participants: ["agent:pai", "user:kyle"], message_count: 2,
                 last_message: preview(dmMessages[1]) }),
  // An untitled group: the UI has to name it by who is in it, and only its
  // members are mentionable inside it.
  relayChannel({ id: "rg1", kind: "group", topic: "", open: false,
                 participants: ["agent:news", "agent:pai", "user:kyle"] }),
  // …and one the API gave a title, which wins over the member list.
  relayChannel({ id: "rg2", kind: "group", title: "release crew", topic: "", open: false,
                 participants: ["agent:pai", "user:kyle"] }),
];

// Keyed by channel so the route below can honour `after` and `thread` — the
// two cursors the pane actually pages with.
const relayLog: Record<string, typeof generalMessages> = {
  rc1: generalMessages, rc2: [], rd1: dmMessages, rg1: [], rg2: [],
};

/** The messages route's real contract: `after` pages FORWARDS oldest-first
 * from a cursor (an id we cannot place replays nothing), `thread` narrows to
 * a root and its replies, and a bare page comes back newest-first. */
function relayPage(channel: string, params: URLSearchParams) {
  const all = relayLog[channel] ?? [];
  const thread = params.get("thread");
  const rows = thread
    ? all.filter((m) => m.id === thread || m.thread_root === thread)
    : all;
  const after = params.get("after");
  if (after) {
    const seen = rows.findIndex((m) => m.id === after);
    return seen < 0 ? [] : rows.slice(seen + 1);
  }
  return [...rows].reverse();
}

/** Search's contract: newest-first message rows whose body matches, across
 * every room the caller can see unless `channel` narrows it. The real thing is
 * a postgres tsvector; a substring scan is the same answer for these fixtures. */
function relaySearch(params: URLSearchParams) {
  const q = (params.get("q") ?? "").toLowerCase();
  const only = params.get("channel");
  const limit = Number(params.get("limit") ?? 50);
  const rows = Object.entries(relayLog)
    .filter(([id]) => !only || id === only)
    .flatMap(([, messages]) => messages)
    .filter((m) => m.body.toLowerCase().includes(q));
  return [...rows].reverse().slice(0, limit);
}

const detail = (id: string, faces: Record<string, { emoji: string; hue: number }>) =>
  ({ ...relayChannels.find((c) => c.id === id)!, faces });

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
  "/api/whoami": { principal: "kyle", role: "admin", agent: null, run_id: null, tools: null },
  "/api/relay/channels": relayChannels,
  "/api/relay/channels/rc1": detail("rc1", { news: FACES.news, "health-monitor": FACES["health-monitor"] }),
  "/api/relay/channels/rc2": detail("rc2", {}),
  "/api/relay/channels/rd1": detail("rd1", { pai: FACES.pai }),
  "/api/relay/channels/rg1": detail("rg1", { news: FACES.news, pai: FACES.pai }),
  "/api/relay/channels/rg2": detail("rg2", { pai: FACES.pai }),
  // Two of the day's mentions were REFUSED (a hop cap, an hour over budget),
  // which is what puts the Relay row in the Dashboard's triage queue. Wakes —
  // mentions coalesced into one reply — are not counted here.
  "/api/relay/stats": {
    messages_24h: 42, agent_messages_24h: 17, invocations_24h: 9, suppressed_24h: 2,
    // Zero-filled the way the API answers, routine reasons included: the four
    // coalesced wakes are the guard working and must not read as trouble.
    suppressed_by_reason: { hop_limit: 1, budget: 1, not_member: 0,
                            coalesced: 4, facade_owns_turn: 0 },
    budget: { channel_per_hour: 30, global_per_hour: 120, global_used_last_hour: 96 },
    settings: { default_grant: true, max_hops: 4, channel_per_hour: 30,
                global_per_hour: 120, cooldown_seconds: 20 },
  },
  "/api/relay/presence": [
    { agent: "news", state: "thinking", thinking_in: ["rc1"], face: FACES.news },
    { agent: "health-monitor", state: "idle", thinking_in: [], face: FACES["health-monitor"] },
    { agent: "pai", state: "idle", thinking_in: [], face: FACES.pai },
  ],
  // Get-or-create, so the same answer whatever the caller asks for: the
  // AgentDetail Conversations tab opens its agent's dm through this.
  "/api/relay/dm": detail("rd1", { pai: FACES.pai }),
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
  "/api/metrics/agents": agents.map((a) => ({ ...agg, agent: a.name, failure_streak: a.name === "news" ? 2 : 0,
    last_failed_at: a.name === "news" ? new Date(Date.now() - 6 * 3600 * 1000).toISOString() : null })),
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
    { slug: "relay", title: "Relay" },
  ],
  "/api/help/topics/agents": {
    slug: "agents", title: "Agents",
    markdown: "# Agents\n\n**What:** who runs — one folder per agent.",
  },
  "/api/help/topics/relay": {
    slug: "relay", title: "Relay",
    markdown: "# Relay\n\n**What:** the agent messenger — the rooms the platform"
      + " talks in. `@name` summons that agent.\n\n## The loop guards, in plain words"
      + "\n\n- **Hops.** A human or system message is hop 0.",
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

// The cron preview is the one endpoint whose answer depends on the query, so
// it gets a stand-in renderer rather than a fixture. The real sentences come
// from the backend (agentplatform/cronenglish.py, tested there); what the UI
// tests need is that a valid expression comes back described and an invalid one
// comes back with a reason, both 200.
const NEXT_FIRES = ["2026-08-24T09:00:00Z", "2026-08-25T09:00:00Z", "2026-08-26T09:00:00Z"];

function cronPreview(expr: string) {
  const fields = expr.trim().split(/\s+/);
  if (expr.trim() === "") return { english: "", next: [], error: "a cron expression is required" };
  if (fields.length !== 5) {
    return { english: "", next: [], error: `expected 5 fields, got ${fields.length}` };
  }
  if (!/^[\d*,\-/#LA-Za-z]+$/.test(fields.join(""))) {
    return { english: "", next: [], error: "not a valid cron expression" };
  }
  return { english: `Cron ${expr.trim()} explained`, next: NEXT_FIRES, error: null };
}

// Relay's writes answer with the row they created — the pane appends what
// comes back rather than guessing, so `{ ok: true }` would leave it empty.
function relayPost(path: string, body: Record<string, string>): unknown {
  const message = /^\/api\/relay\/channels\/([^/]+)\/messages$/.exec(path);
  if (message) {
    return relayMessage({ id: `posted-${message[1]}`, channel_id: message[1],
                          author: "user:kyle", body: body.body,
                          // The API resolves the root of the chain it is
                          // answering; every fixture reply answers a root.
                          reply_to: body.reply_to ?? null,
                          thread_root: body.reply_to ?? null,
                          created_at: new Date().toISOString() });
  }
  if (/^\/api\/relay\/messages\/[^/]+\/reactions$/.test(path)) {
    return { emoji: body.emoji, count: 1, mine: true };
  }
  if (path === "/api/relay/channels") {
    return { ...relayChannel({ id: "rc9", name: body.name, topic: body.topic }), faces: {} };
  }
  return undefined;
}

export async function mockApi(page: Page): Promise<string[]> {
  const unmatched: string[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/cron/preview") {
      await route.fulfill({ json: cronPreview(url.searchParams.get("expr") ?? "") });
      return;
    }
    // Relay's SSE stream. A route cannot hold a connection open, so the mock
    // answers a well-formed but finished stream: one heartbeat comment, then
    // EOF. The browser reads that as a dropped connection, the pane closes it
    // and falls back to its poll, and — unlike a 404 or a wrong content type —
    // nothing is logged to the console, which the smoke suite gates on.
    if (path.endsWith("/events")) {
      await route.fulfill({ status: 200, contentType: "text/event-stream",
                            headers: { "Cache-Control": "no-cache" },
                            body: ": heartbeat\n\n" });
      return;
    }
    if (path === "/api/relay/search" && route.request().method() === "GET") {
      await route.fulfill({ json: relaySearch(url.searchParams) });
      return;
    }
    const page_ = /^\/api\/relay\/channels\/([^/]+)\/messages$/.exec(path);
    if (page_ && route.request().method() === "GET") {
      await route.fulfill({ json: relayPage(page_[1], url.searchParams) });
      return;
    }
    if (route.request().method() === "POST" && path.startsWith("/api/relay/")) {
      const posted = relayPost(path, route.request().postDataJSON() ?? {});
      if (posted !== undefined) {
        await route.fulfill({ json: posted });
        return;
      }
    }
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
