import { expect, test, type Page } from "@playwright/test";
import { mockApi, STATE_APP_ID } from "./mock-api";

// State Apps (docs/design/39): the Apps list, the typed/v2 renderer and the
// read-only builder area, against the contract fixture in mock-api.ts.

const appPath = `/apps/state/${STATE_APP_ID}`;
const pagePath = (page: string, qs = "") => `${appPath}/pages/${page}${qs}`;

test("the Apps page lists state Apps beside the legacy ones", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/apps");
  const states = page.getByRole("region", { name: "State Apps" });
  const habits = states.locator(".state-app-card").filter({ hasText: "habits" });
  await expect(habits.getByRole("link", { name: "habits" })).toHaveAttribute("href", appPath);
  await expect(habits).toContainText("pai");
  await expect(habits).toContainText(/failing/i);
  await expect(habits).toContainText("2 issues");
  await expect(states.locator(".state-app-card").filter({ hasText: "old-reading-list" }))
    .toContainText(/retired/i);
  // The legacy collections are still there, unchanged.
  await expect(page.locator(".app-card").filter({ has: page.getByText("news", { exact: true }) })
    .locator(".app-open")).toHaveAttribute("href", `/live-views/${"n1".repeat(16)}`);
  expect(unmatched).toEqual([]);
});

test("a v2 page renders text, a metric and a table with formats and row links", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const unmatched = await mockApi(page);
  await page.goto(pagePath("overview"));
  await expect(page.getByRole("heading", { level: 1, name: "Habits" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "This week" })).toBeVisible();
  await expect(page.getByText("Logged by pai every evening.")).toBeVisible();

  const metric = page.locator(".live-view-metric").filter({ hasText: "Days done" });
  await expect(metric.locator("strong")).toHaveText("23");

  const table = page.locator(".live-view-table").filter({ hasText: "Recent days" });
  await expect(table.locator("thead th")).toHaveText(["Habit", "Day", "Done", "Streak", "Score", "Note"]);
  const first = table.locator("tbody tr").first();
  await expect(first.locator("td").nth(2)).toHaveText("Yes");
  await expect(first.locator("td").nth(4)).toHaveText("75%");
  await expect(first.getByRole("link", { name: "run" }))
    .toHaveAttribute("href", pagePath("entry", "?id=r1"));
  // Freshness: every result says when it was computed.
  await expect(table.locator(".v2-as-of")).toContainText("As of");
  expect(errors).toEqual([]);
  expect(unmatched).toEqual([]);
});

test("a restricted field is a muted marker, never the value or an empty cell", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("overview"));
  const row = page.locator(".live-view-table tbody tr").nth(1);
  const note = row.locator("td").last();
  await expect(note.locator(".v2-restricted")).toHaveText("restricted");
  // An empty-but-readable value is a dash, distinct from restricted.
  await expect(page.locator(".live-view-table tbody tr").nth(2).locator("td").last()).toHaveText("—");
});

test("a table pages through its view with the cursor", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("overview"));
  const table = page.locator(".live-view-table").filter({ hasText: "Recent days" });
  await expect(table.locator("tbody tr")).toHaveCount(3);
  await table.getByRole("button", { name: "Load more" }).click();
  await expect(table.locator("tbody tr")).toHaveCount(4);
  await expect(table.getByRole("button", { name: "Load more" })).toHaveCount(0);
});

test("a row link opens the detail page for one record", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto(pagePath("overview"));
  await page.locator(".live-view-table tbody tr").nth(1).getByRole("link", { name: "read" }).click();
  await expect(page).toHaveURL(new RegExp(`/pages/entry\\?id=r2$`));
  const detail = page.locator(".v2-detail");
  await expect(detail.locator("dt")).toHaveText(["Habit", "Day", "Done", "Note"]);
  await expect(detail.locator("dd").nth(0)).toHaveText("read");
  await expect(detail.locator("dd").nth(2)).toHaveText("No");
  await expect(detail.locator("dd").nth(3).locator(".v2-restricted")).toHaveText("restricted");
  expect(unmatched).toEqual([]);
});

test("text links reach only the App's pages and platform pages", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("links"));
  await expect(page.getByRole("link", { name: "Back to the overview" }))
    .toHaveAttribute("href", pagePath("overview"));
  await expect(page.getByRole("link", { name: "Open tickets" })).toHaveAttribute("href", "/tickets");
  // Anything that could leave the platform is plain text.
  for (const text of ["Protocol-relative", "Outbound", "Script"]) {
    await expect(page.getByText(text, { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: text })).toHaveCount(0);
  }
});

test("loading, then the data", async ({ page }) => {
  await mockApi(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/app-data/apps/*/views/done_count*", async (route) => {
    await held;
    await route.fallback();
  });
  await page.goto(pagePath("overview"));
  const metric = page.locator(".live-view-metric").filter({ hasText: "Days done" });
  await expect(metric).toContainText("Loading");
  release();
  await expect(metric.locator("strong")).toHaveText("23");
});

test("empty views say so", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("empty"));
  await expect(page.locator(".live-view-table")).toContainText("No records yet.");
  await expect(page.locator(".v2-detail")).toContainText("Record not found.");
});

test("a stale result is flagged with its as-of time", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("stale"));
  const metric = page.locator(".live-view-metric");
  await expect(metric.locator("strong")).toHaveText("19");
  await expect(metric.locator(".v2-as-of")).toContainText(/stale/i);
});

test("a page the viewer can't read is a denied state", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("denied"));
  await expect(page.getByRole("heading", { level: 1, name: "No access" })).toBeVisible();
  await expect(page.getByText("not a reader of this page")).toBeVisible();
});

test("a page that no longer validates is the 503 state", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("broken"));
  await expect(page.getByRole("heading", { level: 1, name: "Page unavailable" })).toBeVisible();
  await expect(page.getByText(/no longer matches its App/)).toBeVisible();
});

test("a denied or invalid view fails only its own component", async ({ page }) => {
  await mockApi(page);
  await page.goto(pagePath("mixed"));
  await expect(page.locator(".live-view-metric").filter({ hasText: /^Days done/ }).locator("strong"))
    .toHaveText("23");
  await expect(page.locator(".live-view-table")).toContainText("You can't read this view.");
  await expect(page.locator(".live-view-metric").filter({ hasText: "Broken count" }))
    .toContainText("This view no longer validates.");
});

test("the builder area: pages, definitions, build notes and health", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto(appPath);
  await expect(page.getByRole("heading", { level: 1, name: "habits" })).toBeVisible();
  // Pages tab: the overview renders in place, and each page is listed.
  await expect(page.locator(".live-view-metric").filter({ hasText: "Days done" }).locator("strong"))
    .toHaveText("23");
  await expect(page.getByRole("link", { name: "entry" })).toHaveAttribute("href", pagePath("entry"));

  await page.getByRole("tab", { name: "Definitions" }).click();
  const approved = page.getByRole("region", { name: "Approved" });
  await expect(approved).toContainText("collection");
  await expect(approved).toContainText("habits");
  await expect(approved).toContainText("v2");
  await expect(approved).toContainText("tracker");   // an App tool, shown like the rest
  const drafts = page.getByRole("region", { name: "Drafts" });
  await expect(drafts).toContainText("revision 3");
  await expect(drafts).toContainText("based on v2");
  await expect(drafts).toContainText("never published");
  await drafts.getByText("habits", { exact: true }).click();
  await expect(drafts.locator("pre").first()).toContainText('"mood"');

  await page.getByRole("tab", { name: "Build notes" }).click();
  await expect(page.locator(".build-notes")).toContainText("Routine: pai logs each habit at 21:00.");
  await expect(page.getByText("revision 5")).toBeVisible();

  await page.getByRole("tab", { name: "Health" }).click();
  await expect(page.getByText("column `mood` is not a field of `habits`")).toBeVisible();
  await expect(page.getByText("unique(habit, day)")).toBeVisible();
  await expect(page.getByText(/412 of 100,000 records/)).toBeVisible();
  await expect(page.getByText("Outside the approved state: note readable by kyle.")).toBeVisible();
  // Read-only: nothing here writes.
  await expect(page.getByRole("button", { name: /publish|save|delete/i })).toHaveCount(0);
  expect(unmatched).toEqual([]);
});

test("the builder keeps its tab in the URL", async ({ page }) => {
  await mockApi(page);
  await page.goto(`${appPath}?tab=health`);
  await expect(page.getByRole("tab", { name: "Health" })).toHaveAttribute("aria-selected", "true");
});

test("an App with no published pages says so", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/apps/state/${"5b".repeat(16)}`);
  await expect(page.getByText("No published pages yet.")).toBeVisible();
});

async function noSidewaysScroll(page: Page, where: string) {
  const over = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `${where} scrolls sideways at 390`).toBeLessThanOrEqual(1);
}

for (const theme of ["dark", "light"] as const) {
  test(`at 390 in ${theme}: the v2 page and the builder fit, and the marker shows`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem("theme", t), theme);
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApi(page);
    await page.goto(pagePath("overview"));
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const marker = page.locator(".v2-restricted").first();
    await expect(marker).toBeVisible();
    const colours = await marker.evaluate((el) => ({
      text: getComputedStyle(el).color,
      cell: getComputedStyle(el.closest("td")!).color,
    }));
    // Muted: a different colour from the readable values around it.
    expect(colours.text).not.toBe(colours.cell);
    await noSidewaysScroll(page, "the v2 page");

    for (const tab of ["", "?tab=definitions", "?tab=notes", "?tab=health"]) {
      await page.goto(`${appPath}${tab}`);
      await expect(page.getByRole("heading", { level: 1, name: "habits" })).toBeVisible();
      // An open definition is the widest thing here: its JSON scrolls in place.
      if (tab === "?tab=definitions") await page.locator(".definition summary").first().click();
      await noSidewaysScroll(page, `the builder${tab}`);
    }
  });
}
