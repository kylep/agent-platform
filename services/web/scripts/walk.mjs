#!/usr/bin/env node
// The QA's scripted walk (docs/design/25): every console route, at a desktop
// and a phone width, in both themes — a full-page PNG each and one index the
// agent reads selectively. It is the cheap tier of exploratory QA: one run's
// worth of turns instead of a screenshot per decision, so it is never gated
// on quota the way a live browser session is.
//
//   node scripts/walk.mjs --base <url> [--state <storage-state.json>]
//                         [--out <dir>] [--routes <routes.json>] [--allow-any-host]
//
// The exit code is always 0 once the walk ran: the index is the result, and a
// flagged route is a finding for the agent, not a failure of the walk. Only a
// refused base (a host that is not the platform's own web service) exits
// non-zero, before any page is opened.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

export const VIEWPORTS = [{ width: 1280, height: 800 }, { width: 390, height: 844 }];
export const THEMES = ["dark", "light"];
// networkidle never arrives on a page holding an SSE stream open (Relay), so
// the wait is capped and the screenshot is taken of whatever has rendered.
const SETTLE_MS = 10_000;
const HEADING_MS = 3_000;

/** The pod never passes --allow-any-host: the walk cannot be pointed off the
 * platform's own hostname, whatever a page it rendered may have suggested. */
export function checkHost(base, allowedUrl, allowAny) {
  const host = new URL(base).host;
  if (allowAny) return host;
  let allowed;
  try { allowed = allowedUrl ? new URL(allowedUrl).host : ""; } catch { allowed = ""; }
  if (!allowed || host !== allowed) {
    throw new Error(`refusing --base ${base}: host ${JSON.stringify(host)} is not AP_WEB_URL's `
                    + `(${JSON.stringify(allowed)}); pass --allow-any-host to override outside the pod`);
  }
  return host;
}

/** A route is one absolute path on the platform — exactly one leading slash,
 * no scheme, no backslash — because `new URL(path, base)` would otherwise let
 * an absolute, protocol-relative or file: "path" in a --routes file discard
 * the base and carry the qa cookie past the host guard. */
export function checkPath(path) {
  if (typeof path !== "string" || !/^\/(?!\/)/.test(path) || path.includes("\\")) {
    throw new Error(`path refused: ${JSON.stringify(path)} is not a single absolute path`);
  }
  return path;
}

export function slug(path) {
  const s = path.replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase();
  return s || "root";
}

/** One page per viewport × theme; the theme is written before the app boots
 * so the first paint is already the theme under test (useTheme reads
 * localStorage.theme once, at mount). */
export async function openPage(context, { width, height, theme }) {
  const page = await context.newPage();
  await page.setViewportSize({ width, height });
  await page.addInitScript((t) => { localStorage.setItem("theme", t); }, theme);
  return page;
}

/** Visit one route on a prepared page and return its index entry. Listeners
 * are attached for this visit only, so a page walks many routes without the
 * earlier ones' noise bleeding into later entries. */
export async function walkRoute(page, route, { base, out, width, theme }) {
  const console_errors = [];
  const failed_requests = [];
  const onConsole = (m) => { if (m.type() === "error") console_errors.push(m.text()); };
  const onPageError = (e) => console_errors.push(String(e));
  const onResponse = (r) => {
    if (r.status() >= 400) failed_requests.push(`${r.status()} ${r.request().method()} ${r.url()}`);
  };
  checkPath(route.path);
  const url = new URL(route.path, base);
  if (url.host !== new URL(base).host) throw new Error(`path refused: ${route.path} left ${base}`);
  page.on("console", onConsole);
  page.on("pageerror", onPageError);
  page.on("response", onResponse);

  const file = `${slug(route.path)}-${width}-${theme}.png`;
  let heading_found = false;
  let overflow = false;
  let written = false;
  try {
    try {
      await page.goto(url.href, { waitUntil: "networkidle", timeout: SETTLE_MS });
    } catch (e) {
      // A timeout here is the SSE case above; anything else never loaded.
      if (!/Timeout/i.test(String(e))) failed_requests.push(`navigation ${route.path}: ${e.message}`);
    }
    const h1 = page.getByRole("heading", { level: 1, name: new RegExp(route.heading, "i") }).first();
    heading_found = await h1.waitFor({ state: "visible", timeout: HEADING_MS })
      .then(() => true, () => false);
    overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
      .catch(() => false);
    await page.screenshot({ path: join(out, file), fullPage: true, timeout: SETTLE_MS })
      .then(() => { written = true; }, (e) => console_errors.push(`screenshot: ${e.message}`));
  } finally {
    page.off("console", onConsole);
    page.off("pageerror", onPageError);
    page.off("response", onResponse);
  }
  const ok = heading_found && !overflow && written
    && console_errors.length === 0 && failed_requests.length === 0;
  return { route: route.path, width, theme, file: written ? file : null, ok, heading_found,
           console_errors, failed_requests, overflow };
}

async function loadRoutes(file) {
  if (file) {
    const routes = JSON.parse(readFileSync(file, "utf8"));
    if (!Array.isArray(routes) || routes.some((r) => typeof r?.path !== "string" || typeof r?.heading !== "string")) {
      throw new Error(`${file}: expected [{path, heading}, ...]`);
    }
    for (const r of routes) checkPath(r.path);
    return routes;
  }
  // The specs' own list, loaded as TypeScript by Node's type stripping (Node
  // 24 in the dev image): the smoke, axe and walk sweeps cannot drift apart.
  const mod = await import(new URL("../tests/pages.ts", import.meta.url).href);
  return mod.PAGES;
}

export async function walk({ base, out, state, routes }) {
  mkdirSync(out, { recursive: true });
  // The dev-run pod (docs/design/24) has no user namespaces, so Chromium's
  // sandbox cannot start there; laptops and CI keep it (as playwright.config).
  const browser = await chromium.launch({ chromiumSandbox: !process.env.AP_WORKSPACE });
  const index = [];
  try {
    for (const { width, height } of VIEWPORTS) {
      for (const theme of THEMES) {
        const context = await browser.newContext({ storageState: state });
        const page = await openPage(context, { width, height, theme });
        for (const route of routes) {
          index.push(await walkRoute(page, route, { base, out, width, theme }));
        }
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
  writeFileSync(join(out, "index.json"), JSON.stringify(index, null, 2) + "\n");
  return index;
}

export function summary(index, routes, out) {
  const n = (k) => index.filter(k).length;
  return `walk: ${routes.length} routes × ${VIEWPORTS.length * THEMES.length} = ${index.length} entries, `
    + `${n((e) => e.ok)} ok, ${n((e) => !e.ok)} flagged `
    + `(heading missing ${n((e) => !e.heading_found)}, console errors ${n((e) => e.console_errors.length)}, `
    + `failed requests ${n((e) => e.failed_requests.length)}, overflow ${n((e) => e.overflow)}) `
    + `→ ${join(out, "index.json")}`;
}

async function main() {
  const { values } = parseArgs({
    options: {
      base: { type: "string" },
      state: { type: "string" },
      out: { type: "string", default: "walk" },
      routes: { type: "string" },
      "allow-any-host": { type: "boolean", default: false },
    },
  });
  if (!values.base) {
    process.stderr.write(`usage: ${basename(process.argv[1])} --base <url> [--state <file>] [--out <dir>] `
                         + `[--routes <file>] [--allow-any-host]\n`);
    process.exit(2);
  }
  try {
    checkHost(values.base, process.env.AP_WEB_URL, values["allow-any-host"]);
  } catch (e) {
    process.stderr.write(`${e.message}\n`);
    process.exit(2);
  }
  const routes = await loadRoutes(values.routes);
  const out = resolve(values.out);
  const index = await walk({ base: values.base, out, state: values.state, routes });
  process.stdout.write(summary(index, routes, out) + "\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => { process.stderr.write(`${e.message}\n`); process.exit(2); });
}
