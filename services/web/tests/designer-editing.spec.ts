import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./mock-api";

type Node = { id: string; type: string; title: string; x: number; y: number; w: number; h: number; [key: string]: unknown };
async function setup(page: Page, role = "admin") {
  await mockApi(page);
  await page.addInitScript(() => {
    const streams: { listener?: (e: { data: string }) => void }[] = [];
    class FakeSource {
      listener?: (e: { data: string }) => void;
      constructor() { streams.push(this); }
      addEventListener(name: string, fn: (e: { data: string }) => void) { if (name === 'design') this.listener = fn; }
      close() { this.listener = undefined; }
    }
    Object.assign(window, { EventSource: FakeSource, designEvent: (ev: unknown) => streams.forEach((s) => s.listener?.({ data: JSON.stringify(ev) })) });
  });
  let version = 1;
  const nodes: Node[] = [
    { id: "box", type: "rect", title: "Box", x: 10, y: 20, w: 100, h: 80, fill: "#aabbcc" },
    { id: "text", type: "text", title: "Greeting", x: 200, y: 20, w: 100, h: 40, text: [{ text: "Hello", style: { weight: 700 } }] },
    { id: "frame", type: "frame", title: "Container", x: 10, y: 150, w: 150, h: 100, layout: { dir: "row", gap: 4, pad: [4, 8] }, children: [] },
  ];
  const writes: { ops: { id: string; set: Record<string, unknown> }[]; expected_version: number; request_id: string }[] = [];
  const receipts = new Map<string, number>();
  let failAfterCommit = false;
  let failNetwork = false, conflict = false, ownEvent = false, externalDuringSave = false;
  await page.route('**/api/whoami', (r) => r.fulfill({ json: { role } }));
  await page.route('**/api/ui-designer/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/ui-designer/docs') return route.fulfill({ json: { docs: [{ slug: 'second', title: 'Second document', owner: 'kyle' }] } });
    if (url.pathname.endsWith('/events')) return route.fulfill({ body: '' });
    if (url.pathname.endsWith('/versions')) return route.fulfill({ json: { version, versions: Array.from({ length: version }, (_, i) => ({ version: version-i, author: 'kyle' })) } });
    if (url.pathname.endsWith('/comments')) return route.fulfill({ json: { comments: [] } });
    if (url.pathname.endsWith('/resolved')) return route.fulfill({ json: { pages: [{ id: 'page', title: 'Main', children: nodes }] } });
    if (url.pathname.endsWith('/inspect')) {
      const n = nodes.find((n) => n.id === url.searchParams.get('node'));
      return route.fulfill({ json: { id: n?.id, type: n?.type, version: Number(url.searchParams.get('version')), authored: n, text: n?.id === 'text' ? 'Hello' : undefined } });
    }
    if (url.pathname.endsWith('/render')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="250"/>' });
    if (url.pathname.endsWith('/edit')) {
      const body = route.request().postDataJSON(); writes.push(body);
      if (receipts.has(body.request_id)) return route.fulfill({ json: { version: receipts.get(body.request_id), replayed: true, warnings: [] } });
      if (failNetwork) { failNetwork = false; return route.abort('failed'); }
      if (conflict || body.expected_version !== version) { conflict = false; version++; return route.fulfill({ status: 409, json: { detail: 'Version conflict' } }); }
      for (const op of body.ops) Object.assign(nodes.find((n) => n.id === op.id)!, op.set);
      version++; receipts.set(body.request_id, version);
      if (failAfterCommit) { failAfterCommit = false; return route.fulfill({ status: 502, json: { detail: "Upstream unavailable" } }); }
      if (ownEvent || externalDuringSave) await page.evaluate((v) => (window as unknown as { designEvent: (ev: unknown) => void }).designEvent({ slug: 'demo', type: 'version', version: v }), externalDuringSave ? ++version : version);
      await new Promise((resolve) => setTimeout(resolve, 50));
      return route.fulfill({ json: { version: externalDuringSave ? version-1 : version, warnings: [] } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto('/ui-designer/demo');
  await expect(page.getByRole('button', { name: 'rect Box' })).toBeVisible();
  return { writes, nodes, failAfterCommit: () => { failAfterCommit = true; }, failNetwork: () => { failNetwork = true; }, conflict: () => { conflict = true; }, ownEvent: () => { ownEvent = true; }, externalDuringSave: () => { externalDuringSave = true; }, external: async () => {
    version++;
    await page.evaluate((v) => (window as unknown as { designEvent: (ev: unknown) => void }).designEvent({ slug: 'demo', type: 'version', version: v }), version);
  } };
}

async function pick(page: Page, name = 'rect Box') {
  await page.getByRole('button', { name, exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save node' })).toBeVisible();
}

test('property edits, undo/redo and own SSE preserve CAS history', async ({ page }) => {
  const state = await setup(page); state.ownEvent(); await pick(page);
  await page.getByLabel('Width', { exact: true }).fill('120');
  await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Redo', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(3);
  expect(state.writes.map((w) => w.expected_version)).toEqual([1,2,3]);
  expect(state.writes[1].ops).toEqual([{ op: 'update', id: 'box', set: { w: 100 } }]);
});

test('text and layout edits preserve authored values for undo', async ({ page }) => {
  const state = await setup(page); await pick(page, 'text Greeting');
  const original = structuredClone(state.nodes[1].text);
  await page.getByLabel('Text', { exact: true }).fill('New text'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled(); await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2); expect(state.writes[1].ops[0].set.text).toEqual(original);
  await pick(page, 'frame Container'); await page.getByLabel('Gap', { exact: true }).fill('12'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect.poll(() => state.writes.length).toBe(3); expect(state.writes[2].ops[0].set.layout).toEqual({ dir: 'row', gap: 12, pad: [4,8] });
});

test('uncertain save blocks writes and retries exact receipt', async ({ page }) => {
  const state = await setup(page); await pick(page); state.failNetwork();
  await page.getByLabel('Width', { exact: true }).fill('120'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Retry save' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save node' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Retry save' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  expect(state.writes[0]).toEqual(state.writes[1]);
});

test('external SSE preserves draft but clears undo, including event before save response', async ({ page }) => {
  const state = await setup(page); await pick(page); state.externalDuringSave();
  await page.getByLabel('Width', { exact: true }).fill('120'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Discard draft and reload inspector' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Save node' })).toBeDisabled();
  await expect(page.getByLabel('Width', { exact: true })).toHaveValue('120');
});

test('conflicting undo never rebases or retries', async ({ page }) => {
  const state = await setup(page); await pick(page);
  await page.getByLabel('Width', { exact: true }).fill('120'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled(); state.conflict();
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByText('Another edit arrived first.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled(); expect(state.writes.length).toBe(2);
});

test('reader and historical views cannot edit', async ({ page }) => {
  await setup(page, 'reader'); await pick(page);
  await expect(page.getByRole('button', { name: 'Save node' })).toBeDisabled(); await expect(page.getByRole('button', { name: 'Undo', exact: true })).toHaveCount(0);
  await page.goto('/ui-designer/demo?version=1'); await pick(page);
  await expect(page.getByRole('button', { name: 'Save node' })).toBeDisabled();
});

test('canvas drag respects zoom, resize and nudge each commit once', async ({ page }) => {
  const state = await setup(page); await pick(page);
  await page.getByRole('button', { name: 'Zoom in' }).click();
  const box = await page.locator('.ud-hit[data-selected]').boundingBox(); if (!box) throw new Error('No selection');
  await page.mouse.move(box.x+20,box.y+20); await page.mouse.down(); await page.mouse.move(box.x+45,box.y+32.5); await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1); expect(state.writes[0].ops[0].set).toEqual({ x:30,y:30 });
  await expect(page.locator('[data-resize=se]')).toBeVisible();
  const handle = await page.locator('[data-resize=se]').boundingBox(); if (!handle) throw new Error('No resize');
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2); await page.mouse.down(); await page.mouse.move(handle.x+handle.width/2+25,handle.y+handle.height/2+25); await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(2); expect(state.writes[1].ops[0].set).toEqual({ w:120,h:100 });
  await expect(page.locator('[data-resize=se]')).toBeVisible(); await page.locator('.ud-viewport').focus(); await page.keyboard.press('Shift+ArrowRight');
  await expect.poll(() => state.writes.length).toBe(3); expect(state.writes[2].ops[0].set).toEqual({ x:40,y:30 });
});


test('returning from history repins edits, document navigation drops uncertain receipt', async ({ page }) => {
  const state = await setup(page); await pick(page);
  await page.getByLabel('Version', { exact: true }).selectOption('1');
  await expect(page.getByText('Viewing a historical version.', { exact: false })).toBeVisible();
  await page.getByLabel('Version', { exact: true }).selectOption('');
  await pick(page); await page.getByLabel('Width', { exact: true }).fill('123');
  await page.getByRole('button', { name: 'Save node' }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  state.failNetwork(); await page.getByLabel('Width', { exact: true }).fill('124'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Retry save' })).toBeVisible();
  await page.getByRole('link', { name: 'UI Designer', exact: true }).last().click();
  await page.getByRole('link', { name: 'Second document' }).click();
  await pick(page);
  await expect(page.getByRole('button', { name: 'Retry save' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeDisabled();
  expect(state.writes.length).toBe(2);
});

test('editor visual review captures desktop and mobile selection', async ({ page }) => {
  await setup(page); await pick(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: '/tmp/ui-designer-editor-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '/tmp/ui-designer-editor-mobile.png', fullPage: true });
});


test('a proxy failure after commit retries receipt without applying twice', async ({ page }) => {
  const state = await setup(page); await pick(page); state.failAfterCommit();
  await page.getByLabel('Width', { exact: true }).fill('125'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Retry save' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry save' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  expect(state.writes[0]).toEqual(state.writes[1]);
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(3);
  expect(state.writes[2].expected_version).toBe(2);
  expect(state.writes[2].ops[0].set.w).toBe(100);
});
