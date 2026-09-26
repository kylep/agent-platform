import { ARTIFACTS } from "./mock-api.ts";

// The console's route list, shared by the smoke sweep, the axe sweep and the
// QA's scripted walk (scripts/walk.mjs, docs/design/25) — one place to add a
// page so none of the three can drift from the others. Node loads this file
// directly (type stripping), which is why the import above carries its
// extension and nothing here is more than data.

export type PageRoute = {
  path: string;
  /** The h1 the page must show (matched case-insensitively). */
  heading: string;
  /** Text only the real content carries: the page rendered, not just its shell. */
  probe?: RegExp;
  /** The layout is a DIFFERENT layout on a phone: columns re-stack, the rail
   * becomes a drawer, wrapper boxes are dissolved to reorder what they hold.
   * A sweep that only ever ran at 1280 cannot see what any of that costs — a
   * landmark dropped by a mobile-only rule passed the axe sweep for months. */
  mobile: boolean;
  /** Routes one sweep holds and the other never did; the walk visits all. */
  only?: "smoke" | "a11y";
};

export const PAGES: PageRoute[] = [
  { path: "/", heading: "Dashboard", probe: /news blocked|blocked: skill/, mobile: false },
  { path: "/agents", heading: "Agents", probe: /blocked/, mobile: false },
  { path: "/agents/health-monitor", heading: "health-monitor", probe: /Entrypoints/, mobile: false },
  { path: "/agents/health-monitor?tab=history", heading: "health-monitor", probe: /Change log/, mobile: false },
  { path: "/agents/new", heading: "New Agent", probe: /Grants/, mobile: false },
  { path: "/runs", heading: "Runs", mobile: false },
  { path: "/relay", heading: "Relay", probe: /Morning — what's on fire/, mobile: true },
  { path: "/relay?kind=dm", heading: "Relay", probe: /What's my day look like/, mobile: false },
  // the thread pane is a second live region on the page
  { path: "/relay?channel=rc1&thread=m6", heading: "Relay", mobile: true, only: "a11y" },
  // /conversations is a redirect now (docs/design/19) — the row stays to prove
  // an old bookmark still lands somewhere real.
  { path: "/conversations", heading: "Relay", probe: /general/, mobile: false, only: "smoke" },
  { path: "/tickets", heading: "Tickets", probe: /OPS-1/, mobile: true },
  { path: "/tickets/OPS-1", heading: "OPS-1", probe: /Weather repeats across the digest/, mobile: true },
  { path: "/wiki", heading: "Wiki", probe: /Everything the platform knows/, mobile: true },
  { path: "/wiki/deploying", heading: "Deploying", probe: /reuse-values/, mobile: true },
  { path: "/artifacts", heading: "Artifacts", probe: /a-dragon-over-the-harbour/, mobile: true },
  // The deep link is the lightbox: the probe is provenance only the open
  // picture shows.
  { path: `/artifacts/${ARTIFACTS.generated.id}`, heading: "Artifacts", probe: /gpt-image-1 · 3 s/, mobile: true },
  // The probe is a priced model option: the registry landed.
  { path: "/studio", heading: "Studio", probe: /GPT Image 1 · \$0\.04/, mobile: true },
  { path: `/studio/${ARTIFACTS.generated.id}`, heading: "Studio", probe: /gpt-image-1 \(openai\)/, mobile: true },
  // the #art room: artifact cards inside the transcript
  { path: "/relay?channel=rc4", heading: "Relay", mobile: false, only: "a11y" },
  // The probe is the wiki badge: a memory that has graduated into a page is
  // the one thing on this table the wiki put there (docs/design/21).
  { path: "/memories", heading: "Memories", probe: /📖 promoted/, mobile: false },
  { path: "/changes", heading: "Pending Changes", probe: /skill: release-review/, mobile: false },
  { path: "/schedules", heading: "Schedules", probe: /health-monitor/, mobile: false },
  { path: "/skills", heading: "Skills & Tools", probe: /stocks/, mobile: false },
  { path: "/secrets", heading: "Connections", probe: /Discord chat identities/, mobile: true },
  { path: "/dlq", heading: "Dead-letter queue", mobile: false },
  { path: "/reporting", heading: "Reporting", probe: /Seconds per run/, mobile: false },
  { path: "/reports", heading: "Reports", probe: /daily-news/, mobile: false },
  { path: "/reports/daily-news", heading: "daily-news", probe: /Open latest/, mobile: false },
  { path: "/apps", heading: "Apps", probe: /running|not deployed/, mobile: false },
  { path: "/help", heading: "Help", probe: /building blocks|configuration lives in git/i, mobile: false },
  { path: "/help/tools", heading: "Tools", probe: /Workbench only/, mobile: false },
  { path: "/help/agents", heading: "Agents", probe: /who runs/, mobile: false, only: "smoke" },
  { path: "/help/relay", heading: "Relay", probe: /the agent messenger|hop 0/, mobile: false, only: "smoke" },
  { path: "/help/tickets", heading: "Tickets", probe: /assign = summon|OPS-12/i, mobile: false },
  { path: "/help/wiki", heading: "Wiki", probe: /wanted page|wiki-link/i, mobile: false },
  // The Relay section is read-only and env-fed, so its probe is the sentence
  // that tells an operator where the numbers actually come from.
  { path: "/settings", heading: "Settings", probe: /AP_RELAY_MAX_HOPS/, mobile: false },
];

export const SMOKE_PAGES = PAGES.filter((p) => p.only !== "a11y");
export const A11Y_PAGES = PAGES.filter((p) => p.only !== "smoke");
export const MOBILE_PAGES = A11Y_PAGES.filter((p) => p.mobile);
