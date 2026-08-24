import { expect, test, type Page, type Request } from "@playwright/test";
import { mockApi } from "./mock-api";

// The DB-first agent editor (docs/design/15): edits go straight to the row, so
// what matters is the WRITE each control produces. These tests capture the
// request the UI sends — the backend suite covers what the API does with it.

function captureWrites(page: Page): Request[] {
  const writes: Request[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/")) writes.push(r);
  });
  return writes;
}

test("saving the editor PUTs the whole definition, grants included", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByLabel("Description").fill("Watches everything.");
  await page.getByLabel("Timeout (seconds)").fill("900");
  // A grant lives in the same draft as the config fields.
  await page.getByRole("checkbox", { name: "Todo" }).check();

  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();

  const put = writes.find((w) => w.method() === "PUT");
  expect(put, "a PUT to the agent row").toBeTruthy();
  expect(new URL(put!.url()).pathname).toBe("/api/agents/health-monitor");
  const body = JSON.parse(put!.postData() ?? "{}");
  expect(body.description).toBe("Watches everything.");
  expect(body.timeout_seconds).toBe(900);
  expect(body.harness_tools).toContain("TodoWrite");
  expect(body.harness_tools).toContain("WebSearch");     // untouched grant survives
  expect(body.platform_tools).toEqual(["mcp__platform__metrics"]);
  expect(body.prompt).toContain("You watch health.");
  expect(body.entrypoints.crons[0].schedule).toBe("*/15 * * * *");
  // No PR/pending language anywhere on the page.
  await expect(page.locator("body")).not.toContainText(/pull request|pending change|opens PR/i);
});

test("entrypoints edit round-trips into the saved definition", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add webhook" }).click();
  await page.getByLabel("Webhook path").fill("deploy-done");
  await expect(page.locator("body")).toContainText("/api/webhooks/deploy-done");

  await page.getByRole("button", { name: "Save changes" }).first().click();
  const put = writes.find((w) => w.method() === "PUT");
  expect(JSON.parse(put!.postData() ?? "{}").entrypoints.webhooks).toEqual([{ path: "deploy-done" }]);
});

test("version history lists the change log and rolls back after confirming", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor?tab=history");

  await expect(page.locator("body")).toContainText("import");
  await page.getByRole("button", { name: "View" }).first().click();
  await expect(page.locator("pre.agent-md")).toContainText('"harness_tools"');

  await page.getByRole("button", { name: "Roll back" }).first().click();
  await page.getByRole("button", { name: "Roll back" }).last().click();   // confirm dialog

  const post = writes.find((w) => w.method() === "POST");
  expect(new URL(post!.url()).pathname).toBe("/api/agents/health-monitor/rollback/1");
});

test("deleting an agent is confirm-gated and DELETEs the row", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  // health-monitor is a system agent — no delete offered.
  await page.goto("/agents/health-monitor");
  await expect(page.getByRole("button", { name: "Delete agent" })).toHaveCount(0);

  // The listing's `pai` row is not a system agent; serve it as the detail.
  await page.route("**/api/agents/pai", async (route) => {
    if (route.request().method() !== "GET") { await route.fulfill({ json: { ok: true } }); return; }
    await route.fulfill({ json: {
      name: "pai", prompt: "You are pai.", description: "Conversational assistant.", model: "",
      role: "operator", system: false, can_invoke: false, concurrency: 1, timeout_seconds: 1800,
      result_topic: "", transcript_retention_days: null, harness_tools: [], platform_tools: [],
      skills: [], secrets: [], entrypoints: { crons: [], webhooks: [], topics: [], timezone: "" },
      enabled: true,
    } });
  });
  await page.goto("/agents/pai");
  await page.getByRole("button", { name: "Delete agent" }).click();
  await page.getByRole("button", { name: "Delete agent" }).last().click();   // confirm dialog

  const del = writes.find((w) => w.method() === "DELETE");
  expect(new URL(del!.url()).pathname).toBe("/api/agents/pai");
  await expect(page.getByRole("heading", { level: 1, name: "Agents" })).toBeVisible();
});

test("the wizard POSTs a full definition — no PR flow", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/new");

  await expect(page.getByRole("button", { name: /Create agent/ })).toBeDisabled();
  await page.getByLabel("Name").fill("scratch-agent");
  await page.getByLabel("Description").fill("A scratch agent.");
  await page.getByLabel("Agent prompt").fill("You are a scratch agent.");
  await page.getByRole("checkbox", { name: "news-lookup" }).check();
  await page.getByRole("button", { name: "Create agent" }).click();

  const post = writes.find((w) => w.method() === "POST");
  expect(new URL(post!.url()).pathname).toBe("/api/agents");
  const body = JSON.parse(post!.postData() ?? "{}");
  expect(body.name).toBe("scratch-agent");
  expect(body.description).toBe("A scratch agent.");
  expect(body.prompt).toBe("You are a scratch agent.");
  expect(body.skills).toEqual(["news-lookup"]);
  expect(body.role).toBe("operator");
  expect(body.enabled).toBe(true);
});
