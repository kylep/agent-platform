import { expect, test, type Page, type Request } from "@playwright/test";
import { ARTIFACTS, mockApi } from "./mock-api";

// The Agents page as a card grid, and an agent's picture (docs/design/23).
// The grid is the table said another way — the same chip, the same schedule
// line — so what these gate is that nothing the table promised was lost, and
// that the profile-image section writes exactly the two calls it owes: the
// artifact first, then the agent's image route with the id that came back.

function captureWrites(page: Page): Request[] {
  const writes: Request[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/")) writes.push(r);
  });
  return writes;
}

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64");

test("the grid is the default view, and the table is a persisted choice", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents");

  const toggle = page.getByRole("group", { name: "View" });
  await expect(toggle.getByRole("button", { name: "Grid" })).toHaveAttribute("aria-pressed", "true");
  await expect(toggle.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "false");
  // Two regular agents as cards, the system one under its own heading.
  await expect(page.locator(".agent-grid").first().locator(".agent-card")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "System agents" })).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);

  // The control is keyboard-operable: it is two real buttons.
  await toggle.getByRole("button", { name: "Table" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("table")).toHaveCount(2);
  await expect(page.locator(".agent-grid")).toHaveCount(0);
  await expect(toggle.getByRole("button", { name: "Table" })).toHaveAttribute("aria-pressed", "true");

  await page.reload();
  await expect(page.locator("table")).toHaveCount(2);
  await expect(page.getByRole("group", { name: "View" }).getByRole("button", { name: "Table" }))
    .toHaveAttribute("aria-pressed", "true");
});

test("a card carries the row's status chip and its picture", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents");

  const news = page.locator(".agent-card[data-agent=news]");
  await expect(news).toContainText("blocked");
  await expect(news.getByRole("link", { name: "news" })).toHaveAttribute("href", "/agents/news");
  // news wears its artifact; pai has no picture and is its emoji.
  await expect(news.locator(".relay-face img")).toHaveAttribute("src", ARTIFACTS.face.thumb_url!);
  const pai = page.locator(".agent-card[data-agent=pai]");
  await expect(pai.locator(".relay-face img")).toHaveCount(0);
  await expect(pai.locator(".relay-face")).toContainText("🐢");
  await expect(pai).toContainText("ok");
});

test("uploading a picture posts the file, then puts the returned id on the agent", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await expect(page.getByRole("heading", { name: "Profile image" })).toBeVisible();
  await page.locator("input[type=file]").setInputFiles({
    name: "face.png", mimeType: "image/png", buffer: PNG,
  });

  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(1);
  const post = writes.find((w) => w.method() === "POST");
  expect(new URL(post!.url()).pathname).toBe("/api/artifacts");
  expect(post!.headers()["content-type"]).toContain("multipart/form-data");
  const put = writes.find((w) => w.method() === "PUT")!;
  expect(new URL(put.url()).pathname).toBe("/api/agents/health-monitor/image");
  expect(JSON.parse(put.postData() ?? "{}")).toEqual({ artifact_id: ARTIFACTS.uploaded.id });
});

test("generating shows the picture first, and only Use puts it on the agent", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "Generate", exact: true }).click();
  const dialog = page.getByRole("dialog");
  // The prompt is the house style, pre-filled from the agent's own row.
  await expect(dialog.getByLabel("Prompt")).toHaveValue(
    'Portrait of "health-monitor": Watches platform health. flat, friendly avatar, square, centred, no text');
  // The unconfigured provider is offered but cannot be picked.
  const model = dialog.getByLabel("Model");
  await expect(model).toHaveValue("gpt-image-1");
  await expect(model.locator("option", { hasText: "FLUX" })).toBeDisabled();
  await expect(model.locator("option", { hasText: "FLUX" })).toContainText("add key in Secrets");
  await expect(model.locator("option:not([disabled])")).toHaveCount(2);

  await dialog.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(dialog.locator("img.profile-image-preview"))
    .toHaveAttribute("src", ARTIFACTS.fresh.content_url);
  // Nothing has been put on the agent yet.
  expect(writes.filter((w) => w.method() === "PUT")).toHaveLength(0);
  const gen = writes.find((w) => w.method() === "POST")!;
  expect(new URL(gen.url()).pathname).toBe("/api/artifacts/generate");
  expect(JSON.parse(gen.postData() ?? "{}")).toMatchObject({ model: "gpt-image-1", size: "1024x1024" });

  await dialog.getByRole("button", { name: "Use as profile image" }).click();
  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(1);
  const put = writes.find((w) => w.method() === "PUT")!;
  expect(new URL(put.url()).pathname).toBe("/api/agents/health-monitor/image");
  expect(JSON.parse(put.postData() ?? "{}")).toEqual({ artifact_id: ARTIFACTS.fresh.id });
  await expect(dialog).toHaveCount(0);
});

test("choosing from artifacts offers only images, and Remove clears the picture", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/news");

  // The header wears the picture the row carries.
  await expect(page.locator(".page-header .relay-face img"))
    .toHaveAttribute("src", ARTIFACTS.face.thumb_url!);

  await page.getByRole("button", { name: "Choose from artifacts" }).click();
  const dialog = page.getByRole("dialog");
  // Every image the store lists, and only images: the csv is not a face.
  const images = await page.evaluate(() =>
    fetch("/api/artifacts?kind=image").then((r) => r.json() as Promise<unknown[]>));
  await expect(dialog.locator(".artifact-card")).toHaveCount(images.length);
  await expect(dialog.locator(".artifact-card", { hasText: "digest.csv" })).toHaveCount(0);
  await dialog.getByRole("button", { name: `Open ${ARTIFACTS.generated.name}` }).click();
  await dialog.getByRole("button", { name: "Use as profile image" }).click();
  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(1);
  expect(JSON.parse(writes.find((w) => w.method() === "PUT")!.postData() ?? "{}"))
    .toEqual({ artifact_id: ARTIFACTS.generated.id });

  await page.getByRole("button", { name: "Remove" }).click();
  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(2);
  const put = writes.filter((w) => w.method() === "PUT")[1];
  expect(new URL(put.url()).pathname).toBe("/api/agents/news/image");
  expect(JSON.parse(put.postData() ?? "{}")).toEqual({ artifact_id: null });
});

// The section owns every write, so a refusal is never lost behind a dialog
// that has already gone: the Banner under the face is where it lands.
test("a Use whose PUT fails is said under the face, and the dialog cannot close mid-flight",
     async ({ page }) => {
  await mockApi(page);
  let release: () => void = () => {};
  const held = new Promise<void>((r) => { release = r; });
  await page.route("**/api/agents/news/image", async (route) => {
    await held;
    await route.fulfill({ status: 502, json: { detail: "the provider said no" } });
  });
  await page.goto("/agents/news");

  await page.getByRole("button", { name: "Choose from artifacts" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: `Open ${ARTIFACTS.generated.name}` }).click();
  await dialog.getByRole("button", { name: "Use as profile image" }).click();
  await expect(dialog.getByRole("button", { name: "Setting…" })).toBeDisabled();
  // Escape, the overlay and Cancel are all ignored while the write is out.
  await page.keyboard.press("Escape");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeVisible();

  release();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".profile-image-error")).toContainText("the provider said no");
});

test("a second drop mid-flight is ignored", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");
  await expect(page.getByRole("heading", { name: "Profile image" })).toBeVisible();

  await page.evaluate(() => {
    const zone = document.querySelector(".drop-zone")!;
    const dt = () => {
      const t = new DataTransfer();
      t.items.add(new File([new Uint8Array([137, 80, 78, 71])], "a.png", { type: "image/png" }));
      return t;
    };
    for (let i = 0; i < 2; i++) {
      zone.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt() }));
    }
  });
  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(1);
  expect(writes.filter((w) => w.method() === "POST" && new URL(w.url()).pathname === "/api/artifacts"))
    .toHaveLength(1);
});

test("a file that is not an image, or is too big, is refused before any request", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");
  const input = page.locator("input[type=file]");

  await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hi") });
  await expect(page.locator(".profile-image-error")).toContainText("not an image");

  await input.setInputFiles({ name: "huge.png", mimeType: "image/png", buffer: Buffer.alloc(9 * 1024 * 1024) });
  await expect(page.locator(".profile-image-error")).toContainText("8 MiB");

  expect(writes).toHaveLength(0);
});

test("generating cannot be cancelled once it is billed", async ({ page }) => {
  await mockApi(page);
  let release: () => void = () => {};
  const held = new Promise<void>((r) => { release = r; });
  await page.route("**/api/artifacts/generate", async (route) => {
    await held;
    await route.fulfill({ status: 201, json: ARTIFACTS.fresh });
  });
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "Generate", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Generate", exact: true }).click();
  await expect(dialog).toContainText(/generating…/i);
  await page.keyboard.press("Escape");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeVisible();

  release();
  await expect(dialog.locator("img.profile-image-preview")).toBeVisible();
});
