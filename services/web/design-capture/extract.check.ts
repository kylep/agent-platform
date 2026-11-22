import { expect, test } from "@playwright/test";
import { captureScene } from "./extract";

test("captures rendered copy and vector artwork as editable primitives", async ({ page }) => {
  await page.route("http://capture.test/", (route) => route.fulfill({
    contentType: "text/html",
    body: `
      <style>
        html { background: oklch(.21 0 0); color: white }
        .wrap { width: 105px }
        .cut { height: 26px; overflow: hidden }
        .below { margin-top: 800px }
      </style>
      <main>
        <a href="/next?q=value#destination">Continue</a>
        <div class="wrap">one two three four five six seven eight nine ten stars</div>
        <div hidden>hidden text</div>
        <div class="cut"><p>partly clipped</p><p>fully clipped</p></div>
        <input value="rendered value">
        <input type="password" value="secret">
        <textarea>line 1
line 2</textarea>
        <select aria-label="Options"><option selected>selected label</option><option>not selected</option></select>
        <input type="date" aria-label="Blank date">
        <input type="datetime-local" value="2026-11-17T15:30" aria-label="Scheduled date">
        <br>
        <svg viewBox="0 0 24 24" width="24" height="24" aria-label="Icon">
          <defs><clipPath id="bounds"><rect width="24" height="24"/></clipPath></defs>
          <g transform="translate(1 2)"><path fill="none" stroke="currentColor" d="M1 1 L20 20"/></g>
        </svg>
        <svg viewBox="0 0 100 30" width="100" height="30" aria-label="Chart">
          <g opacity="0.5"><text x="2" y="18" font-size="10" fill="red">Axis <tspan fill="white">label</tspan></text></g>
        </svg>
        <canvas width="24" height="24" aria-label="Sample chart"></canvas>
        <iframe style="display: block; height: 45px" srcdoc="<p>copy in frame</p>"></iframe>
        <p class="below">below fold</p>
      </main>`,
  }));
  await page.goto("http://capture.test/");
  await page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => canvas.getContext("2d")?.fillRect(0, 0, 24, 24));
  const scene = await page.evaluate(captureScene, {});
  const copy = scene.primitives.flatMap((item) => item.kind === "text" ? [item.text] : []);

  expect(scene.height).toBe(400);
  expect(scene.primitives[0]).toMatchObject({ kind: "rect", fill: "#181818" });
  expect(copy.join(" ")).toContain("one two three four five six seven eight nine ten stars");
  expect(copy).toEqual(expect.arrayContaining([
    "Continue", "rendered value", "••••••", "line 1", "line 2", "selected label",
    "partly clipped", "copy in frame", "mm/dd/yyyy", "Axis", "label",
  ]));
  for (const hidden of ["hidden text", "fully clipped", "secret", "not selected", "below fold"]) {
    expect(copy).not.toContain(hidden);
  }

  expect(scene.groups).toContainEqual(expect.objectContaining({ tag: "a", href: "/next?q=value#destination" }));
  const iconGroup = scene.groups.find(({ label }) => label === "Icon");
  const icon = scene.primitives.find((item) => item.kind === "svg" && item.groupId === iconGroup?.id);
  expect(icon?.kind === "svg" && icon.shapes).toMatchObject([
    { tag: "path", attributes: { stroke: "#ffffff" }, matrix: [1, 0, 0, 1, 1, 2] },
  ]);
  const chart = scene.groups.find(({ label }) => label === "Chart");
  expect(scene.primitives).toEqual(expect.arrayContaining([
    expect.objectContaining({ kind: "text", groupId: chart?.id, text: "Axis", fontSize: 10, color: "#ff0000", opacity: 0.5 }),
    expect.objectContaining({ kind: "text", groupId: chart?.id, text: "label", fontSize: 10, color: "#ffffff", opacity: 0.5 }),
  ]));
  for (const label of ["Options", "Blank date", "Scheduled date"]) {
    const group = scene.groups.find((item) => item.label === label);
    expect(scene.primitives).toContainEqual(expect.objectContaining({ kind: "svg", groupId: group?.id }));
  }
  expect(scene.primitives).toContainEqual(expect.objectContaining({
    kind: "image", sourceKind: "canvas", alt: "Sample chart", src: expect.stringMatching(/^data:image\/png;base64,/),
  }));
  const full = await page.evaluate(captureScene, { fullPage: true, maxHeight: 1200 });
  expect(full.height).toBeGreaterThan(400);
  expect(full.height).toBeLessThanOrEqual(1200);
  expect(full.primitives).toContainEqual(expect.objectContaining({ kind: "text", text: "below fold" }));
  const screenshot = await page.screenshot({ fullPage: true, clip: { x: 0, y: 0, width: full.width, height: full.height } });
  expect(screenshot.readUInt32BE(20)).toBe(full.height); // PNG IHDR height.
});
