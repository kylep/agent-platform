import { expect, test } from "@playwright/test";
import { mockApi, ticketFixtures } from "./mock-api";

// The ticket page (docs/design/20) is where a piece of work is a page rather
// than a card: the fields somebody has to be able to change, the thread it is
// actually being discussed in, and the run that is working on it right now.
// These gate that, plus the four places a ticket shows up somewhere else — a
// chip in a room, a run's own page, the agent's page, the dashboard.

test("the ticket page is its fields and its thread", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets/OPS-1");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("OPS-1");
  await expect(page.locator("body")).toContainText("Weather repeats across the digest");

  const fields = page.locator(".ticket-fields");
  await expect(fields.locator(".ticket-state")).toHaveText("open");
  await expect(fields).toContainText("high");        // p1, in words
  await expect(fields).toContainText("news");        // assignee
  // kyle reported it, and a room calls the person reading it "you".
  await expect(fields).toContainText("you");
  await expect(fields).toContainText("weather");     // labels
  await expect(fields).toContainText("ops");         // project
  // A run summoned from the ticket links to the run, not to a run id in prose.
  await expect(fields.getByRole("link", { name: /r-news-1|news/ }).first())
    .toHaveAttribute("href", "/runs/r-news-1");

  // The thread is Relay's, bound to the card the ticket left in the room.
  const thread = page.getByRole("region", { name: "Thread" });
  await expect(thread).toContainText("Dedupe by day, not by url.");
  await expect(thread).toContainText("the forecast is one item a day");

  // The structured history is the same story in one line each.
  await expect(page.locator(".ticket-activity")).toContainText("assigned");
});

test("a ticket somebody is on says who, and links the run", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets/OPS-2");
  const working = page.locator(".ticket-working");
  await expect(working).toContainText("health-monitor is working on this");
  await expect(working.getByRole("link", { name: /view run/ }))
    .toHaveAttribute("href", "/runs/r-hm-1");
  // The face pulses for the same reason it does in a room: a live run.
  await expect(working.locator(".relay-face.thinking")).toBeVisible();
});

test("assigning hands the ticket over and wakes the assignee by default",
     async ({ page }) => {
  await mockApi(page);
  const posted: { path: string; body: Record<string, unknown> }[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/tickets/")) {
      posted.push({ path: new URL(r.url()).pathname, body: r.postDataJSON() });
    }
  });
  await page.goto("/tickets/OPS-1");
  await page.getByRole("button", { name: "Assign" }).click();
  // Notify is on without anybody asking: assigning an agent IS the ask.
  await expect(page.getByLabel("Notify the assignee")).toBeChecked();
  await page.getByLabel("Assign to").selectOption("agent:pai");
  await page.getByRole("button", { name: "Hand it over" }).click();

  await expect.poll(() => posted.map((p) => p.path)).toContain("/api/tickets/OPS-1/assign");
  expect(posted[0].body).toMatchObject({ to: "agent:pai", notify: true });
  await expect(page.locator(".ticket-fields")).toContainText("pai");
});

test("the move control on the page is the same move as the board's",
     async ({ page }) => {
  await mockApi(page);
  const posted: { path: string; body: Record<string, unknown> }[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/api/tickets/")) {
      posted.push({ path: new URL(r.url()).pathname, body: r.postDataJSON() });
    }
  });
  await page.goto("/tickets/OPS-1");
  await page.getByLabel("Move OPS-1 to…").selectOption("review");
  await expect.poll(() => posted.map((p) => p.path)).toContain("/api/tickets/OPS-1/move");
  expect(posted[0].body).toMatchObject({ state: "review" });
  await expect(page.locator(".ticket-state")).toHaveText("review");
});

test("a move somebody else made lands on the page without a reload", async ({ page }) => {
  await mockApi(page);
  // The stream carries the row; the page re-reads the ticket, because a move
  // changes its history and who is on it as well as its state.
  let reads = 0;
  await page.route((url) => url.pathname === "/api/tickets/OPS-1", async (route) => {
    reads += 1;
    const base = ticketFixtures.find((t) => t.key === "OPS-1")!;
    await route.fulfill({ json: {
      ticket: reads > 1 ? { ...base, state: "blocked" } : base,
      events: [], root_message_id: "k1", runs: [], thinking: null,
    } });
  });
  await page.route((url) => url.pathname === "/api/tickets/events", (route) => route.fulfill({
    status: 200, contentType: "text/event-stream", headers: { "Cache-Control": "no-cache" },
    body: `event: ticket\ndata: ${JSON.stringify({ ...ticketFixtures[0], state: "blocked" })}\n\n`,
  }));
  await page.goto("/tickets/OPS-1");
  await expect(page.locator(".ticket-state")).toHaveText("blocked");
});

test("a ticket key in a room is a chip; a key inside code is not", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rc3&thread=k1");
  const thread = page.getByRole("region", { name: "Thread" });
  const chip = thread.getByRole("link", { name: "OPS-2" });
  await expect(chip).toHaveAttribute("href", "/tickets/OPS-2");
  // Quoted text is not a reference — the same fence rule the backend applies.
  await expect(thread.getByRole("link", { name: "OPS-3" })).toHaveCount(0);
  await expect(thread).toContainText("OPS-3");

  await chip.click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("OPS-2");
});

test("a run summoned from a ticket says which one", async ({ page }) => {
  await mockApi(page);
  await page.goto("/runs/r-news-1");
  const link = page.getByRole("link", { name: /OPS-1/ });
  await expect(link).toHaveAttribute("href", "/tickets/OPS-1");
});

test("an agent's page says what it is on the hook for", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/news?tab=tickets");
  const assigned = page.getByRole("region", { name: "Assigned to news" });
  await expect(assigned).toContainText("OPS-1");
  await expect(assigned).toContainText("OPS-5");
  await expect(assigned).not.toContainText("OPS-2");
  // news reports nothing; the tab says so rather than showing an empty table.
  await expect(page.getByRole("region", { name: "Reported by news" }))
    .toContainText(/has not opened any tickets/i);

  await page.goto("/agents/health-monitor?tab=tickets");
  await expect(page.getByRole("region", { name: "Reported by health-monitor" }))
    .toContainText("OPS-2");
});

test("the dashboard counts the board and raises what is stuck", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  // the tile, not the sidebar's link to the same place
  const tile = page.locator("a[href='/tickets']").filter({ hasText: "tickets · open" });
  await expect(tile).toContainText("2");            // open
  await expect(tile).toContainText("in progress");
  await expect(tile).toContainText("blocked");
  await expect(tile).toContainText("done");

  const attention = page.locator(".attention-list");
  await expect(attention).toContainText(/1 ticket is blocked/i);
  await expect(attention).toContainText(/1 ticket has gone quiet|stale/i);
  await expect(attention).toContainText(/orphan/i);
});

test("at 390 the fields stack above the thread and nothing scrolls sideways",
     async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/tickets/OPS-1");
  await expect(page.locator(".ticket-fields")).toBeVisible();
  const stacked = await page.locator(".ticket-detail").evaluate(
    (el) => getComputedStyle(el).flexDirection);
  expect(stacked).toBe("column");
  // Relay's thread is a full-screen sheet on a phone; on this page it is the
  // page, and a sheet would cover the fields above it.
  const pane = await page.locator(".relay-thread").evaluate(
    (el) => getComputedStyle(el).position);
  expect(pane).toBe("static");

  const doc = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth, view: window.innerWidth,
  }));
  expect(doc.width).toBeLessThanOrEqual(doc.view + 1);
});

// --- what the repair round pinned ------------------------------------------

test("a done ticket offers the only move it has: back open", async ({ page }) => {
  await mockApi(page);
  await page.goto("/tickets/OPS-6");
  const menu = page.getByLabel("Move OPS-6 to…");
  // Not "every state except this one": the API refuses done → in_progress, and
  // a menu of moves the server always rejects is a menu of mistakes.
  await expect(menu.locator("option")).toHaveText(["Move to…", "open"]);
});

test("an overflow frame makes the page re-read the ticket", async ({ page }) => {
  await mockApi(page);
  // The stream says it dropped frames without saying which. There is no cursor
  // to resume from, so the page re-reads — the same answer the board gives.
  let reads = 0;
  await page.route((url) => url.pathname === "/api/tickets/OPS-1", async (route) => {
    reads += 1;
    const base = ticketFixtures.find((t) => t.key === "OPS-1")!;
    await route.fulfill({ json: {
      ticket: reads > 1 ? { ...base, state: "review" } : base,
      events: [], root_message_id: null, runs: [], thinking: null,
    } });
  });
  await page.route((url) => url.pathname === "/api/tickets/events", (route) => route.fulfill({
    status: 200, contentType: "text/event-stream", headers: { "Cache-Control": "no-cache" },
    body: "event: overflow\ndata: {}\n\n",
  }));
  await page.goto("/tickets/OPS-1");
  await expect(page.locator(".ticket-state")).toHaveText("review");
  expect(reads).toBeGreaterThan(1);
});

/** A room whose whole transcript is ONE crafted message — the cheapest way to
 * put an exact piece of markdown in front of the renderer. The handler is
 * registered once and reads the body from here, so a test can step through a
 * dozen shapes without stacking a dozen routes. */
async function crafted(page: import("@playwright/test").Page) {
  let body = "";
  await page.route((url) => url.pathname === "/api/relay/channels/rc3/messages",
                   (route) => route.fulfill({ json: [{
                     id: "shape", channel_id: "rc3", author: "user:kyle", kind: "text",
                     body, card: null, reply_to: null, thread_root: null, run_id: null,
                     hop: 0, mentions: [], created_at: new Date().toISOString(),
                     edited_at: null, face: null, reactions: [],
                   }] }));
  return async (text: string) => {
    body = text;
    await page.goto("/relay?channel=rc3");
    const row = page.locator('[data-message-id="shape"]');
    await expect(row).toBeVisible();
    return row;
  };
}

test("a key is a chip in prose and nowhere else", async ({ page }) => {
  await mockApi(page);
  const show = await crafted(page);

  // Prose: the chip fires.
  let row = await show("Same root cause as OPS-2.");
  await expect(row.getByRole("link", { name: "OPS-2" }))
    .toHaveAttribute("href", "/tickets/OPS-2");

  // A link's URL is a URL: rewriting inside one leaves a dead address.
  row = await show("See [click here](https://x.com/OPS-2/page).");
  await expect(row.getByRole("link", { name: "click here" }))
    .toHaveAttribute("href", "https://x.com/OPS-2/page");
  await expect(row.locator('a[href="/tickets/OPS-2"]')).toHaveCount(0);

  // Inline code is code on screen; a rewrite would put the markdown in it.
  row = await show("the key is `OPS-2` exactly");
  await expect(row.locator("code")).toHaveText("OPS-2");
  await expect(row.locator("a")).toHaveCount(0);

  // An image's src is a URL too (the tag itself is stripped by the sanitizer).
  row = await show("![shot](https://x.com/OPS-2.png)");
  await expect(row.locator('a[href="/tickets/OPS-2"]')).toHaveCount(0);

  // An autolink, and a bare URL gfm autolinks for itself.
  row = await show("here: <https://x.com/OPS-2>");
  await expect(row.locator("a").first()).toHaveAttribute("href", "https://x.com/OPS-2");
  row = await show("here: https://x.com/OPS-2/page");
  await expect(row.locator("a").first()).toHaveAttribute("href", "https://x.com/OPS-2/page");

  // A fenced block is quoted text, as it is on the backend.
  row = await show("example:\n\n```\nOPS-2 is only an example\n```");
  await expect(row.locator("pre")).toContainText("OPS-2");
  await expect(row.locator("a")).toHaveCount(0);
});

test("a ticket-shaped link to a script never becomes an href", async ({ page }) => {
  await mockApi(page);
  const show = await crafted(page);
  const row = await show("[OPS-1](javascript:alert(1))");
  // The sanitizer drops the scheme; the point of the assertion is that the
  // rewrite above cannot smuggle one past it either.
  await expect(row.locator('[href^="javascript:"]')).toHaveCount(0);
  await expect(row).toContainText("OPS-1");
});

test("one refused projects read does not cost the session its chips",
     async ({ page }) => {
  await mockApi(page);
  let calls = 0;
  await page.route((url) => url.pathname === "/api/tickets/projects", async (route) => {
    calls += 1;
    if (calls === 1) { await route.fulfill({ status: 500, json: { detail: "down" } }); return; }
    await route.fulfill({ json: [{ id: "rc3", name: "ops", title: "ops", prefix: "OPS",
                                   open: 1, in_progress: 2 }] });
  });
  await page.goto("/relay?channel=rc3");
  await expect.poll(() => calls).toBeGreaterThan(0);

  // The next message asks again rather than inheriting the blip forever.
  await page.getByRole("textbox", { name: "Message" }).fill("same as OPS-2");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("link", { name: "OPS-2" }).first())
    .toHaveAttribute("href", "/tickets/OPS-2");
});

test("a ticket's card in the room reads like the ticket", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rc3");
  const card = page.locator('[data-message-id="k1"]');
  await expect(card.getByRole("link", { name: "OPS-1" }))
    .toHaveAttribute("href", "/tickets/OPS-1");
  await expect(card).toContainText("Weather repeats across the digest");
  await expect(card).toContainText("open");        // state
  await expect(card).toContainText("high");        // p1, in words
  await expect(card.locator(".ticket-who")).toContainText("news");
  await expect(card.locator(".ticket-who .relay-face")).toBeVisible();
});

test("a refused move says so beside the control that asked", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/tickets/OPS-1/move",
                   (route) => route.fulfill({ status: 409, body: "that is not a legal move" }));
  await page.goto("/tickets/OPS-1");
  await page.getByLabel("Move OPS-1 to…").selectOption("review");
  await expect(page.locator(".ticket-fields .ticket-move-error"))
    .toContainText(/could not move/i);
  // …and the ticket is still where it was, because the server said no.
  await expect(page.locator(".ticket-state")).toHaveText("open");
});

test("a hand-off being typed survives a change somebody else made",
     async ({ page }) => {
  await mockApi(page);
  let reads = 0;
  await page.route((url) => url.pathname === "/api/tickets/OPS-1", async (route) => {
    reads += 1;
    const base = ticketFixtures.find((t) => t.key === "OPS-1")!;
    await route.fulfill({ json: {
      ticket: reads > 1 ? { ...base, assignee: "agent:pai", assignee_face: null } : base,
      events: [], root_message_id: null, runs: [], thinking: null,
    } });
  });
  // The frame lands while the dialog is open and half-filled.
  await page.route((url) => url.pathname === "/api/tickets/events", async (route) => {
    await new Promise((r) => setTimeout(r, 1200));
    await route.fulfill({
      status: 200, contentType: "text/event-stream", headers: { "Cache-Control": "no-cache" },
      body: `event: ticket\ndata: ${JSON.stringify(
        { ...ticketFixtures[0], assignee: "agent:pai" })}\n\n`,
    });
  });
  await page.goto("/tickets/OPS-1");
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByLabel("Reason").fill("handing it to pai");
  await expect(page.locator(".ticket-fields")).toContainText("pai");
  await expect(page.getByLabel("Reason")).toHaveValue("handing it to pai");
});
