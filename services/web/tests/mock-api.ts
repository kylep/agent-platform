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
  system: false, responds_to_all: true, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
  result_topic: "", transcript_retention_days: null,
  harness_tools: [], platform_tools: [], skills: [], secrets: [],
  entrypoints: { crons: [], webhooks: [], topics: [], timezone: "" }, enabled: true,
  push_path_globs: [], may_delete_tests: false, quota_5h_max_pct: 80, quota_7d_max_pct: 50,
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
  { ...def({ name: "news", description: "Gathers the day's notable news." }),
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
  { name: "codex-credentials", status: "valid", declared: true, required: true,
    hint: "Codex ChatGPT OAuth credentials.", key: "", probeable: false },
  { name: "discord-webhook", status: "missing", declared: true, required: false,
    hint: "Discord incoming webhook URL", key: "DISCORD_WEBHOOK_URL", probeable: true },
  { name: "mystery-value", status: "unprobed", declared: false, required: false,
    hint: "", key: "", probeable: false },
];

// Pending changes are platform CODE only now — agent definitions are rows and
// save directly (docs/design/15). A Workbench publish (docs/design/24) lands
// here too, under either prefix, with the ticket parsed off its head and the
// agent read from the PR body; the self-edit row carries neither.
const prs = [
  { number: 12, title: "Edit release-review: skill body", url: "https://github.com/x/y/pull/12",
    branch: "coder/skill-release-review", author: "pericakai[bot]", created_at: new Date().toISOString(),
    ticket_key: null, agent: null, auto_merge: null },
  { number: 13, title: "OPS-5: freshness gates, second pass", url: "https://github.com/x/y/pull/13",
    branch: "coder/ops-5", author: "pericakai[bot]",
    created_at: new Date(Date.now() - 30 * 60000).toISOString(),
    ticket_key: "OPS-5", agent: "engineer", auto_merge: true },
  { number: 14, title: "OPS-1: a test for the weather dedup", url: "https://github.com/x/y/pull/14",
    branch: "qa/ops-1", author: "pericakai[bot]",
    created_at: new Date(Date.now() - 20 * 60000).toISOString(),
    ticket_key: "OPS-1", agent: "qa", auto_merge: false },
];

// The `workbench` transcript frame the runner emits after a dev run
// (services/runner/runner.py): the API's publish body on success, or a
// `published: false` record naming why not. The run page's tail is a
// WebSocket, which `page.route` never sees — `runTail` serves these.
export const workbenchFrames = {
  published: [
    { seq: 1, type: "assistant", message: { content: [{ type: "text", text: "Done — publishing." }] } },
    { seq: 2, type: "workbench", published: true, branch: "coder/ops-5",
      pr: { number: 13, url: "https://github.com/x/y/pull/13" },
      paths: ["services/web/src/pages/Tickets.tsx", "services/web/tests/tickets.spec.ts",
              "services/backend/agentplatform/tickets.py", "docs/design/20-tickets.md"],
      tests_removed: ["services/web/tests/old.spec.ts"], ticket_state: "review",
      auto_merge: true, verify_ok: true, warnings: [] },
    { seq: 3, type: "result", result: "Done — publishing.", terminal: true },
  ],
  refused: [
    { seq: 1, type: "workbench", published: false, status: 422,
      reason: "services/backend/agentplatform/relay.py is outside its test paths" },
    { seq: 2, type: "result", result: "Tests added.", terminal: true },
  ],
};

export async function runTail(page: Page, runId: string, frames: unknown[]): Promise<void> {
  await page.routeWebSocket((url) => url.pathname === `/api/runs/${runId}/tail`, (ws) => {
    for (const f of frames) ws.send(JSON.stringify(f));
  });
}

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


// --- Artifacts (docs/design/23) ----------------------------------------------
// Six rows: enough for every tile the grid draws (a generated image with its
// whole provenance, an upload, a derived crop, a file with no thumb) and for
// the two faces the block puts on other pages — a card in a room, a picture
// on an agent. Ids are 32 hex chars, as the API mints them.
const MINUTE = 60000;
// The instant every `at(...)` row is measured from. It is read ONCE, when the
// worker loads this module, so a page rendered later reads the rows as older
// than they were built — and a minute-rounded "15m ago" turns into "16m" once
// the suite has been running for half a minute. A test that asserts on a
// relative time freezes the browser's clock here (`page.clock.setFixedTime`)
// so the render measures from the same instant the rows were built.
export const FIXTURE_NOW = Date.now();
const at = (minsAgo: number) => new Date(FIXTURE_NOW - minsAgo * MINUTE).toISOString();
const aid = (seed: string) => seed.repeat(32).slice(0, 32);

// A real 1×1 PNG, so every <img> the suite draws off the thumb and content
// routes decodes — a fake body would fire `onerror`, and the face would fall
// back to its emoji exactly where the test expects the picture.
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

const artifact = (id: string, over: Record<string, unknown>) => ({
  id, name: "", mime: "image/png", size: 1024, sha256: aid("f"), kind: "image",
  width: 1024, height: 1024, owner: "user:kyle", run_id: null, source: "upload",
  meta: {}, tags: [], created_at: at(30), deleted_at: null,
  thumb_url: `/api/artifacts/${id}/thumb`, content_url: `/api/artifacts/${id}/content`,
  ...over,
});

const dragon = artifact(aid("a1"), {
  name: "gpt-image-1-a-dragon-over-the-harbour.png", size: 412000, owner: "agent:pai",
  run_id: runs[2].id, source: "generated", tags: ["dragon", "harbour"], created_at: at(12),
  // The generated row's meta is its provenance, as image_gen writes it.
  meta: { provider: "openai", model: "gpt-image-1", prompt: "A dragon over the harbour at dawn",
          params: { size: "1024x1024", quality: "medium" }, seed: 42, cost_usd: 0.04,
          duration_ms: 3120, reference_ids: [], tool: "image_gen" },
});
const logo = artifact(aid("a2"), { name: "logo.png", size: 20480, tags: ["logo"],
                                    width: 512, height: 512, created_at: at(600) });
const artifacts = [
  dragon,
  logo,
  artifact(aid("a3"), { name: "digest.csv", mime: "text/csv", kind: "file", size: 3072,
                        width: null, height: null, owner: "agent:news", run_id: runs[1].id,
                        source: "tool", thumb_url: null, created_at: at(90) }),
  // Cropped from the logo: derived, and it names its parent.
  artifact(aid("a4"), { name: "logo-crop.png", size: 8192, source: "derived",
                        width: 128, height: 128, meta: { parent_id: aid("a2") },
                        created_at: at(500) }),
  // news's own picture — what its face wears (see FACES below).
  artifact(aid("a5"), { name: "news-face.png", size: 65536, owner: "agent:news",
                        source: "generated", tags: ["face"], width: 256, height: 256,
                        meta: { provider: "openai", model: "gpt-image-1",
                                prompt: "A cheerful balloon reading a newspaper", params: {},
                                seed: null, cost_usd: 0.02, duration_ms: 2400,
                                reference_ids: [], tool: "image_gen" },
                        created_at: at(2000) }),
  artifact(aid("a6"), { name: "dashboard-screenshot.png", size: 150000,
                        owner: "agent:health-monitor", width: 1440, height: 900,
                        created_at: at(4000) }),
  // A row whose byte URLs point somewhere other than the artifacts routes —
  // what a compromised API (or a bad migration) could hand a browser. The UI
  // must draw a glyph and no link rather than put them in `src`/`href`.
  artifact(aid("a7"), { name: "hostile.png", size: 100, owner: "agent:pai",
                        thumb_url: "https://evil.example/thumb.png",
                        content_url: "https://evil.example/content.png",
                        created_at: at(9000) }),
];

// What the two write routes answer with: the upload as the API would name it,
// and a fresh generation carrying the dragon's provenance under a new id.
// Module-level so a spec can name the id it expects to see PUT onto an agent.
const uploaded = artifact(aid("b1"), { name: "upload.png", created_at: at(0) });
const generated = artifact(aid("b2"), { ...dragon, id: aid("b2"),
                                        name: "gpt-image-1-generated.png", created_at: at(0) });

/** The rows a spec reaches for by role rather than by position. */
export const ARTIFACTS = { generated: dragon, file: artifacts[2], face: artifacts[4],
                           hostile: artifacts[6], uploaded, fresh: generated,
                           // Named in #art and held by nobody — until a spec
                           // makes it land through `artifactFeed`.
                           missing_id: aid("d0") };

/** The artifacts stream, driven by a spec. A route cannot hold a connection
 * open, so frames a test emits are queued and delivered whole on the stream's
 * NEXT connection — the hook's own reconnect, which the mock's EOF answer
 * provokes every couple of seconds. A row created here is also answered by
 * `/api/artifacts/<id>` for this page only, so a card that re-fetches finds
 * it; the shared fixtures are never mutated. Registered after `mockApi`, so
 * its routes win (Playwright tries handlers newest first). */
export async function artifactFeed(page: Page): Promise<{
  created: (id: string, over?: Record<string, unknown>) => Record<string, unknown>;
  deleted: (row: Record<string, unknown>) => void;
}> {
  const pending: string[] = [];
  const late = new Map<string, Record<string, unknown>>();
  const frame = (data: unknown) => `event: artifact\ndata: ${JSON.stringify(data)}\n\n`;
  await page.route("**/api/artifacts/events", async (route: Route) => {
    await route.fulfill({ status: 200, contentType: "text/event-stream",
                          headers: { "Cache-Control": "no-cache" },
                          body: pending.splice(0).join("") || ": heartbeat\n\n" });
  });
  await page.route(/\/api\/artifacts\/[0-9a-f]{32}(\/thumb|\/content)?$/, async (route: Route) => {
    const m = /\/api\/artifacts\/([0-9a-f]{32})(\/thumb|\/content)?$/
      .exec(new URL(route.request().url()).pathname);
    const row = m && late.get(m[1]);
    if (!row || route.request().method() !== "GET") await route.fallback();
    else if (m[2]) {
      await route.fulfill({ body: Buffer.from(PNG_B64, "base64"), contentType: "image/png" });
    } else await route.fulfill({ json: row });
  });
  return {
    created(id, over = {}) {
      const row = artifact(id, { name: `late-${id.slice(0, 4)}.png`, created_at: at(0), ...over });
      late.set(id, row);
      pending.push(frame({ event: "created", artifact: row, agent: null }));
      return row;
    },
    deleted(row) {
      late.delete(String(row.id));
      pending.push(frame({ event: "deleted", artifact: { ...row, deleted_at: at(0) }, agent: null }));
    },
  };
}

/** A store with more pictures than the Studio's recent strip shows, for this
 * page only: the strip asks for the last 24 and the shared fixtures hold six.
 * The rows are answered by id and served bytes too, so a thumb decodes and a
 * click on one fetches a real row; registered after `mockApi`, so it wins. */
export async function studioRecent(page: Page, n = 30): Promise<Record<string, unknown>[]> {
  const rows = Array.from({ length: n }, (_, i) =>
    artifact(`c${i.toString(16).padStart(2, "0")}`.padEnd(32, "0"),
             { name: `recent-${i}.png`, created_at: at(i + 1) }));
  await page.route("**/api/artifacts?**", async (route: Route) => {
    const params = new URL(route.request().url()).searchParams;
    if (route.request().method() !== "GET" || params.get("kind") !== "image" || !params.get("limit")) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json: rows.slice(0, Number(params.get("limit"))) });
  });
  await page.route(/\/api\/artifacts\/c[0-9a-f]{2}0{29}(\/thumb|\/content)?$/, async (route: Route) => {
    const m = /\/api\/artifacts\/([0-9a-f]{32})(\/thumb|\/content)?$/
      .exec(new URL(route.request().url()).pathname);
    const row = m && rows.find((r) => r.id === m[1]);
    if (!row || route.request().method() !== "GET") await route.fallback();
    else if (m[2]) {
      await route.fulfill({ body: Buffer.from(PNG_B64, "base64"), contentType: "image/png" });
    } else await route.fulfill({ json: row });
  });
  return rows;
}

const artifactStats = {
  count: artifacts.length, bytes: artifacts.reduce((n, a) => n + a.size, 0),
  total_cap: 500 * 1024 * 1024, generated_this_month: 2, spend_this_month_usd: 0.06,
  spend_today_usd: 0.04, daily_cap_usd: 2,
};

const imageModels = [
  { id: "gpt-image-1", provider: "openai", label: "GPT Image 1", price_usd: 0.04,
    sizes: ["1024x1024", "1536x1024", "1024x1536"], aspects: null, custom_size: false,
    qualities: ["low", "medium", "high"], edits: true, configured: true, default: true },
  { id: "flux-pro", provider: "bfl", label: "FLUX 1.1 pro", price_usd: 0.05, sizes: null,
    aspects: ["1:1", "16:9", "9:16"], custom_size: true, qualities: null, edits: false,
    configured: false, default: false },
  // A second configured provider, and one that speaks aspects rather than
  // sizes: what proves a picker offers every key that is set and asks each
  // model for its geometry in its own words.
  { id: "imagen-4", provider: "google", label: "Imagen 4", price_usd: 0.04, sizes: null,
    aspects: ["1:1", "3:4", "4:3", "16:9", "9:16"], custom_size: false, qualities: null,
    edits: false, configured: true, default: false },
];

/** The list route's own filters, over the fixtures: every one is a column
 * match except `q`, which the API runs over the name. */
function artifactList(params: URLSearchParams) {
  const q = (params.get("q") ?? "").toLowerCase();
  return artifacts.filter((a) =>
    (!params.get("kind") || a.kind === params.get("kind"))
    && (!params.get("owner") || a.owner === params.get("owner"))
    && (!params.get("source") || a.source === params.get("source"))
    && (!params.get("tag") || a.tags.includes(params.get("tag")!))
    && (!q || a.name.toLowerCase().includes(q)));
}

type Fulfil = { status?: number; json?: unknown; body?: Buffer; contentType?: string };

/** The whole artifacts surface, bytes included. An unknown id is a 404 here
 * rather than an unfixtured call: the room's "artifact not found" chip is
 * built on that answer. Multipart is matched by path BEFORE anything reads
 * the body as JSON, because `postDataJSON()` throws on a form. */
function artifactRoute(path: string, method: string, params: URLSearchParams): Fulfil | undefined {
  if (path === "/api/artifacts") {
    if (method === "GET") return { json: artifactList(params) };
    if (method === "POST") return { status: 201, json: uploaded };
    return undefined;
  }
  if (path === "/api/artifacts/stats") return { json: artifactStats };
  if (path === "/api/artifacts/models") return { json: imageModels };
  if (path === "/api/artifacts/generate" && method === "POST") {
    return { status: 201, json: generated };
  }
  const m = /^\/api\/artifacts\/([0-9a-f]{32})(?:\/(thumb|content))?$/.exec(path);
  if (!m) return undefined;
  const row = artifacts.find((a) => a.id === m[1]);
  if (!row) return { status: 404, json: { detail: "unknown artifact" } };
  if (m[2] === "thumb" && row.kind !== "image") {
    return { status: 404, json: { detail: "no thumb for this artifact" } };
  }
  if (m[2]) return { body: Buffer.from(PNG_B64, "base64"), contentType: "image/png" };
  if (method === "GET") return { json: row };
  if (method === "PATCH") return { json: row };
  if (method === "DELETE") return { json: { ...row, deleted_at: at(0) } };
  return undefined;
}


// --- Relay (docs/design/19) --------------------------------------------------
// Two channels (one busy, one empty) and a dm, with a face on the agents and a
// message mix wide enough to exercise every row the pane can draw: grouped
// agent messages with a run link, a system notice, an event card, reactions, a
// threaded reply and a bridged Discord user.
const face = (emoji: string, hue: number) => ({ emoji, hue });
// Derived by the backend (agentplatform.relay.face_for) — the frontend's
// lib/face.ts reproduces these exactly, and tests/faces.spec.ts guards that.
// news additionally has a picture (docs/design/23): the thumb of its image
// artifact rides on the face, and every consumer shows it over the emoji.
const FACES = {
  news: { ...face("🎈", 9), image_url: ARTIFACTS.face.thumb_url },
  "health-monitor": face("🧭", 109),
  pai: face("🐢", 145),
};

// The listing and the row both wear the face (docs/design/23), so the Agents
// pages draw a picture without a second fetch — news's is the artifact above.
export const agentRows = agents.map((a) => ({
  ...a, face: FACES[a.name as keyof typeof FACES] ?? null,
  image_artifact_id: a.name === "news" ? ARTIFACTS.face.id : null,
}));
/** Every agent `/api/agents` lists, in its order — what a picker built on
 * the listing has to offer, no more and no fewer. */
export const AGENT_NAMES = agentRows.map((a) => a.name);

/** `PUT /api/agents/{name}/image`: the row as the API answers it, the picture
 * set (or cleared) from the body, so a refetch after the write shows it. */
function agentImageWrite(path: string, body: Record<string, unknown>): unknown {
  const m = /^\/api\/agents\/([^/]+)\/image$/.exec(path);
  if (!m) return undefined;
  const row = agentRows.find((a) => a.name === decodeURIComponent(m[1]));
  if (!row) return undefined;
  const id = typeof body.artifact_id === "string" ? body.artifact_id : null;
  return { ...row, image_artifact_id: id,
           face: { ...(row.face ?? face("🧱", 0)), image_url: id ? `/api/artifacts/${id}/thumb` : null } };
}

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

// The agent's answer is threaded under the human's message: that is how every
// DM reply landed before QA-16, and a DM pane has to show that history inline
// rather than behind a "replies" chip it never draws.
const dmMessages = [
  relayMessage({ id: "d1", channel_id: "rd1", author: "user:kyle",
                 body: "What's my day look like?", created_at: at(90) }),
  relayMessage({ id: "d2", channel_id: "rd1", author: "agent:pai", face: FACES.pai,
                 body: "Quiet. One deploy, one review.", run_id: runs[2].id,
                 reply_to: "d1", thread_root: "d1", created_at: at(89) }),
];

const connectedMessages = [
  relayMessage({ id: "x1", channel_id: "rx1", author: "discord:55",
                 body: "Can you check the deploy?", created_at: at(12) }),
  relayMessage({ id: "x2", channel_id: "rx1", author: "agent:pai", face: FACES.pai,
                 body: "It is healthy.", run_id: runs[2].id,
                 reply_to: "x1", thread_root: "x1", created_at: at(11) }),
];

// The ops room is the OPS project (docs/design/20): every ticket opened there
// left an event card behind, and the card is the root of the ticket's thread —
// which is why the detail page can show a discussion without storing one.
const ticketCard = (key: string, title: string, state: string, priority: string,
                    assignee: string | null, over: Record<string, unknown> = {}) =>
  relayMessage({
    channel_id: "rc3", kind: "event", author: assignee ?? "user:kyle",
    card: { type: "ticket", key, title, state, priority, assignee, url: `/tickets/${key}` },
    ...over,
  });

const opsMessages = [
  ticketCard("OPS-1", "Weather repeats across the digest", "open", "p1", "agent:news",
             { id: "k1", author: "agent:news", face: FACES.news, run_id: runs[1].id,
               created_at: at(600) }),
  relayMessage({ id: "k1r1", channel_id: "rc3", author: "user:kyle", reply_to: "k1",
                 thread_root: "k1", body: "Dedupe by day, not by url.", created_at: at(590) }),
  relayMessage({ id: "k1r2", channel_id: "rc3", author: "agent:news", face: FACES.news,
                 reply_to: "k1", thread_root: "k1", run_id: runs[1].id,
                 body: "Agreed — the forecast is one item a day.", created_at: at(585) }),
  // One reply that names another ticket in prose and quotes a third inside a
  // fence: the chip fires on the first and never on the second.
  relayMessage({ id: "k1r3", channel_id: "rc3", author: "user:kyle", reply_to: "k1",
                 thread_root: "k1", created_at: at(583),
                 body: "Same root cause as OPS-2.\n\n```\nOPS-3 is only an example\n```" }),
  // Two rows that CITE a wiki page: `[[deploying]]` is a chip in a room the
  // same way a ticket key is, and the page's "cited in 2 messages" is these.
  relayMessage({ id: "k1r4", channel_id: "rc3", author: "agent:pai", face: FACES.pai,
                 reply_to: "k1", thread_root: "k1", created_at: at(583),
                 body: "The helm trap is written up in [[deploying]] now." }),
  relayMessage({ id: "k1r5", channel_id: "rc3", author: "user:kyle", reply_to: "k1",
                 thread_root: "k1", created_at: at(590),
                 body: "Read [[deploying]] before the next release." }),
  ticketCard("OPS-2", "Kafka lag alert fires every night", "in_progress", "p0",
             "agent:health-monitor",
             { id: "k2", author: "agent:health-monitor", face: FACES["health-monitor"],
               created_at: at(500) }),
  ticketCard("OPS-3", "Rotate the Discord bot token", "in_progress", "p2", "agent:pai",
             { id: "k3", author: "agent:pai", face: FACES.pai, created_at: at(9000) }),
  ticketCard("OPS-4", "Decide the retention window", "blocked", "p2", "user:admin",
             { id: "k4", created_at: at(400) }),
  ticketCard("OPS-5", "Freshness gates need a second pair of eyes", "review", "p1",
             "agent:news", { id: "k5", author: "agent:news", face: FACES.news,
                             created_at: at(300) }),
  ticketCard("OPS-6", "Back up the scheduler's state", "done", "p3", "agent:pai",
             { id: "k6", author: "agent:pai", face: FACES.pai, created_at: at(260) }),
  // The Workbench's cards (docs/design/24), in the thread of the ticket they
  // were published for: one landed publish that dropped a test file, and one
  // refusal — the platform's voice, `system:relay`, with the reason in the
  // body a bridge can read.
  relayMessage({ id: "pub1", channel_id: "rc3", author: "system:relay", kind: "event",
                 reply_to: "k5", thread_root: "k5", run_id: "r-eng-1", created_at: at(200),
                 body: "🔀 engineer published coder/ops-5 → PR #13 · 4 files · verify ✓ backend ✓ web"
                   + " · removes tests: services/web/tests/old.spec.ts",
                 card: { type: "publish", pr: 13, url: "https://github.com/x/y/pull/13",
                         branch: "coder/ops-5", files: 4,
                         tests_removed: ["services/web/tests/old.spec.ts"], verify_ok: true,
                         refused_reason: null, run_id: "r-eng-1", agent: "engineer",
                         warnings: [] } }),
  relayMessage({ id: "pub2", channel_id: "rc3", author: "system:relay", kind: "event",
                 reply_to: "k5", thread_root: "k5", run_id: "r-qa-1", created_at: at(190),
                 body: "⛔ publish refused for qa: services/backend/agentplatform/relay.py is outside its test paths",
                 card: { type: "publish", pr: null, url: null, branch: "qa/ops-5", files: 0,
                         tests_removed: [], verify_ok: null,
                         refused_reason: "services/backend/agentplatform/relay.py is outside its test paths",
                         run_id: "r-qa-1", agent: "qa", warnings: [] } }),
];

// The #art room (docs/design/23): the card the API posts when an image is
// generated — an EVENT row, its body the chip plus one flattened line — and
// the three ways prose can carry the chip: a live id, an id nobody has, and
// the syntax quoted in a code span, which is documentation and not a card.
const artMessages = [
  relayMessage({ id: "art1", channel_id: "rc4", author: "system:platform", kind: "event",
                 created_at: at(12),
                 body: `[[artifact:${dragon.id}]]\nby pai · gpt-image-1 · "A dragon over the harbour at dawn"`,
                 card: { type: "artifact", artifact_id: dragon.id, owner: "agent:pai",
                         model: "gpt-image-1", prompt: "A dragon over the harbour at dawn" } }),
  relayMessage({ id: "art2", channel_id: "rc4", author: "user:kyle", created_at: at(10),
                 body: `Love it — pin this one [[artifact:${dragon.id}]] for the readme.` }),
  relayMessage({ id: "art3", channel_id: "rc4", author: "agent:pai", face: FACES.pai,
                 run_id: runs[2].id, created_at: at(9),
                 body: `The earlier draft was [[artifact:${aid("d0")}]], since removed.` }),
  relayMessage({ id: "art4", channel_id: "rc4", author: "user:kyle", created_at: at(8),
                 body: `For reference, the syntax is \`[[artifact:${dragon.id}]]\` in any message.` }),
];

const relayChannel = (over: Record<string, unknown>) => ({
  id: "rc1", kind: "channel", name: null, topic: "", open: true, archived_at: null,
  home: "relay", reply_mode: "threaded", dispatch_mode: "mentions", default_agent: null,
  agent: null, participants: [], last_message: null, message_count: 0, unread: 0,
  ...over,
});

const preview = (m: Record<string, unknown>) =>
  ({ id: m.id, author: m.author, body: m.body, created_at: m.created_at });

const relayChannels = [
  relayChannel({ id: "rc1", name: "general", topic: "everyone", message_count: 8, unread: 2,
                 last_message: preview(generalMessages[generalMessages.length - 1]) }),
  relayChannel({ id: "rc2", name: "quiet", topic: "nothing has happened here yet" }),
  // The OPS project's room. Deliberately not #quiet: that one is the empty
  // channel the Relay suite reads its welcome state out of.
  relayChannel({ id: "rc3", name: "ops", title: "ops", topic: "the platform's own work",
                 message_count: opsMessages.length,
                 last_message: preview(opsMessages[opsMessages.length - 1]) }),
  relayChannel({ id: "rc4", name: "art", topic: "what the artist made today",
                 message_count: artMessages.length,
                 last_message: preview(artMessages[artMessages.length - 1]) }),
  relayChannel({ id: "rd1", kind: "dm", topic: "", open: false, agent: "pai",
                 reply_mode: "linear", dispatch_mode: "facade", default_agent: "pai",
                 participants: ["agent:pai", "user:kyle"], message_count: 2,
                 last_message: preview(dmMessages[1]) }),
  relayChannel({ id: "rx1", kind: "dm", home: "external", title: "chat-8675309",
                 topic: "Discord thread", open: false, agent: "pai",
                 reply_mode: "linear", dispatch_mode: "default", default_agent: "pai",
                 participants: ["agent:pai", "discord:55"], message_count: 2,
                 last_message: preview(connectedMessages[1]) }),
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
  rc1: generalMessages, rc2: [], rc3: opsMessages, rc4: artMessages, rd1: dmMessages,
  rx1: connectedMessages,
  rg1: [], rg2: [],
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

const detail = (id: string, faces: Record<string, { emoji: string; hue: number }>,
                extra: Record<string, unknown> = {}) =>
  ({ ...relayChannels.find((c) => c.id === id)!, faces, ...extra });


// --- Tickets (docs/design/20) ------------------------------------------------
// Two projects and a board wide enough to draw every card state the UI has a
// badge for: a stale one, an orphaned one (assigned to an agent presence has
// never heard of), one an agent is thinking about, and a closed pair. `stale`
// and the faces ride in on the row exactly as the API attaches them.
const agentFace = (participant: string | null) =>
  (participant?.startsWith("agent:")
    ? FACES[participant.slice(6) as keyof typeof FACES] ?? null : null);

const ticket = (over: Record<string, unknown>) => ({
  id: "t0", key: "OPS-0", channel_id: "rc3", title: "", body: "", state: "open",
  priority: "p2", assignee: null as string | null, reporter: "user:kyle",
  labels: [] as string[], parent_id: null, due_at: null, run_id: null,
  root_message_id: null as string | null,
  created_at: at(600), updated_at: at(120), last_activity_at: at(120), closed_at: null,
  assignee_face: null as { emoji: string; hue: number } | null,
  reporter_face: null as { emoji: string; hue: number } | null,
  stale: false,
  ...over,
});

/** The board, as the list route answers it. Exported so a spec can stream one
 * of these back through the SSE mock rather than inventing a second shape. */
export const ticketFixtures = [
  ticket({ id: "t1", key: "OPS-1", title: "Weather repeats across the digest",
           body: "The forecast is one item a day, not one per source.",
           state: "open", priority: "p1", assignee: "agent:news",
           assignee_face: FACES.news, labels: ["weather", "dedup"],
           run_id: runs[1].id, root_message_id: "k1", created_at: at(600),
           updated_at: at(30), last_activity_at: at(30) }),
  ticket({ id: "t2", key: "OPS-2", title: "Kafka lag alert fires every night",
           body: "Every night at 03:00, and every night it clears itself.",
           state: "in_progress", priority: "p0", assignee: "agent:health-monitor",
           assignee_face: FACES["health-monitor"], reporter: "agent:health-monitor",
           reporter_face: FACES["health-monitor"], labels: ["alerting"],
           root_message_id: "k2", created_at: at(500),
           updated_at: at(10), last_activity_at: at(10) }),
  ticket({ id: "t3", key: "OPS-3", title: "Rotate the Discord bot token",
           state: "in_progress", assignee: "agent:pai", assignee_face: FACES.pai,
           root_message_id: "k3", stale: true, created_at: at(9000),
           updated_at: at(8000), last_activity_at: at(8000) }),
  ticket({ id: "t4", key: "OPS-4", title: "Decide the retention window",
           state: "blocked", assignee: "user:admin", labels: ["policy"],
           root_message_id: "k4", created_at: at(400),
           updated_at: at(60), last_activity_at: at(60) }),
  ticket({ id: "t5", key: "OPS-5", title: "Freshness gates need a second pair of eyes",
           state: "review", priority: "p1", assignee: "agent:news",
           assignee_face: FACES.news, root_message_id: "k5", created_at: at(300),
           updated_at: at(45), last_activity_at: at(45) }),
  ticket({ id: "t6", key: "OPS-6", title: "Back up the scheduler's state",
           state: "done", priority: "p3", assignee: "agent:pai",
           assignee_face: FACES.pai, root_message_id: "k6", created_at: at(260),
           updated_at: at(120), last_activity_at: at(120), closed_at: at(120) }),
  ticket({ id: "t7", key: "GEN-1", channel_id: "rc1", title: "Write down what the platform is for",
           state: "open", priority: "p3", created_at: at(800),
           updated_at: at(700), last_activity_at: at(700) }),
  // The assignee is an agent that no longer exists: presence has no row for
  // `ghost`, which is the whole definition of orphaned.
  ticket({ id: "t8", key: "GEN-2", channel_id: "rc1", title: "Try the old importer again",
           state: "cancelled", assignee: "agent:ghost", created_at: at(900),
           updated_at: at(180), last_activity_at: at(180), closed_at: at(180) }),
];

const ticketProjects = [
  { id: "rc1", name: "general", title: "general", prefix: "GEN", open: 1, in_progress: 0 },
  { id: "rc3", name: "ops", title: "ops", prefix: "OPS", open: 1, in_progress: 2 },
];

const ticketEvent = (over: Record<string, unknown>) => ({
  id: "e0", ticket_id: "t1", actor: "agent:news", kind: "created", from_value: null,
  to_value: null, reason: null, message_id: null, run_id: null, created_at: at(600),
  actor_face: FACES.news, ...over,
});

const ticketDetails: Record<string, unknown> = {
  "OPS-1": {
    ticket: ticketFixtures[0],
    events: [
      ticketEvent({ id: "e1", kind: "created", message_id: "k1", run_id: runs[1].id }),
      ticketEvent({ id: "e2", kind: "assigned", to_value: "agent:news", actor: "user:kyle",
                    actor_face: null, created_at: at(598) }),
      ticketEvent({ id: "e3", kind: "moved", from_value: "open", to_value: "in_progress",
                    reason: "picking it up", run_id: runs[1].id, created_at: at(595) }),
      ticketEvent({ id: "e4", kind: "commented", message_id: "k1r2", run_id: runs[1].id,
                    created_at: at(585) }),
    ],
    root_message_id: "k1",
    runs: [{ id: "r-news-1", agent: "news", state: "succeeded", trigger: "mention",
             created_at: at(590) }],
    thinking: null,
  },
  "OPS-2": {
    ticket: ticketFixtures[1],
    events: [ticketEvent({ id: "e5", ticket_id: "t2", actor: "agent:health-monitor",
                           actor_face: FACES["health-monitor"], message_id: "k2",
                           created_at: at(500) })],
    root_message_id: "k2",
    runs: [{ id: "r-hm-1", agent: "health-monitor", state: "running", trigger: "schedule",
             created_at: at(3) }],
    thinking: { run_id: "r-hm-1", agent: "health-monitor", started_at: at(3) },
  },
};

// OPS-6 is the closed one: its page is where "a done ticket only reopens"
// is visible, so it needs a detail of its own.
ticketDetails["OPS-6"] = {
  ticket: ticketFixtures[5],
  events: [ticketEvent({ id: "e6", ticket_id: "t6", actor: "agent:pai", actor_face: FACES.pai,
                         kind: "moved", from_value: "review", to_value: "done",
                         message_id: "k6", created_at: at(120) })],
  root_message_id: "k6",
  runs: [],
  thinking: null,
};

const ticketStats = {
  open: 2, in_progress: 2, blocked: 1, review: 1, done_24h: 1,
  moved_24h: [
    { actor: "agent:news", label: "news", count: 3, face: FACES.news },
    { actor: "user:kyle", label: "kyle", count: 2, face: null },
  ],
  stale: 1, orphaned: 1,
  budget: {
    creates_per_hour: 10, stale_days: 3,
    agents: [{ agent: "health-monitor", used: 4, left: 6, face: FACES["health-monitor"] }],
  },
};

/** The list route's filters, which the board does NOT use (it loads the whole
 * board once and narrows in the browser) but T8's reads and any future server
 * filtering do — a mock that answered every query with everything would make
 * the wrong thing pass. */
function ticketList(params: URLSearchParams) {
  const q = (params.get("q") ?? "").toLowerCase();
  const label = params.get("label");
  return ticketFixtures.filter((t) =>
    (!params.get("channel") || t.channel_id === params.get("channel"))
    && (!params.get("state") || t.state === params.get("state"))
    && (!params.get("assignee") || t.assignee === params.get("assignee"))
    && (params.get("mine") !== "true" || t.assignee === "user:kyle")
    && (!label || t.labels.includes(label))
    && (!q || `${t.title} ${t.body}`.toLowerCase().includes(q)));
}

/** Every ticket write answers with the row it produced, the way the API does:
 * the board absorbs what came back rather than guessing at it. */
function ticketWrite(path: string, method: string, body: Record<string, unknown>): unknown {
  if (path === "/api/tickets" && method === "POST") {
    const project = ticketProjects.find((p) => p.id === body.channel) ?? ticketProjects[1];
    return ticket({ id: "t9", key: `${project.prefix}-9`, channel_id: project.id,
                    title: String(body.title ?? ""), body: String(body.body ?? ""),
                    priority: String(body.priority ?? "p2"),
                    assignee: (body.assignee as string) ?? null,
                    assignee_face: agentFace((body.assignee as string) ?? null),
                    labels: (body.labels as string[]) ?? [],
                    created_at: new Date().toISOString(),
                    updated_at: new Date().toISOString(),
                    last_activity_at: new Date().toISOString() });
  }
  const m = /^\/api\/tickets\/([^/]+)(?:\/(move|assign|comments))?$/.exec(path);
  const row = m && ticketFixtures.find((t) => t.key === m[1] || t.id === m[1]);
  if (!m || !row) return undefined;
  if (m[2] === "comments") {
    return relayMessage({ id: `c-${row.key}`, channel_id: row.channel_id,
                          author: "user:kyle", body: String(body.body ?? ""),
                          reply_to: row.root_message_id, thread_root: row.root_message_id,
                          created_at: new Date().toISOString() });
  }
  const changed: Record<string, unknown> =
    m[2] === "move" ? { state: body.state, closed_at: ["done", "cancelled"].includes(String(body.state)) ? new Date().toISOString() : null }
    : m[2] === "assign" ? { assignee: body.to ?? null, assignee_face: agentFace((body.to as string) ?? null) }
    : { ...body };
  return { ...row, ...changed, updated_at: new Date().toISOString(),
           last_activity_at: new Date().toISOString() };
}

// --- the wiki (docs/design/21) ------------------------------------------------
// Three live pages, one archived, and two slugs the live ones point at that
// nobody has written — which is what a wanted page IS. Between them they cover
// every state the UI draws: a promoted page, a stale one, an agent-written
// version with a run behind it, a chip, a red chip, and a `[[slug]]` in
// backticks that is neither.
const DAY = 24 * 60;

const wikiRow = (over: Record<string, unknown>) => ({
  id: "w0", slug: "page", title: "Page", body: "", summary: "",
  tags: [] as string[], version: 1,
  created_by: "user:admin", updated_by: "user:admin",
  source_memory_id: null as string | null,
  created_at: at(60 * DAY), updated_at: at(60),
  archived_at: null as string | null,
  updated_by_face: null as { emoji: string; hue: number } | null,
  ...over,
});

const homeBody = `# The wiki

Everything the platform knows, written where anybody can cite it — the people
and the agents alike.

Start with [[deploying]]: it is the page the rooms point at most.

Write \`[[standup]]\` in a message and it becomes a chip; the same words in
backticks stay words. A page nobody has written yet is red, and following one
opens the editor — we still need a [[standup]] page.

A link somebody wrote on purpose is left alone, brackets and all:
[See [[deploying]]](https://example.com/deploy).
`;

const deployingBody = `# Deploying

Push the image, sync the agents, then \`helm upgrade\`.

Do not pass \`--reuse-values\` on a chart whose defaults moved: it keeps the old
ones and the release drifts from the chart.

| step | who | command | needs | takes | notes |
| --- | --- | --- | --- | --- | --- |
| build | ci | \`docker buildx build --provenance=false .\` | a tag | 4 min | the flag matters |
| import | ops | \`ctr -n k8s.io images import agent-platform.tar\` | the tarball | 2 min | on the node |
| release | ops | \`helm upgrade agent-platform ./chart\` | the chart | 1 min | never --reuse-values |

See also [[kafka-lag]] and the [[standup]] we owe ourselves.
`;

/** The wiki as the list route answers it. Exported so a spec can stream one of
 * these back through the SSE mock rather than inventing a second shape. */
export const wikiFixtures = [
  wikiRow({ id: "w-home", slug: "home", title: "The wiki", body: homeBody,
            summary: "Everything the platform knows, written where anybody can cite it.",
            version: 3, updated_by: "agent:news", updated_by_face: FACES.news,
            updated_at: at(20) }),
  wikiRow({ id: "w-deploying", slug: "deploying", title: "Deploying",
            body: deployingBody, tags: ["ops"],
            summary: "Push the image, sync the agents, then helm upgrade.",
            version: 2, updated_by: "agent:pai", updated_by_face: FACES.pai,
            updated_at: at(90) }),
  // Promoted out of pai's own memory, and untouched since — the stale one.
  wikiRow({ id: "w-kyle", slug: "kyle-location", title: "Kyle's location",
            body: "Kyle is in Whitby, Ontario — UTC-5, UTC-4 in the summer.\n",
            summary: "Kyle is in Whitby, Ontario.", tags: ["memory", "pai"],
            source_memory_id: "m-pai-location", updated_by: "user:admin",
            created_at: at(120 * DAY), updated_at: at(45 * DAY) }),
];

// Archived: out of the wiki, still in the record. It is never listed and reads
// as missing, exactly as the API answers it.
const wikiArchived = wikiRow({ id: "w-old", slug: "old-notes", title: "Old notes",
                               body: "Superseded.\n", version: 4,
                               updated_at: at(200 * DAY), archived_at: at(30 * DAY) });

const wikiCitation = (message_id: string, author: string, minsAgo: number) =>
  ({ message_id, channel_id: "rc3", author, created_at: at(minsAgo) });

const wikiDetails: Record<string, unknown> = {
  home: { page: wikiFixtures[0], backlinks: [{ slug: "deploying", title: "Deploying" }],
          cited_in: { count: 0, count_capped: false, last: [] } },
  deploying: {
    page: wikiFixtures[1],
    backlinks: [{ slug: "home", title: "The wiki" }],
    cited_in: { count: 2, count_capped: false,
                last: [wikiCitation("k1r4", "agent:pai", 583),
                       wikiCitation("k1r5", "user:kyle", 590)] },
  },
  "kyle-location": { page: wikiFixtures[2], backlinks: [],
                     cited_in: { count: 0, count_capped: false, last: [] } },
};

const wikiHistoryRow = (over: Record<string, unknown>) => ({
  id: "v0", version: 1, title: "Page", author: "user:admin", run_id: null,
  reason: "", created_at: at(60), added: 0, removed: 0,
  author_face: null as { emoji: string; hue: number } | null, ...over,
});

const wikiHistories: Record<string, unknown[]> = {
  deploying: [
    wikiHistoryRow({ id: "vd2", version: 2, title: "Deploying", author: "agent:pai",
                     author_face: FACES.pai, run_id: "r-pai-1",
                     reason: "add the helm --reuse-values trap", added: 12, removed: 1,
                     created_at: at(90) }),
    wikiHistoryRow({ id: "vd1", version: 1, title: "Deploying", reason: "first draft",
                     added: 20, removed: 0, created_at: at(40 * DAY) }),
  ],
  home: [
    wikiHistoryRow({ id: "vh3", version: 3, title: "The wiki", author: "agent:news",
                     author_face: FACES.news, run_id: "r-news-1",
                     reason: "link the deploying page", added: 4, removed: 2,
                     created_at: at(20) }),
    wikiHistoryRow({ id: "vh2", version: 2, title: "The wiki", reason: "say what a red link is",
                     added: 6, removed: 0, created_at: at(2 * DAY) }),
    wikiHistoryRow({ id: "vh1", version: 1, title: "The wiki", reason: "first draft",
                     added: 9, removed: 0, created_at: at(60 * DAY) }),
  ],
  "kyle-location": [
    wikiHistoryRow({ id: "vk1", version: 1, title: "Kyle's location",
                     reason: "promoted from memory", added: 2, removed: 0,
                     created_at: at(45 * DAY) }),
  ],
};

// One version and what it changed. The diff is a real unified diff so the view
// has hunk headers, context, additions and removals to colour and to prefix.
const deployingDiff = [
  "--- deploying v1",
  "+++ deploying v2",
  "@@ -1,6 +1,8 @@",
  " # Deploying",
  " ",
  " Push the image, sync the agents, then `helm upgrade`.",
  "-Then check the release.",
  "+",
  "+Do not pass `--reuse-values` on a chart whose defaults moved: it keeps the old",
  "+ones and the release drifts from the chart.",
  " ",
  " See also [[kafka-lag]] and the [[standup]] we owe ourselves.",
].join("\n");

const wikiVersion = (slug: string, version: number, over: Record<string, unknown> = {}) => {
  const row = (wikiHistories[slug] ?? []).find(
    (r) => (r as { version: number }).version === version) as Record<string, unknown> | undefined;
  if (!row) return undefined;
  const page = wikiFixtures.find((p) => p.slug === slug);
  return {
    version: { ...row, page_id: page?.id ?? "w0", body: page?.body ?? "" },
    diff: `--- ${slug} v${version - 1}\n+++ ${slug} v${version}\n@@ -1,1 +1,1 @@\n-before\n+after`,
    added: row.added, removed: row.removed, ...over,
  };
};

const wikiWanted = [
  { slug: "standup", linked_from: ["home", "deploying"] },
  { slug: "kafka-lag", linked_from: ["deploying"] },
];

const wikiStats = {
  pages: 3,
  edits_24h: [
    { author: "agent:pai", face: FACES.pai, count: 2 },
    { author: "user:admin", face: null, count: 1 },
  ],
  wanted: 2, stale: 1,
  budget: { limit: 30, agents: [{ agent: "pai", used: 2, left: 28 }] },
};

/** The list route's filters. The rail asks the server for `q` and `tag`
 * because only the server can rank a search — a mock that answered every query
 * with everything would make the wrong thing pass. */
function wikiList(params: URLSearchParams) {
  const q = (params.get("q") ?? "").toLowerCase();
  const tag = params.get("tag");
  const memory = params.get("source_memory_id");
  const since = params.get("changed_since");
  const limit = Number(params.get("limit") ?? 200);
  return wikiFixtures
    .filter((p) => (!q || `${p.title} ${p.body}`.toLowerCase().includes(q))
      && (!tag || p.tags.includes(tag))
      && (!memory || p.source_memory_id === memory)
      && (!since || (p.updated_at ?? "") >= since))
    .slice(0, limit);
}

/** Every wiki read, including the 404s. A slug nobody has written answers 404
 * the way the API does — that IS the answer the wanted-page flow is built on,
 * so it must not be recorded as a missing fixture. */
function wikiGet(path: string, params: URLSearchParams):
  { status?: number; json: unknown } | undefined {
  if (path === "/api/wiki/pages") return { json: wikiList(params) };
  if (path === "/api/wiki/wanted") return { json: wikiWanted };
  if (path === "/api/wiki/stats") return { json: wikiStats };
  const m = /^\/api\/wiki\/pages\/([^/]+)(?:\/(history|versions\/(\d+)))?$/.exec(path);
  if (!m) return undefined;
  const [, slug, kind, version] = m;
  const known = wikiFixtures.some((p) => p.slug === slug) || slug === wikiArchived.slug;
  const missing = { status: 404, json: { detail: "unknown wiki page" } };
  if (!known) return missing;
  // An archived page keeps its history and loses everything else.
  if (!kind) {
    return wikiDetails[slug] ? { json: wikiDetails[slug] } : missing;
  }
  if (kind === "history") return { json: wikiHistories[slug] ?? [] };
  const view = slug === "deploying" && version === "2"
    ? wikiVersion(slug, 2, { diff: deployingDiff })
    : wikiVersion(slug, Number(version));
  return view ? { json: view } : { status: 404, json: { detail: `${slug} has no v${version}` } };
}

/** Every wiki write answers with the page it produced, the way the API does.
 * A PUT that names a base version the page has moved past is the 409 the
 * editor is built around — body and all, since the version it has got to and
 * what it now says are what makes merging possible. */
function wikiWrite(path: string, method: string, body: Record<string, unknown>):
  { status?: number; json: unknown } | undefined {
  const now = new Date().toISOString();
  if (path === "/api/wiki/pages" && method === "POST") {
    return { status: 201, json: wikiRow({
      id: `w-${body.slug}`, slug: String(body.slug ?? "new"),
      title: String(body.title ?? body.slug ?? "New page"),
      body: String(body.body ?? ""), summary: String(body.body ?? "").split("\n")[0],
      tags: (body.tags as string[]) ?? [], version: 1,
      updated_by: "user:kyle", created_at: now, updated_at: now }) };
  }
  if (path === "/api/wiki/promote" && method === "POST") {
    return { json: wikiFixtures[2] };
  }
  const m = /^\/api\/wiki\/pages\/([^/]+)(?:\/(append|restore))?$/.exec(path);
  const row = m && wikiFixtures.find((p) => p.slug === m[1]);
  if (!m || !row) return undefined;
  if (method === "DELETE") {
    return { json: { ...row, archived_at: now, updated_at: now } };
  }
  if (m[2] === "append") {
    return { json: { ...row, version: row.version + 1, updated_at: now,
                     body: `${row.body}\n\n${String(body.body ?? "")}` } };
  }
  if (m[2] === "restore") {
    return { json: { ...row, version: row.version + 1, updated_at: now,
                     updated_by: "user:kyle", updated_by_face: null } };
  }
  if (method !== "PUT") return undefined;
  if (body.base_version !== row.version) {
    return { status: 409, json: {
      detail: `${row.slug} has moved on: you read v${body.base_version}, it is at v5`,
      current_version: 5, current_summary: row.summary } };
  }
  return { json: { ...row, version: row.version + 1, updated_at: now,
                   updated_by: "user:kyle", updated_by_face: null,
                   body: String(body.body ?? row.body),
                   title: String(body.title ?? row.title),
                   tags: (body.tags as string[]) ?? row.tags } };
}

const FIXTURES: Record<string, unknown> = {
  "/api/setup-state": { needs_admin: false, secrets },
  "/api/agents": agentRows,
  "/api/agents/health-monitor": { ...healthMonitor, face: FACES["health-monitor"], image_artifact_id: null },
  // news has a definition too, so its own page (and its Tickets tab) can be
  // opened the way health-monitor's can.
  "/api/agents/news": agentRows[1],
  "/api/agents/health-monitor/versions": versions,
  "/api/agents/health-monitor/versions/1": { ...versions[1], snapshot: def({ name: "health-monitor" }) },
  "/api/agents/health-monitor/versions/2": { ...versions[0], snapshot: healthMonitor },
  "/api/agent-models": {
    models: [{ id: "", label: "CLI default" }, { id: "sonnet", label: "Sonnet" }],
    codex_models: [
      { id: "gpt-6-astra", label: "GPT-6 Astra — most capable" },
      { id: "gpt-5.6-sol", label: "GPT-5.6 Sol — complex professional work" },
    ],
  },
  "/api/runs": runs,
  [`/api/runs/${runs[0].id}`]: runDetail,
  [`/api/runs/${runs[0].id}/transcript`]: [],
  // The run OPS-1 summoned. `ticket_id` is the run row's own column, so a run
  // page can say which piece of work it belongs to.
  "/api/runs/r-news-1": { ...runDetail, id: "r-news-1", agent: "news", trigger: "mention",
                          ticket_id: "t1", prompt: "Fix the weather duplication." },
  "/api/runs/r-news-1/transcript": [],
  // The dev runs behind the publish cards (docs/design/24); their transcripts
  // come over the tail socket, see `runTail`.
  "/api/runs/r-eng-1": { ...runDetail, id: "r-eng-1", agent: "engineer", trigger: "mention",
                         prompt: "Take OPS-5 through review." },
  "/api/runs/r-qa-1": { ...runDetail, id: "r-qa-1", agent: "qa", trigger: "mention", exit_code: 1,
                        state: "failed", prompt: "Write the test for OPS-5." },
  "/api/whoami": { principal: "kyle", role: "admin", agent: null, run_id: null, tools: null },
  "/api/relay/channels": relayChannels,
  "/api/relay/channels/rc1": detail("rc1", { news: FACES.news, "health-monitor": FACES["health-monitor"] }),
  "/api/relay/channels/rc2": detail("rc2", {}),
  "/api/relay/channels/rc3": detail("rc3", { news: FACES.news, pai: FACES.pai,
                                             "health-monitor": FACES["health-monitor"] }),
  "/api/relay/channels/rc4": detail("rc4", { pai: FACES.pai }),
  "/api/relay/channels/rd1": detail("rd1", { pai: FACES.pai }),
  "/api/relay/channels/rx1": detail("rx1", { pai: FACES.pai }, { bindings: [{
    id: "bind-x", connector: "discord", external_ref: "8675309", external_kind: "thread",
    parent_external_ref: "1234", display_name: "chat-8675309",
    external_url: "https://discord.com/channels/1/2/3", status: "active", config: {},
  }] }),
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
                global_per_hour: 120, cooldown_seconds: 20, context_messages: 30 },
  },
  // health-monitor is thinking in the OPS room and nowhere else: that is what
  // puts the pulse on OPS-2's card and keeps it off #general's thinking line.
  "/api/relay/presence": [
    { agent: "news", state: "thinking", thinking_in: ["rc1"], face: FACES.news },
    { agent: "health-monitor", state: "thinking", thinking_in: ["rc3"],
      face: FACES["health-monitor"] },
    // pai is the idle one — an agent with nothing running, which is what an
    // assignee with no pulse looks like (OPS-3) — and old-importer is switched
    // off, which is what the pickers must not offer.
    { agent: "pai", state: "idle", thinking_in: [], face: FACES.pai },
    { agent: "old-importer", state: "disabled", thinking_in: [], face: face("🧱", 210) },
  ],
  // Get-or-create, so the same answer whatever the caller asks for: the
  // AgentDetail Conversations tab opens its agent's dm through this.
  "/api/relay/dm": detail("rd1", { pai: FACES.pai }),
  "/api/memories": [
    { id: "m1", agent: "pai", key: null, content: "Kyle likes terminals.", tags: ["style"],
      created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    // The two states a memory can be in once the wiki exists (docs/design/21):
    // one that has graduated into a page — `w-kyle` names it, which is what
    // the badge reads — and one that has not, which is what Promote is for.
    { id: "m-pai-location", agent: "pai", key: "Kyle location",
      content: "Kyle is in Whitby, Ontario — UTC-5, UTC-4 in the summer.", tags: ["place"],
      created_at: at(120 * DAY), updated_at: at(45 * DAY) },
    { id: "m-news-dedup", agent: "news", key: "Weather dedup",
      content: "The forecast is one item a day, not one per source.", tags: ["rules"],
      created_at: at(300), updated_at: at(120) },
  ],
  "/api/tags": [],
  "/api/pull-requests": prs,
  "/api/pull-requests/12/files": [
    { filename: "skills/release-review/SKILL.md", status: "modified", additions: 2, deletions: 1,
      patch: "@@ -1,2 +1,3 @@\n-old line\n+new line\n+another" },
  ],
  "/api/pull-requests/12/summary": {
    state: "ready", sha: "abc123",
    summary: "Changes the release-review skill: adds one instruction line. Low risk — no secrets, triggers, or permissions change.",
  },
  "/api/pull-requests/12/impact": {
    items: [{ file: "skills/release-review/SKILL.md", block: "skill: release-review", area: "definition",
              status: "modified", additions: 2, deletions: 1, notable: [] }],
    warnings: [],
  },
  "/api/sync-status": { sha: "abc123" },
  "/api/dlq": [],
  "/api/skills": [
    { name: "release-review", description: "Query the news archive.", icon: "🗞️",
      secrets: [], error: null, used_by: ["news-librarian"] },
  ],
  "/api/skills/release-review": {
    name: "release-review", description: "Query the news archive.", icon: "🗞️",
    secrets: [], error: null, used_by: ["news-librarian"],
    body: "Query it.", raw: "---\nname: release-review\n---\nQuery it.",
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
  // The seeded #standup summons (docs/design/19): a job with no agent, which is
  // what proves the Schedules page can render a row that belongs to a room.
  // `next_fire` is null so it stays out of the Dashboard's upcoming tile — that
  // tile is covered by the entrypoint cron above.
  "/api/jobs": [
    { id: "jstandup", name: "relay-standup", agent: null, relay_channel: "standup",
      cron: "0 9 * * *", timezone: "America/Toronto", enabled: true,
      prompt: "@all — what did you do in the last 24h? Two lines, link anything you touched.",
      last_fire: null, next_fire: null },
  ],
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
  "/api/teams": [],
  "/api/projects": [],
  "/api/apps": [
    { name: "news", source_app: "news", display_name: "", description: "Browse gathered news by calendar and topic.", icon: "🗞️",
      ui: true, api: true, postgres: true, kafka_topics: ["app.news.item.ingested"],
      redis: false, agent_key_role: "operator", error: null, ready: true, ready_replicas: 1 },
    { name: "scratch", source_app: null, display_name: "", description: "A declared-but-undeployed app.", icon: "🧩",
      ui: false, api: true, postgres: false, kafka_topics: [], redis: false,
      agent_key_role: null, error: null, ready: null, ready_replicas: 0 },
    { name: "tcms", source_app: "tcms", display_name: "", description: "Test cases, runs and the platform's own health, as the QA sees them.",
      icon: "🧪", ui: true, api: true, postgres: true, kafka_topics: ["app.tcms.run.finished"],
      redis: false, agent_key_role: "operator", error: null, ready: true, ready_replicas: 1 },
  ],
  "/api/metrics/tools": [
    { tool: "stocks", calls: 12, denials: 0, errors: 1, avg_latency_ms: 900.5 },
  ],
  "/api/help/topics": [
    { slug: "agents", title: "Agents" },
    { slug: "changes", title: "Changes — the change loop" },
    { slug: "relay", title: "Relay" },
    { slug: "tickets", title: "Tickets" },
    { slug: "wiki", title: "Wiki" },
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
  "/api/help/topics/tickets": {
    slug: "tickets", title: "Tickets",
    markdown: "# Tickets\n\n**What:** the board the platform's work is tracked on."
      + " A ticket has a key — `OPS-12` — and lives in the project room it was"
      + " opened in.\n\n**assign = summon:** handing a ticket to an agent posts a"
      + " real mention in its thread, so the ask and the assignment are one act.",
  },
  "/api/help/topics/wiki": {
    slug: "wiki", title: "Wiki",
    markdown: "# Wiki\n\n**What:** the shared knowledge the platform writes down"
      + " — pages with slugs, `[[wiki-links]]`, and a version history.\n\n"
      + "A link to a slug nobody has written yet is a **wanted page**: it renders"
      + " red, and following it opens the editor.",
  },
  "/api/help/tools": [
    { name: "mcp__platform__memory", kind: "platform", sensitive: false,
      display_name: "Memory", description: "Private persistent memory across runs." },
    { name: "Bash", kind: "claude", sensitive: true,
      description: "Run shell commands inside the agent's pod." },
    { name: "WebSearch", kind: "claude", sensitive: false,
      description: "Search the public web." },
    { name: "TodoWrite", kind: "claude", sensitive: false, display_name: "Todo",
      description: "Keep an internal working task list during a run." },
    { name: "PlaywrightMCP", kind: "claude", sensitive: false, dev_only: true,
      display_name: "Playwright browser",
      description: "A live browser through the Playwright MCP server, locked to the platform's"
        + " own UI; only a `role: dev` agent can use it, and the runner starts the server." },
    { name: "mcp__platform__query_app", kind: "platform", sensitive: false,
      description: "Call a read-only API endpoint of an installed platform app." },
    { name: "mcp__platform__quota_ok", kind: "platform", sensitive: false,
      display_name: "Quota guard",
      description: "Check current subscription usage against this agent's thresholds." },
  ],
  "/api/report-types": [
    { name: "daily-news", description: "Morning digest of gathered news.", icon: "📰",
      generator: "news", cadence: "daily", retention_days: 365, error: null,
      count: 1, latest_date: today },
  ],
  "/api/reports": reports,
  [`/api/reports/${reports[0].id}`]: { ...reports[0], html: reportHtml },
  "/api/tickets/projects": ticketProjects,
  "/api/tickets/stats": ticketStats,
  "/api/tickets/OPS-1": ticketDetails["OPS-1"],
  "/api/tickets/t1": ticketDetails["OPS-1"],
  "/api/tickets/OPS-2": ticketDetails["OPS-2"],
  "/api/tickets/OPS-6": ticketDetails["OPS-6"],
  "/api/tickets/t2": ticketDetails["OPS-2"],
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

/** The usage snapshot the sidebar bars are drawn from (docs/design/22).
 * `resets_at` is relative to the moment the fixture is asked for so the
 * countdown in the label is always a countdown, never a date in the past. */
function quotaSnapshot(over: Record<string, unknown> = {}) {
  const now = Date.now();
  const at = (ms: number) => new Date(now + ms).toISOString();
  return {
    five_hour: { utilization: 0.22, resets_at: at(3.9 * 3600e3) },
    seven_day: { utilization: 0.81, resets_at: at(4.2 * 86400e3) },
    status: "allowed", observed_at: at(-120e3), source: "proxy",
    stale: false, age_seconds: 120, probe: null,
    codex: {
      five_hour: { utilization: 0.11, resets_at: at(2.5 * 3600e3) },
      seven_day: { utilization: 0.95, resets_at: at(5 * 86400e3) },
      status: "allowed", observed_at: at(-30e3), source: "refresh",
      stale: false, age_seconds: 30, probe: "usage",
    },
    ...over,
  };
}

/** A snapshot the hook has to refresh, and a count of the refreshes it made.
 * Registered AFTER `mockApi` on purpose — Playwright tries handlers in
 * reverse registration order, so the last one in wins. */
export async function staleQuota(page: Page): Promise<{ count: () => number }> {
  let refreshes = 0;
  const stale = quotaSnapshot({
    five_hour: { utilization: 0.05, resets_at: null },
    seven_day: { utilization: 0.40, resets_at: null },
    observed_at: new Date(Date.now() - 7200e3).toISOString(),
    stale: true, age_seconds: 7200,
  });
  await page.route("**/api/quota", async (route: Route) => {
    await route.fulfill({ json: stale });
  });
  await page.route("**/api/quota/refresh*", async (route: Route) => {
    refreshes += 1;
    await route.fulfill({ json: quotaSnapshot({ source: "refresh", probe: "count_tokens" }) });
  });
  return { count: () => refreshes };
}

/** Take a route back out of the mock: what the UI must do when an endpoint
 * it asks for is simply not answering. */
export async function unfixtured(page: Page, glob: string): Promise<void> {
  await page.route(glob, async (route: Route) => {
    await route.fulfill({ status: 500, json: { detail: "unavailable" } });
  });
}

export async function mockApi(page: Page): Promise<string[]> {
  const unmatched: string[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/quota" || path === "/api/quota/refresh") {
      await route.fulfill({ json: quotaSnapshot() });
      return;
    }
    if (path === "/api/cron/preview") {
      await route.fulfill({ json: cronPreview(url.searchParams.get("expr") ?? "") });
      return;
    }
    if (path === "/api/live-views" && route.request().method() === "GET") {
      const app = url.searchParams.get("app_name");
      await route.fulfill({ json: app === "news" ? [{
        id: "n1".repeat(16), app_name: "news", slug: "overview", published_version: 2,
      }] : [] });
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
    // The whole wiki surface, reads and writes alike — including its 404s: a
    // slug nobody has written is an ANSWER here (the wanted-page flow is built
    // on it), never a fixture somebody forgot.
    if (path.startsWith("/api/wiki/")) {
      const method = route.request().method();
      const answer = method === "GET"
        ? wikiGet(path, url.searchParams)
        : wikiWrite(path, method, route.request().postDataJSON() ?? {});
      if (answer) {
        await route.fulfill({ status: answer.status ?? 200, json: answer.json });
        return;
      }
    }
    if (path.startsWith("/api/artifacts")) {
      const answer = artifactRoute(path, route.request().method(), url.searchParams);
      if (answer) {
        await route.fulfill(answer.body
          ? { status: 200, body: answer.body, contentType: answer.contentType }
          : { status: answer.status ?? 200, json: answer.json });
        return;
      }
    }
    if (path === "/api/tickets" && route.request().method() === "GET") {
      await route.fulfill({ json: ticketList(url.searchParams) });
      return;
    }
    if (["POST", "PATCH"].includes(route.request().method())
        && path.startsWith("/api/tickets")) {
      const written = ticketWrite(path, route.request().method(),
                                  route.request().postDataJSON() ?? {});
      if (written !== undefined) {
        await route.fulfill({ json: written });
        return;
      }
    }
    if (route.request().method() === "POST" && path.startsWith("/api/relay/")) {
      const posted = relayPost(path, route.request().postDataJSON() ?? {});
      if (posted !== undefined) {
        await route.fulfill({ json: posted });
        return;
      }
    }
    // Login names a principal (docs/design/25). One 401 for a bad name and a
    // bad password alike, as the real route answers; the mock's password is
    // anything but "wrong", so a spec can pick either outcome.
    if (path === "/api/login" && route.request().method() === "POST") {
      const { principal = "admin", password = "" } = route.request().postDataJSON() ?? {};
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(principal)) {
        await route.fulfill({ status: 422, json: { detail: "invalid principal" } });
      } else if (password === "wrong") {
        await route.fulfill({ status: 401, json: { detail: "Unauthorized" } });
      } else {
        await route.fulfill({ json: { ok: true } });
      }
      return;
    }
    if (route.request().method() === "PUT" && path.endsWith("/image")) {
      const row = agentImageWrite(path, route.request().postDataJSON() ?? {});
      if (row !== undefined) {
        await route.fulfill({ json: row });
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
