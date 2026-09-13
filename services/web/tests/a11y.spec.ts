import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// axe-core over every page: serious/critical violations fail the build.
// (moderate/minor are reported in the failure message when the gate trips,
// but don't gate — tighten later if the baseline stays clean.)

const PAGES = ["/", "/agents", "/agents/health-monitor", "/agents/health-monitor?tab=history",
               "/agents/new", "/runs", "/relay", "/relay?kind=dm",
               // the thread pane is a second live region on the page
               "/relay?channel=rc1&thread=m6",
               "/tickets", "/tickets/OPS-1", "/wiki", "/wiki/deploying",
               "/memories", "/changes", "/schedules", "/skills", "/secrets",
               "/dlq", "/reporting", "/reports", "/reports/daily-news", "/apps",
               "/help", "/help/tools", "/help/tickets", "/help/wiki", "/settings"];

/** The pages whose layout is a DIFFERENT layout on a phone: columns re-stack,
 * the rail becomes a drawer, wrapper boxes are dissolved to reorder what they
 * hold. A sweep that only ever ran at 1280 cannot see what any of that costs —
 * a landmark dropped by a mobile-only rule passed this file for months. */
const MOBILE = ["/tickets", "/tickets/OPS-1", "/wiki", "/wiki/deploying",
                "/relay", "/relay?channel=rc1&thread=m6"];

async function axe(page: import("@playwright/test").Page, path: string, where: string) {
  await mockApi(page);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).analyze();
  const gating = results.violations.filter((v) =>
    v.impact === "serious" || v.impact === "critical");
  expect(gating.map((v) => `${v.id}: ${v.help} (${v.nodes.length} nodes)`),
         `axe violations on ${path} ${where}`).toEqual([]);
}

for (const path of PAGES) {
  test(`${path} passes axe`, async ({ page }) => {
    await axe(page, path, "at 1280");
  });
}

for (const path of MOBILE) {
  test(`${path} passes axe at 390`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await axe(page, path, "at 390");
  });
}
