import { expect, test } from "@playwright/test";
import { ARTIFACTS, artifactFeed, mockApi } from "./mock-api";

// The artifacts block (docs/design/23): a picture the platform keeps is a row
// with bytes behind it, and it has to read the same way everywhere — a tile
// on /artifacts, a card in a room, a face on an agent. These gate the three.

const dragon = ARTIFACTS.generated;
const csv = ARTIFACTS.file;

test("the grid draws an image as a thumb and a file as a glyph tile", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/artifacts");
  await expect(page.getByRole("heading", { level: 1, name: "Artifacts" })).toBeVisible();
  const grid = page.locator(".artifact-grid");
  await expect(grid.locator(".artifact-card")).toHaveCount(7);
  // Five images carry a real <img> off the thumb route; the csv has no thumb
  // and gets a glyph and its name instead of a broken picture, and so does
  // the row whose thumb points off the artifacts routes.
  await expect(grid.locator(".artifact-card img")).toHaveCount(5);
  const file = grid.locator(".artifact-card.file");
  await expect(file).toHaveCount(1);
  await expect(file).toContainText(csv.name);
  await expect(file.locator("img")).toHaveCount(0);
  // The total-bytes bar reads against the cap, as a meter with a real label.
  const meter = page.getByRole("meter", { name: /of 500 MB/ });
  await expect(meter).toBeVisible();
  expect(unmatched).toEqual([]);
});

test("a filter lives in the URL, so a filtered view is a link", async ({ page }) => {
  await mockApi(page);
  await page.goto("/artifacts");
  await page.getByLabel("Filter by kind").selectOption("file");
  await expect(page).toHaveURL(/\/artifacts\?kind=file$/);
  await expect(page.locator(".artifact-card")).toHaveCount(1);
  await expect(page.locator(".artifact-card")).toContainText(csv.name);

  // …and the link works the other way: landing on it applies the filter.
  await page.goto(`/artifacts?source=generated&owner=${encodeURIComponent(dragon.owner)}`);
  await expect(page.locator(".artifact-card")).toHaveCount(1);
  await expect(page.getByLabel("Filter by source")).toHaveValue("generated");

  await page.goto("/artifacts?q=nothing-is-called-this");
  await expect(page.locator(".artifact-card")).toHaveCount(0);
  await expect(page.locator("body")).toContainText("Nothing here yet — try the Studio");
});

test("a tile opens the lightbox, and /artifacts/<id> is the same lightbox", async ({ page }) => {
  await mockApi(page);
  await page.goto("/artifacts");
  await page.locator(".artifact-card", { hasText: dragon.name }).click();
  const box = page.getByRole("dialog", { name: dragon.name });
  await expect(box).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/artifacts/${dragon.id}$`));
  // Provenance is the generated image's meta: model, how long, what it cost.
  await expect(box).toContainText("gpt-image-1 · 3 s · $0.04");
  await expect(box).toContainText(dragon.meta.prompt as string);
  await expect(box.locator("img")).toHaveAttribute("src", dragon.content_url);
  await expect(box.getByRole("link", { name: /open in Studio/i }))
    .toHaveAttribute("href", `/studio?ref=${dragon.id}`);
  await box.getByRole("button", { name: "Close" }).click();
  await expect(box).toBeHidden();
  await expect(page).toHaveURL(/\/artifacts$/);

  // The deep link: the page lands with the lightbox already open, and its
  // tab says which picture it is.
  const unmatched = await mockApi(page);
  await page.goto(`/artifacts/${dragon.id}`);
  await expect(page.getByRole("dialog", { name: dragon.name })).toBeVisible();
  await expect.poll(() => page.title()).toBe(`${dragon.name} · Artifacts · Agent Platform`);
  expect(unmatched).toEqual([]);
});

test("delete asks first, then sends the DELETE and drops the tile", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/artifacts/${dragon.id}`);
  const box = page.getByRole("dialog", { name: dragon.name });
  await box.getByRole("button", { name: "Delete" }).click();
  // A one-way door: the confirm is its own dialog, and cancelling it changes
  // nothing.
  const confirm = page.getByRole("dialog", { name: /delete/i });
  await expect(confirm).toBeVisible();
  const sent: string[] = [];
  page.on("request", (r) => { if (r.method() === "DELETE") sent.push(new URL(r.url()).pathname); });
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toBeHidden();
  expect(sent).toEqual([]);

  await box.getByRole("button", { name: "Delete" }).click();
  const deleted = page.waitForRequest((r) => r.method() === "DELETE");
  await page.getByRole("dialog", { name: /delete/i }).getByRole("button", { name: "Delete" }).click();
  expect(new URL((await deleted).url()).pathname).toBe(`/api/artifacts/${dragon.id}`);
  await expect(box).toBeHidden();
  await expect(page).toHaveURL(/\/artifacts$/);
  await expect(page.locator(".artifact-card", { hasText: dragon.name })).toHaveCount(0);
});

test("[[artifact:id]] in a room is a card; an unknown id is a muted chip; code is code",
     async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/relay?channel=rc4");
  const pane = page.locator(".relay-messages");
  // The #art event card the API posts, and the chip in ordinary prose: the
  // same component, with the picture and the owner's face on it.
  const cards = pane.locator(".artifact-card");
  await expect(cards).toHaveCount(2);
  for (const card of await cards.all()) {
    await expect(card.locator("img").first()).toHaveAttribute("src", dragon.thumb_url!);
    await expect(card.locator(".relay-face")).toBeVisible();
  }
  await expect(pane.locator(".artifact-card").first()).toContainText("gpt-image-1 · 3 s · $0.04");
  // An id nobody has: a chip that says so, never a broken card and never a
  // request that fails the page.
  const missing = pane.locator(".artifact-missing");
  await expect(missing).toHaveCount(1);
  await expect(missing).toContainText("artifact not found");
  // Inside a code span the syntax is being quoted, not used.
  const quoted = pane.locator(".relay-message", { hasText: "the syntax is" });
  await expect(quoted.locator("code")).toContainText(`[[artifact:${dragon.id}]]`);
  await expect(quoted.locator(".artifact-card")).toHaveCount(0);
  expect(unmatched).toEqual([]);
});

test("an agent with a picture wears it on its face, everywhere a face is drawn",
     async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay");
  // news has an image; its face in the transcript is the picture inside the
  // same disc, and the disc keeps its class and stays out of the a11y tree.
  const face = page.locator(".relay-block", { hasText: "Nothing is on fire" }).locator(".relay-face");
  await expect(face).toHaveAttribute("aria-hidden", "true");
  await expect(face.locator("img")).toHaveAttribute("src", ARTIFACTS.face.thumb_url!);
  // …and an agent without one is still its emoji.
  await page.goto("/relay?channel=rc4");
  const plain = page.locator(".relay-block", { hasText: "earlier draft" }).locator(".relay-face").first();
  await expect(plain).toContainText("🐢");
  await expect(plain.locator("img")).toHaveCount(0);
});

// The stream reaches every mounted card, not only the page that owns a list:
// a chip drawn before its row was servable and a chip whose picture has since
// been deleted must both change under the reader, without a reload. The mock
// delivers a frame on the hook's next reconnect, so these wait a little.
const LANDS = { timeout: 15000 };

test("a card that was not found becomes a card when the artifact lands", async ({ page }) => {
  await mockApi(page);
  const feed = await artifactFeed(page);
  await page.goto("/relay?channel=rc4");
  const row = page.locator(".relay-message", { hasText: "earlier draft" });
  await expect(row.locator(".artifact-missing")).toBeVisible();
  const late = feed.created(ARTIFACTS.missing_id, { name: "earlier-draft.png" });
  await expect(row.locator(".artifact-card")).toContainText("earlier-draft.png", LANDS);
  await expect(row.locator(".artifact-card img")).toHaveAttribute("src", String(late.thumb_url));
  await expect(row.locator(".artifact-missing")).toHaveCount(0);
});

test("a deleted frame flips every mounted card to not found", async ({ page }) => {
  await mockApi(page);
  const feed = await artifactFeed(page);
  await page.goto("/relay?channel=rc4");
  const pane = page.locator(".relay-messages");
  await expect(pane.locator(".artifact-card")).toHaveCount(2);
  feed.deleted(dragon);
  await expect(pane.locator(".artifact-missing")).toHaveCount(3, LANDS);
  await expect(pane.locator(".artifact-card")).toHaveCount(0);
});

test("byte URLs off the artifacts routes are never put in src or href", async ({ page }) => {
  await mockApi(page);
  const hostile = ARTIFACTS.hostile;
  await page.goto(`/artifacts/${hostile.id}`);
  const tile = page.locator(".artifact-card", { hasText: hostile.name });
  await expect(tile).toBeVisible();
  await expect(tile.locator("img")).toHaveCount(0);
  const box = page.getByRole("dialog", { name: hostile.name });
  await expect(box).toBeVisible();
  await expect(box.locator("img")).toHaveCount(0);
  await expect(box.getByRole("link", { name: "Download" })).toHaveCount(0);
  await expect(page.locator('[src*="evil.example"], [href*="evil.example"]')).toHaveCount(0);
});
