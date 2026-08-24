import { expect, test, type Page, type Request } from "@playwright/test";
import { mockApi } from "./mock-api";

// Per-webhook shared-secret auth (docs/design/16). The whole point of the
// design is where the secret goes: onto its own write-only endpoint, after the
// definition, and never into the definition — which is snapshotted into the
// change log on every write. These tests watch the wire for exactly that.

const SECRET = "correct-horse-battery-staple";
const HEADER_TIP = "X-AP-Webhook-Secret: <your secret>";

function captureWrites(page: Page): Request[] {
  const writes: Request[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/")) writes.push(r);
  });
  return writes;
}

// An agent whose one webhook already has a secret — the state the editor can
// only ever be told about, never read.
const hooked = {
  name: "hooked", prompt: "You are hooked.", description: "Has a live webhook.",
  model: "", role: "operator", system: false, can_invoke: false, concurrency: 1,
  timeout_seconds: 1800, result_topic: "", transcript_retention_days: null,
  harness_tools: [], platform_tools: [], skills: [], secrets: [], enabled: true,
  entrypoints: { crons: [], topics: [], timezone: "",
                 webhooks: [{ path: "gh-push", auth: "secret", secret_set: true }] },
};

// The real API answers a definition write with the row it stored, and the
// editor adopts that answer. The shared mock replays its GET fixture for every
// method, so the write has to echo instead — otherwise the editor "adopts" a
// row that predates the edit.
async function echoDefWrites(page: Page, name: string) {
  await page.route(`**/api/agents/${name}`, async (route) => {
    if (route.request().method() === "GET") { await route.fallback(); return; }
    await route.fulfill({ json: JSON.parse(route.request().postData() ?? "{}") });
  });
}

async function serveHooked(page: Page) {
  await page.route("**/api/agents/hooked", async (route) => {
    if (route.request().method() !== "GET") { await route.fulfill({ json: { ok: true } }); return; }
    await route.fulfill({ json: hooked });
  });
}

test("choosing Secret reveals a masked field the eye unmasks", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  // None is the default and asks for nothing extra.
  await expect(page.getByLabel("Webhook auth")).toHaveValue("none");
  await expect(page.getByLabel("Webhook secret")).toHaveCount(0);

  await page.getByLabel("Webhook auth").selectOption("secret");
  const field = page.getByLabel("Webhook secret");
  await expect(field).toHaveAttribute("type", "password");
  await field.fill(SECRET);
  await expect(field).toHaveAttribute("type", "password");   // still masked while typing

  await page.getByRole("button", { name: "Show secret" }).click();
  await expect(field).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Hide secret" }).click();
  await expect(field).toHaveAttribute("type", "password");

  // The row prints the literal line an external caller has to send.
  await expect(page.locator(`[title="${HEADER_TIP}"]`)).toHaveCount(1);

  // Generate hands over a high-entropy value and unmasks it, because a secret
  // nobody can read is a secret nobody can give to the caller.
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(field).toHaveAttribute("type", "text");
  const generated = await field.inputValue();
  expect(generated).not.toBe(SECRET);
  expect(generated.length).toBeGreaterThanOrEqual(32);
});

test("an out-of-bounds secret blocks the save client-side", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  await page.getByLabel("Webhook auth").selectOption("secret");
  await page.getByLabel("Webhook secret").fill("short");

  await expect(page.getByText("At least 16 characters.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" }).first()).toBeDisabled();

  // The ceiling is enforced here too, and for a sharper reason: the server's
  // rejection is a pydantic 422 that quotes the offending input back, and this
  // page renders API error text. An overlong secret must not make that trip.
  await page.getByLabel("Webhook secret").fill("x".repeat(513));
  await expect(page.getByText("At most 512 characters.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" }).first()).toBeDisabled();

  await page.getByLabel("Webhook secret").fill(SECRET);
  await expect(page.getByRole("button", { name: "Save changes" }).first()).toBeEnabled();
  // Nothing was ever sent while the value was out of bounds.
  expect(writes).toEqual([]);
});

test("saving PUTs the definition first, then the secret on its own call", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await echoDefWrites(page, "health-monitor");
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  await page.getByLabel("Webhook auth").selectOption("secret");
  await page.getByLabel("Webhook secret").fill(SECRET);
  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();

  const defPut = writes.findIndex((w) => new URL(w.url()).pathname === "/api/agents/health-monitor");
  const secretPut = writes.findIndex(
    (w) => new URL(w.url()).pathname === "/api/agents/health-monitor/webhooks/deploy-done/secret");
  expect(defPut, "the definition write").toBeGreaterThanOrEqual(0);
  expect(secretPut, "the secret write").toBeGreaterThanOrEqual(0);
  // Order is load-bearing: the secret endpoint 404s until the path is declared.
  expect(defPut).toBeLessThan(secretPut);

  // The definition carries the MODE and nothing else — no secret rides along
  // into `agent_versions`.
  const body = JSON.parse(writes[defPut].postData() ?? "{}");
  expect(body.entrypoints.webhooks).toEqual([{ path: "deploy-done", auth: "secret", secret_set: false }]);
  expect(writes[defPut].postData() ?? "").not.toContain(SECRET);

  expect(writes[secretPut].method()).toBe("PUT");
  expect(JSON.parse(writes[secretPut].postData() ?? "{}")).toEqual({ secret: SECRET });
});

test("the saved secret is never rendered back — the row reports state instead", async ({ page }) => {
  await mockApi(page);
  await echoDefWrites(page, "health-monitor");
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  await page.getByLabel("Webhook auth").selectOption("secret");
  await page.getByLabel("Webhook secret").fill(SECRET);
  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();

  // The field is gone the moment the write lands, replaced by state.
  await expect(page.getByLabel("Webhook secret")).toHaveCount(0);
  await expect(page.getByText("secret set")).toBeVisible();
  expect(await page.locator("body").innerText()).not.toContain(SECRET);
  expect(await page.locator("body").innerHTML()).not.toContain(SECRET);

  // And a fresh load of a row that HAS a secret shows the same thing: the API
  // only ever reports `secret_set`, so there is nothing to render.
  await serveHooked(page);
  await page.goto("/agents/hooked");
  await expect(page.getByLabel("Webhook auth")).toHaveValue("secret");
  await expect(page.getByLabel("Webhook secret")).toHaveCount(0);
  await expect(page.getByText("secret set")).toBeVisible();
});

test("rotate re-reveals an empty field and sends only the new secret", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await serveHooked(page);
  await page.goto("/agents/hooked");

  // Nothing to save until the secret is rotated: the row is unchanged.
  await expect(page.getByRole("button", { name: "Save changes" }).first()).toBeDisabled();

  await page.getByRole("button", { name: "rotate" }).click();
  const field = page.getByLabel("Webhook secret");
  await expect(field).toHaveValue("");                       // the old one is unreadable
  await expect(field).toHaveAttribute("type", "password");
  await field.fill(SECRET);

  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();

  // A rotation touches nothing in the row, so it appends no change-log
  // snapshot — the secret endpoint is the only write.
  expect(writes.map((w) => new URL(w.url()).pathname))
    .toEqual(["/api/agents/hooked/webhooks/gh-push/secret"]);
  expect(JSON.parse(writes[0].postData() ?? "{}")).toEqual({ secret: SECRET });
  await expect(page.getByLabel("Webhook secret")).toHaveCount(0);
  await expect(page.getByText("secret set")).toBeVisible();
});

test("a create whose secret write fails hands over the editor, not a dead form", async ({ page }) => {
  await mockApi(page);
  const created = {
    name: "scratch-agent", prompt: "You are scratch.", description: "A scratch agent.", model: "",
    role: "operator", system: false, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
    result_topic: "", transcript_retention_days: null, harness_tools: [], platform_tools: [],
    skills: [], secrets: [], enabled: true,
    // Declared, and fail-closed: the mode is stored, the secret is not.
    entrypoints: { crons: [], topics: [], timezone: "",
                   webhooks: [{ path: "deploy-done", auth: "secret", secret_set: false }] },
  };
  await page.route("**/api/agents", async (route) => {
    if (route.request().method() === "GET") { await route.fallback(); return; }
    await route.fulfill({ status: 201, json: created });
  });
  await page.route("**/api/agents/scratch-agent", (r) => r.fulfill({ json: created }));
  await page.route("**/api/agents/scratch-agent/webhooks/*/secret",
                   (r) => r.fulfill({ status: 500, json: { detail: "storage is down" } }));

  await page.goto("/agents/new");
  await page.getByLabel("Name").fill("scratch-agent");
  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  await page.getByLabel("Webhook auth").selectOption("secret");
  await page.getByLabel("Webhook secret").fill(SECRET);
  await page.getByRole("button", { name: "Create agent" }).click();

  // The agent exists, so the wizard must not be where the operator is left:
  // clicking Create again would collide with the row it just made.
  await expect(page).toHaveURL(/\/agents\/scratch-agent$/);
  await expect(page.getByRole("heading", { level: 1, name: "scratch-agent" })).toBeVisible();
  await expect(page.getByText(/was created, but its webhook secret could not be set/)).toBeVisible();

  // And the editor is a real recovery: the field is here, empty, on a webhook
  // the API reports as having no secret.
  await expect(page.getByLabel("Webhook secret")).toHaveValue("");
  expect(await page.locator("body").innerHTML()).not.toContain(SECRET);
});

test("the agents listing marks who has a webhook", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents");

  const cell = (agent: string) => page.locator("tr", { has: page.getByRole("link", { name: agent, exact: true }) })
    .locator("td").nth(3);
  await expect(cell("pai")).toHaveText("✓");
  await expect(cell("news")).toHaveText("—");
  await expect(cell("health-monitor")).toHaveText("—");   // system table too
});
