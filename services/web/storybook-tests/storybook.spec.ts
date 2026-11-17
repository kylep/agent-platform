import AxeBuilder from "@axe-core/playwright";
import { expect, test as base } from "@playwright/test";
import { readFileSync } from "node:fs";

const coverage: { components: { states: { story: string }[] }[] } = JSON.parse(
  readFileSync(new URL("../../../packages/ui/story-coverage.json", import.meta.url), "utf8"),
);

const storyIds = [...new Set(coverage.components.flatMap(
  ({ states }) => states.map(({ story }) => story),
))].sort();

type Theme = "dark" | "light";

const test = base.extend<{ openStory: (story: string, theme: Theme) => Promise<void> }>({
  openStory: async ({ page }, runTest) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await runTest(async (story, theme) => {
      await page.clock.setFixedTime(new Date("2026-01-15T12:00:00Z"));
      await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
      // Open the built preview directly so the manager UI is excluded from axe and screenshots.
      const query = new URLSearchParams({ id: story, viewMode: "story", globals: `theme:${theme}` });
      const response = await page.goto(`/iframe.html?${query}`);
      expect(response?.ok(), `load the iframe for ${story}`).toBe(true);
      await expect(page.locator("body")).toHaveClass(/\bsb-show-main\b/);
      await expect(page.locator("#storybook-root > *").first()).toBeVisible();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.evaluate(async () => { await document.fonts.ready; });
    });
    await expect(page.locator("body")).not.toHaveClass(/\bsb-show-errordisplay\b/);
    expect(errors, "Storybook browser errors").toEqual([]);
  },
});

for (const theme of ["dark", "light"] as const) {
  test.describe(theme, () => {
    for (const story of storyIds) {
      test(story, async ({ openStory, page }) => {
        await openStory(story, theme);
        const result = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
          .analyze();
        const violations = result.violations.map(({ id, impact, help, nodes }) => ({
          id, impact, help, nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
        }));
        expect(violations).toEqual([]);

        if (process.env.AP_STORYBOOK_VISUAL === "1") {
          await expect(page).toHaveScreenshot(`${story}-${theme}.png`);
        }
      });
    }

    test("Button is keyboard focusable and disabled Button is skipped", async ({ openStory, page }) => {
      await openStory("ui-button--variants", theme);
      const accept = page.getByRole("button", { name: "Accept", exact: true });
      await page.keyboard.press("Tab");
      await expect(accept).toBeFocused();
      await expect(page.getByRole("button", { name: "Saving…" })).toBeDisabled();
      await page.getByRole("button", { name: "review & accept under Changes" }).focus();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "Verify" })).toBeFocused();
    });

    test("ChipButton updates pressed state with the keyboard", async ({ openStory, page }) => {
      await openStory("ui-chip--toggle", theme);
      const toggle = page.getByRole("button", { name: "Failures only", pressed: false });
      await toggle.press("Space");
      await expect(page.getByRole("button", { name: "Failures only", pressed: true })).toBeFocused();
    });

    test("ConfirmDialog cancels with Escape and reports confirmation", async ({ openStory, page }) => {
      await openStory("ui-confirmdialog--discard", theme);
      const discard = page.getByRole("button", { name: "Discard", exact: true });
      const dialog = page.getByRole("dialog", { name: "Discard change #12?" });
      await discard.press("Enter");
      await expect(dialog).toBeVisible();
      const confirm = dialog.getByRole("button", { name: "Discard it" });
      await confirm.focus();
      await page.keyboard.press("Shift+Tab");
      await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await expect(page.getByRole("status")).toBeEmpty();
      await discard.click();
      await dialog.getByRole("button", { name: "Discard it" }).click();
      await expect(dialog).toBeHidden();
      await expect(page.getByRole("status")).toHaveText("Change discarded.");
    });

    test("FormDialog validates and submits a draft", async ({ openStory, page }) => {
      await openStory("ui-formdialog--rename", theme);
      await page.getByRole("button", { name: "Rename agent" }).click();
      const dialog = page.getByRole("dialog", { name: "Rename agent" });
      const field = dialog.getByRole("textbox", { name: "Agent name" });
      const save = dialog.getByRole("button", { name: "Save name" });
      await expect(save).toBeDisabled();
      await field.fill("health-monitor");
      await expect(save).toBeEnabled();
      await save.click();
      await expect(dialog).toBeHidden();
      await expect(page.getByRole("status")).toHaveText("Saved name: health-monitor");
    });

    test("InfoDialog opens and closes", async ({ openStory, page }) => {
      await openStory("ui-infodialog--help", theme);
      await page.getByRole("button", { name: "About run history" }).click();
      const dialog = page.getByRole("dialog", { name: "About run history" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Close" }).click();
      await expect(dialog).toBeHidden();
    });

    test("fields accept text and selection", async ({ openStory, page }) => {
      await openStory("ui-field--all-fields", theme);
      const name = page.getByRole("textbox", { name: "Name", exact: true });
      const filter = page.getByRole("combobox", { name: "Filter by agent" });
      const instruction = page.getByRole("textbox", { name: "Instruction" });
      const editor = page.getByRole("textbox", { name: "Agent definition" });
      await name.fill("discord-bot");
      await filter.selectOption("health-monitor");
      await instruction.fill("Check service health.");
      await editor.fill("name: health-monitor\ntools: []");
      await expect(name).toHaveValue("discord-bot");
      await expect(filter).toHaveValue("health-monitor");
      await expect(instruction).toHaveValue("Check service health.");
      await expect(editor).toHaveValue("name: health-monitor\ntools: []");
    });

    test("SideNav expands Apps and marks the chosen page", async ({ openStory, page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await openStory("ui-sidenav--console-on-dashboard", theme);
      const news = page.getByRole("link", { name: /news/i });
      await expect(news).toBeHidden();
      await page.getByRole("button", { name: "Expand Apps" }).press("Enter");
      await expect(page.getByRole("button", { name: "Collapse Apps" })).toHaveAttribute("aria-expanded", "true");
      await news.click();
      await expect(news).toHaveAttribute("aria-current", "page");
    });

    test("ThemeToggle switches and stores the theme", async ({ openStory, page }) => {
      await openStory("ui-sidenav--theme-switch", theme);
      const next = theme === "dark" ? "light" : "dark";
      await page.getByRole("button", { name: `Switch to ${next} theme` }).click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", next);
      await expect(page.getByRole("button", { name: `Switch to ${theme} theme` })).toBeVisible();
      expect(await page.evaluate(() => localStorage.getItem("theme"))).toBe(next);
    });
  });
}
