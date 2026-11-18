import { expect, test, type Locator } from "@playwright/test";
import { readFileSync } from "node:fs";

type NativeStyle = {
  fill: string | null;
  ink: string;
  border: string | null;
  border_opacity: number;
  radius: number;
  opacity: number;
  font_family: string;
  font_size: number;
  font_weight: number;
  line_height: number;
  text_case: string;
};
type Sample = {
  key: string;
  component: string;
  variant: string;
  mode: "dark" | "light";
  background: string;
  width: number;
  height: number;
  style?: NativeStyle;
};

// Designer draws unhinted glyph outlines with fractional advances. Use
// Chromium's unhinted metrics for this comparison so FreeType's integer
// grid fitting cannot turn subpixel text geometry into a false mismatch.
test.use({
  launchOptions: process.env.AP_STORYBOOK_VISUAL === "1" ? {
    args: ["--disable-gpu", "--no-zygote", "--in-process-gpu", "--font-render-hinting=none"],
  } : undefined,
});

const fixtures = new URL("../../../.ap/designer-parity/", import.meta.url);
function readSamples(): Sample[] {
  try {
    return JSON.parse(readFileSync(new URL("samples.json", fixtures), "utf8"));
  } catch (error) {
    throw new Error("Designer parity fixtures are missing or invalid. Run bin/build-designer-parity --fixtures with the backend Python environment.", { cause: error });
  }
}

async function checkPrimitive(root: Locator, sample: Sample) {
  const native = sample.style!;
  const actual = await root.evaluate((element, style) => {
    const css = getComputedStyle(element);
    const bounds = element.getBoundingClientRect();
    // CSS color-mix() and rgb() serialize differently. Normalize them on a
    // transparent browser canvas before comparing with native token colors.
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true })!;
    function color(value: string | null, opacity = 1) {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value ?? "transparent";
      context.globalAlpha = opacity;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data];
    }
    return {
      width: bounds.width,
      height: bounds.height,
      fill: color(css.backgroundColor),
      expectedFill: color(style.fill),
      ink: color(css.color),
      expectedInk: color(style.ink),
      border: color(parseFloat(css.borderTopWidth) ? css.borderTopColor : null),
      expectedBorder: color(style.border, style.border_opacity),
      radius: parseFloat(css.borderTopLeftRadius),
      opacity: parseFloat(css.opacity),
      fontFamily: css.fontFamily.split(",")[0].trim().replace(/["']/g, ""),
      fontSize: parseFloat(css.fontSize),
      fontWeight: parseInt(css.fontWeight),
      // Chromium exposes the native select's line height as "normal".
      // Its one-line content box is the used line height in this fixture.
      lineHeight: element instanceof HTMLSelectElement && css.lineHeight === "normal"
        ? bounds.height - [css.paddingTop, css.paddingBottom, css.borderTopWidth, css.borderBottomWidth]
          .reduce((total, value) => total + parseFloat(value), 0)
        : parseFloat(css.lineHeight),
      textCase: css.textTransform,
    };
  }, native);

  expect(Math.abs(actual.width - sample.width), "width").toBeLessThan(1);
  expect(Math.abs(actual.height - sample.height), "height").toBeLessThan(1);
  expect(actual.fill, "background color").toEqual(actual.expectedFill);
  expect(actual.ink, "text color").toEqual(actual.expectedInk);
  for (let channel = 0; channel < 4; channel++) {
    expect(Math.abs(actual.border[channel] - actual.expectedBorder[channel]), "border color").toBeLessThanOrEqual(2);
  }
  expect(Math.abs(Math.min(actual.radius, sample.height / 2) - Math.min(native.radius, sample.height / 2)), "corner radius")
    .toBeLessThan(.5);
  expect(actual.opacity, "opacity").toBeCloseTo(native.opacity);
  expect(actual.fontFamily, "font family").toBe(native.font_family);
  expect(actual.fontSize, "font size").toBe(native.font_size);
  expect(actual.fontWeight, "font weight").toBe(native.font_weight);
  expect(Math.abs(actual.lineHeight - native.line_height), "line height").toBeLessThan(.25);
  expect(actual.textCase, "text transform").toBe(native.text_case);
}

for (const sample of readSamples()) {
  test(`Designer parity ${sample.key}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const query = new URLSearchParams({
      id: "ui-designer-agent-platform-library--reference",
      viewMode: "story",
      globals: `theme:${sample.mode}`,
      args: `component:${sample.component};variant:${sample.variant}`,
    });
    expect((await page.goto(`/iframe.html?${query}`))?.ok()).toBe(true);
    const fixture = page.locator("[data-designer-fixture]");
    await expect(fixture).toHaveAttribute("data-component", sample.component);
    await expect(fixture).toHaveAttribute("data-variant", sample.variant);
    await expect(page.locator("html")).toHaveAttribute("data-theme", sample.mode);
    const root = sample.component.endsWith("Dialog") ? page.getByRole("dialog")
      : ["TD", "TH"].includes(sample.component) ? fixture.locator("[data-designer-cell]")
      : fixture.locator(":scope > *").first();
    await expect(root).toBeVisible();
    await page.evaluate(async () => { await document.fonts.ready; });
    expect(errors).toEqual([]);

    if (sample.style) await checkPrimitive(root, sample);

    if (process.env.AP_STORYBOOK_VISUAL === "1") {
      // Radix opens with its first action focused. Capture the resting
      // panel appearance independently of the browser's focus ring.
      if (sample.component.endsWith("Dialog")) {
        await root.evaluate((element) => {
          const focused = document.activeElement;
          if (focused instanceof HTMLElement && element.contains(focused)) focused.blur();
        });
      }
      const react = await root.screenshot({ animations: "disabled", caret: "hide", scale: "css" });
      const designer = readFileSync(new URL(`${sample.key}.svg`, fixtures));
      const columns = [react, designer].map((image, i) => {
        const type = i === 0 ? "png" : "svg+xml";
        const label = i === 0 ? "React" : "Designer";
        return `<section><p>${label}</p><img alt="${label}" src="data:image/${type};base64,${image.toString("base64")}"></section>`;
      });
      await page.setContent(`
        <!doctype html><html lang="en"><style>
          body { margin: 0; }
          main { display: inline-flex; align-items: start; gap: 16px; padding: 16px;
                 background: ${sample.background}; color: ${sample.mode === "dark" ? "white" : "black"}; }
          p { margin: 0 0 8px; font: 12px/1.4 sans-serif; }
          img { display: block; }
        </style><main>${columns.join("")}</main></html>`);
      await page.locator("img").evaluateAll(async (images) => {
        await Promise.all(images.map((image) => (image as HTMLImageElement).decode()));
      });
      await expect(page.locator("main")).toHaveScreenshot(`designer-${sample.key}.png`);
    }
  });
}
