import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { version } = require("@playwright/test/package.json");
const repo = fileURLToPath(new URL("../../..", import.meta.url));
const web = join(repo, "services/web");
const prepare = spawnSync(process.execPath, [join(repo, "services/web/bin/prepare-designer-parity.mjs")], { stdio: "inherit" });
if (prepare.error) throw prepare.error;
if (prepare.status !== 0) process.exit(prepare.status ?? 1);
const output = join(web, "storybook-tests/test-results");
const snapshots = join(web, "storybook-tests/snapshots");
mkdirSync(output, { recursive: true });
mkdirSync(snapshots, { recursive: true });

const args = process.argv.slice(2);
const update = args.some((arg) => /^--update-snapshots(?:=|$)|^-u$/.test(arg));
const result = spawnSync("docker", [
  "run", "--rm", "--platform", "linux/arm64", "--ipc=host", "--network=none",
  "--user", `${process.getuid()}:${process.getgid()}`,
  "--env", "AP_STORYBOOK_VISUAL=1", "--env", "HOME=/tmp", "--env", "CI",
  "--mount", `type=bind,src=${repo},dst=/repo,readonly`,
  "--mount", `type=bind,src=${output},dst=/repo/services/web/storybook-tests/test-results`,
  "--mount", `type=bind,src=${snapshots},dst=/repo/services/web/storybook-tests/snapshots${update ? "" : ",readonly"}`,
  "--workdir", "/repo/services/web",
  `mcr.microsoft.com/playwright:v${version}-noble`,
  "node", "/repo/node_modules/@playwright/test/cli.js", "test",
  "--config", "playwright.storybook.config.ts", ...args,
], { stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
