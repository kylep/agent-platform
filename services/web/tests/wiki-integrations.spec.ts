import { expect, test, type Page } from "@playwright/test";
import { mockApi, ticketFixtures } from "./mock-api";

// The wiki's edges (docs/design/21): everywhere a page is cited, promoted or
// counted from OUTSIDE `/wiki`. The pages themselves are gated by wiki.spec;
// these gate the thing that makes a wiki a wiki — that the rest of the
// platform points at it. A page nobody links to from the rooms, the board, the
// memories and the dashboard is a page nobody reads.

/** A room whose whole transcript is ONE crafted message — the cheapest way to
 * put an exact piece of markdown in front of the renderer. */
async function crafted(page: Page, body: string) {
  await page.route((url) => url.pathname === "/api/relay/channels/rc3/messages",
                   (route) => route.fulfill({ json: [{
                     id: "shape", channel_id: "rc3", author: "user:kyle", kind: "text",
                     body, card: null, reply_to: null, thread_root: null, run_id: null,
                     hop: 0, mentions: [], created_at: new Date().toISOString(),
                     edited_at: null, face: null, reactions: [],
                   }] }));
  await page.goto("/relay?channel=rc3");
  const row = page.locator('[data-message-id="shape"]');
  await expect(row).toBeVisible();
  return row;
}

test("a [[slug]] in a room is a chip that opens the page", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/relay?channel=rc3&thread=k1");
  const chip = page.locator('[data-message-id="k1r4"]')
    .getByRole("link", { name: "deploying" });
  await expect(chip).toHaveAttribute("href", "/wiki/deploying");
  // A page that exists is blue — the red one's tooltip is what says otherwise.
  await expect(chip).not.toHaveAttribute("title", /wanted/);
  // And it navigates rather than reloading the app, the way a ticket key does.
  await chip.click();
  await expect(page).toHaveURL(/\/wiki\/deploying$/);
  expect(unmatched, "unfixtured API calls on a room with a citation").toEqual([]);
});

test("a link to a page nobody has written is red; one in a fence is not a link",
     async ({ page }) => {
  await mockApi(page);
  const row = await crafted(page,
    "Start at [[deploying]], then somebody write [[nope]].\n\n```\n[[fenced]]\n```");
  // Red, and it says what the colour means — colour alone tells a reader who
  // cannot see it nothing.
  await expect(row.locator('a[href="/wiki/nope"]')).toHaveAttribute("title", /wanted/);
  await expect(row.locator('a[href="/wiki/deploying"]')).toHaveCount(1);
  // Quoted text is not a citation, so the wanted list stays honest.
  await expect(row.locator('a[href="/wiki/fenced"]')).toHaveCount(0);
  await expect(row.locator("pre")).toContainText("[[fenced]]");
});

test("a ticket's description chips a key and a page alike", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/tickets/OPS-1", (route) =>
    route.fulfill({ json: {
      ticket: { ...ticketFixtures[0],
                body: "The forecast is one item a day — see [[deploying]] and OPS-2." },
      events: [], root_message_id: null, runs: [], thinking: null } }));
  await page.goto("/tickets/OPS-1");
  const body = page.locator(".ticket-body");
  await expect(body.getByRole("link", { name: "deploying" }))
    .toHaveAttribute("href", "/wiki/deploying");
  await expect(body.getByRole("link", { name: "OPS-2" }))
    .toHaveAttribute("href", "/tickets/OPS-2");
});

/** Open the promote dialog on the memory that has NOT graduated yet, and hand
 * back its submit button. */
async function openPromote(page: Page) {
  await page.goto("/memories");
  await page.locator("tr", { hasText: "Weather dedup" })
    .getByRole("button", { name: "Promote to wiki" }).click();
  return page.getByRole("button", { name: "Promote", exact: true });
}

test("promoting a memory names the memory, the slug and the title", async ({ page }) => {
  const unmatched = await mockApi(page);
  const posted: Record<string, unknown>[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/wiki/promote") {
      posted.push(r.postDataJSON());
    }
  });
  const submit = await openPromote(page);
  // The slug is derived from the key by the backend's own rule, so a page
  // promoted here and one promoted by the agent are the same page.
  await expect(page.getByLabel("Slug")).toHaveValue("weather-dedup");
  await expect(page.getByLabel("Page title")).toHaveValue("Weather dedup");
  await submit.click();

  await expect.poll(() => posted).toEqual([
    { memory_id: "m-news-dedup", slug: "weather-dedup", title: "Weather dedup" },
  ]);
  await expect(page.getByLabel("Slug")).toHaveCount(0);
  expect(unmatched, "unfixtured API calls on /memories").toEqual([]);
});

test("a memory the wiki already has a page for wears the badge", async ({ page }) => {
  const unmatched = await mockApi(page);
  let listed = 0;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/wiki/pages") listed += 1;
  });
  await page.goto("/memories");
  await expect(page.locator("tbody")).toContainText("Kyle likes terminals");

  const row = page.locator("tr", { hasText: "Kyle location" });
  await expect(row.getByRole("link", { name: /promoted/ }))
    .toHaveAttribute("href", "/wiki/kyle-location");
  // …and it does not also offer the verb. Promoting it again is an edit, and
  // the badge already leads to the place an edit happens.
  await expect(row.getByRole("button", { name: "Promote to wiki" })).toHaveCount(0);
  // One question for the whole table. A page-per-memory lookup is the same
  // answer asked N times, and N grows with the memories.
  expect(listed, "the promoted index is one call per page load").toBe(1);
  expect(unmatched, "unfixtured API calls on /memories").toEqual([]);
});

test("the dashboard counts the wiki", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/");
  const tile = page.locator("main a[href='/wiki']");
  await expect(tile).toContainText("3");
  await expect(tile).toContainText("3 edits · 24h · 2 wanted");
  // Two red links is a normal Tuesday, not something to triage.
  await expect(page.locator(".attention-item", { hasText: "wanted" })).toHaveCount(0);
  expect(unmatched, "unfixtured API calls on the dashboard").toEqual([]);
});

test("a wiki with too many red links, or too much dust, says so", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/wiki/stats", (route) => route.fulfill({
    json: { pages: 40, edits_24h: [], wanted: 6, stale: 12,
            budget: { limit: 30, agents: [] } } }));
  await page.goto("/");
  await expect(page.locator(".attention-item", { hasText: "6 wanted pages" })).toBeVisible();
  await expect(page.locator(".attention-item", { hasText: "12 stale pages" })).toBeVisible();
});

test("a refused promotion says which refusal it was, never raw JSON",
     async ({ page }) => {
  await mockApi(page);
  let answer: { status: number; json: unknown } = {
    status: 403, json: { detail: "that memory belongs to pai" } };
  await page.route((url) => url.pathname === "/api/wiki/promote",
                   (route) => route.fulfill(answer));
  const submit = await openPromote(page);
  const dialog = page.getByRole("dialog");

  // Somebody else's note: nothing in this box fixes that.
  await submit.click();
  await expect(dialog).toContainText("You can only promote your own memories.");
  await expect(dialog).not.toContainText("{");

  // The name is taken. The fix IS the field, so the dialog stays open on it.
  answer = { status: 409, json: { detail: "slug already exists" } };
  await submit.click();
  await expect(dialog).toContainText("already exists — pick another slug");
  await expect(page.getByLabel("Slug")).toBeFocused();

  // The page has moved on since it was promoted — promotion is the wrong tool
  // now, so the sentence hands over the right one.
  answer = { status: 409, json: { detail: "page has moved on", current_version: 4 } };
  await submit.click();
  await expect(dialog.getByRole("link", { name: "edit it directly" }))
    .toHaveAttribute("href", "/wiki/weather-dedup");
  await expect(dialog).not.toContainText("{");
});

test("the memories table fits a phone without collapsing or clipping",
     async ({ page }) => {
  // A fixed-layout table whose columns add up to more than the screen makes
  // the free column zero-wide and stacks the headers on top of each other.
  // The overflow belongs to the table's own scroller, as it does everywhere
  // else in the console.
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/memories");

  const memory = page.locator(".memory-content").first();
  await expect(memory).toBeVisible();
  expect((await memory.boundingBox())!.width,
         "the memory column collapsed").toBeGreaterThan(100);

  // Header cells side by side, in order, never on top of one another.
  const edges = await page.locator("thead th").evaluateAll(
    (els) => els.map((el) => {
      const r = el.getBoundingClientRect();
      return [r.x, r.right] as [number, number];
    }));
  for (let i = 1; i < edges.length; i++) {
    expect(edges[i][0], "header cells overlap").toBeGreaterThanOrEqual(edges[i - 1][1] - 1);
  }

  // The page does not scroll sideways; the table does.
  const doc = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(doc, "/memories scrolls sideways at 390").toBeLessThanOrEqual(1);
  const scroller = page.locator(".ui-table-scroll").first();
  expect(await scroller.evaluate((el) => el.scrollWidth - el.clientWidth))
    .toBeGreaterThan(0);

  // And the verb is a whole word, not "Promo".
  const button = page.getByRole("button", { name: "Promote to wiki" }).first();
  const drawn = Math.round((await button.boundingBox())!.width);
  const wanted = await button.evaluate((el) => el.scrollWidth);
  expect(drawn, "the promote button clips").toBeGreaterThanOrEqual(wanted - 1);
});

test("the wiki's attention rows sit exactly on their thresholds", async ({ page }) => {
  await mockApi(page);
  let stats = { pages: 40, edits_24h: [] as unknown[], wanted: 4, stale: 9,
                budget: { limit: 30, agents: [] as unknown[] } };
  await page.route((url) => url.pathname === "/api/wiki/stats",
                   (route) => route.fulfill({ json: stats }));
  const wanted = page.locator(".attention-item", { hasText: "wanted pages" });
  const stale = page.locator(".attention-item", { hasText: "stale pages" });

  // One under each bar: gardening, not triage.
  await page.goto("/");
  await expect(page.locator(".attention-list, .text-success").first()).toBeVisible();
  await expect(wanted).toHaveCount(0);
  await expect(stale).toHaveCount(0);

  // Exactly on them, which is where a `>=` is worth a test.
  stats = { ...stats, wanted: 5, stale: 10 };
  await page.goto("/");
  await expect(wanted).toHaveText(/5 wanted pages/);
  await expect(stale).toHaveText(/10 stale pages/);
});
