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

test("reply in thread narrows the pane to that thread", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  const block = page.locator(".relay-block", { hasText: "dig into that one" });
  await block.hover();
  await block.getByRole("button", { name: "reply in thread" }).click();
  await expect(page).toHaveURL(/thread=m6/);
  const pane = page.locator(".relay-messages");
  await expect(pane).toContainText("On it — reading the run now.");
  await expect(pane).not.toContainText("hello from discord");
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
  await expect(page.locator(".relay-messages")).toContainText("On it — reading the run now.");
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
