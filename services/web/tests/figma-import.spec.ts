import { test, expect } from "@playwright/test";
import { mockApi } from "./mock-api";

const job = { id: "import-1", slug: "desk-buddy", state: "queued", pages: { "1:1": { state: "pending" } }, report: null, error: null };

test("admin starts import, sees progress and resumes after reload", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/ui-designer/docs", (r) => r.fulfill({ json: { docs: [] } }));
  let state = "running";
  await page.route("**/api/ui-designer/imports", async (r) => {
    expect(r.request().postDataJSON()).toEqual({ file_key: "ABC123", slug: "desk-buddy", title: "Desk Buddy", pages: ["1:1", "2:1"] });
    await r.fulfill({ status: 202, json: job });
  });
  await page.route("**/api/ui-designer/imports/import-1", (r) => r.fulfill({ json: { ...job, state, pages: { "1:1": { state: state === "done" ? "done" : "pending" } }, report: state === "done" ? { pages_imported: 1, failed_pages: [], options: { title: "PRIVATE OPTIONS" }, notification_pending: true, next_attempt_at: "PRIVATE RETRY", references: [{ node_id: "1:2", artifact_id: "ref-artifact" }] } : null } }));
  await page.goto("/ui-designer");
  await page.getByLabel("Figma file URL or key").fill("https://www.figma.com/design/ABC123/Desk?node-id=1-1");
  await page.getByLabel("Document slug").fill("desk-buddy");
  await page.getByLabel("Title (optional)").fill("Desk Buddy");
  await page.getByLabel("Page IDs").fill("1:1, 2:1");
  await page.getByRole("button", { name: "Import from Figma", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Import in progress" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("0 of 1 pages imported.")).toBeVisible();
  state = "done";
  await expect(page.getByRole("link", { name: "Open imported document" })).toHaveAttribute("href", "/ui-designer/desk-buddy");
  await page.getByText("Import report", { exact: true }).click();
  await expect(page.getByText("Pages imported: 1")).toBeVisible();
  await expect(page.getByRole("link", { name: "View Figma reference for 1:2" })).toHaveAttribute("href", "/artifacts/ref-artifact");
  await expect(page.getByText("PRIVATE OPTIONS")).toHaveCount(0);
  await expect(page.getByText("PRIVATE RETRY")).toHaveCount(0);
  await expect(page.getByText("Notification pending")).toHaveCount(0);
});

test("invalid input, start failure and failed import are actionable", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/ui-designer/docs", (r) => r.fulfill({ json: { docs: [] } }));
  await page.route("**/api/ui-designer/imports", (r) => r.fulfill({ status: 409, json: { detail: "Document slug already exists." } }));
  await page.goto("/ui-designer");
  await page.getByLabel("Figma file URL or key").fill("https://example.com/design/ABC/test");
  await page.getByRole("button", { name: "Import from Figma", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Enter a Figma file URL or file key.");
  await page.getByLabel("Figma file URL or key").fill("ABC");
  await page.getByRole("button", { name: "Import from Figma", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Document slug already exists.");
  await page.evaluate(() => localStorage.setItem("ui-designer-figma-import", "import-1"));
  await page.route("**/api/ui-designer/imports/import-1", (r) => r.fulfill({ json: { ...job, state: "failed", error: "Figma connection is unavailable." } }));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Import failed" })).toBeVisible();
  await expect(page.getByText("Figma connection is unavailable.")).toBeVisible();
  await page.getByRole("button", { name: "Dismiss status" }).click();
  await expect(page.getByRole("button", { name: "Import from Figma", exact: true })).toBeEnabled();
});

test("non-admin cannot see import controls or poll saved admin jobs", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/whoami", (r) => r.fulfill({ json: { role: "reader" } }));
  await page.route("**/api/ui-designer/docs", (r) => r.fulfill({ json: { docs: [] } }));
  await page.addInitScript(() => localStorage.setItem("ui-designer-figma-import", "private-job"));
  let polled = false;
  await page.route("**/api/ui-designer/imports/**", (r) => { polled = true; return r.fulfill({ status: 403 }); });
  await page.goto("/ui-designer");
  await expect(page.getByText("No documents yet.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Import from Figma" })).toHaveCount(0);
  expect(polled).toBe(false);
});


test("default document slug handles long Figma keys with underscores", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/ui-designer/docs", (r) => r.fulfill({ json: { docs: [] } }));
  const key = "A_".repeat(50);
  let submitted = false;
  await page.route("**/api/ui-designer/imports", async (r) => {
    expect(r.request().postDataJSON()).toEqual({ file_key: key, slug: `figma-${key.toLowerCase().replaceAll("_", "-")}`.slice(0, 64) });
    submitted = true;
    await r.fulfill({ status: 202, json: { ...job, state: "failed" } });
  });
  await page.route("**/api/ui-designer/imports/import-1", (r) => r.fulfill({ json: { ...job, state: "failed" } }));
  await page.goto("/ui-designer");
  await page.getByLabel("Figma file URL or key").fill(key);
  await page.getByRole("button", { name: "Import from Figma", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Import failed" })).toBeVisible();
  expect(submitted).toBe(true);
});
