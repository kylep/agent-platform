import { expect, test } from "@playwright/test";
import { mockApi, staleQuota, unfixtured } from "./mock-api";

// The usage bars (docs/design/22) are shell, not a page: two thin underlines
// to the brand, on every route. What these gate is the part a reader actually
// reads — the percentage, the fill that agrees with it, and the fact that a
// snapshot the platform knows to be old is refreshed once and only once.

/** The fraction of the bar the coloured overlay covers, as the browser lays
 * it out — the one check that the number and the picture agree. */
async function fillRatio(page: import("@playwright/test").Page, name: string) {
  return page.evaluate((label) => {
    const bar = document.querySelector(`[aria-label^="${label}"]`)!;
    const clip = bar.querySelector(".quota-clip")!;
    return clip.getBoundingClientRect().width / bar.getBoundingClientRect().width;
  }, name);
}

test("the bars sit under the brand and say what they are", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  const five = page.getByRole("meter", { name: /^Claude 5-hour window: 22% used/ });
  const seven = page.getByRole("meter", { name: /^Claude 7-day window: 81% used/ });
  await expect(five).toBeVisible();
  await expect(seven).toBeVisible();
  await expect(five).toContainText("22%");
  await expect(seven).toContainText("81%");
  await expect(five).toHaveAttribute("aria-valuenow", "22");
  await expect(seven).toHaveAttribute("aria-valuenow", "81");
  await expect(page.getByRole("meter", { name: /^Codex 5-hour window: 11% used/ }))
    .toBeVisible();
  await expect(page.getByRole("meter", { name: /^Codex 7-day window: 95% used/ }))
    .toBeVisible();
  // The window name and the countdown ride in the label, since the bar itself
  // is only ever NN%.
  await expect(five).toHaveAttribute("title", /5-hour window: 22% used, resets in/);

  // Under the brand, not beside it or below the links.
  const brand = (await page.locator(".nav-brand").boundingBox())!;
  const bar = (await five.boundingBox())!;
  const firstLink = (await page.locator(".nav-link").first().boundingBox())!;
  expect(bar.y).toBeGreaterThan(brand.y);
  expect(bar.y).toBeLessThan(firstLink.y);

  expect(await fillRatio(page, "Claude 5-hour window")).toBeCloseTo(0.22, 2);
  expect(await fillRatio(page, "Claude 7-day window")).toBeCloseTo(0.81, 2);
});

test("a stale snapshot is refreshed exactly once, and the bars follow",
     async ({ page }) => {
  await mockApi(page);
  const refresh = await staleQuota(page);
  await page.goto("/");
  // The stale snapshot is what the GET answered; the refreshed one is what
  // the bars end up drawing.
  await expect(page.getByRole("meter", { name: /^Claude 5-hour window: 22% used/ }))
    .toBeVisible();
  await expect(page.getByRole("meter", { name: /^Claude 7-day window: 81% used/ }))
    .toBeVisible();
  await page.waitForTimeout(500);
  expect(refresh.count()).toBe(1);
});

test("no snapshot, no bars — and the nav does not move", async ({ page }) => {
  await mockApi(page);
  await unfixtured(page, "**/api/quota");
  await page.goto("/");
  await expect(page.locator(".nav-link").first()).toBeVisible();
  const withoutBars = (await page.locator(".nav-link").first().boundingBox())!;
  await expect(page.locator(".quota-bar")).toHaveCount(0);

  await page.goto("/agents");
  await expect(page.locator(".quota-bar")).toHaveCount(0);
  const stillThere = (await page.locator(".nav-link").first().boundingBox())!;
  expect(stillThere.y).toBeCloseTo(withoutBars.y, 0);
});

test("a late read never walks the bars back, but a frame is the server's word",
     async ({ page }) => {
  // Three snapshots, deliberately out of order in TIME: the one the stream
  // carries is older than the one the mount read, and the one the catch-up
  // read answers with is older still.
  const at = (minsAgo: number) => new Date(Date.now() - minsAgo * 60000).toISOString();
  const snapshot = (five: number, seven: number, observed: string) => ({
    five_hour: { utilization: five, resets_at: null },
    seven_day: { utilization: seven, resets_at: null },
    status: "allowed", observed_at: observed, source: "proxy",
    stale: false, age_seconds: 0, probe: null,
  });
  let reads = 0;
  await mockApi(page);
  await page.route("**/api/quota", async (route) => {
    reads += 1;
    await route.fulfill({ json: reads === 1 ? snapshot(0.22, 0.81, at(2))
                                            : snapshot(0.05, 0.40, at(180)) });
  });
  // One well-formed frame, then EOF — which is also what makes the hook fall
  // back to its catch-up read, so both halves of this test run off it.
  await page.route("**/api/quota/events", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/event-stream",
                          headers: { "Cache-Control": "no-cache" },
                          body: `event: quota\ndata: ${JSON.stringify(
                            snapshot(0.66, 0.12, at(150)))}\n\n` });
  });
  await page.goto("/");

  // The frame is older than what the mount read and still wins: the server
  // owns the snapshot, and a clock that stepped backwards must not wedge the
  // bars for the life of the mount.
  await expect(page.getByRole("meter", { name: /^Claude 5-hour window: 66% used/ })).toBeVisible();
  // The catch-up read that the dropped stream triggered answered with an older
  // snapshot, and it is ignored.
  await expect.poll(() => reads).toBeGreaterThan(1);
  await expect(page.getByRole("meter", { name: /^Claude 5-hour window: 66% used/ })).toBeVisible();
  await expect(page.getByRole("meter", { name: /^Claude 5-hour window: 5% used/ }))
    .toHaveCount(0);
});

for (const theme of ["dark", "light"] as const) {
  test(`the ${theme} track is visible against the nav`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
    await mockApi(page);
    await page.goto("/");
    await expect(page.locator(".quota-bar").first()).toBeVisible();
    const colours = await page.evaluate(() => {
      const bar = document.querySelector(".quota-bar")!;
      return { track: getComputedStyle(bar).backgroundColor,
               nav: getComputedStyle(document.querySelector(".nav")!).backgroundColor };
    });
    expect(colours.track).not.toBe(colours.nav);
    expect(colours.track).not.toBe("rgba(0, 0, 0, 0)");
  });
}

test("on a phone the bars stay under the brand and span the strip", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/");
  const brand = (await page.locator(".nav-brand").boundingBox())!;
  const bar = (await page.getByRole("meter", { name: /^Claude 5-hour/ }).boundingBox())!;
  expect(bar.y).toBeGreaterThan(brand.y);
  // The nav is a full-width strip at this size; the bars are as wide as it.
  expect(bar.width).toBeCloseTo(brand.width, 0);
});
