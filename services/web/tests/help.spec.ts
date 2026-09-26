import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// Help lays a 190px topic subnav beside the doc pane. With no breakpoint that
// pair rendered wider than a phone screen and scrolled sideways (QA-19) — the
// same class of bug QA-10/QA-11 fixed on the ticket detail page. Below tablet
// width the two must stack, and the page must not scroll sideways.

for (const path of ["/help", "/help/tools"]) {
  test(`${path} stacks and does not scroll sideways at 390`, async ({ page }) => {
    await mockApi(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(path);

    // The subnav and the doc pane are a column now, not two side-by-side rails.
    await expect(page.locator(".help-subnav")).toBeVisible();
    const stacked = await page.locator(".help-layout").evaluate(
      (el) => getComputedStyle(el).flexDirection);
    expect(stacked).toBe("column");
    // The sticky rail becomes a normal in-flow strip when it is the full width.
    const pos = await page.locator(".help-subnav").evaluate(
      (el) => getComputedStyle(el).position);
    expect(pos).toBe("static");

    const doc = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(doc, `${path} scrolls sideways at 390`).toBeLessThanOrEqual(1);
  });
}

test("help stays side-by-side on a wide screen", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/help/tools");
  const dir = await page.locator(".help-layout").evaluate(
    (el) => getComputedStyle(el).flexDirection);
  expect(dir).toBe("row");
});
