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
  await expect(page.getByText(/Setup guide checked 2026-09-26/)).toBeVisible();
  await expect(page.getByText("Platform Discord bot")).toBeVisible();

  await page.getByLabel("Discord account display name").fill("Family bot");
  await page.getByLabel("Discord identity ID").fill("discord-family");
  await page.getByLabel("Discord bot token").fill("test-token");
  await page.getByRole("button", { name: "Save token and add account" }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes.map((write) => write.path)).toEqual([
    "/api/secrets/discord-family-bot", "/api/chat-identities",
  ]);
  expect(writes[0].body).toEqual({ data: { token: "test-token" } });
  expect(writes[1].body).toEqual({ id: "discord-family", display_name: "Family bot",
    secret_name: "discord-family-bot" });
});

test("agent editor offers one Discord outbound account or none", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/health-monitor");
  const picker = page.getByLabel("Discord outbound account");
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
