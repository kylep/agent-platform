import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "extract.check.ts",
  use: { viewport: { width: 700, height: 400 } },
});
