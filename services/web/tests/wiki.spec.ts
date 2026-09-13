import { expect, test } from "@playwright/test";
import { mockApi, wikiFixtures } from "./mock-api";

// The wiki (docs/design/21) is the one page where the platform's knowledge is
// a document rather than a feed, so these gate what makes it usable as one:
// the rail that says what is happening to it, the chips that turn prose into
// navigation, an edit that cannot silently lose somebody else's words, and the
// diff that shows what a version actually did.

test("the home page renders, and the rail says what is happening to the wiki",
     async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/wiki");

  // The `home` page IS the home page — rendered, not summarised.
  await expect(page.locator(".wiki-body")).toContainText("Everything the platform knows");

  // Recent changes: who wrote what, and how much of it.
  const recent = page.getByRole("region", { name: "Recent changes" });
  await expect(recent).toContainText("deploying");

  // The red links, most-asked first.
  const wanted = page.getByRole("region", { name: "Wanted pages" });
  await expect(wanted.locator("a").first()).toHaveText("standup");
  await expect(wanted).toContainText("kafka-lag");

  // Untouched for a month.
  await expect(page.getByRole("region", { name: "Stale pages" }))
    .toContainText("Kyle's location");

  expect(unmatched, "unfixtured API calls on /wiki").toEqual([]);
});

test("a [[slug]] is a chip in prose and plain text in code", async ({ page }) => {
  await mockApi(page);
  await page.goto("/wiki");
  const body = page.locator(".wiki-body");
  // An existing page is a link…
  await expect(body.locator('a[href="/wiki/deploying"]')).toHaveCount(1);
  // …and one nobody has written yet is a red one that still goes somewhere.
  const red = body.locator('a[href="/wiki/standup"]');
  await expect(red).toHaveCount(1);
  await expect(red).toHaveAttribute("title", /wanted/);
  // The same slug inside backticks is somebody showing the syntax.
  await expect(body.locator("code")).toContainText("[[standup]]");
  await expect(body.locator("code a")).toHaveCount(0);
});

test("a red chip opens the editor to write the page", async ({ page }) => {
  await mockApi(page);
  await page.goto("/wiki");
  await page.locator('.wiki-body a[href="/wiki/standup"]').click();
  await expect(page).toHaveURL(/\/wiki\/standup$/);
  // No page yet: the editor is already open, named after the slug.
  await expect(page.getByLabel("Title")).toHaveValue("standup");
  await expect(page.getByRole("button", { name: "Create page" })).toBeVisible();
});

test("an edit carries the version it read, and the page re-renders",
     async ({ page }) => {
  await mockApi(page);
  const writes: Record<string, unknown>[] = [];
  page.on("request", (r) => {
    if (r.method() === "PUT" && new URL(r.url()).pathname === "/api/wiki/pages/deploying") {
      writes.push(r.postDataJSON());
    }
  });
  await page.goto("/wiki/deploying");
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Page body").fill("# Deploying\n\nPush, sync, then helm.");
  await page.getByLabel("Reason").fill("shorter");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.locator(".wiki-body")).toContainText("Push, sync, then helm.");
  expect(writes).toHaveLength(1);
  // v2 is what the page said when it was opened — the base the server checks.
  expect(writes[0].base_version).toBe(2);
  expect(writes[0].reason).toBe("shorter");
  // The version on screen is the one the server answered with.
  await expect(page.locator(".wiki-meta")).toContainText("v3");
});

test("a page that moved under an edit keeps the draft and says so", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/wiki/pages/deploying"
                            && url.searchParams.toString() === "",
                   async (route) => {
                     if (route.request().method() !== "PUT") { await route.fallback(); return; }
                     await route.fulfill({ status: 409, json: {
                       detail: "deploying has moved on: you read v2, it is at v5",
                       current_version: 5,
                       current_summary: "How the platform is deployed, with the helm trap.",
                     } });
                   });
  await page.goto("/wiki/deploying");
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Page body").fill("my careful rewrite");
  await page.getByRole("button", { name: "Save" }).click();

  const banner = page.locator(".wiki-conflict");
  await expect(banner).toContainText("This page changed (now v5)");
  // The words somebody typed are the one thing a conflict must never cost.
  await expect(page.getByLabel("Page body")).toHaveValue("my careful rewrite");
  await page.getByRole("button", { name: "Reload and keep my text" }).click();
  await expect(page.getByLabel("Page body")).toHaveValue("my careful rewrite");
  await expect(banner).toHaveCount(0);
});

test("the history drawer shows who changed what, and the diff marks the lines",
     async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/wiki/deploying");
  await page.getByRole("button", { name: "History" }).click();
  const history = page.getByRole("region", { name: "History" });
  await expect(history).toContainText("add the helm --reuse-values trap");
  await expect(history).toContainText("first draft");

  await history.getByRole("button", { name: /v2/ }).click();
  const diff = page.locator(".wiki-diff");
  await expect(diff).toBeVisible();
  // Added and removed are marked as text, not by colour alone.
  await expect(diff.locator('[data-kind="add"]').first()).toHaveText(/^\+/);
  await expect(diff.locator('[data-kind="del"]').first()).toHaveText(/^-/);
  expect(unmatched, "unfixtured API calls on /wiki/deploying").toEqual([]);
});

test("a page frame lands in Recent changes without a reload", async ({ page }) => {
  await mockApi(page);
  const home = wikiFixtures.find((p) => p.slug === "home")!;
  await page.route((url) => url.pathname === "/api/wiki/events", async (route) => {
    await route.fulfill({
      status: 200, contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: `event: page\ndata: ${JSON.stringify({
        event: "edited",
        page: { ...home, version: 4, updated_at: new Date().toISOString() },
        version: 4, author: "agent:news", run_id: "r-news-1",
        reason: "linked the standup page", added: 7, removed: 2,
      })}\n\n`,
    });
  });
  await page.goto("/wiki");
  const recent = page.getByRole("region", { name: "Recent changes" });
  await expect(recent).toContainText("linked the standup page");
  await expect(recent).toContainText("+7");
  await expect(recent).toContainText("−2");
});

test("the wiki fits a phone", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/wiki", "/wiki/deploying"]) {
    await page.goto(path);
    await expect(page.locator(".wiki-body").first()).toBeVisible();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} scrolls sideways at 390`).toBeLessThanOrEqual(1);
  }
});

test("a diff that cannot be read says so instead of loading forever", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/wiki/pages/deploying/versions/2",
                   (route) => route.fulfill({ status: 404,
                                              json: { detail: "deploying has no v2" } }));
  await page.goto("/wiki/deploying");
  await page.getByRole("button", { name: "History" }).click();
  const history = page.getByRole("region", { name: "History" });
  await history.getByRole("button", { name: /v2/ }).click();
  await expect(history.locator(".wiki-diff-error")).toContainText(/404|could not be read/i);
  await expect(history).not.toContainText("Loading the diff…");
});

test("a page somebody else writes while you are looking at the blank one appears",
     async ({ page }) => {
  await mockApi(page);
  const written = {
    ...wikiFixtures[0], id: "w-standup", slug: "standup", title: "Standup",
    body: "# Standup\n\nEvery morning at 09:00, in #standup.", summary: "Every morning at 09:00.",
    version: 1, updated_by: "agent:pai", updated_by_face: null,
    updated_at: new Date().toISOString(),
  };
  // The first read is the 404 that opens the create editor; the frame below
  // then says the page exists, and the read after it is the page itself.
  let reads = 0;
  await page.route((url) => url.pathname === "/api/wiki/pages/standup", async (route) => {
    reads += 1;
    if (reads === 1) {
      await route.fulfill({ status: 404, json: { detail: "unknown wiki page" } });
      return;
    }
    await route.fulfill({ json: { page: written, backlinks: [],
                                  cited_in: { count: 0, count_capped: false, last: [] } } });
  });
  await page.route((url) => url.pathname === "/api/wiki/events", async (route) => {
    await route.fulfill({
      status: 200, contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache" },
      body: `event: page\ndata: ${JSON.stringify({
        event: "created", page: written, version: 1, author: "agent:pai",
        run_id: "r-pai-1", reason: "wrote the standup page", added: 3, removed: 0,
      })}\n\n`,
    });
  });
  await page.goto("/wiki/standup");
  // Nobody reloaded: the page arrived.
  await expect(page.locator(".wiki-body")).toContainText("Every morning at 09:00");
  await expect(page.getByRole("button", { name: "Create page" })).toHaveCount(0);
});

test("a create that lost the race says so instead of a raw refusal", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/wiki/pages", async (route) => {
    if (route.request().method() !== "POST") { await route.fallback(); return; }
    // The API's create conflict carries a sentence and nothing else — there is
    // no version to merge against, because the caller wrote none.
    await route.fulfill({ status: 409, json: { detail: "standup already exists" } });
  });
  await page.goto("/wiki/standup");
  await page.getByLabel("Page body").fill("every morning at 09:00");
  await page.getByRole("button", { name: "Create page" }).click();
  const banner = page.locator(".wiki-conflict");
  await expect(banner).toContainText(/just created/i);
  await expect(page.getByLabel("Page body")).toHaveValue("every morning at 09:00");
  await expect(page.getByRole("button", { name: "Reload and keep my text" })).toBeVisible();
});

test("a slow search answer never overwrites a newer one", async ({ page }) => {
  await mockApi(page);
  await page.route((url) => url.pathname === "/api/wiki/pages" && !!url.searchParams.get("q"),
                   async (route) => {
                     const q = new URL(route.request().url()).searchParams.get("q") ?? "";
                     // The first query is the slow one, and answers last.
                     if (q.includes("slow")) {
                       await new Promise((r) => setTimeout(r, 1500));
                       await route.fulfill({ json: [{ ...wikiFixtures[2], title: "Stale answer" }] });
                       return;
                     }
                     await route.fulfill({ json: [{ ...wikiFixtures[1], title: "Fresh answer" }] });
                   });
  await page.goto("/wiki");
  const box = page.getByLabel("Search the wiki");
  await box.fill("slow");
  await box.fill("helm");
  const results = page.getByRole("region", { name: "Search results" });
  await expect(results).toContainText("Fresh answer");
  // …and it is still the fresh one once the slow request has landed.
  await page.waitForTimeout(2000);
  await expect(results).toContainText("Fresh answer");
  await expect(results).not.toContainText("Stale answer");
});

test("a [[slug]] inside a link's text is left alone", async ({ page }) => {
  await mockApi(page);
  await page.goto("/wiki");
  const body = page.locator(".wiki-body");
  // Rewriting inside link text nests an anchor in an anchor, which the browser
  // un-nests into something nobody can click. So the brackets stay exactly as
  // the author typed them (markdown itself declines to make a link of them)…
  await expect(body).toContainText("[See [[deploying]]]");
  // …and the only wiki chip on the page is the one in ordinary prose. Without
  // the protection this would be two: the prose one and the nested wreck.
  await expect(body.locator('a[href="/wiki/deploying"]')).toHaveCount(1);
  await expect(body.locator('a[href="https://example.com/deploy"]')).toHaveCount(1);
});

test("a wide table cannot push the page sideways on a phone", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/wiki/deploying");
  await expect(page.locator(".wiki-body table")).toBeVisible();
  const overflow = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "a table must not widen the page at 390").toBeLessThanOrEqual(1);
  // …and the guard that keeps it true for a table too wide to wrap: the body
  // scrolls the table where it stands. The <table> itself is deliberately NOT
  // `display: block` — that drops its role out of the accessibility tree.
  const body = page.locator(".wiki-body");
  await expect(body).toHaveCSS("overflow-x", "auto");
  await expect(page.locator(".wiki-body table")).toHaveCSS("display", "table");
});

test("a wiki link is a chip, cut to the same pattern as a ticket key",
     async ({ page }) => {
  await mockApi(page);
  // Retried, not read once: a chip inside a live pane can be swapped out by a
  // re-render between resolving it and measuring it, and `getComputedStyle` on
  // a node that has just left the document answers with empty strings rather
  // than with a style.
  type Pill = { radius: string; padding: string; font: string; style: string; width: string };
  async function pill(locator: import("@playwright/test").Locator): Promise<Pill> {
    let out: Pill | null = null;
    await expect.poll(async () => {
      out = await locator.evaluate((el) => {
        const cs = getComputedStyle(el);
        return { radius: cs.borderTopLeftRadius, padding: cs.padding,
                 font: cs.fontFamily, style: cs.borderTopStyle, width: cs.borderTopWidth };
      }).catch(() => null);
      return out?.radius ?? "";
    }).not.toBe("");
    return out!;
  }

  // The reference: a ticket key in a room message.
  await page.goto("/relay?channel=rc3&thread=k1");
  const key = page.locator('.md a[href="/tickets/OPS-2"]').first();
  await expect(key).toBeVisible();
  const ticket = await pill(key);

  // A `[[slug]]` is the same kind of thing in a sentence — a named object you
  // can open — so it is the same shape. Only the tint differs.
  await page.goto("/wiki");
  const existing = await pill(page.locator('.wiki-body a[href="/wiki/deploying"]').first());
  expect(existing.radius).toBe(ticket.radius);
  expect(existing.padding).toBe(ticket.padding);
  expect(existing.font).toBe(ticket.font);
  expect(existing.width).toBe(ticket.width);
  expect(existing.radius).not.toBe("0px");

  // A wanted page is the same pill with a dashed border — the difference is
  // never colour alone.
  const wanted = await pill(page.locator('.wiki-body a[href="/wiki/standup"]').first());
  expect(wanted.radius).toBe(ticket.radius);
  expect(wanted.padding).toBe(ticket.padding);
  expect(wanted.style).toBe("dashed");
  expect(existing.style).toBe("solid");
});
