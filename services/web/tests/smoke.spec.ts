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
  { path: "/runs", heading: "Runs" },
  { path: "/conversations", heading: "Conversations", probe: /hello there/ },
  { path: "/memories", heading: "Memories", probe: /Kyle likes terminals/ },
  { path: "/changes", heading: "Pending Changes", probe: /agent: news/ },
  { path: "/schedules", heading: "Schedules", probe: /health-monitor/ },
  { path: "/skills", heading: "Skills", probe: /discord/ },
  { path: "/secrets", heading: "Secrets", probe: /undeclared/ },
  { path: "/dlq", heading: "Dead-letter queue" },
  { path: "/reporting", heading: "Reporting", probe: /Seconds per run/ },
  { path: "/reports", heading: "Reports", probe: /daily-news/ },
  { path: "/apps", heading: "Apps", probe: /running|not deployed/ },
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
  await expect(frame).toHaveAttribute("sandbox", "");
  await expect(frame.contentFrame().locator(".rk-title")).toContainText("Daily news");
  await expect(page.locator("body")).toContainText(/generated/);
  expect(unmatched).toEqual([]);
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

test("run detail renders from a run row", async ({ page }) => {
  await mockApi(page);
  await page.goto("/runs");
  await page.getByRole("link", { name: /a1a1a1a1/ }).first().click();
  await expect(page.locator("body")).toContainText(/health-monitor/);
});
