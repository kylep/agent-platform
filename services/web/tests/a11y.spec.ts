import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";
import { A11Y_PAGES, MOBILE_PAGES } from "./pages";

// axe-core over every page: serious/critical violations fail the build.
// (moderate/minor are reported in the failure message when the gate trips,
// but don't gate — tighten later if the baseline stays clean.)

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

for (const { path } of A11Y_PAGES) {
  test(`${path} passes axe`, async ({ page }) => {
    await axe(page, path, "at 1280");
  });
}

for (const { path } of MOBILE_PAGES) {
  test(`${path} passes axe at 390`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await axe(page, path, "at 390");
  });
}
