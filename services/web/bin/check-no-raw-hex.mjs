#!/usr/bin/env node
// Design-system enforcement: no raw hex colors outside the token source of
// truth. Components and pages must use semantic utilities (bg-canvas,
// text-accent, border-border, …) or var(--ds-*) — a raw hex is a token that
// escaped the system. Run by `npm run check:tokens` and CI.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../src", import.meta.url).pathname;
const ALLOW = new Set([
  "design-system/tokens.css",   // the single place hex is legal
]);
const HEX = /#[0-9a-fA-F]{3,8}\b/;

const bad = [];
function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { walk(p); continue; }
    if (!/\.(tsx?|css)$/.test(name)) continue;
    const rel = p.slice(ROOT.length + 1);
    if (ALLOW.has(rel)) continue;
    readFileSync(p, "utf8").split("\n").forEach((line, i) => {
      if (HEX.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim().slice(0, 100)}`);
    });
  }
}
walk(ROOT);

if (bad.length) {
  console.error("Raw hex colors outside design-system/tokens.css:\n" + bad.join("\n"));
  console.error("\nUse token utilities (text-accent, bg-surface, …) or var(--ds-*).");
  process.exit(1);
}
console.log("check-no-raw-hex: clean");
