import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("connection card opens a dated guide and writes a new Discord token before registering its identity", async ({ page }) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await mockApi(page);
  page.on("request", (request) => {
    if (request.method() === "GET" || !request.url().includes("/api/")
        || request.url().includes("/api/quota/refresh")) return;
    writes.push({ path: new URL(request.url()).pathname,
      body: JSON.parse(request.postData() || "{}") });
  });
  await page.goto("/secrets");
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  await page.getByRole("button", { name: /Discord chat identities/ }).click();
  await expect(page).toHaveURL(/\?connection=discord$/);
  await expect(page.locator(".connection-card")).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toContainText("Connections / Discord chat identities");
  await expect(page.getByText("Platform Discord bot")).toBeVisible();
  await expect(page.getByLabel("Discord bot token")).toHaveCount(0);
  await page.getByRole("button", { name: "+ Add account" }).click();
  await expect(page).toHaveURL(/\?connection=discord&add=1$/);
  await expect(page.getByRole("heading", { name: "Add Discord account" })).toBeVisible();
  await expect(page.getByText(/Setup guide checked 2026-09-29/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Discord Developer Portal" }))
    .toHaveAttribute("href", "https://discord.com/developers/applications");
  await expect(page.getByText("Platform Discord bot")).toHaveCount(0);

  await page.getByLabel("Discord account display name").fill("Family bot");
  await expect(page.getByText("discord-family", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Discord identity ID")).toHaveCount(0);
  await page.getByLabel("Discord bot token").fill("test-token");
  await page.getByRole("button", { name: "Save token and add account" }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes.map((write) => write.path)).toEqual([
    "/api/secrets/discord-family-bot", "/api/chat-identities",
  ]);
  expect(writes[0].body).toEqual({ data: { token: "test-token" } });
  expect(writes[1].body).toEqual({ id: "discord-family", display_name: "Family bot",
    secret_name: "discord-family-bot" });
  await expect(page).toHaveURL(/\?connection=discord$/);
  await expect(page.getByLabel("Discord bot token")).toHaveCount(0);
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Connections" }).click();
  await expect(page.locator(".connection-card")).toHaveCount(9);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Discord chat identities" })).toBeVisible();
});

test("Discord account ID is generated from the name, avoiding existing IDs", async ({ page }) => {
  const writes: string[] = [];
  await mockApi(page);
  page.on("request", (request) => {
    if (request.method() !== "GET" && request.url().includes("/api/chat-identities")) {
      writes.push(JSON.parse(request.postData() || "{}").id);
    }
  });
  await page.goto("/secrets?connection=discord&add=1");
  await page.getByLabel("Discord account display name").fill("Default");
  await expect(page.getByText("discord-default-2", { exact: true })).toBeVisible();
  await page.getByLabel("Discord account display name").fill("Kai");
  await expect(page.getByText("discord-kai", { exact: true })).toBeVisible();
  const token = page.getByLabel("Discord bot token");
  await expect(token).toHaveAttribute("type", "password");
  await token.fill("test-token");
  await page.getByRole("button", { name: "Save token and add account" }).click();
  await expect.poll(() => writes).toEqual(["discord-kai"]);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("cancel returns to the Discord inventory and clears the unsaved token", async ({ page }) => {
  await mockApi(page);
  await page.goto("/secrets?connection=discord");
  await page.getByRole("button", { name: "+ Add account" }).click();
  await page.getByLabel("Discord account display name").fill("Kai");
  await page.getByLabel("Discord bot token").fill("unsaved-token");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page).toHaveURL(/\?connection=discord$/);
  await expect(page.getByText("Platform Discord bot")).toBeVisible();
  await page.getByRole("button", { name: "+ Add account" }).click();
  await expect(page.getByLabel("Discord bot token")).toHaveValue("");
});

test("only personas select an owned Discord account", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/health-monitor");
  await expect(page.getByLabel("Owned Discord account")).toHaveCount(0);
  await page.getByLabel("Type", { exact: true }).selectOption("persona");
  const picker = page.getByLabel("Owned Discord account");
  await expect(picker).toHaveValue("");
  await expect(picker.locator("option")).toHaveCount(2);
  await picker.selectOption("discord-default");
  await expect(picker).toHaveValue("discord-default");
});

test("connection cards use the available width and step down on smaller screens", async ({ page }) => {
  await mockApi(page);
  for (const [width, columns] of [[2048, 3], [1280, 2], [600, 1]]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/secrets");
    const cards = page.locator(".connection-card");
    await expect(cards).toHaveCount(9);
    const leftEdges = await cards.evaluateAll((items) => items.slice(0, 6)
      .map((item) => Math.round(item.getBoundingClientRect().left)));
    expect(new Set(leftEdges).size).toBe(columns);
  }
});

test("Discord inventory offers Edit, Verify, Delete with a separate edit form", async ({ page }) => {
  await mockApi(page);
  const edits: unknown[] = [];
  await page.route("**/api/chat-identities/discord-default", async (route) => {
    edits.push(route.request().postDataJSON());
    await route.fulfill({ json: { detail: "Account updated." } });
  });
  await page.route("**/api/chat-identities/discord-default/verify", (route) => route.fulfill({
    json: { checks: [{ ok: true, detail: "Discord authenticated: Example." },
      { ok: false, detail: "Enable Message Content Intent." }] },
  }));
  await page.goto("/secrets?connection=discord");
  await expect(page.getByRole("button", { name: /Pause|Resume|Set token/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Verify", exact: true }).click();
  await expect(page.getByLabel("Verification for Platform Discord bot")).toContainText("Enable Message Content Intent.");
  await expect(page.getByRole("status")).toContainText("Setup needs attention");
  await expect(page.getByRole("link", { name: "Open Discord applications" }))
    .toHaveAttribute("href", "https://discord.com/developers/applications");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page).toHaveURL(/edit=discord-default/);
  await expect(page.getByLabel("Display name", { exact: true })).toHaveValue("Platform Discord bot");
  await expect(page.getByLabel("Replacement bot token")).toHaveAttribute("type", "password");
  await page.getByLabel("Display name", { exact: true }).fill("My bot");
  await page.getByRole("button", { name: "Save account" }).click();
  await expect.poll(() => edits).toEqual([{ display_name: "My bot", owner_agent: null }]);
  await expect(page).toHaveURL(/\?connection=discord$/);
});

test("Discord Delete asks for confirmation and preserves cancellation", async ({ page }) => {
  await mockApi(page);
  let deletes = 0;
  await page.route("**/api/chat-identities/discord-default", async (route) => {
    if (route.request().method() === "DELETE") deletes++;
    await route.fulfill({ json: { detail: "Account deleted." } });
  });
  await page.goto("/secrets?connection=discord");
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Chat history stays");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(deletes).toBe(0);
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Delete account", exact: true }).click();
  await expect.poll(() => deletes).toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
