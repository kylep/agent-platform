import { expect, test, type Page } from "@playwright/test";
import { mockApi, ticketFixtures } from "./mock-api";

// The board (docs/design/20) is the one page where the platform's work is
// visible as work rather than as rows of runs, so these gate the things that
// make it readable and usable: which column a ticket is in, who is on it, what
// the filters hide, and that a move happens the same way from a mouse and from
// a keyboard.

const COLUMNS = ["open", "in progress", "blocked", "review", "closed"];

/** Playwright's mouse cannot drive HTML5 drag-and-drop, so the events are
 * dispatched the way the browser would. */
async function drag(page: Page, key: string, column: string) {
  await page.evaluate(([k, c]) => {
    const card = document.querySelector(`[data-key="${k}"]`)!;
    const target = document.querySelector(`[aria-label="${c}"]`)!;
    const dataTransfer = new DataTransfer();
    const fire = (el: Element, type: string) =>
      el.dispatchEvent(new DragEvent(type, { dataTransfer, bubbles: true, cancelable: true }));
    fire(card, "dragstart");
    fire(target, "dragover");
    fire(target, "drop");
  }, [key, column]);
}

test("the board is a column per state, with the work in them", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  for (const name of COLUMNS) {
    await expect(page.getByRole("region", { name })).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "open" })).toContainText("OPS-1");
  await expect(page.getByRole("region", { name: "in progress" })).toContainText("OPS-2");
  await expect(page.getByRole("region", { name: "blocked" })).toContainText("OPS-4");
  await expect(page.getByRole("region", { name: "review" })).toContainText("OPS-5");
  // done and cancelled are one column: both are finished work.
  const closed = page.getByRole("region", { name: "closed" });
  await expect(closed).toContainText("OPS-6");
  await expect(closed).toContainText("GEN-2");

  // A card says what it is: key, title, labels, who is on it.
  const card = page.locator(".ticket-card", { hasText: "OPS-1" });
  await expect(card).toContainText("weather");
  await expect(card).toContainText("news");
  await expect(card.getByRole("link", { name: "OPS-1" }))
    .toHaveAttribute("href", "/tickets/OPS-1");
});

test("a card badges what is wrong with it and pulses when someone is on it",
     async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  // in progress, untouched for days — the one definition of stale rides in on
  // the row rather than being recomputed here.
  await expect(page.locator(".ticket-card", { hasText: "OPS-3" })).toContainText("stale");
  // assigned to an agent presence has never heard of
  await expect(page.locator(".ticket-card", { hasText: "GEN-2" })).toContainText("orphaned");
  // health-monitor has a live run in the ops room, and OPS-2 is its ticket
  await expect(page.locator(".ticket-card", { hasText: "OPS-2" })).toContainText("thinking");
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).not.toContainText("thinking");
});

test("the Today strip is the standup nobody wrote", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  const strip = page.getByRole("region", { name: /moved in the last 24 hours/i });
  await expect(strip).toContainText("news");
  await expect(strip).toContainText("3");
  await expect(strip).toContainText("kyle");
});

test("filters narrow the board and live in the URL", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  await expect(page.locator(".ticket-card", { hasText: "GEN-1" })).toBeVisible();

  await page.getByLabel("Filter by project").selectOption("OPS");
  await expect(page).toHaveURL(/project=OPS/);
  await expect(page.locator(".ticket-card", { hasText: "GEN-1" })).toHaveCount(0);
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toBeVisible();

  await page.getByLabel("Filter by assignee").selectOption("agent:pai");
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toHaveCount(0);
  await expect(page.locator(".ticket-card", { hasText: "OPS-3" })).toBeVisible();

  // a shared link carries the whole view
  await page.goto("/tickets?label=weather");
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toBeVisible();
  await expect(page.locator(".ticket-card", { hasText: "OPS-2" })).toHaveCount(0);

  // "mine" is the signed-in human's own queue. Nothing on this board is kyle's
  // — OPS-4 is admin's — so the honest answer is an empty board, not the whole
  // one.
  await page.goto("/tickets?assignee=user%3Aadmin");
  await expect(page.locator(".ticket-card")).toHaveCount(1);
  await expect(page.locator(".ticket-card")).toContainText("OPS-4");
  await page.getByLabel("Only mine").click();
  await expect(page.getByLabel("Only mine")).toBeChecked();
  await expect(page).toHaveURL(/mine=1/);
  await expect(page.locator(".ticket-card")).toHaveCount(0);
  await expect(page.locator(".ticket-empty")).toBeVisible();
});

test("`/` goes to the search box, and the search narrows the board", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  await page.locator("body").press("/");
  const box = page.getByRole("searchbox", { name: "Search tickets" });
  await expect(box).toBeFocused();
  await box.pressSequentially("forecast");
  await expect(page).toHaveURL(/q=forecast/);
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toBeVisible();
  await expect(page.locator(".ticket-card", { hasText: "OPS-5" })).toHaveCount(0);
});

test("the move menu is the keyboard path to the drag, and the card moves",
     async ({ page }) => {
  await mockApi(page);
  const posted: { url: string; body: unknown }[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/tickets/")) {
      posted.push({ url: r.url(), body: r.postDataJSON() });
    }
  });
  await page.goto("/tickets");
  await expect(page.getByRole("region", { name: "open" })).toContainText("OPS-1");
  await page.getByLabel("Move OPS-1 to…").selectOption("review");

  await expect.poll(() => posted.map((p) => new URL(p.url).pathname))
    .toContain("/api/tickets/OPS-1/move");
  expect(posted[0].body).toMatchObject({ state: "review" });
  // the card is where it was put, not where it was
  await expect(page.getByRole("region", { name: "review" })).toContainText("OPS-1");
  await expect(page.getByRole("region", { name: "open" })).not.toContainText("OPS-1");
});

test("dragging a card onto a column is the same move as the menu", async ({ page }) => {
  await mockApi(page);
  const posted: { url: string; body: unknown }[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && !r.url().includes("/api/quota/refresh")) {
      posted.push({ url: r.url(), body: r.postDataJSON() });
    }
  });
  await page.goto("/tickets");
  await expect(page.locator('[data-key="OPS-1"]')).toBeVisible();
  // What this pins is the wiring: the card hands over its id and the column
  // reads it back.
  await drag(page, "OPS-1", "review");
  await expect.poll(() => posted.map((p) => new URL(p.url).pathname))
    .toContain("/api/tickets/OPS-1/move");
  expect(posted[0].body).toMatchObject({ state: "review" });
  await expect(page.getByRole("region", { name: "review" })).toContainText("OPS-1");
});

test("a streamed ticket frame moves a card without a reload", async ({ page }) => {
  await mockApi(page);
  // The stream is the board's truth: a move somebody else made lands here as a
  // `ticket` frame and the card walks across on its own.
  const moved = {
    ...ticketFixtures.find((t) => t.key === "OPS-4")!,
    state: "review", updated_at: new Date().toISOString(),
  };
  await page.route((url) => url.pathname === "/api/tickets/events", async (route) => {
    await route.fulfill({
      status: 200, contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: `event: ticket\ndata: ${JSON.stringify(moved)}\n\n`,
    });
  });
  await page.goto("/tickets");
  await expect(page.getByRole("region", { name: "review" })).toContainText("OPS-4");
  await expect(page.getByRole("region", { name: "blocked" })).not.toContainText("OPS-4");
});

test("the new-ticket dialog opens a ticket and puts it on the board",
     async ({ page }) => {
  await mockApi(page);
  const posted: unknown[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/tickets") {
      posted.push(r.postDataJSON());
    }
  });
  await page.goto("/tickets");
  await page.getByRole("button", { name: "New ticket" }).click();
  await page.getByLabel("Title").fill("The board needs a first ticket");
  await page.getByRole("button", { name: "Open ticket" }).click();

  await expect.poll(() => posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({ title: "The board needs a first ticket" });
  await expect(page.getByRole("region", { name: "open" }))
    .toContainText("The board needs a first ticket");
});

test("a hint too long for its field trails off rather than clipping mid-word",
     async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 780 });
  // A placeholder is the field's explanation, and a dialog field is narrow.
  // "why it is moving (option" reads as a typo; "why it is moving (…" reads
  // as a sentence that ran out of room — which is what it is.
  // On the field, not on `::placeholder`: browsers only honour the
  // ::first-line properties there, and text-overflow is not one of them.
  const ellipsed = (label: string) => page.getByLabel(label).evaluate(
    (el) => getComputedStyle(el).textOverflow);

  await page.goto("/tickets");
  await page.getByRole("button", { name: "New ticket" }).click();
  expect(await ellipsed("Title")).toBe("ellipsis");
  expect(await ellipsed("Labels")).toBe("ellipsis");

  await page.goto("/tickets/OPS-1");
  await page.getByRole("button", { name: "Assign" }).click();
  expect(await ellipsed("Reason")).toBe("ellipsis");
});

test("a board with nothing on it says so", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/tickets",
                   (route) => route.fulfill({ json: [] }));
  await page.goto("/tickets");
  await expect(page.locator(".ticket-empty"))
    .toContainText(/no tickets yet|nothing on the board/i);
  await expect(page.locator(".ticket-card")).toHaveCount(0);
});

test("at 1280 all five columns are on the screen, sidebar and all", async ({ page }) => {
  // A laptop is the window this page is read in. A board whose last column is
  // a sliver past the right edge is a board with four columns, and nobody
  // scrolls sideways to find out what is in review.
  await mockApi(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/tickets");
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toBeVisible();
  for (const name of COLUMNS) {
    const box = (await page.getByRole("region", { name }).boundingBox())!;
    expect(Math.round(box.x + box.width), `${name} is off the right edge`)
      .toBeLessThanOrEqual(1280);
  }
});

test("at 390 the columns scroll inside the board, never the page", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/tickets");
  await expect(page.locator(".ticket-card", { hasText: "OPS-1" })).toBeVisible();

  const board = page.locator(".ticket-board");
  const scrolls = await board.evaluate((el) => ({
    over: el.scrollWidth > el.clientWidth + 1,
    axis: getComputedStyle(el).overflowX,
  }));
  expect(scrolls.over, "five columns must not be squeezed into 390px").toBe(true);
  expect(scrolls.axis).toBe("auto");

  // …and the document itself stays put.
  const page_ = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    view: window.innerWidth,
  }));
  expect(page_.width).toBeLessThanOrEqual(page_.view + 1);
});

test("a closed ticket only reopens — the board never offers a move the API refuses",
     async ({ page }) => {
  // `tickets.can_move`: from done or cancelled the only legal target is open.
  // A menu that listed the others would be a menu of 409s.
  await mockApi(page);
  const posted: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST") posted.push(r.url()); });
  await page.goto("/tickets");
  const menu = page.getByLabel("Move OPS-6 to…");
  await expect(menu.locator("option")).toHaveText(["Move to…", "open"]);

  // …and a drop on a column it cannot reach is a gesture that missed: nothing
  // is sent, and nothing is said about it either.
  await drag(page, "OPS-6", "blocked");
  await page.waitForTimeout(300);
  expect(posted.filter((u) => u.includes("/move"))).toEqual([]);
  await expect(page.getByRole("region", { name: "closed" })).toContainText("OPS-6");
  await expect(page.locator(".error")).toHaveCount(0);
});

test("blocking asks what the work is waiting on, and sends it", async ({ page }) => {
  await mockApi(page);
  const posted: unknown[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/move")) posted.push(r.postDataJSON());
  });
  await page.goto("/tickets");
  await page.getByLabel("Move OPS-1 to…").selectOption("blocked");
  await page.getByLabel("What is it waiting on? (optional)").fill("the Discord token");
  await page.getByRole("button", { name: "Block it" }).click();

  await expect.poll(() => posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({ state: "blocked", reason: "the Discord token" });
  await expect(page.getByRole("region", { name: "blocked" })).toContainText("OPS-1");
});

test("a card with a move in flight holds still", async ({ page }) => {
  // Two moves on one card would make the second a same-state move, which the
  // API refuses — a failure banner for being quick.
  await mockApi(page);
  const posted: string[] = [];
  await page.route((u) => u.pathname === "/api/tickets/OPS-1/move", async (route) => {
    posted.push(route.request().url());
    await new Promise((done) => setTimeout(done, 1200));
    await route.fulfill({ json: { ...ticketFixtures.find((t) => t.key === "OPS-1")!,
                                  state: "review", updated_at: new Date().toISOString() } });
  });
  await page.goto("/tickets");
  const menu = page.getByLabel("Move OPS-1 to…");
  await menu.selectOption("review");
  await expect(menu).toBeDisabled();
  await expect(page.locator('[data-key="OPS-1"]')).toHaveAttribute("data-busy", "yes");

  await expect(page.getByRole("region", { name: "review" })).toContainText("OPS-1");
  expect(posted).toHaveLength(1);
});

test("a board that could not be read says so, and keeps trying", async ({ page }) => {
  // The stream only opens once the first page has landed, so giving up on the
  // first failure leaves a permanently empty page under a happy empty-state.
  await mockApi(page);
  const streams: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/tickets/events") streams.push(r.url());
  });
  let calls = 0;
  await page.route((u) => u.pathname === "/api/tickets", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "the database is asleep" } });
      return;
    }
    await route.fallback();
  });
  await page.goto("/tickets");
  await expect(page.locator(".ticket-failed")).toBeVisible();
  // never the happy copy while the board is broken
  await expect(page.locator(".ticket-empty")).toHaveCount(0);

  await expect(page.locator(".ticket-card", { hasText: "OPS-1" }))
    .toBeVisible({ timeout: 10000 });
  await expect(page.locator(".ticket-failed")).toHaveCount(0);
  await expect.poll(() => streams.length, { timeout: 10000 }).toBeGreaterThan(0);
});

test("the priority word is read out, not drawn", async ({ page }) => {
  // The stripe is a colour; the word beside it is what a screen reader gets.
  // `sr-only` has to actually hide it — undefined, it is a stray "normal" on
  // every card.
  await mockApi(page);
  await page.goto("/tickets");
  const hidden = page.locator('[data-key="OPS-4"] .sr-only');
  await expect(hidden).toHaveText("normal");
  const box = (await hidden.boundingBox())!;
  expect(Math.round(box.width)).toBeLessThanOrEqual(2);
  expect(Math.round(box.height)).toBeLessThanOrEqual(2);
  // …while the two urgent ones say so on the card itself.
  await expect(page.locator('[data-key="OPS-2"]')).toContainText("urgent");
});

test("the new-ticket assignee list is who can actually take it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  await page.getByRole("button", { name: "New ticket" }).click();
  const picker = page.getByLabel("Assignee", { exact: true });
  await expect(picker.locator("option")).toContainText(["unassigned", "you"]);
  // a switched-off agent cannot be summoned, so it is not offered
  await expect(picker.locator("option", { hasText: "old-importer" })).toHaveCount(0);
  await expect(picker.locator("option", { hasText: "news" })).toHaveCount(1);
});

test("`/` belongs to the page, not to a control someone is using", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets");
  const menu = page.getByLabel("Move OPS-1 to…");
  await menu.focus();
  await menu.press("/");
  await expect(page.getByRole("searchbox", { name: "Search tickets" })).not.toBeFocused();
});

test("an idle assignee gets no pulse", async ({ page }) => {
  // OPS-3 belongs to pai, which has nothing running: presence is the only thing
  // that lights a card up, and an idle row must not.
  await mockApi(page);
  await page.goto("/tickets");
  await expect(page.locator('[data-key="OPS-3"]')).not.toContainText("thinking");
  await expect(page.locator('[data-key="OPS-3"] .ticket-face'))
    .not.toHaveAttribute("data-thinking", "yes");
});
