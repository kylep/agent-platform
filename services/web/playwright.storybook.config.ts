import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:6007";
const visual = process.env.AP_STORYBOOK_VISUAL === "1";

export default defineConfig({
  testDir: "./storybook-tests",
  outputDir: "./storybook-tests/test-results/browser",
  updateSnapshots: "none",
  snapshotPathTemplate: "{testDir}/snapshots/{arg}{ext}",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  workers: visual ? 2 : undefined,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    viewport: { width: 1000, height: 680 },
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
    launchOptions: visual ? {
      // Keep screenshot rendering independent of host GPU availability.
      args: ["--disable-gpu", "--no-zygote", "--in-process-gpu"],
    } : undefined,
  },
  expect: {
    toHaveScreenshot: { animations: "disabled", caret: "hide", scale: "css" },
  },
  webServer: {
    command: "node bin/serve-storybook.mjs",
    url: baseURL + "/iframe.html",
  },
});
