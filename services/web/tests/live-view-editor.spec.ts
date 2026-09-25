import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";

test("page author can enable and revoke their own trusted action", async ({ page }) => {
  await mockApi(page);
  let enabled = false;
  const writes: string[] = [];
  const definition = {
    renderer: "typed/v1", title: "Running", reads: [],
    actions: [{ alias: "feedback", operation: "tickets.create@1", channel: "general" }],
    blocks: [{ kind: "action", label: "Report issue", action_alias: "feedback" }],
  };
  await page.route("**/api/live-views/example/draft", (route) => route.fulfill({ json: {
    id: "example", app_name: "running", slug: "overview", draft_revision: 1,
    published_version: 1, definition,
  } }));
  await page.route("**/api/live-views/example/versions", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/live-operations?eligible_only=true", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/live-operation-grants**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: [{ principal_id: "kyle", operation: "tickets.create@1", enabled }] });
      return;
    }
    expect(route.request().postDataJSON()).toEqual({
      app_name: "running", principal_id: "kyle", operation: "tickets.create@1",
    });
    writes.push(route.request().url());
    enabled = !route.request().url().endsWith("/revoke");
    await route.fulfill({ json: { enabled } });
  });
  await page.goto("/live-views/example/edit");
  const access = page.getByRole("heading", { name: "Action access" }).locator("..");
  await expect(access).toContainText("disabled");
  await access.getByRole("button", { name: "Enable for me" }).click();
  await expect(access).toContainText("enabled");
  await access.getByRole("button", { name: "Revoke" }).click();
  await expect.poll(() => writes.length).toBe(2);
  await expect(access).toContainText("disabled");
});
