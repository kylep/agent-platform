import { expect, test, type Page } from "@playwright/test";
import type { RNode, UiEditOp } from "../src/lib/uiDesigner";
import type { PlatformLibrary } from "../src/lib/uiPlatformComponents";
import { mockApi } from "./mock-api";

const library: PlatformLibrary = {
  id: "agent-platform",
  revision: 1,
  components: [
    {
      name: "Button",
      category: "Actions",
      import_path: "@ap/ui/button",
      description: "Trigger an action.",
      story_id: "ui-button--default",
      variants: [
        { id: "default", label: "Primary · md · enabled", selection: { style: "primary", size: "md", availability: "enabled" }, props: { variant: "primary" }, width: 80, height: 32 },
        { id: "danger", label: "Danger · md · enabled", selection: { style: "danger", size: "md", availability: "enabled" }, props: { variant: "danger" }, width: 80, height: 32 },
      ],
      properties: [{ name: "children", type: "text", default: "Continue", prop: "children" }],
      limitations: ["Add the click handler in your application."],
    },
    {
      name: "Input",
      category: "Fields",
      import_path: "@ap/ui/field",
      description: "Enter a value.",
      variants: [{ id: "default", label: "Default", selection: {}, props: {}, width: 160, height: 32 }],
      properties: [],
      limitations: [],
    },
  ],
};

type Edit = { ops: UiEditOp[]; expected_version: number; request_id: string };
type Node = RNode & { variant?: Record<string, string>; props?: Record<string, string>; fill?: string };
const exactSnippet = 'import { Button } from "@ap/ui/button";\n\n<Button variant="danger">Delete item</Button>';

async function setup(page: Page, { historical = false, reader = false, seed = false, trimSnippet = false } = {}) {
  await mockApi(page);
  const frame: RNode = { id: "frame", type: "frame", title: "Work area", x: 0, y: 0, w: 320, h: 240 };
  const children: Node[] = [frame];
  const placed: Node[] = seed ? [{
    id: "seeded-button", title: "Button", type: "frame", of: "ap-ui-button",
    variant: { style: "danger", size: "md", availability: "enabled" }, props: { children: "Delete item" },
  }] : [];
  frame.children = placed;
  const writes: Edit[] = [];
  const receipts = new Map<string, { version: number; undo_ops: UiEditOp[] }>();
  let version = 1;
  let failure: "uncertain" | "conflict" | null = null;

  await page.route("**/api/whoami", (route) => route.fulfill({ json: { role: reader ? "reader" : "admin" } }));
  await page.route("**/api/ui-designer/**", async (route) => {
    const url = new URL(route.request().url());
    const body = (json: unknown) => route.fulfill({ json });
    if (url.pathname.endsWith("/component-library")) return body(library);
    if (url.pathname.endsWith("/comments")) return body({ comments: [] });
    if (url.pathname.endsWith("/fonts")) return body({ fonts: [] });
    if (url.pathname.endsWith("/versions")) return body({ version, versions: [{ version: 1, author: "kyle" }] });
    if (url.pathname.endsWith("/resolved")) return body({ pages: [{ id: "page", title: "Main page", children }] });
    if (url.pathname.endsWith("/render")) return route.fulfill({
      contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"/>',
    });
    if (url.pathname.endsWith("/inspect")) {
      const node = [...children, ...placed].find((item) => item.id === url.searchParams.get("node"));
      const implementation = node?.of ? {
        status: node.fill ? "detached" : "exact",
        component: "Button", import_path: "@ap/ui/button", revision: 1, variant: node.variant?.style === "primary" ? "default" : node.variant?.style,
        snippet: node.fill || trimSnippet ? null : exactSnippet,
        story_id: "ui-button--default",
      } : null;
      return body({ id: node?.id, type: node?.type, version: Number(url.searchParams.get("version")), authored: node, implementation });
    }
    if (url.pathname.endsWith("/edit")) {
      const edit = route.request().postDataJSON() as Edit;
      writes.push(edit);
      const prior = receipts.get(edit.request_id);
      if (prior) return body({ ...prior, replayed: true });
      if (failure === "conflict") {
        failure = null;
        version += 1;
        return route.fulfill({ status: 409, json: { detail: "Version conflict." } });
      }
      const inverse: UiEditOp[] = [];
      for (const op of edit.ops) {
        if (op.op === "platform_component") {
          const destination = op.parent === "frame" ? placed : children;
          destination.push({ id: op.id, title: op.component, type: "frame", of: "ap-ui-button", variant: { style: op.variant === "default" ? "primary" : op.variant, size: "md", availability: "enabled" }, props: op.props });
          inverse.push({ op: "remove", ids: [op.id] });
        } else if (op.op === "remove") {
          for (const group of [placed, children]) {
            for (const id of op.ids) {
              const index = group.findIndex((node) => node.id === id);
              if (index >= 0) group.splice(index, 1);
            }
          }
        } else if (op.op === "update") {
          const node = [...placed, ...children].find((item) => item.id === op.id);
          if (node) Object.assign(node, op.set);
        }
      }
      const receipt = { version: ++version, undo_ops: inverse };
      receipts.set(edit.request_id, receipt);
      if (failure === "uncertain") {
        failure = null;
        return route.fulfill({ status: 502, json: { detail: "Proxy failed after save." } });
      }
      return body(receipt);
    }
    return route.fallback();
  });
  await page.goto(`/ui-designer/demo${historical ? "?version=1" : ""}`);
  await page.getByRole("button", { name: "Components", exact: true }).click();
  const components = page.getByRole("region", { name: "Components", exact: true });
  return { components, writes, placed, fail: (mode: typeof failure) => { failure = mode; } };
}

test("browse Agent Platform components, insert in the selected frame, and inspect the implementation", async ({ page }) => {
  const { components, writes } = await setup(page);
  await expect(components.getByRole("heading", { name: "Agent Platform" })).toBeVisible();
  await components.getByLabel("Category").selectOption("Actions");
  await expect(components.getByRole("button", { name: "Input", exact: true })).toHaveCount(0);
  await components.getByLabel("Search components").fill("missing");
  await expect(components.getByText("No components match these filters.")).toBeVisible();
  await components.getByLabel("Search components").fill("button");
  await components.getByRole("button", { name: "Button", exact: true }).click();
  await expect(components.getByText("Insert into Main page")).toBeVisible();
  await page.getByRole("button", { name: "frame Work area" }).click();
  await expect(components.getByText("Insert into Work area")).toBeVisible();
  await components.getByLabel("Style").selectOption({ label: "Danger" });
  await components.getByLabel("Text", { exact: true }).fill("Delete item");
  await components.getByRole("button", { name: "Insert component" }).click();
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeEnabled();
  expect(writes[0]).toMatchObject({
    expected_version: 1, ops: [{ op: "platform_component", component: "Button", variant: "danger", props: { children: "Delete item" }, parent: "frame", revision: 1, id: expect.stringMatching(/^component-[a-f0-9]+$/) }],
  });

  await page.getByRole("button", { name: "frame Button" }).click();
  const implementation = page.getByRole("region", { name: "Platform implementation" });
  await expect(implementation.getByText("@ap/ui/button", { exact: true })).toBeVisible();
  await expect(implementation.getByLabel("Implementation JSX")).toHaveValue(exactSnippet);
  await expect(implementation.getByRole("link", { name: "Button in Storybook" })).toHaveAttribute("href", /ui-button--default$/);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await implementation.getByRole("button", { name: "Copy JSX" }).click();
  await expect(implementation.getByRole("status")).toHaveText("Copied JSX.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(exactSnippet);
  await implementation.getByLabel("Style").selectOption({ label: "Primary" });
  await implementation.getByRole("button", { name: "Save component properties" }).click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1].ops[0]).toMatchObject({ op: "update", set: { variant: { style: "primary", size: "md", availability: "enabled" }, props: { children: "Delete item" } } });
  await page.getByLabel("Fill (colour or token)", { exact: true }).fill("$color.warning");
  await page.getByRole("button", { name: "Save node" }).click();
  await expect(implementation.getByText(/Local changes have detached/)).toBeVisible();
  await expect(implementation.getByRole("button", { name: "Copy JSX" })).toHaveCount(0);
});

test("insertion recovers an uncertain receipt and remains undoable", async ({ page }) => {
  const { components, writes, placed, fail } = await setup(page);
  await page.getByRole("button", { name: "frame Work area" }).click();
  await components.getByRole("button", { name: "Button", exact: true }).click();
  const insert = components.getByRole("button", { name: "Insert component" });
  fail("uncertain");
  await insert.click();
  await expect(insert).toBeDisabled();
  await page.getByRole("button", { name: "Retry save" }).click();
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeEnabled();
  expect(writes[1]).toEqual(writes[0]);
  expect(placed).toHaveLength(1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByRole("button", { name: "Redo", exact: true })).toBeEnabled();
  expect(placed).toHaveLength(0);
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await expect.poll(() => placed.length).toBe(1);
  expect(writes[3].ops).toEqual(writes[0].ops);
});

test("a version conflict keeps the configured component and blocks insertion until reload", async ({ page }) => {
  const { components, writes, fail } = await setup(page);
  await components.getByRole("button", { name: "Button", exact: true }).click();
  await components.getByLabel("Text", { exact: true }).fill("Publish");
  const insert = components.getByRole("button", { name: "Insert component" });
  fail("conflict");
  await insert.click();
  await expect(page.getByText(/Another edit arrived first/)).toBeVisible();
  await expect(insert).toBeDisabled();
  await page.getByRole("button", { name: "Discard draft and reload inspector" }).click();
  await expect(components.getByLabel("Text", { exact: true })).toHaveValue("Publish");
  await insert.click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]).toMatchObject({ expected_version: 2, ops: [{ parent: "page", props: { children: "Publish" } }] });
});

for (const mode of ["historical", "reader"] as const) {
  test(`${mode} views can browse the library without writing`, async ({ page }) => {
    const { components, writes } = await setup(page, { [mode]: true });
    await components.getByRole("button", { name: "Button", exact: true }).click();
    await components.getByLabel("Style").selectOption({ label: "Danger" });
    await expect(components.getByRole("button", { name: "Insert component" })).toBeDisabled();
    expect(writes).toHaveLength(0);
  });
}

test("an instance draft remains visible while a newer version requires reload", async ({ page }) => {
  const { fail } = await setup(page, { seed: true });
  await page.getByRole("button", { name: "frame Button" }).click();
  const implementation = page.getByRole("region", { name: "Platform implementation" });
  const text = implementation.getByLabel("Text", { exact: true });
  await text.fill("Unsaved draft");
  fail("conflict");
  await implementation.getByRole("button", { name: "Save component properties" }).click();
  await expect(page.getByRole("option", { name: "Latest (v2)" })).toBeAttached();
  await expect(text).toHaveValue("Unsaved draft");
  await expect(text).toBeDisabled();
  await page.getByRole("button", { name: "Discard draft and reload inspector" }).click();
  await expect(text).toHaveValue("Delete item");
  await expect(text).toBeEnabled();
});

test("read-only inspections display the mapping when JSX exceeds the response budget", async ({ page }) => {
  const { writes } = await setup(page, { reader: true, seed: true, trimSnippet: true });
  await page.getByRole("button", { name: "frame Button" }).click();
  const implementation = page.getByRole("region", { name: "Platform implementation" });
  await expect(implementation.getByText("@ap/ui/button", { exact: true })).toBeVisible();
  await expect(implementation.getByText("JSX is unavailable for this inspection.")).toBeVisible();
  await expect(implementation.getByRole("button", { name: "Copy JSX" })).toHaveCount(0);
  await expect(implementation.getByRole("button", { name: "Save component properties" })).toBeDisabled();
  expect(writes).toHaveLength(0);
});
