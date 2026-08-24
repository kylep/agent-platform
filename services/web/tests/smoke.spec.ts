import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// Every page renders against the mocked API with zero console errors and its
// key content present. This is the UI's backpressure: an agent (or human)
// shipping a page that crashes, blanks, or calls a missing endpoint fails CI
// instead of being discovered by clicking around production.

const PAGES: { path: string; heading: string; probe?: RegExp }[] = [
  { path: "/", heading: "Dashboard", probe: /news blocked|blocked: skill/ },
  { path: "/agents", heading: "Agents", probe: /blocked/ },
  { path: "/agents/health-monitor", heading: "health-monitor", probe: /Entrypoints/ },
  { path: "/agents/health-monitor?tab=history", heading: "health-monitor", probe: /Change log/ },
  { path: "/agents/new", heading: "New Agent", probe: /Grants/ },
  { path: "/runs", heading: "Runs" },
  { path: "/conversations", heading: "Conversations", probe: /hello there/ },
  { path: "/memories", heading: "Memories", probe: /Kyle likes terminals/ },
  { path: "/changes", heading: "Pending Changes", probe: /skill: news-lookup/ },
  { path: "/schedules", heading: "Schedules", probe: /health-monitor/ },
  { path: "/skills", heading: "Skills & Tools", probe: /stocks/ },
  { path: "/secrets", heading: "Secrets", probe: /undeclared/ },
  { path: "/dlq", heading: "Dead-letter queue" },
  { path: "/reporting", heading: "Reporting", probe: /Seconds per run/ },
  { path: "/reports", heading: "Reports", probe: /daily-news/ },
  { path: "/apps", heading: "Apps", probe: /running|not deployed/ },
  { path: "/help", heading: "Help", probe: /building blocks|configuration lives in git/i },
  { path: "/help/tools", heading: "Tools", probe: /self-edit only/ },
  { path: "/help/agents", heading: "Agents", probe: /who runs/ },
  { path: "/reports/daily-news", heading: "daily-news", probe: /Open latest/ },
  { path: "/settings", heading: "Settings" },
];

for (const { path, heading, probe } of PAGES) {
  test(`${path} renders clean`, async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));
    const unmatched = await mockApi(page);

    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: new RegExp(heading, "i") }))
      .toBeVisible();
    if (probe) await expect(page.locator("body")).toContainText(probe);

    expect(errors, `console errors on ${path}`).toEqual([]);
    expect(unmatched, `unfixtured API calls on ${path}`).toEqual([]);
  });
}

test("tailwind utilities are actually generated (source-detection canary)", async ({ page }) => {
  // Regression guard: after the @ap/ui extraction, Tailwind's automatic
  // source detection scanned only the package and every utility used by the
  // PAGES silently vanished — content intact, spacing gone, all tests green.
  // Assert a page-level utility (Dashboard's `mb-2`) produces real CSS.
  await mockApi(page);
  await page.goto("/");
  const mb = await page.locator(".mb-2").first().evaluate(
    (el) => getComputedStyle(el).marginBottom);
  expect(mb, "mb-2 must resolve to a nonzero margin — if this fails, check " +
             "the @source declaration in packages/ui/src/tokens.css").not.toBe("0px");
});

test("report viewer renders the sanitized fragment in a sandboxed frame", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/reports/daily-news");
  // the calendar marks today's report; clicking it opens the viewer
  await page.locator(".cal-has").first().click();
  const frame = page.locator("iframe.report-frame");
  await expect(frame).toBeVisible();
  // The sandbox invariant: same-origin so the parent can measure the report,
  // and NEVER allow-scripts alongside it — the two together would let
  // agent-generated HTML script itself into this origin.
  await expect(frame).toHaveAttribute("sandbox", "allow-same-origin");
  await expect(frame.contentFrame().locator(".rk-title")).toContainText("Daily news");
  await expect(page.locator("body")).toContainText(/generated/);
  expect(unmatched).toEqual([]);
});

test("the report kit's base rule survives — reports are not serif and full-bleed", async ({ page }) => {
  // Regression guard: a literal `*/` inside report-kit.css's header comment
  // once closed it early, so the `.rk-page` rule after it parsed as garbage and
  // was dropped — every report rendered in Times, full width, with black text
  // on the dark canvas. Computed styles are the only place that shows up.
  await mockApi(page);
  await page.goto("/reports/daily-news");
  await page.locator(".cal-has").first().click();
  const rkPage = page.locator("iframe.report-frame").contentFrame().locator(".rk-page");
  await expect(rkPage).toBeVisible();
  const style = await rkPage.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { maxWidth: cs.maxWidth, fontFamily: cs.fontFamily, padding: cs.paddingTop };
  });
  expect(style.maxWidth).toBe("760px");
  expect(style.fontFamily).toContain("Inter");
  expect(style.padding).not.toBe("0px");
});

test("the report frame is sized to its content, not to a fixed guess", async ({ page }) => {
  // A static height either strands dead space under a short report or clips the
  // tail of a tall one; the frame measures its own body instead.
  await mockApi(page);
  await page.goto("/reports/daily-news");
  await page.locator(".cal-has").first().click();
  const frame = page.locator("iframe.report-frame");
  await expect(frame).toBeVisible();
  const bodyHeight = await frame.contentFrame().locator("body")
    .evaluate((el) => el.getBoundingClientRect().height);
  await expect.poll(async () => Math.round((await frame.boundingBox())!.height))
    .toBeGreaterThanOrEqual(Math.round(bodyHeight) - 2);
  const frameHeight = Math.round((await frame.boundingBox())!.height);
  // 120px is the collapse floor; anything much above the content height would
  // be the old 70vh guess coming back.
  expect(frameHeight).toBeLessThanOrEqual(Math.max(Math.round(bodyHeight), 120) + 4);
});

test("sidebar navigation reaches grouped pages", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await page.getByRole("link", { name: "Agents", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();
  // being inside the group auto-expands it; take a child link
  await page.getByRole("link", { name: "Memories" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Memories" })).toBeVisible();
});

test("a table that overflows shows which edge has more behind it", async ({ page }) => {
  // Without the cue, a column scrolled past the container edge (the runs
  // list's CREATED timestamp on a narrow window) reads as clipped data.
  await mockApi(page);
  await page.setViewportSize({ width: 620, height: 700 });
  await page.goto("/runs");
  const scroller = page.locator(".ui-table-scroll").first();
  await expect(scroller).toHaveAttribute("data-overflow", "end");
  await scroller.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  await expect(scroller).toHaveAttribute("data-overflow", "start");
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(scroller).toHaveAttribute("data-overflow", "");
});

test("run detail renders from a run row", async ({ page }) => {
  await mockApi(page);
  await page.goto("/runs");
  await page.getByRole("link", { name: /a1a1a1a1/ }).first().click();
  await expect(page.locator("body")).toContainText(/health-monitor/);
});
