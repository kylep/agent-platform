import { expect, test, type Page, type Request } from "@playwright/test";
import { mockApi } from "./mock-api";

// The schedule builder is an INPUT METHOD, nothing more: the definition still
// carries a 5-field cron string and the API is unchanged. So what these tests
// watch is the wire — what cron string each control produces — plus the two
// directions that make it safe to open on an existing schedule: a preset shape
// comes back as its preset, and anything else falls back to the raw field
// rather than being quietly rewritten.

function captureWrites(page: Page): Request[] {
  const writes: Request[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/")) writes.push(r);
  });
  return writes;
}

/** Open the editor on an agent whose single cron entrypoint is `cron`. */
async function openEditorWithCron(page: Page, cron: string) {
  await mockApi(page);
  await page.route("**/api/agents/health-monitor", async (route) => {
    if (route.request().method() !== "GET") { await route.fulfill({ json: { ok: true } }); return; }
    await route.fulfill({ json: {
      name: "health-monitor", prompt: "You watch health.", description: "Watches platform health.",
      model: "", role: "operator", system: true, can_invoke: false, concurrency: 1,
      timeout_seconds: 600, result_topic: "", transcript_retention_days: null,
      harness_tools: [], platform_tools: [], skills: [], secrets: [], enabled: true,
      entrypoints: { crons: [{ schedule: cron, prompt: "Check platform health." }],
                     webhooks: [], topics: [], timezone: "" },
    } });
  });
  await page.goto("/agents/health-monitor");
  await expect(page.getByLabel("Cron schedule frequency")).toBeVisible();
}

async function savedCron(page: Page, writes: Request[]): Promise<string> {
  await page.getByRole("button", { name: "Save changes" }).first().click();
  await expect(page.getByText("Saved — live now.").first()).toBeVisible();
  const put = writes.find((w) => w.method() === "PUT");
  return JSON.parse(put!.postData() ?? "{}").entrypoints.crons[0].schedule;
}

test("each frequency serializes to its canonical cron", async ({ page }) => {
  const writes = captureWrites(page);
  await openEditorWithCron(page, "0 9 * * *");
  const freq = page.getByLabel("Cron schedule frequency");

  // Every N minutes
  await freq.selectOption("minutes");
  await page.getByLabel("Minutes between runs").fill("20");
  expect(await savedCron(page, writes)).toBe("*/20 * * * *");
});

test("hourly serializes the minute past the hour", async ({ page }) => {
  const writes = captureWrites(page);
  await openEditorWithCron(page, "0 9 * * *");
  await page.getByLabel("Cron schedule frequency").selectOption("hourly");
  await page.getByLabel("Minute of the hour").fill("35");
  expect(await savedCron(page, writes)).toBe("35 * * * *");
});

test("daily serializes the clock time", async ({ page }) => {
  const writes = captureWrites(page);
  await openEditorWithCron(page, "*/15 * * * *");
  await page.getByLabel("Cron schedule frequency").selectOption("daily");
  await page.getByLabel("Time of day").fill("17:45");
  expect(await savedCron(page, writes)).toBe("45 17 * * *");
});

test("weekly serializes the chosen weekdays, cron-numbered from Sunday", async ({ page }) => {
  const writes = captureWrites(page);
  await openEditorWithCron(page, "0 9 * * *");
  await page.getByLabel("Cron schedule frequency").selectOption("weekly");
  // The preset opens on Monday; add Friday, drop Monday — the order the chips
  // were clicked must not reach the expression.
  await page.getByRole("checkbox", { name: "Friday" }).check();
  await page.getByRole("checkbox", { name: "Monday" }).uncheck();
  await page.getByRole("checkbox", { name: "Sunday" }).check();
  await page.getByLabel("Time of day").fill("08:00");
  expect(await savedCron(page, writes)).toBe("0 8 * * 0,5");
});

test("monthly serializes the day of the month", async ({ page }) => {
  const writes = captureWrites(page);
  await openEditorWithCron(page, "0 9 * * *");
  await page.getByLabel("Cron schedule frequency").selectOption("monthly");
  await page.getByLabel("Day of the month").fill("15");
  expect(await savedCron(page, writes)).toBe("0 9 15 * *");
});

test("an existing cron opens in the preset that produced it", async ({ page }) => {
  // Round-trip: the stored expression decides the controls, so opening the
  // editor on a schedule shows what it means instead of resetting it.
  await openEditorWithCron(page, "*/15 * * * *");
  await expect(page.getByLabel("Cron schedule frequency")).toHaveValue("minutes");
  await expect(page.getByLabel("Minutes between runs")).toHaveValue("15");

  await openEditorWithCron(page, "30 6 * * 1,2,3,4,5");
  await expect(page.getByLabel("Cron schedule frequency")).toHaveValue("weekly");
  await expect(page.getByLabel("Time of day")).toHaveValue("06:30");
  await expect(page.getByRole("checkbox", { name: "Monday" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Friday" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Sunday" })).not.toBeChecked();

  await openEditorWithCron(page, "0 0 1 * *");
  await expect(page.getByLabel("Cron schedule frequency")).toHaveValue("monthly");
  await expect(page.getByLabel("Day of the month")).toHaveValue("1");
});

test("an expression no preset covers opens in Custom, verbatim", async ({ page }) => {
  const writes = captureWrites(page);
  // A step over an hour range plus an nth-weekday: valid cron, no preset shape.
  await openEditorWithCron(page, "*/7 3-5 * * 1#2");
  await expect(page.getByLabel("Cron schedule frequency")).toHaveValue("custom");
  await expect(page.getByLabel("Cron expression")).toHaveValue("*/7 3-5 * * 1#2");
  // Saving an edit made elsewhere on the form must carry the cron back exactly
  // as it was — the builder is never a reason a schedule loses precision.
  await page.getByLabel("Description").fill("Watches everything.");
  expect(await savedCron(page, writes)).toBe("*/7 3-5 * * 1#2");
});

test("an untouched cron round-trips byte-identical", async ({ page }) => {
  const writes = captureWrites(page);
  // A weekday RANGE is the shape most at risk: the chips serialize to a list,
  // so if merely rendering the row re-emitted the value, opening any agent and
  // saving would silently rewrite its stored schedule.
  await openEditorWithCron(page, "0 9 * * 1-5");
  await expect(page.getByLabel("Cron schedule frequency")).toHaveValue("weekly");
  await page.getByLabel("Description").fill("Untouched schedule.");
  expect(await savedCron(page, writes)).toBe("0 9 * * 1-5");
});

test("editing a weekday range canonicalizes it to a list — deliberately", async ({ page }) => {
  const writes = captureWrites(page);
  // The other half of the round-trip rule: once a chip is actually clicked, the
  // builder owns the expression and emits its canonical list form. Same firing
  // days, different text — asserted here so it can never happen by accident
  // without a test noticing.
  await openEditorWithCron(page, "0 9 * * 1-5");
  await page.getByRole("checkbox", { name: "Wednesday" }).uncheck();
  expect(await savedCron(page, writes)).toBe("0 9 * * 1,2,4,5");
});

test("+ Add cron adds an EMPTY row — adding is not scheduling", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor");

  await page.getByRole("button", { name: "+ Add cron" }).click();
  const added = page.locator(".cron-entry").last();
  // The controls open on the commonest shape so they are usable at once...
  await expect(added.getByLabel("Cron schedule frequency")).toHaveValue("daily");
  // ...but nothing is committed until one is touched: an accidental Add + Save
  // must not arm a live daily run.
  await expect(added).toContainText("No schedule yet");

  await page.getByLabel("Description").fill("Added a row by accident.");
  await page.getByRole("button", { name: "Save changes" }).first().click();
  const put = writes.find((w) => w.method() === "PUT");
  const crons = JSON.parse(put!.postData() ?? "{}").entrypoints.crons;
  expect(crons[crons.length - 1].schedule).toBe("");
});

test("switching to Custom carries the preset's expression over", async ({ page }) => {
  await openEditorWithCron(page, "0 9 * * *");
  await page.getByLabel("Cron schedule frequency").selectOption("weekly");
  await page.getByLabel("Cron schedule frequency").selectOption("custom");
  await expect(page.getByLabel("Cron expression")).toHaveValue("0 9 * * 1");
});

test("the preview line shows the platform's own description and next fires", async ({ page }) => {
  await openEditorWithCron(page, "0 9 * * *");
  const row = page.locator(".cron-entry").first();
  // English and the times both come from /api/cron/preview — the UI does not
  // describe crons itself.
  await expect(row).toContainText("Cron 0 9 * * * explained (UTC)");
  await expect(row).toContainText("next");

  await page.getByLabel("Cron schedule frequency").selectOption("minutes");
  await page.getByLabel("Minutes between runs").fill("5");
  await expect(row).toContainText("Cron */5 * * * * explained");
});

test("an invalid custom expression shows the reason inline", async ({ page }) => {
  await openEditorWithCron(page, "*/7 3-5 * * 1#2");
  await page.getByLabel("Cron expression").fill("0 9 *");
  const row = page.locator(".cron-entry").first();
  await expect(row.locator(".error")).toContainText("expected 5 fields, got 3");
});

test("an expression the preview accepts keeps the Jobs form saveable", async ({ page }) => {
  // Guards the wiring, not the renderer: `cronOk` must follow the preview's
  // `error`, so an expression the platform accepts — a month name, say — can
  // never leave Create disabled with nothing on screen explaining why. (That
  // the backend accepts `JUL` at all is pinned in test_cron_preview.py.)
  await mockApi(page);
  await page.goto("/agents/health-monitor?tab=schedules");
  await page.getByRole("button", { name: "+ New Job" }).click();
  await page.getByLabel("Job name").fill("summer-only");
  await page.getByLabel("Job prompt").fill("Only in July.");
  await page.getByLabel("Job schedule frequency").selectOption("custom");
  await page.getByLabel("Cron expression").fill("0 9 * JUL *");
  await expect(page.getByRole("button", { name: "Create job" })).toBeEnabled();
});

test("a bad timezone is reported at the timezone field, not under the schedule", async ({ page }) => {
  await mockApi(page);
  await page.goto("/agents/health-monitor?tab=schedules");
  await page.getByRole("button", { name: "+ New Job" }).click();
  await page.getByLabel("Timezone").fill("Mars/Olympus");

  await expect(page.getByText("Unknown timezone — use an IANA name")).toBeVisible();
  // The schedule keeps describing the schedule: a complaint about a different
  // field does not belong under it, and must not disable Create either.
  await expect(page.locator(".cron-builder .error")).toHaveCount(0);
  await expect(page.locator(".cron-builder")).toContainText("explained");
});

test("the Jobs form edits its cron with the same builder", async ({ page }) => {
  const writes = captureWrites(page);
  await mockApi(page);
  await page.goto("/agents/health-monitor?tab=schedules");

  await page.getByRole("button", { name: "+ New Job" }).click();
  await page.getByLabel("Job name").fill("morning-brief");
  await page.getByLabel("Job prompt").fill("Write the brief.");
  await page.getByLabel("Job schedule frequency").selectOption("daily");
  await page.getByLabel("Time of day").fill("07:15");
  await page.getByRole("button", { name: "Create job" }).click();

  const post = writes.find((w) => w.method() === "POST");
  expect(new URL(post!.url()).pathname).toBe("/api/jobs");
  const body = JSON.parse(post!.postData() ?? "{}");
  expect(body.cron).toBe("15 7 * * *");
  expect(body.name).toBe("morning-brief");
});
