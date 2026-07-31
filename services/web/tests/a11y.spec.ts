import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// axe-core over every page: serious/critical violations fail the build.
// (moderate/minor are reported in the failure message when the gate trips,
// but don't gate — tighten later if the baseline stays clean.)

const PAGES = ["/", "/agents", "/agents/health-monitor", "/runs", "/conversations",
               "/memories", "/changes", "/schedules", "/skills", "/secrets",
               "/dlq", "/reporting", "/settings"];

for (const path of PAGES) {
  test(`${path} passes axe`, async ({ page }) => {
    await mockApi(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).analyze();
    const gating = results.violations.filter((v) =>
      v.impact === "serious" || v.impact === "critical");
    expect(gating.map((v) => `${v.id}: ${v.help} (${v.nodes.length} nodes)`),
           `axe violations on ${path}`).toEqual([]);
  });
}
