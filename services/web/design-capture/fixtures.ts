import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { agentRows, ARTIFACTS, mockAccounts, runTail, STATE_APP_ID } from "../tests/mock-api.ts";
import { appFixtures } from "./fixtures-apps.ts";

type CaptureRoute = { source: string; scenario?: string; slug: string };
const instant = "2026-11-16T14:00:00Z";
const runId = "a1".repeat(16);
const liveId = "6a".repeat(16);
const proposalId = "5d".repeat(16);
const livePath = `/api/live-views/${liveId}`;
const designPath = "/api/ui-designer/docs/release-dashboard";

const artwork = (name: string) => readFileSync(new URL(`./assets/${name}.svg`, import.meta.url), "utf8");
const releaseMark = artwork("release-mark");
const artifactArtwork = new Map([
  [ARTIFACTS.generated.id, artwork("harbour")],
  ["a2".repeat(16), releaseMark],
  ["a4".repeat(16), releaseMark],
]);

const report = {
  id: "c4".repeat(16), type: "daily-news", title: "Daily news", date: "2026-11-16",
  time: "", meta: {}, run_id: runId, created_at: instant, updated_at: instant,
};
const liveDefinition = {
  renderer: "typed/v1", title: "Release overview", reads: [],
  actions: [{ alias: "feedback", operation: "tickets.create@1", channel: "general" }],
  blocks: [
    { kind: "heading", text: "Operational snapshot" },
    { kind: "paragraph", text: "The release was verified against the smoke and accessibility checks." },
    { kind: "metric", label: "Checks passed", value: "48" },
    { kind: "action", label: "Report a release issue", action_alias: "feedback" },
  ],
};
const live = {
  id: liveId, app_name: "news", slug: "overview", draft_revision: 3,
  published_version: 2, version: 2, definition: liveDefinition,
};
const proposal = {
  id: proposalId, app_id: STATE_APP_ID, state: "open", kind: "publish",
  proposer: "pai", created_at: instant, reason: "Weekly habits summary for the team.",
  base_version: 4, current_approved_version: 4, authority_generation: 2,
  current_authority_generation: 2, digest: "d".repeat(64),
  delta: { widening: ["Allow team members to view weekly totals."], added: [], removed: [] },
  validation: { data_dropping: [], reindex: ["Rebuild the weekly totals index."] },
  diff: [{ kind: "page", name: "weekly", current: null, proposed: { title: "Weekly habits" } }],
};
const rapid = {
  id: "release-check", title: "Release check", goal: "Review the release checklist and record open items.",
  participants: ["pai", "health-monitor"], state: "completed", end_reason: "finished",
  sink: { kind: "wiki", slug: "deploying", mode: "append" },
  areas: [
    { key: "smoke", title: "Smoke checks", status: "done", turns: 3, summary: "All primary routes passed." },
    { key: "handover", title: "Handover", status: "done", turns: 2, summary: "On-call notes updated." },
  ],
  started_at: "2026-11-16T13:45:00Z", ended_at: instant, created_at: instant,
  duration_minutes: 30, max_turns: 12, turns_total: 5, last_quota: null,
  output_ref: { kind: "wiki", slug: "deploying", version: 3 },
  summary: "The release is ready for operator review.", last_error: null,
};

const layers = [
  { id: "background", type: "rect", title: "Background", x: 0, y: 0, w: 520, h: 290, fill: "#111923" },
  { id: "card", type: "rect", title: "Release status card", x: 28, y: 80, w: 464, h: 92, fill: "#203041" },
  { id: "headline", type: "text", title: "Release dashboard title", x: 28, y: 25, w: 450, h: 37,
    text: [{ text: "Release dashboard", style: { size: 26, weight: 700 } }] },
  { id: "status", type: "text", title: "Release status", x: 48, y: 106, w: 405, h: 30,
    text: [{ text: "48 checks passed", style: { size: 20 } }] },
  { id: "next", type: "rect", title: "View checklist", x: 28, y: 206, w: 178, h: 41,
    fill: "#0aa3a3", link_to: { target: "checklist" } },
];
const designTree = {
  pages: [{ id: "page", title: "Release dashboard", children: [
    { id: "dashboard", type: "frame", title: "Release dashboard", x: 0, y: 0, w: 520, h: 290, children: layers },
    { id: "checklist", type: "frame", title: "Checklist", x: 560, y: 0, w: 520, h: 290, children: [] },
  ] }],
};
const designImage = `<svg xmlns="http://www.w3.org/2000/svg" width="520" height="290" viewBox="0 0 520 290">
  <rect width="520" height="290" rx="12" fill="#111923"/>
  <text x="28" y="55" fill="#eef5fc" font-family="sans-serif" font-size="27" font-weight="700">Release dashboard</text>
  <rect x="28" y="80" width="464" height="92" rx="8" fill="#203041"/>
  <text x="48" y="118" fill="#dce9f5" font-family="sans-serif" font-size="21">48 checks passed</text>
  <text x="48" y="146" fill="#9aafc5" font-family="sans-serif" font-size="14">Smoke and accessibility checks</text>
  <rect x="28" y="206" width="178" height="41" rx="7" fill="#0aa3a3"/>
  <text x="47" y="232" fill="#061617" font-family="sans-serif" font-weight="700" font-size="15">View checklist →</text>
</svg>`;

const appEntries = [
  ["running", "Running Coach", "🏃", "Activity, weekly mileage and personal records."],
  ["stockmarket", "Stockmarket", "📈", "Market summaries and backtest experiments."],
  ["tcms", "TCMS", "🧪", "Platform test runs, cases and test health."],
  ["judgment", "Judgment", "🧭", "Beliefs, predictions and feedback."],
].map(([name, display_name, icon, description]) => ({
  name, source_app: name, display_name, icon, description, ui: true, api: true,
  postgres: true, kafka_topics: [], redis: false, agent_key_role: "operator",
  error: null, ready: true, ready_replicas: 1,
}));

const fixtures: Record<string, unknown> = {
  "/api/apps": appEntries,
  "/api/teams": [{ id: "team-ops", slug: "ops", name: "Platform Operations",
    description: "Operate the platform and verify releases.", agents: ["pai", "news"],
    humans: ["admin"], relay_channel_id: "rg1", archived: false }],
  "/api/projects": [{ id: "proj-release", slug: "release", name: "Release readiness",
    description: "Coordinate checklists and handover.", team_slug: "ops",
    agents: ["pai", "news"], archived: false }],
  "/api/dlq": [{ id: "ee".repeat(16), agent: "health-monitor", trigger: "schedule.health-monitor",
    error: "Worker did not acknowledge the launch request.", created_at: instant, finished_at: instant }],
  "/api/agents/pai": agentRows.find((agent) => agent.name === "pai"),
  "/api/tasks/grants": [{ creator: "pai", target: "news" }],
  "/api/tasks/t-upcoming/events": [{ kind: "scheduled", actor: "kyle", reason: null, run_id: null, created_at: instant }],
  "/api/rapid-chats": [rapid],
  "/api/rapid-chats/release-check/turns": [
    { id: 1, seq: 1, agent: "pai", area_key: "smoke", role: "author", outcome: "done",
      state: "succeeded", run_id: null, prompt: "Summarize smoke results.",
      result: "The main console routes passed.", started_at: rapid.started_at, finished_at: instant, reason: null },
  ],
  "/api/local-persona/sessions": [{ id: "local-1", agent: "pai", harness: "codex", state: "active",
    conversation_id: "c-local", run_id: runId, accepted_sequence: 12,
    last_seen_at: instant, expires_at: null, device_label: "Workstation" }],
  "/api/local-persona/enrollments": [{ id: "local-device", agent: "pai", device_label: "Workstation", revoked: false }],
  "/api/backups": [{
    name: "agent-platform-2026-11-16.tar.age", bytes: 7214208,
    created_at: Date.parse(instant) / 1000, uploaded: true,
    gcs_uri: "gs://platform-recovery/daily/agent-platform-2026-11-16.tar.age",
  }],
  "/api/backups/connection": {
    bucket: "platform-recovery", prefix: "daily", age_recipient: "", credential_set: true, ready: true,
  },
  "/api/maintenance/restore-report": {
    maintenance: { mode: "restore", reason: "Recovery rehearsal", entered_at: instant, resumed_by: null },
    generated_at: instant, disabled_count: 1,
    apps: [{ app_id: STATE_APP_ID, name: "habits", status: "active",
      disabled_bindings: [{ kind: "view", name: "weekly", reason: "Source approval needs review." }] }],
    task_watermarks: [{ agent: "health-monitor", last_fired_at: instant, overdue_scheduled: 1 }],
    outbox: { status: "ready", message: "Previously sent notifications will not be replayed." },
  },
  "/api/app-data/proposals": [proposal],
  [`/api/app-data/proposals/${proposalId}`]: proposal,
  [livePath]: live,
  [`${livePath}/draft`]: live,
  [`${livePath}/versions`]: [{ version: 2, published_at: instant, published_by: "kyle", current: true }],
  "/api/live-operations": [{ id: "tickets.create@1", tool: "tickets",
    source: "platform-adapter", effects: ["write"], reason: "Create an issue in a fixed project.", view_eligible: true }],
  "/api/live-operation-grants": [{ principal_id: "kyle", operation: "tickets.create@1", enabled: true }],
  "/api/reports": [report],
  [`/api/reports/${report.id}`]: { ...report, html:
    '<header class="rk-header"><h1 class="rk-title">Daily news</h1><p class="rk-meta">November 16 · 3 topics</p></header>' +
    '<section class="rk-section"><h2>Infrastructure</h2><div class="rk-item"><span class="rk-item-title">Platform release verified</span>' +
    '<p class="rk-item-sum">Smoke checks and accessibility checks passed for the console and application routes.</p></div></section>' },
  "/api/ui-designer/docs": { docs: [
    { slug: "release-dashboard", title: "Release dashboard", owner: "kyle", updated_at: instant },
    { slug: "incident-review", title: "Incident review", owner: "ops", updated_at: instant },
  ] },
  [`${designPath}/versions`]: { version: 3, versions: [{ version: 3, author: "kyle", created_at: instant }] },
  [`${designPath}/resolved`]: designTree,
  [`${designPath}/comments`]: { comments: [] },
  [`${designPath}/fonts`]: { fonts: [] },
  "/api/ui-designer/libraries": { libraries: [] },
  "/api/ui-designer/findings": { findings: [] },
};

/** Layer capture-only fixture reads over the console test API. */
export async function prepareCapture(page: Page, capture: CaptureRoute) {
  const { source, scenario } = capture;
  if (scenario === "accounts" || scenario === "profile" || scenario === "anonymous") {
    const who = scenario === "anonymous" ? null
      : scenario === "profile" ? { username: "alice", role: "user" as const, group_id: "01".repeat(16) }
      : { username: "admin", role: "admin" as const, group_id: null };
    await mockAccounts(page, { who });
  }
  if (scenario === "run-tail") await runTail(page, runId, [{
    seq: 1, type: "assistant", message: { content: [
      { type: "text", text: "All scheduled health checks passed. Kafka is reachable and no jobs need retrying." },
    ] },
  }]);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET") return route.fallback();
    const path = url.pathname;
    if (scenario === "setup" && path === "/api/setup-state") {
      return route.fulfill({ json: { needs_admin: true, secrets: [] } });
    }
    if (path === `${designPath}/render`) {
      return route.fulfill({ contentType: "image/svg+xml", body: designImage });
    }
    const artifact = /^\/api\/artifacts\/([a-f0-9]+)\/(?:thumb|content)$/.exec(path);
    const image = artifact && artifactArtwork.get(artifact[1]);
    if (image) return route.fulfill({ contentType: "image/svg+xml", body: image });
    const value = Object.hasOwn(fixtures, path) ? fixtures[path] : appFixtures(source, url);
    if (value === undefined) return route.fallback();
    return route.fulfill({ json: value });
  });
}
