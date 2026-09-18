import { expect, test, type Page, type Request, type Route } from "@playwright/test";
import { deflateSync } from "node:zlib";
import { AGENT_NAMES, ARTIFACTS, artifactFeed, mockApi, studioRecent } from "./mock-api";

// The Studio (docs/design/23): where a picture is asked for. What these gate
// is the contract between the compose panel and the generate route — the
// body it posts is exactly what the form shows — and that the three states
// of the stage (empty, pending, result) each say what they are.

const dragon = ARTIFACTS.generated;
const fresh = ARTIFACTS.fresh;

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

test("the model picker offers every configured model, and the form follows the chosen one", async ({ page }) => {
  const unmatched = await mockApi(page);
  await page.goto("/studio");
  await expect(page.getByRole("heading", { level: 1, name: "Studio" })).toBeVisible();

  const model = page.getByLabel("Model");
  // The registry's default is preselected; the unconfigured provider is
  // offered but cannot be picked, and says why.
  await expect(model).toHaveValue("gpt-image-1");
  await expect(model.locator("option", { hasText: "GPT Image 1" })).toHaveText("GPT Image 1 · $0.04");
  await expect(model.locator("option", { hasText: "FLUX" })).toBeDisabled();
  await expect(model.locator("option", { hasText: "FLUX" })).toContainText("add key in Secrets");
  await expect(model.locator("option:not([disabled])")).toHaveCount(2);

  // gpt-image-1 speaks sizes and qualities, and takes references.
  await expect(page.getByLabel("Size")).toHaveValue("1024x1024");
  await expect(page.getByLabel("Quality")).toBeVisible();
  await expect(page.getByLabel("Aspect")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Pick from artifacts" })).toBeEnabled();
  await expect(page.getByRole("button", { name: /^Generate/ })).toContainText("$0.04");

  // imagen-4 speaks aspects, has no quality, and takes no references.
  await model.selectOption("imagen-4");
  await expect(page.getByLabel("Aspect")).toHaveValue("1:1");
  await expect(page.getByLabel("Size")).toHaveCount(0);
  await expect(page.getByLabel("Quality")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Pick from artifacts" })).toBeDisabled();

  // The stat row reads the store's own sums.
  await expect(page.locator("body")).toContainText("$0.06");
  await expect(page.locator("body")).toContainText("$0.04 / $2.00");
  // An empty stage says what to do.
  await expect(page.locator(".studio-stage")).toContainText(/describe/i);
  expect(unmatched).toEqual([]);
});

test("generate posts what the form shows, references included, and the result is the hero", async ({ page }) => {
  const writes = captureWrites(page);
  const unmatched = await mockApi(page);
  // The lightbox's "Open in Studio" link: the picture arrives as a reference.
  await page.goto(`/studio?ref=${dragon.id}`);
  const strip = page.locator(".reference-strip");
  await expect(strip.locator("img")).toHaveAttribute("src", dragon.thumb_url!);

  await page.getByLabel("Prompt").fill("The same dragon, at night");
  await page.getByLabel("Quality").selectOption("high");
  await page.getByLabel("Seed").fill("7");
  await page.getByRole("button", { name: /^Generate/ }).click();

  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", fresh.content_url);
  const gen = writes.find((w) => w.method() === "POST")!;
  expect(new URL(gen.url()).pathname).toBe("/api/artifacts/generate");
  expect(JSON.parse(gen.postData() ?? "{}")).toEqual({
    model: "gpt-image-1", prompt: "The same dragon, at night", size: "1024x1024",
    quality: "high", seed: 7, reference_ids: [dragon.id],
  });
  // The result's provenance is under it, and the URL names it.
  await expect(page.locator(".studio-stage")).toContainText("gpt-image-1");
  await expect(page.locator(".studio-stage")).toContainText(fresh.meta.prompt as string);
  await expect(page).toHaveURL(new RegExp(`/studio/${fresh.id}$`));
  await expect(page.getByRole("link", { name: "Download" }))
    .toHaveAttribute("href", fresh.content_url);
  await expect(page.getByRole("button", { name: "Mark up" })).toBeEnabled();

  // Iterate: the result becomes a reference, the prompt is kept and focused,
  // the stage is empty again.
  await expect(page.getByRole("button", { name: "Iterate" })).toHaveClass(/bg-accent/);
  await page.getByRole("button", { name: "Iterate" }).click();
  await expect(strip.locator("img")).toHaveCount(2);
  await expect(strip.locator("img").nth(1)).toHaveAttribute("src", fresh.thumb_url!);
  await expect(page.getByLabel("Prompt")).toHaveValue("The same dragon, at night");
  await expect(page.getByLabel("Prompt")).toBeFocused();
  await expect(page.locator(".studio-stage img")).toHaveCount(0);
  await expect(page).toHaveURL(/\/studio$/);

  // A reference can be taken off the strip.
  await strip.getByRole("button", { name: `Remove ${dragon.name}` }).click();
  await expect(strip.locator("img")).toHaveCount(1);
  expect(unmatched).toEqual([]);
});

test("dropping a file uploads it as a reference, and the picker offers the store's images", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/studio");
  await page.locator(".reference-strip input[type=file]").setInputFiles({
    name: "ref.png", mimeType: "image/png", buffer: PNG,
  });
  const strip = page.locator(".reference-strip");
  await expect(strip.locator("img")).toHaveAttribute("src", ARTIFACTS.uploaded.thumb_url!);
  const post = writes.find((w) => w.method() === "POST")!;
  expect(new URL(post.url()).pathname).toBe("/api/artifacts");
  expect(post.headers()["content-type"]).toContain("multipart/form-data");

  await page.getByRole("button", { name: "Pick from artifacts" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".artifact-card", { hasText: "digest.csv" })).toHaveCount(0);
  await dialog.getByRole("button", { name: `Open ${dragon.name}` }).click();
  await dialog.getByRole("button", { name: "Use as reference" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(strip.locator("img")).toHaveCount(2);
});

test("Use as agent image picks an agent from the listing and PUTs the id", async ({ page }) => {
  const writes = captureWrites(page);
  const unmatched = await mockApi(page);
  await page.goto(`/studio/${dragon.id}`);
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", dragon.content_url);

  await page.getByRole("button", { name: "Use as agent image" }).click();
  const dialog = page.getByRole("dialog");
  const agent = dialog.getByLabel("Agent");
  await expect(agent.locator("option")).toHaveText(AGENT_NAMES);
  await agent.selectOption("news");
  await dialog.getByRole("button", { name: "Use as profile image" }).click();
  await expect.poll(() => writes.filter((w) => w.method() === "PUT").length).toBe(1);
  const put = writes.find((w) => w.method() === "PUT")!;
  expect(new URL(put.url()).pathname).toBe("/api/agents/news/image");
  expect(JSON.parse(put.postData() ?? "{}")).toEqual({ artifact_id: dragon.id });
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("body")).toContainText("news");
  expect(unmatched).toEqual([]);
});

test("a refusal from the generate route is shown as the server wrote it", async ({ page }) => {
  await mockApi(page);
  const detail = "Daily image budget of $2.00 reached; try again after midnight UTC.";
  await page.route("**/api/artifacts/generate", async (route: Route) => {
    await route.fulfill({ status: 402, json: { detail } });
  });
  await page.goto("/studio");
  await page.getByLabel("Prompt").fill("Anything at all");
  await page.getByRole("button", { name: /^Generate/ }).click();
  await expect(page.getByText(detail)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Generate/ })).toBeEnabled();
  await expect(page.locator(".studio-stage img")).toHaveCount(0);
});

test("⌘Enter generates, and the wait shows the model and the seconds over a shimmer", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.route("**/api/artifacts/generate", async (route: Route) => {
    await new Promise((r) => setTimeout(r, 1800));
    await route.fulfill({ status: 201, json: fresh });
  });
  await page.goto("/studio");
  await page.getByLabel("Prompt").fill("A slow picture");
  await page.getByLabel("Prompt").press("ControlOrMeta+Enter");

  const stage = page.locator(".studio-stage");
  await expect(stage.locator(".studio-shimmer")).toBeVisible();
  await expect(stage).toContainText("GPT Image 1");
  await expect(stage).toContainText(/1 s/);
  await expect(page.getByRole("button", { name: /^Generating/ })).toBeDisabled();
  await expect(stage.locator("img")).toHaveAttribute("src", fresh.content_url);
  expect(writes.filter((w) => new URL(w.url()).pathname === "/api/artifacts/generate")).toHaveLength(1);
});

test("the recent strip shows the last 24, and one of them fills the stage", async ({ page }) => {
  const unmatched = await mockApi(page);
  const rows = await studioRecent(page);
  await page.goto("/studio");
  const recent = page.locator(".recent-strip");
  await expect(recent.locator("img")).toHaveCount(24);
  await expect(page.getByRole("link", { name: "Browse all" })).toHaveAttribute("href", "/artifacts");

  await recent.getByRole("button", { name: `Open ${rows[3].name}` }).click();
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", rows[3].content_url as string);
  await expect(page).toHaveURL(new RegExp(`/studio/${rows[3].id}$`));
  expect(unmatched).toEqual([]);
});

test("delete asks first, then the stage is empty and the strip no longer has it", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto(`/studio/${dragon.id}`);
  await page.getByRole("button", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect.poll(() => writes.filter((w) => w.method() === "DELETE").length).toBe(1);
  await expect(page.locator(".studio-stage img")).toHaveCount(0);
  await expect(page.locator(".recent-strip").getByRole("button", { name: `Open ${dragon.name}` }))
    .toHaveCount(0);
  await expect(page).toHaveURL(/\/studio$/);
});

test("with no provider configured the compose panel says where the key goes", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/artifacts/models", async (route: Route) => {
    await route.fulfill({ json: [
      { id: "flux-pro", provider: "bfl", label: "FLUX 1.1 pro", price_usd: 0.05, sizes: null,
        aspects: ["1:1"], custom_size: true, qualities: null, edits: false,
        configured: false, default: true },
    ] });
  });
  await page.goto("/studio");
  await expect(page.getByRole("link", { name: /Secrets/ })).toHaveAttribute("href", "/secrets");
  await expect(page.getByRole("button", { name: /^Generate/ })).toBeDisabled();
});

test("the two columns stack on a phone", async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/studio");
  const compose = await page.locator(".studio-compose").boundingBox();
  const stage = await page.locator(".studio-stage").boundingBox();
  expect(stage!.y).toBeGreaterThanOrEqual(compose!.y + compose!.height);
  expect(stage!.width).toBeLessThanOrEqual(390);

  await page.setViewportSize({ width: 1280, height: 800 });
  const wide = await page.locator(".studio-compose").boundingBox();
  const beside = await page.locator(".studio-stage").boundingBox();
  expect(beside!.x).toBeGreaterThanOrEqual(wide!.x + wide!.width);

  // A shared link lands on the picture: with a result up, the stacked stage
  // comes first; the empty stage stays below the form.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/studio/${dragon.id}`);
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", dragon.content_url);
  const form = await page.locator(".studio-compose").boundingBox();
  const hero = await page.locator(".studio-stage").boundingBox();
  expect(hero!.y + hero!.height).toBeLessThanOrEqual(form!.y);
});

test("a generation that lands after the reader has left does not pull them back", async ({ page }) => {
  await mockApi(page);
  let release: () => void = () => {};
  const held = new Promise<void>((r) => { release = r; });
  await page.route("**/api/artifacts/generate", async (route: Route) => {
    await held;
    await route.fulfill({ status: 201, json: fresh });
  });
  await page.goto("/studio");
  await page.getByLabel("Prompt").fill("A slow picture");
  await page.getByRole("button", { name: /^Generate/ }).click();
  await expect(page.getByRole("button", { name: /^Generating/ })).toBeDisabled();

  await page.getByRole("link", { name: "Tickets", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tickets" })).toBeVisible();
  release();
  // The answer arrives; the reader stays where they went.
  await page.waitForTimeout(500);
  await expect(page).toHaveURL(/\/tickets$/);
  await expect(page.getByRole("heading", { level: 1, name: "Tickets" })).toBeVisible();
});

test("two drops in one tick upload once", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/studio");
  await page.locator(".reference-strip .drop-zone").evaluate((el, png) => {
    const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
    for (const name of ["one.png", "two.png"]) {
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], name, { type: "image/png" }));
      el.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }
  }, PNG.toString("base64"));
  await expect(page.locator(".reference-strip img")).toHaveCount(1);
  await page.waitForTimeout(300);
  expect(writes.filter((w) => new URL(w.url()).pathname === "/api/artifacts")).toHaveLength(1);
});

test("a file on the stage is its glyph and a download, and cannot be a reference", async ({ page }) => {
  const csv = ARTIFACTS.file;
  await mockApi(page);
  await page.goto(`/studio/${csv.id}`);
  const stage = page.locator(".studio-stage");
  await expect(stage).toContainText(csv.name);
  await expect(stage.locator("img")).toHaveCount(0);
  await expect(stage.getByRole("link", { name: "Download" })).toHaveAttribute("href", csv.content_url);
  await expect(stage.getByRole("button", { name: "Iterate" })).toHaveCount(0);
  await expect(stage.getByRole("button", { name: "Mark up" })).toHaveCount(0);
  await expect(stage.getByRole("button", { name: "Use as agent image" })).toHaveCount(0);

  await page.goto(`/studio?ref=${csv.id}`);
  await expect(page.getByText(/only images can be references/i)).toBeVisible();
  await expect(page.locator(".reference-strip img")).toHaveCount(0);
  await expect(page).toHaveURL(/\/studio$/);
});

test("the stage empties when its picture is deleted elsewhere", async ({ page }) => {
  await mockApi(page);
  const feed = await artifactFeed(page);
  await page.goto(`/studio/${dragon.id}`);
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", dragon.content_url);
  feed.deleted(dragon);
  await expect(page.locator(".studio-stage img")).toHaveCount(0, { timeout: 15000 });
  await expect(page.locator(".studio-stage")).toContainText(/was deleted/i);
  await expect(page).toHaveURL(/\/studio$/);
});

// --- Markup (T13) ------------------------------------------------------------
// A canvas the size of the picture, drawn on by pointer, flattened to a PNG
// and posted as a derived artifact. The fixtures' bytes are a 1×1 PNG, which
// is no canvas to draw on, so the picture under test is served as a white
// 64×32 — every pixel is known, and a stroke is the only thing that is not
// white. Coordinates in the tests are the picture's own pixels, mapped onto
// the screen through the canvas's box, which is how the component maps them
// back.
const W = 64, H = 32;

function crc32(buf: Buffer): number {
  let c = ~0;
  for (const b of buf) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

/** A white, opaque RGBA PNG of the given size. */
function whitePng(w: number, h: number): Buffer {
  const chunk = (type: string, data: Buffer) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, "latin1"), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const stride = w * 4 + 1;
  const raw = Buffer.alloc(stride * h, 255);
  for (let y = 0; y < h; y++) raw[y * stride] = 0;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** The width and height of the PNG inside a multipart body, off its IHDR. */
function pngSizeIn(body: Buffer): { width: number; height: number } {
  const at = body.indexOf(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  expect(at).toBeGreaterThanOrEqual(0);
  return { width: body.readUInt32BE(at + 16), height: body.readUInt32BE(at + 20) };
}

/** The text of one form field in a multipart body. */
function fieldIn(body: Buffer, name: string): string {
  const text = body.toString("latin1");
  const m = new RegExp(`name="${name}"\\r\\n\\r\\n([\\s\\S]*?)\\r\\n--`).exec(text);
  expect(m, `multipart field ${name}`).not.toBeNull();
  return m![1];
}

/** A toolbar button by its whole name: the strip below the stage has an
 * "Open logo-crop.png" that "Crop" would otherwise match. */
function tool(page: Page, name: string) {
  return page.getByRole("toolbar", { name: "Markup tools" }).getByRole("button", { name, exact: true });
}

async function enterMarkup(page: Page) {
  await mockApi(page);
  await page.route(`**/api/artifacts/${dragon.id}/content`, async (route: Route) => {
    await route.fulfill({ body: whitePng(W, H), contentType: "image/png" });
  });
  await page.goto(`/studio/${dragon.id}`);
  await page.getByRole("button", { name: "Mark up" }).click();
  const canvas = page.getByRole("img", { name: /^Markup canvas/ });
  await expect(canvas).toBeVisible();
  return canvas;
}

/** Pointer positions for the picture's own pixels: the centre of each. */
async function pixel(page: Page, x: number, y: number): Promise<{ x: number; y: number }> {
  const box = (await page.getByRole("img", { name: /^Markup canvas/ }).boundingBox())!;
  return { x: box.x + (x + 0.5) * box.width / W, y: box.y + (y + 0.5) * box.height / H };
}

async function drag(page: Page, from: [number, number], to: [number, number]) {
  const a = await pixel(page, ...from);
  const b = await pixel(page, ...to);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(b.x, b.y, { steps: 4 });
  await page.mouse.up();
}

/** How many pixels are no longer the white picture. */
function painted(page: Page): Promise<number> {
  return page.evaluate(() => {
    const c = document.querySelector<HTMLCanvasElement>("canvas.markup-canvas")!;
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] !== 255 || d[i + 1] !== 255 || d[i + 2] !== 255) n++;
    return n;
  });
}

function rgba(page: Page, x: number, y: number): Promise<number[]> {
  return page.evaluate(([x, y]) => {
    const c = document.querySelector<HTMLCanvasElement>("canvas.markup-canvas")!;
    return Array.from(c.getContext("2d")!.getImageData(x, y, 1, 1).data);
  }, [x, y]);
}

test("markup: a rectangle drawn by pointer lands on the canvas, and Undo takes it off", async ({ page }) => {
  const canvas = await enterMarkup(page);
  await expect(canvas).toHaveAttribute("aria-label", /pen/i);
  await expect(tool(page, "Pen")).toHaveAttribute("aria-pressed", "true");
  // Entering the mode puts the keyboard on its first tool, and with nothing
  // drawn there is nothing to keep.
  await expect(tool(page, "Pen")).toBeFocused();
  await expect(tool(page, "Undo")).toBeDisabled();
  await expect(tool(page, "Save")).toBeDisabled();

  // A click is not a shape: an arrow needs a direction.
  await tool(page, "Arrow").click();
  const at = await pixel(page, 30, 16);
  await page.mouse.click(at.x, at.y);
  expect(await painted(page)).toBe(0);
  await expect(tool(page, "Undo")).toBeDisabled();

  await tool(page, "Rectangle").click();
  await expect(canvas).toHaveAttribute("aria-label", /rectangle/i);
  await drag(page, [8, 8], [40, 24]);
  // The left edge is painted; the inside is still the picture.
  expect(await rgba(page, 8, 16)).not.toEqual([255, 255, 255, 255]);
  expect(await rgba(page, 20, 16)).toEqual([255, 255, 255, 255]);
  await expect(tool(page, "Save")).toBeEnabled();

  await tool(page, "Undo").click();
  expect(await rgba(page, 8, 16)).toEqual([255, 255, 255, 255]);
  await expect(tool(page, "Undo")).toBeDisabled();

  // Leaving hands the keyboard back to the button that opened the mode.
  await tool(page, "Cancel").click();
  await expect(canvas).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Mark up" })).toBeFocused();
});

test("markup: Save posts the flattened PNG as a derived artifact, and it takes the stage", async ({ page }) => {
  const bodies: Buffer[] = [];
  await enterMarkup(page);
  await page.route("**/api/artifacts", async (route: Route) => {
    if (route.request().method() !== "POST") { await route.fallback(); return; }
    bodies.push(route.request().postDataBuffer()!);
    await route.fulfill({ status: 201, json: ARTIFACTS.uploaded });
  });
  await tool(page, "Arrow").click();
  await drag(page, [4, 4], [50, 20]);
  await tool(page, "Save").click();

  await expect.poll(() => bodies.length).toBe(1);
  const body = bodies[0];
  expect(fieldIn(body, "name")).toBe("gpt-image-1-a-dragon-over-the-harbour-markup.png");
  expect(JSON.parse(fieldIn(body, "meta"))).toEqual({ parent_id: dragon.id, operation: "markup" });
  expect(pngSizeIn(body)).toEqual({ width: W, height: H });
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", ARTIFACTS.uploaded.content_url);
  await expect(page).toHaveURL(new RegExp(`/studio/${ARTIFACTS.uploaded.id}$`));
  await expect(page.locator("canvas.markup-canvas")).toHaveCount(0);
});

test("markup: a crop exports only the region, and a crop alone is a crop", async ({ page }) => {
  const bodies: Buffer[] = [];
  await enterMarkup(page);
  await page.route("**/api/artifacts", async (route: Route) => {
    if (route.request().method() !== "POST") { await route.fallback(); return; }
    bodies.push(route.request().postDataBuffer()!);
    await route.fulfill({ status: 201, json: ARTIFACTS.uploaded });
  });
  await tool(page, "Crop").click();
  await drag(page, [8, 8], [40, 24]);
  await tool(page, "Save").click();
  await expect.poll(() => bodies.length).toBe(1);
  expect(pngSizeIn(bodies[0])).toEqual({ width: 32, height: 16 });
  expect(JSON.parse(fieldIn(bodies[0], "meta"))).toEqual({ parent_id: dragon.id, operation: "crop" });
});

test("markup: text is typed in place and drawn on Enter; Esc leaves without a POST", async ({ page }) => {
  const writes = captureWrites(page);
  const canvas = await enterMarkup(page);
  await tool(page, "Text").click();
  const at = await pixel(page, 6, 20);
  await page.mouse.click(at.x, at.y);
  const input = page.getByLabel("Text to draw");
  await expect(input).toBeFocused();
  await input.fill("hi");
  await input.press("Enter");
  await expect(input).toHaveCount(0);
  await expect(tool(page, "Undo")).toBeEnabled();
  // Something is painted at the baseline the click set.
  expect(await painted(page)).toBeGreaterThan(0);

  await page.keyboard.press("Escape");
  await expect(canvas).toHaveCount(0);
  await expect(page.locator(".studio-stage img")).toHaveAttribute("src", dragon.content_url);
  expect(writes.filter((w) => w.method() === "POST")).toEqual([]);
});
