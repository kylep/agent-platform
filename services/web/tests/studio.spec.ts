import { expect, test, type Page, type Request, type Route } from "@playwright/test";
import { AGENT_NAMES, ARTIFACTS, mockApi, studioRecent } from "./mock-api";

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
  await expect(page.getByRole("button", { name: "Mark up" })).toBeDisabled();

  // Iterate: the result becomes a reference, the prompt is kept and focused,
  // the stage is empty again.
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
});
