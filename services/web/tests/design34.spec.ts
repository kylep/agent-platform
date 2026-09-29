import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("external archive has no message or reaction controls", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rx1");
  await expect(page.getByText("read-only external archive", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add reaction" })).toHaveCount(0);
});

test("code-owned definitions lock content and grants while preserving operational edits", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/agents/health-monitor", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: {
      name: "health-monitor", agent_type: "worker", runtime: "claude", model: "", role: "operator",
      prompt: "Monitor health", description: "Health", system: true,
      system_source: "platform.health", system_revision: "34", external_observer: false,
      responds_to_all: false, can_invoke: false, concurrency: 1, timeout_seconds: 600,
      result_topic: "", transcript_retention_days: null, enabled: true,
      harness_tools: [], platform_tools: [], skills: [], secrets: [], discord_identity_id: null,
      entrypoints: { crons: [], webhooks: [], topics: [], timezone: "" },
      push_path_globs: [], may_delete_tests: false, quota_5h_max_pct: 80, quota_7d_max_pct: 50,
    } });
  });
  await page.goto("/agents/health-monitor");
  await expect(page.getByText(/Code-owned definition:/)).toContainText("platform.health");
  await expect(page.getByLabel("Agent prompt")).toBeDisabled();
  await expect(page.getByLabel("Type", { exact: true })).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: /Read all external mirrors/ })).toBeDisabled();
  await expect(page.getByLabel("Run time limit (seconds)")).toBeEnabled();
  await expect(page.getByLabel("Model", { exact: true })).toBeEnabled();
});

test("account unassignment sends explicit null ownership", async ({ page }) => {
  await mockApi(page);
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/chat-identities/discord-default", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    body = route.request().postDataJSON();
    await route.fulfill({ json: { detail: "Saved" } });
  });
  await page.goto("/secrets?connection=discord&edit=discord-default");
  await page.getByLabel("Owning persona").selectOption("");
  await page.getByRole("button", { name: "Save account", exact: true }).click();
  await expect.poll(() => body?.owner_agent).toBe(null);
});


test("owned Discord tool and connector secrets never reappear as unknown grants", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/chat-identities", (route) => route.fulfill({ json: [{ id: "discord-default", connector: "discord", display_name: "Pai", status: "active", configured: true, secret_refs: { bot_token: { secret: "custom-bot-credential", key: "token" } } }] }));
  await page.route("**/api/agents/pai", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const def = { name: "pai", prompt: "Chat", runtime: "codex", role: "operator", enabled: true, system: false };
    await route.fulfill({ json: { ...def, agent_type: "persona",
      platform_tools: ["mcp__platform__discord", "mcp__platform__discord_chat", "old-custom-tool"],
      secrets: ["discord-bot", "discord-webhook", "custom-bot-credential", "old-custom-secret"],
      discord_identity_id: "discord-default" } });
  });
  await page.goto("/agents/pai");
  await expect(page.getByLabel("Owned Discord account")).toBeVisible();
  for (const name of ["mcp__platform__discord", "mcp__platform__discord_chat", "discord-bot", "discord-webhook", "custom-bot-credential"]) {
    await expect(page.getByRole("checkbox", { name: new RegExp(name) })).toHaveCount(0);
  }
  await expect(page.getByRole("checkbox", { name: /old-custom-tool/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /old-custom-secret/ })).toBeVisible();
});


test("Relay warns before oversized sends and counts emoji as characters", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rc1");
  const box = page.getByRole("textbox", { name: "Message", exact: true });
  await box.fill("x".repeat(64001));
  await expect(page.getByRole("alert")).toContainText("64,001 characters; the limit is 64,000");
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
  await box.press("Enter");
  await expect(box).toHaveValue("x".repeat(64001));
  await box.fill("🌸".repeat(64000));
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
});
