import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

// Relay is the one page where the platform is a conversation rather than a
// table, so these gate the things that make it readable: who said what, in
// what order, which run it came out of, and whether the room is live.

test("the rail lists channels and direct messages", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const rail = page.getByRole("complementary", { name: "Channels" });
  await expect(rail.getByRole("heading", { name: "Channels" })).toBeVisible();
  await expect(rail.getByRole("heading", { name: "Direct messages" })).toBeVisible();
  await expect(rail.getByRole("button", { name: /general/ })).toBeVisible();
  await expect(rail.getByRole("button", { name: /quiet/ })).toBeVisible();
  // A dm is listed as who it is with, never as its row id.
  await expect(rail.getByRole("button", { name: /^pai/ })).toBeVisible();
});

test("a channel reads oldest-first, with each author named once", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const pane = page.locator(".relay-messages");
  await expect(pane).toContainText("Morning — what's on fire?");

  const text = (await pane.textContent()) ?? "";
  expect(text.indexOf("Morning — what's on fire?"))
    .toBeLessThan(text.indexOf("hello from discord"));

  // `user:kyle` is the signed-in principal (mocked /api/whoami), so it reads
  // as "you"; everyone else is their own name.
  await expect(pane.locator(".relay-author").first()).toHaveText("you");
  await expect(pane.getByText("news", { exact: true }).first()).toBeVisible();
  await expect(pane.getByText("152911", { exact: true })).toBeVisible();

  // Two consecutive messages from news inside the window are ONE block.
  await expect(pane.locator(".relay-block", { hasText: "Nothing is on fire" })
    .locator(".relay-author")).toHaveCount(1);
  await expect(pane.locator(".relay-block", { hasText: "Nothing is on fire" }))
    .toContainText("The third one is a duplicate");

  // The room's own voice, and an event card.
  await expect(pane.locator(".relay-system")).toContainText("archived #old-standup");
  await expect(pane.locator(".relay-card-title")).toHaveText("Run failed");
});

test("an agent message links to the run that wrote it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const link = page.getByRole("link", { name: /view run/ }).first();
  await expect(link).toHaveAttribute("href", /^\/runs\/b2b2/);
});

test("presence says who is thinking, and only in this room", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  await expect(page.locator(".relay-thinking")).toContainText("news is thinking…");
  // health-monitor is idle in the fixture and must not appear.
  await expect(page.locator(".relay-thinking")).not.toContainText("health-monitor");
});

test("@ opens an autocomplete over the agents a mention can reach", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.click();
  await box.pressSequentially("@ne");
  await expect(page.getByRole("option", { name: /news/ })).toBeVisible();
  // Enter accepts the highlighted name rather than sending a half-typed one.
  await box.press("Enter");
  await expect(box).toHaveValue("@news ");
});

test("sending appends the message the server actually stored", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.click();
  await box.pressSequentially("ship it");
  await box.press("Enter");
  await expect(page.locator(".relay-messages")).toContainText("ship it");
  await expect(box).toHaveValue("");
});

test("an empty channel invites the first message instead of showing nothing",
     async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  await page.getByRole("button", { name: /quiet/ }).click();
  await expect(page).toHaveURL(/channel=rc2/);
  await expect(page.locator(".relay-welcome"))
    .toContainText("Say something, or @mention an agent to wake it up.");
  await expect(page.locator(".relay-welcome")).toContainText("nothing has happened here yet");
});

test("?kind=dm opens the direct messages side", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?kind=dm");
  await expect(page.locator(".relay-messages")).toContainText("What's my day look like?");
  // and the nav says which half you are in
  await expect(page.getByRole("link", { name: "DMs" })).toHaveClass(/active/);
  await expect(page.getByRole("link", { name: "Channels" })).not.toHaveClass(/active/);
});

test("/conversations redirects to Relay's dm side", async ({ page }) => {
  await mockApi(page);
  await page.goto("/conversations");
  await expect(page).toHaveURL(/\/relay\?kind=dm$/);
  await expect(page.getByRole("heading", { level: 1, name: "Relay" })).toBeVisible();
});

test("a thread opens beside the room, not instead of it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  // The root advertises the conversation it already has, rather than offering
  // to start one.
  const block = page.locator(".relay-block", { hasText: "dig into that one" });
  const opener = block.getByRole("button", { name: /1 reply/ });
  // Visible without hovering: unlike the other per-message verbs, a reply
  // count is how you find out the conversation is there at all.
  await expect(opener).toHaveCSS("opacity", "1");
  await opener.click();
  await expect(page).toHaveURL(/thread=m6/);

  const thread = page.getByRole("region", { name: "Thread" });
  await expect(thread).toContainText("@news dig into that one.");     // the root
  await expect(thread).toContainText("On it — reading the run now."); // the reply
  // …and the channel is still there behind it.
  await expect(page.getByRole("region", { name: /^Channel/ }))
    .toContainText("hello from discord");
});

test("replies stay out of the room and are counted on their root", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const room = page.getByRole("region", { name: /^Channel/ });
  await expect(room).toContainText("@news dig into that one.");
  // m7 answers m6: it belongs to the thread, not to the transcript.
  await expect(room).not.toContainText("On it — reading the run now.");
  // …and the root says the conversation is there, and how fresh it is.
  const opener = room.getByRole("button", { name: /1 reply/ });
  await expect(opener).toContainText("💬 1 reply");
  await expect(opener).toContainText("last 15m ago");
  // A message nobody answered offers the verb instead of a count.
  const lonely = page.locator(".relay-block", { hasText: "hello from discord" });
  await lonely.hover();
  await expect(lonely.getByRole("button", { name: "reply in thread" })).toBeVisible();
});

test("search is driveable from the keyboard", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const box = page.getByRole("searchbox", { name: "Search messages" });
  await box.click();
  await box.pressSequentially("reading");
  // Newest first: m7 ("reading the run now") is selected, m2 is under it.
  await expect(page.getByRole("option").first()).toHaveAttribute("aria-selected", "true");
  await box.press("ArrowDown");
  await expect(page.getByRole("option").nth(1)).toHaveAttribute("aria-selected", "true");
  await box.press("Enter");
  // m2 is not a reply, so it is its own thread root.
  await expect(page).toHaveURL(/thread=m2/);
  await expect(page.locator('.relay-message.highlight[data-message-id="m2"]').first())
    .toBeVisible();
});

test("a jumped-to message is still marked when the room loads slowly",
     async ({ page }) => {
  await mockApi(page);
  // Longer than the mark lasts: a countdown started at the click would be over
  // before this room had drawn a single row.
  await page.route("**/api/relay/channels/rd1/messages*", async (route) => {
    await new Promise((done) => setTimeout(done, 3000));
    await route.fallback();
  });
  await page.goto("/relay");
  const box = page.getByRole("searchbox", { name: "Search messages" });
  await box.click();
  await box.pressSequentially("day look");
  await page.getByRole("option", { name: /day look/ }).click();
  await expect(page).toHaveURL(/channel=rd1/);
  await expect(page.locator('.relay-message.highlight[data-message-id="d1"]').first())
    .toBeVisible({ timeout: 10000 });
});

test("an arrival does not yank a reader who is up in the history",
     async ({ page }) => {
  await mockApi(page);
  // A room long enough to scroll, and a catch-up that delivers somebody else's
  // message once the reader has gone looking through history.
  const older = Array.from({ length: 60 }, (_, i) => ({
    id: `h${i}`, channel_id: "rc1", author: "agent:news", kind: "text",
    body: `history line ${i}`, card: null, reply_to: null, thread_root: null,
    run_id: null, hop: 0, mentions: [], edited_at: null, reactions: [],
    face: { emoji: "🎈", hue: 9 },
    created_at: new Date(Date.now() - (120 - i) * 60000).toISOString(),
  }));
  let catchUps = 0;
  await page.route("**/api/relay/channels/rc1/messages*", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.has("after")) {
      catchUps += 1;
      await route.fulfill({ json: catchUps < 2 ? [] : [{ ...older[0], id: "arrival",
        body: "brand new message", created_at: new Date().toISOString() }] });
      return;
    }
    await route.fulfill({ json: [...older].reverse() });   // a page is newest-first
  });
  await page.goto("/relay");
  const pane = page.locator(".relay-messages").first();
  await expect(pane).toContainText("history line 59");
  // The reader goes up to read what happened earlier.
  await pane.evaluate((el) => { el.scrollTop = 0; });
  await expect.poll(() => pane.evaluate((el) => el.scrollTop)).toBe(0);

  await expect(pane).toContainText("brand new message", { timeout: 15000 });
  expect(await pane.evaluate((el) => el.scrollTop),
         "the transcript must not jump to the bottom under the reader").toBe(0);
});

test("a reply from the thread pane posts into the thread", async ({ page }) => {
  await mockApi(page);
  const posted: Record<string, unknown>[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && /\/messages$/.test(new URL(r.url()).pathname)) {
      posted.push(r.postDataJSON());
    }
  });
  await page.goto("/relay?channel=rc1&thread=m6");
  const box = page.getByRole("textbox", { name: "Reply in thread" });
  await box.click();
  await box.pressSequentially("following up");
  await box.press("Enter");
  await expect(page.getByRole("region", { name: "Thread" })).toContainText("following up");
  expect(posted).toEqual([{ body: "following up", reply_to: "m6" }]);
});

test("closing the thread clears the parameter, and the room stays put", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rc1&thread=m6");
  await page.getByRole("button", { name: "Close thread" }).click();
  await expect(page).not.toHaveURL(/thread=/);
  await expect(page).toHaveURL(/channel=rc1/);
  await expect(page.getByRole("region", { name: "Thread" })).toHaveCount(0);
});

test("at 390 the thread is a sheet with a way back", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/relay?channel=rc1&thread=m6");
  const thread = page.getByRole("region", { name: "Thread" });
  // A sheet, not a 360px column squeezed into a phone.
  const box = (await thread.boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(380);
  expect(box.x).toBe(0);
  const back = page.getByRole("button", { name: /Back to the channel/ });
  await expect(back).toBeVisible();
  await back.click();
  await expect(page).not.toHaveURL(/thread=/);
});

test("search finds a message and jumps to it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const box = page.getByRole("searchbox", { name: "Search messages" });
  await box.click();
  await box.pressSequentially("duplicate");
  const hit = page.getByRole("option", { name: /duplicate/ });
  await expect(hit).toBeVisible();
  // The row says who, where, and the words around the match.
  await expect(hit).toContainText("news");
  await expect(hit).toContainText("#general");
  await expect(hit.locator("mark")).toHaveText("duplicate");

  await hit.click();
  await expect(page).toHaveURL(/channel=rc1/);
  // m3 is not a reply, so it is its own thread root.
  await expect(page).toHaveURL(/thread=m3/);
  await expect(page.locator('.relay-message.highlight[data-message-id="m3"]').first())
    .toBeVisible();
});

test("/ focuses search, and Escape closes it", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const box = page.getByRole("searchbox", { name: "Search messages" });
  // Not while you are writing a message — "/" is a character there.
  await page.getByRole("textbox", { name: "Message" }).click();
  await page.keyboard.press("/");
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveValue("/");

  await page.locator("h1").click();
  await page.keyboard.press("/");
  await expect(box).toBeFocused();
  await box.pressSequentially("duplicate");
  await expect(page.locator(".relay-search-results")).toBeVisible();
  await box.press("Escape");
  await expect(page.locator(".relay-search-results")).toHaveCount(0);
  await expect(box).toHaveValue("");
});

test("the Dashboard tile reads the day, and a refused mention is triage",
     async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  // The triage item links to /relay too, so the tile is named by its label.
  const tile = page.locator("a[href='/relay']", { hasText: "relay · 24h" });
  await expect(tile).toContainText("42");
  await expect(tile).toContainText("17 agent · 9 invocations · 2 refused");
  // 96 of 120 is 80% of the hour's budget: the gauge warns before it bites.
  await expect(tile.locator(".text-warning")).toContainText("budget 96/120");

  const refused = page.locator(".attention-item", { hasText: "Relay refused" });
  await expect(refused)
    .toContainText("Relay refused 2 mentions today (hop cap, budget or membership)");
  // Which guard refused them is a hover away, not a trip to the invocations —
  // and the four coalesced wakes in the same breakdown are not refusals.
  await expect(refused.getByRole("link")).toHaveAttribute("title", "hop_limit × 1 · budget × 1");
  await expect(refused.getByRole("link")).toHaveAttribute("href", "/relay");
});

test("a group is named by who is in it, not treated as a 1:1", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?kind=dm");
  // No title on the wire for a group (T9), so the members ARE the name.
  const row = page.getByRole("button", { name: /3 members/ });
  await expect(row).toContainText("news");
  await expect(row).toContainText("you");
  await expect(row.locator(".relay-face")).toHaveCount(3);
});

test("a group with a title is called by it, not by its member list", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?kind=dm");
  await expect(page.getByRole("button", { name: /release crew/ })).toBeVisible();
});

test("a mention offers only the agents this room can actually summon",
     async ({ page }) => {
  await mockApi(page);
  // The dm holds pai and you; news is enabled platform-wide but not a member.
  await page.goto("/relay?kind=dm");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.click();
  await box.pressSequentially("@pa");
  await expect(page.getByRole("option", { name: /pai/ })).toBeVisible();
  await box.press("Escape");
  await box.fill("");
  await box.pressSequentially("@ne");
  await expect(page.getByRole("option")).toHaveCount(0);
  await expect(page.locator(".relay-mention-hint")).toContainText("not in this room");
});

test("a thread is fetched as a thread, not filtered out of what is loaded",
     async ({ page }) => {
  await mockApi(page);
  const asked: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.searchParams.has("thread")) asked.push(u.pathname + u.search);
  });
  await page.goto("/relay?channel=rc1&thread=m6");
  await expect(page.getByRole("region", { name: "Thread" }))
    .toContainText("On it — reading the run now.");
  expect(asked.some((u) => /\/messages\?thread=m6/.test(u)),
         `no thread fetch, saw: ${asked.join(", ")}`).toBe(true);
});

test("a 409 on a send says what is actually going on", async ({ page }) => {
  await mockApi(page);
  // Registered after the mock, so it wins: the dm already has a turn running.
  await page.route("**/api/relay/channels/rd1/messages", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({ status: 409, contentType: "application/json",
                          json: { detail: "conversation is closed, missing, or has a turn in progress" } });
  });
  await page.goto("/relay?kind=dm");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.click();
  await box.pressSequentially("are you there");
  await box.press("Enter");
  await expect(page.locator(".relay-compose .error"))
    .toContainText("a reply is already in progress");
  // and the words you typed are still yours
  await expect(box).toHaveValue("are you there");
});

test("the Relay nav entry stays lit on both of its sides", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  await expect(page.getByRole("link", { name: "Relay", exact: true })).toHaveClass(/active/);
  await expect(page.getByRole("link", { name: "Channels" })).toHaveClass(/active/);
  await page.goto("/relay?kind=dm");
  // The group header is the whole group — it does not go dark on a sub-page.
  await expect(page.getByRole("link", { name: "Relay", exact: true })).toHaveClass(/active/);
});

test("at 390 the compose box is a compose box, and the mention popup fits",
     async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/relay");
  const box = page.getByRole("textbox", { name: "Message" });
  const field = await box.boundingBox();
  expect(field!.width, "the textarea must be wide enough to write in")
    .toBeGreaterThan(200);
  const send = page.getByRole("button", { name: "Send" });
  await expect(send).toBeVisible();
  // Send sits under the field on a phone, not wedged against the edge.
  const button = (await send.boundingBox())!;
  expect(button.y).toBeGreaterThan(field!.y);

  await box.click();
  await box.pressSequentially("@ne");
  const popup = await page.locator(".relay-mentions").boundingBox();
  expect(popup!.x).toBeGreaterThanOrEqual(0);
  expect(popup!.x + popup!.width).toBeLessThanOrEqual(390);
});

test("a dropped stream catches up from the cursor, not from the top",
     async ({ page }) => {
  // The mock's SSE route answers a finished stream, so the pane always lands
  // in its fallback — which must ask for what came AFTER the newest message
  // it holds, oldest-first, rather than re-reading the room.
  await mockApi(page);
  const caught: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.searchParams.has("after") && u.pathname.endsWith("/messages")) {
      caught.push(u.searchParams.get("after")!);
    }
  });
  await page.goto("/relay");
  await expect(page.locator(".relay-messages")).toContainText("hello from discord");
  await expect.poll(() => caught).toContain("m8");
});
