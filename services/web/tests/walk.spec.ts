import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { mockApi } from "./mock-api";
import { checkHost, checkPath, openPage, slug, walkRoute } from "../scripts/walk.mjs";

// The QA's scripted walk (docs/design/25) is a script, not a spec, so this
// drives its exported pieces with pages this test made against the mock — the
// mock's route handler is per-page, so the walk's own browser could never see
// the fixtures. What is pinned here is the index entry the agent reads: a
// clean route says ok, a broken one says which of the four things broke.

const dirs: string[] = [];
const out = () => { const d = mkdtempSync(join(tmpdir(), "walk-")); dirs.push(d); return d; };
test.afterEach(() => { for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true }); });
const base = "http://localhost:4173";

// A --routes file is the one input the walk takes from disk, and the browser
// it drives carries the qa cookie: a path that is secretly a URL would take
// that cookie wherever it points, past the host guard.
const HOSTILE = ["http://evil.com/", "//evil.com/x", "file:///etc/passwd", "\\\\evil.com\\x", "agents", ""];

test("the host guard refuses a base that is not the platform's web service", () => {
  expect(() => checkHost("https://example.com", "http://pai-web:80", false))
    .toThrow(/refusing/);
  // Unset in a laptop shell: still refused without the explicit override.
  expect(() => checkHost("https://example.com", undefined, false)).toThrow(/refusing/);
  expect(() => checkHost("http://pai-web/", "http://pai-web:80", false)).not.toThrow();
  expect(() => checkHost("https://example.com", undefined, true)).not.toThrow();
});

test("a route path is exactly one absolute path on the platform, never a URL", () => {
  for (const path of HOSTILE) {
    expect(() => checkPath(path), path).toThrow(/path refused/);
  }
  for (const path of ["/", "/agents?tab=1", `/artifacts/${"a1".repeat(16)}`]) {
    expect(() => checkPath(path), path).not.toThrow();
  }
});

test("a hostile route is refused before any navigation, cookie and all", async ({ context }) => {
  const dir = out();
  const page = await openPage(context, { width: 1280, height: 800, theme: "dark" });
  await mockApi(page);
  let navigations = 0;
  page.on("request", () => { navigations += 1; });
  for (const path of HOSTILE) {
    await expect(walkRoute(page, { path, heading: "x" }, { base, out: dir, width: 1280, theme: "dark" }), path)
      .rejects.toThrow(/path refused/);
  }
  expect(navigations).toBe(0);
  expect(page.url()).toBe("about:blank");
});

test("a route slug is a filename", () => {
  expect(slug("/")).toBe("root");
  expect(slug("/agents/health-monitor?tab=history")).toBe("agents-health-monitor-tab-history");
  expect(slug("/relay?channel=rc1&thread=m6")).toBe("relay-channel-rc1-thread-m6");
});

test("a clean route: heading found, nothing flagged, PNG on disk, theme applied", async ({ context }) => {
  const dir = out();
  const page = await openPage(context, { width: 390, height: 844, theme: "light" });
  await mockApi(page);
  const entry = await walkRoute(page, { path: "/runs", heading: "Runs" },
                                { base, out: dir, width: 390, theme: "light" });
  expect(entry).toEqual({
    route: "/runs", width: 390, theme: "light", file: "runs-390-light.png",
    ok: true, heading_found: true, console_errors: [], failed_requests: [], overflow: false,
  });
  expect(existsSync(join(dir, entry.file))).toBe(true);
  // A PNG, not an empty file the screenshot call gave up on.
  expect(readFileSync(join(dir, entry.file)).subarray(0, 4)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47]));
  // The theme was set BEFORE the app booted, so the page rendered light.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await page.viewportSize()).toEqual({ width: 390, height: 844 });
});

test("a route that overflows sideways is flagged and not ok", async ({ context }) => {
  const dir = out();
  const page = await openPage(context, { width: 1280, height: 800, theme: "dark" });
  await mockApi(page);
  // Something 5000px wide and unbreakable — a table nobody wrapped in a scroller.
  await page.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      const s = document.createElement("style");
      s.textContent = "body::after{content:'';display:block;width:5000px;height:1px}";
      document.head.appendChild(s);
    });
  });
  const entry = await walkRoute(page, { path: "/agents", heading: "Agents" },
                                { base, out: dir, width: 1280, theme: "dark" });
  expect(entry.file).toBe("agents-1280-dark.png");
  expect(entry.heading_found).toBe(true);
  expect(entry.overflow).toBe(true);
  expect(entry.ok).toBe(false);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("a page that did not render says so: heading missing, request failed", async ({ context }) => {
  const dir = out();
  const page = await openPage(context, { width: 1280, height: 800, theme: "dark" });
  await mockApi(page);
  // No fixture behind this page: the mock 404s its data, the h1 never appears.
  const entry = await walkRoute(page, { path: "/wiki/no-such-page", heading: "Nowhere" },
                                { base, out: dir, width: 1280, theme: "dark" });
  expect(entry.heading_found).toBe(false);
  expect(entry.failed_requests.some((r: string) => /^404 /.test(r))).toBe(true);
  expect(entry.ok).toBe(false);
  expect(existsSync(join(dir, entry.file))).toBe(true);
});
