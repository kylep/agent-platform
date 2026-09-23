import { expect, test, type Page, type Request } from "@playwright/test";
import { mockApi, runTail, workbenchFrames } from "./mock-api";

// The Workbench's face in the UI (docs/design/24): the four definition fields
// the editor writes, the ticket and auto-merge chips on /changes and the live
// row a publish frame brings, the publish card in a thread, and the run page's
// `workbench` frame. The backend suite covers what a publish DOES; these gate
// what a person sees of it.

function captureWrites(page: Page): Request[] {
  const writes: Request[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/")
        && !r.url().includes("/api/quota/refresh")) writes.push(r);
  });
  return writes;
}

test("the editor shows the four workbench fields and saves them", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("checkbox", { name: "Quota guard" }).check();
  await page.getByLabel("5-hour quota ceiling (%)").fill("60");
  await page.getByLabel("Weekly quota ceiling (%)").fill("30");
  // `dev` is an execution profile, and selecting it reveals its grants.
  await page.getByLabel("Execution profile").selectOption("dev");
  await expect(page.locator("body")).toContainText("holds no git credential");
  // One glob per line; blank lines and stray spaces never reach the row.
  await page.getByLabel("Auto-merge paths").fill("docs/**\n\n  services/web/**  \n");
  await page.getByRole("checkbox", { name: "Permit test-file deletion" }).check();

  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();

  const put = writes.find((w) => w.method() === "PUT");
  expect(put, "a PUT to the agent row").toBeTruthy();
  const body = JSON.parse(put!.postData() ?? "{}");
  expect(body.role).toBe("dev");
  expect(body.quota_5h_max_pct).toBe(60);
  expect(body.quota_7d_max_pct).toBe(30);
  expect(body.push_path_globs).toEqual(["docs/**", "services/web/**"]);
  expect(body.may_delete_tests).toBe(true);
});

test("/changes lists both prefixes with the ticket chip, the face and auto-merge",
     async ({ page }) => {
  await mockApi(page);
  await page.goto("/changes");

  const coder = page.locator("tr", { hasText: "coder/ops-5" });
  const qa = page.locator("tr", { hasText: "qa/ops-1" });
  await expect(coder.getByRole("link", { name: "OPS-5" })).toHaveAttribute("href", "/tickets/OPS-5");
  await expect(qa.getByRole("link", { name: "OPS-1" })).toHaveAttribute("href", "/tickets/OPS-1");
  await expect(coder.locator(".relay-face")).toBeVisible();
  await expect(coder).toContainText("engineer");
  await expect(coder).toContainText("auto-merge");
  await expect(qa).not.toContainText("auto-merge");
  // The platform-code row has none of it and still has its buttons.
  const skill = page.locator("tr", { hasText: "skill: release-review" });
  await expect(skill.getByRole("link", { name: /^OPS-/ })).toHaveCount(0);
  await expect(skill.getByRole("button", { name: "Accept" })).toBeVisible();
  await expect(skill.getByRole("button", { name: "Discard" })).toBeVisible();
});

test("a published frame on the workbench stream refreshes the rows", async ({ page }) => {
  await mockApi(page);
  let lists = 0;
  await page.route((url) => url.pathname === "/api/pull-requests", async (route) => {
    lists += 1;
    const rows = [{ number: 12, title: "Edit release-review: skill body",
                    url: "https://github.com/x/y/pull/12", branch: "coder/skill-release-review",
                    author: "pericakai[bot]", created_at: new Date().toISOString(),
                    ticket_key: null, agent: null, auto_merge: null }];
    if (lists > 1) {
      rows.push({ number: 15, title: "OPS-2: quieter lag alert",
                  url: "https://github.com/x/y/pull/15", branch: "coder/ops-2",
                  author: "pericakai[bot]", created_at: new Date().toISOString(),
                  ticket_key: "OPS-2", agent: "engineer", auto_merge: true });
    }
    await route.fulfill({ json: rows });
  });
  // The stream answers once with a frame, then as an empty stream: the page
  // must not depend on the reconnect for the row.
  let sent = false;
  await page.route((url) => url.pathname === "/api/workbench/events", async (route) => {
    const frame = sent ? "" : `event: workbench\ndata: ${JSON.stringify({
      event: "published", agent: "engineer", run_id: "r-eng-1", ticket_key: "OPS-2",
      branch: "coder/ops-2", pr: { number: 15, url: "https://github.com/x/y/pull/15" },
      paths: ["a.py"], tests_removed: [], verify: null, reason: null })}\n\n`;
    sent = true;
    await route.fulfill({ status: 200, contentType: "text/event-stream",
                          headers: { "Cache-Control": "no-cache" }, body: frame || ": heartbeat\n\n" });
  });
  await page.goto("/changes");
  // Well inside the 15 s poll, so only the stream can have brought it.
  await expect(page.locator("tr", { hasText: "coder/ops-2" })).toBeVisible({ timeout: 5000 });
  await expect(page.locator("tr", { hasText: "coder/ops-2" }).getByRole("link", { name: "OPS-2" }))
    .toBeVisible();
});

test("a publish card in a thread is a chip row; a refused one says why", async ({ page }) => {
  await mockApi(page);
  await page.goto("/relay?channel=rc3&thread=k5");
  const thread = page.getByRole("region", { name: "Thread" });

  const card = thread.locator(".relay-publish-card").first();
  await expect(card.locator("code")).toHaveText("coder/ops-5");
  const pr = card.getByRole("link", { name: /PR #13/ });
  await expect(pr).toHaveAttribute("href", "https://github.com/x/y/pull/13");
  await expect(pr).toHaveAttribute("target", "_blank");
  await expect(card).toContainText("4 files");
  await expect(card).toContainText("verify ✓");
  const removes = card.locator(".relay-publish-chip", { hasText: "removes tests" });
  await expect(removes).toBeVisible();
  await expect(removes).toHaveClass(/text-danger/);
  await expect(card.getByRole("link", { name: /view run/ })).toHaveAttribute("href", "/runs/r-eng-1");

  // The refusal reads as the room's own voice, reason and all — not as a
  // chip row with nothing in it.
  const refused = thread.locator(".relay-message", { hasText: "publish refused" });
  await expect(refused.locator(".relay-system")).toContainText(
    "⛔ publish refused for qa: services/backend/agentplatform/relay.py is outside its test paths");
  await expect(refused.locator(".relay-publish-card")).toHaveCount(0);
});

test("the run page renders the workbench frame: branch, PR, or the refusal", async ({ page }) => {
  await mockApi(page);
  await runTail(page, "r-eng-1", workbenchFrames.published);
  await page.goto("/runs/r-eng-1");
  const frame = page.locator(".wb-frame").first();
  await expect(frame).toContainText("Published");
  await expect(frame.locator("code")).toHaveText("coder/ops-5");
  await expect(frame.getByRole("link", { name: /PR #13/ }))
    .toHaveAttribute("href", "https://github.com/x/y/pull/13");
  await expect(frame).toContainText("4 files");
  await expect(frame).toContainText("removes tests");
  // Not a background event: the frame is the run's outcome, so nothing is
  // left over to collapse.
  await expect(page.locator(".transcript-noise")).toHaveCount(0);

  await runTail(page, "r-qa-1", workbenchFrames.refused);
  await page.goto("/runs/r-qa-1");
  const refused = page.locator(".wb-frame").first();
  await expect(refused).toContainText("Publish refused");
  await expect(refused).toContainText("outside its test paths");
});

test("the pages fit a phone: the tab strip scrolls itself, the row's buttons stay on screen",
     async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto("/agents/health-monitor");
  await expect(page.getByRole("button", { name: "Report" })).toBeAttached();
  // The strip is wider than the phone; it scrolls within itself, never the page.
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.locator(".tabs")).toHaveAttribute("tabindex", "0");

  await page.goto("/changes");
  const accept = page.locator("tr", { hasText: "coder/ops-5" }).getByRole("button", { name: "Accept" });
  await expect(accept).toBeVisible();
  const box = await accept.boundingBox();
  expect(box, "the Accept button has a box").toBeTruthy();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
