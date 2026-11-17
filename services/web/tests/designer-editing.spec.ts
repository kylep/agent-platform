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
  const writes: { ops: { op: string; id: string; set: Record<string, unknown> }[]; expected_version: number; request_id: string }[] = [];
  const receipts = new Map<string, number>();
  function resolvedNodes(list: Node[] = nodes): Node[] { return list.map((node) => ({ ...node, w: Math.min(typeof node.max_w === 'number' ? node.max_w : 100000, Math.max(typeof node.min_w === 'number' ? node.min_w : 0, node.w)), h: Math.min(typeof node.max_h === 'number' ? node.max_h : 100000, Math.max(typeof node.min_h === 'number' ? node.min_h : 0, node.h)), ...(Array.isArray(node.children) ? { children: resolvedNodes(node.children as Node[]) } : {}) })); }
  const inverseReceipts = new Map<string, unknown>();
  let resizeModel = false;
  const child: Node = { id: "child", type: "rect", title: "Pinned child", x: 20, y: 10, w: 80, h: 20, max_w: 90, constraints: { horizontal: "stretch" } };
  function findNode(id: string, list: Node[] = nodes): Node | undefined {
    for (const node of list) { if (node.id === id) return node; if (Array.isArray(node.children)) { const hit = findNode(id, node.children as Node[]); if (hit) return hit; } }
    return undefined;
  }
  let failAfterCommit = false;
  let failNetwork = false, conflict = false, ownEvent = false, externalDuringSave = false;
  await page.route('**/api/whoami', (r) => r.fulfill({ json: { role } }));
  await page.route('**/api/ui-designer/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/ui-designer/docs') return route.fulfill({ json: { docs: [{ slug: 'second', title: 'Second document', owner: 'kyle' }] } });
    if (url.pathname.endsWith('/events')) return route.fulfill({ body: '' });
    if (url.pathname.endsWith('/versions')) return route.fulfill({ json: { version, versions: Array.from({ length: version }, (_, i) => ({ version: version-i, author: 'kyle' })) } });
    if (url.pathname.endsWith('/comments')) return route.fulfill({ json: { comments: [] } });
    if (url.pathname.endsWith('/resolved')) return route.fulfill({ json: { pages: [{ id: 'page', title: 'Main', children: resolvedNodes() }] } });
    if (url.pathname.endsWith('/inspect')) {
      const n = findNode(url.searchParams.get('node') || '');
      return route.fulfill({ json: { id: n?.id, type: n?.type, version: Number(url.searchParams.get('version')), authored: n, text: n?.id === 'text' ? 'Hello' : undefined } });
    }
    if (url.pathname.endsWith('/render')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="250"/>' });
    if (url.pathname.endsWith('/edit')) {
      const body = route.request().postDataJSON(); writes.push(body);
      if (receipts.has(body.request_id)) return route.fulfill({ json: { version: receipts.get(body.request_id), replayed: true, undo_ops: inverseReceipts.get(body.request_id), warnings: [] } });
      if (failNetwork) { failNetwork = false; return route.abort('failed'); }
      if (conflict || body.expected_version !== version) { conflict = false; version++; return route.fulfill({ status: 409, json: { detail: 'Version conflict' } }); }
      let undoOps: unknown;
      for (const op of body.ops) {
        const target = findNode(op.id)!;
        if (resizeModel && op.op === 'resize' && op.id === 'frame') {
          const before = Object.fromEntries(Object.keys(op.set).map((key) => [key, target[key] ?? null]));
          const oldWidth = target.w, oldChildWidth = child.w;
          Object.assign(target, op.set); child.w = Math.min(90, oldChildWidth + target.w - oldWidth);
          undoOps = [{ op: 'update', id: 'frame', set: before }, { op: 'update', id: 'child', set: { w: oldChildWidth } }];
        } else Object.assign(target, op.set);
      }
      version++; receipts.set(body.request_id, version); inverseReceipts.set(body.request_id, undoOps);
      if (failAfterCommit) { failAfterCommit = false; return route.fulfill({ status: 502, json: { detail: "Upstream unavailable" } }); }
      if (ownEvent || externalDuringSave) await page.evaluate((v) => (window as unknown as { designEvent: (ev: unknown) => void }).designEvent({ slug: 'demo', type: 'version', version: v }), externalDuringSave ? ++version : version);
      await new Promise((resolve) => setTimeout(resolve, 50));
      return route.fulfill({ json: { version: externalDuringSave ? version-1 : version, undo_ops: undoOps, warnings: [] } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto('/ui-designer/demo');
  await expect(page.getByRole('button', { name: 'rect Box' })).toBeVisible();
  return { writes, nodes, child, configureResize: () => { resizeModel = true; nodes[2].children = [child]; }, failAfterCommit: () => { failAfterCommit = true; }, failNetwork: () => { failNetwork = true; }, conflict: () => { conflict = true; }, ownEvent: () => { ownEvent = true; }, externalDuringSave: () => { externalDuringSave = true; }, external: async () => {
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


test('save undo and redo work when secure-context randomUUID is unavailable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window.crypto, 'randomUUID', { value: undefined }));
  const state = await setup(page); await pick(page);
  await page.getByLabel('Width', { exact: true }).fill('120'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Redo', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Redo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(3);
  expect(state.writes.map((w) => w.expected_version)).toEqual([1, 2, 3]);
  expect(state.writes.every((w) => /^[0-9a-f]{32}$/.test(w.request_id))).toBe(true);
  expect(new Set(state.writes.map((w) => w.request_id)).size).toBe(3);
});

test('prototype link fields save atomically and undo restores absent link', async ({ page }) => {
  const state = await setup(page); await pick(page);
  await page.getByLabel('Destination frame').selectOption('frame');
  await page.getByLabel('Trigger', { exact: true }).selectOption('after-delay');
  await page.getByLabel('Transition', { exact: true }).selectOption('dissolve');
  await page.getByLabel('Delay (ms)', { exact: true }).fill('500');
  await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  expect(state.writes[0].ops[0].set).toEqual({ link_to: { target: 'frame', trigger: 'after-delay', transition: 'dissolve', duration_ms: 200, delay_ms: 500 } });
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2); expect(state.writes[1].ops[0].set).toEqual({ link_to: null });
  await expect(page.getByRole('link', { name: 'Play prototype' })).toHaveAttribute('href', /version=3/);
});

test('typography edits preserve rich authored text and undo restores absent properties', async ({ page }) => {
  const state = await setup(page); await pick(page, 'text Greeting');
  const text = structuredClone(state.nodes[1].text);
  await page.getByLabel('Text horizontal alignment').selectOption('justify');
  await page.getByLabel('Text vertical alignment').selectOption('center');
  await page.getByLabel('Text decoration').selectOption('underline');
  await page.getByLabel('Text case', { exact: true }).selectOption('uppercase');
  await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  expect(state.writes[0].ops).toEqual([{ op: 'update', id: 'text', set: { align: 'justify', vertical_align: 'center', decoration: 'underline', text_case: 'uppercase' } }]);
  expect(state.nodes[1].text).toEqual(text);
  await expect(page.getByLabel('Text', { exact: true })).toHaveValue('Hello');
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1].ops).toEqual([{ op: 'update', id: 'text', set: { align: null, vertical_align: null, decoration: null, text_case: null } }]);
  expect(state.nodes[1].text).toEqual(text);
});

test('typography default clears only that field and controls respect historical views', async ({ page }) => {
  const state = await setup(page); state.nodes[1].text_case = 'lowercase'; state.nodes[1].decoration = 'line-through';
  await pick(page, 'text Greeting'); await page.getByLabel('Text case', { exact: true }).selectOption('');
  await page.getByRole('button', { name: 'Save node' }).click();
  await expect.poll(() => state.writes.length).toBe(1); expect(state.writes[0].ops[0].set).toEqual({ text_case: null });
  expect(state.nodes[1].decoration).toBe('line-through');
  await page.getByLabel('Version', { exact: true }).selectOption('1'); await pick(page, 'text Greeting');
  await expect(page.getByLabel('Text horizontal alignment')).toBeDisabled(); await expect(page.getByLabel('Text vertical alignment')).toBeDisabled();
  await expect(page.getByLabel('Text decoration')).toBeDisabled(); await expect(page.getByLabel('Text case', { exact: true })).toBeDisabled();
});


test('grid mode and row wrap preserve shared layout and remove incompatible fields', async ({ page }) => {
  const state = await setup(page); state.nodes[2].layout = { dir: 'row', gap: 4, pad: [4, 8], wrap: true, justify: 'center' };
  await pick(page, 'frame Container'); await page.getByLabel('Direction', { exact: true }).selectOption('grid');
  await page.getByLabel('Grid columns', { exact: true }).fill('3'); await page.getByLabel('Row gap', { exact: true }).fill('8'); await page.getByLabel('Column gap', { exact: true }).fill('12');
  await page.getByRole('button', { name: 'Save node' }).click(); await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].ops[0].set.layout).toEqual({ dir: 'grid', gap: 4, pad: [4, 8], columns: 3, row_gap: 8, column_gap: 12 });
  await page.getByLabel('Direction', { exact: true }).selectOption('column'); await page.getByLabel('Wrap', { exact: true }).selectOption('true');
  await page.getByRole('button', { name: 'Save node' }).click(); await expect.poll(() => state.writes.length).toBe(2);
  expect(state.writes[1].ops[0].set.layout).toEqual({ dir: 'column', gap: 4, pad: [4, 8], wrap: true });
});

test('bounds and constraints write one resize while contradictory bounds stay local', async ({ page }) => {
  const state = await setup(page); await pick(page);
  await page.getByLabel('Minimum width', { exact: true }).fill('201'); await page.getByLabel('Maximum width', { exact: true }).fill('100');
  await page.getByRole('button', { name: 'Save node' }).click(); await expect(page.getByRole('alert')).toHaveText('Minimum size cannot exceed maximum size.'); expect(state.writes).toHaveLength(0);
  await page.getByLabel('Minimum width', { exact: true }).fill('50'); await page.getByLabel('Maximum width', { exact: true }).fill('200');
  await page.getByLabel('Horizontal resize constraint').selectOption('end'); await page.getByLabel('Vertical resize constraint').selectOption('stretch');
  await page.getByRole('button', { name: 'Save node' }).click(); await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0].ops).toEqual([{ op: 'resize', id: 'box', set: { min_w: 50, max_w: 200, constraints: { horizontal: 'end', vertical: 'stretch' } } }]);
});

test('server inverse restores clamped children exactly after uncertain resize and redo remains resize', async ({ page }) => {
  const state = await setup(page); state.configureResize(); await pick(page, 'frame Container'); state.failAfterCommit();
  await page.getByLabel('Width', { exact: true }).fill('300'); await page.getByRole('button', { name: 'Save node' }).click();
  await expect(page.getByRole('button', { name: 'Retry save' })).toBeVisible(); expect(state.child.w).toBe(90);
  await page.getByRole('button', { name: 'Retry save' }).click(); await expect(page.getByRole('button', { name: 'Undo', exact: true })).toBeEnabled();
  expect(state.writes[0]).toEqual(state.writes[1]); expect(state.writes[0].ops[0].op).toBe('resize');
  await page.getByRole('button', { name: 'Undo', exact: true }).click(); await expect(page.getByRole('button', { name: 'Redo', exact: true })).toBeEnabled();
  expect(state.writes[2].ops).toEqual([{ op: 'update', id: 'frame', set: { w: 150 } }, { op: 'update', id: 'child', set: { w: 80 } }]);
  expect(state.child.w).toBe(80); expect(state.nodes[2].w).toBe(150);
  await page.getByRole('button', { name: 'Redo', exact: true }).click(); await expect.poll(() => state.writes.length).toBe(4);
  expect(state.writes[3].ops).toEqual([{ op: 'resize', id: 'frame', set: { w: 300 } }]); expect(state.child.w).toBe(90);
});


test('canvas resizing starts at resolved clamped size instead of authored size', async ({ page }) => {
  const state = await setup(page); state.nodes[0].min_w = 200; state.nodes[0].max_w = 250;
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; }); await pick(page);
  await expect(page.locator('.ud-hit[data-selected]')).toHaveAttribute('width', '200');
  const handle = await page.locator('[data-resize=se]').boundingBox(); if (!handle) throw new Error('Missing resize handle');
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2); await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + 20, handle.y + handle.height / 2 + 10); await page.mouse.up();
  await expect.poll(() => state.writes.length).toBe(1); expect(state.writes[0].ops).toEqual([{ op: 'resize', id: 'box', set: { w: 220, h: 90 } }]);
});

test('conic paint and backdrop controls preserve layers and exact undo with CAS', async ({ page }) => {
  const state = await setup(page);
  const fill = [{ color: '$color.surface', opacity: 0.3 }, { gradient: 'angular', angle: 17, opacity: 0.7, stops: [[0,'#000000'],[1,'#ffffff']] }, { image: 'a'.repeat(64), fit: 'cover' }];
  state.nodes[0].fill = structuredClone(fill); state.nodes[0].backdrop_blur = 8;
  await pick(page);
  await page.getByLabel('Gradient angle, layer 2').fill('90');
  await page.getByRole('button',{name:'Add stop, layer 2',exact:true}).click();
  await page.getByLabel('Colour or token, layer 2 stop 1',{exact:true}).fill('#ff0000');
  await page.getByLabel('Position, layer 2 stop 2',{exact:true}).fill('0.5');
  await page.getByLabel('Colour or token, layer 2 stop 2',{exact:true}).fill('#00ff00');
  await page.getByLabel('Colour or token, layer 2 stop 3',{exact:true}).fill('#0000ff');
  await page.getByLabel('Backdrop blur (px)').fill('20');
  await page.getByRole('button', {name:'Save node'}).click();
  await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();
  expect(state.writes[0].expected_version).toBe(1);
  expect(state.writes[0].ops[0].set).toEqual({backdrop_blur:20,fill:[fill[0],{...fill[1],angle:90,stops:[[0,'#ff0000'],[0.5,'#00ff00'],[1,'#0000ff']]},fill[2]]});
  await page.getByRole('button',{name:'Undo',exact:true}).click();
  await expect(page.getByRole('button',{name:'Redo',exact:true})).toBeEnabled();
  expect(state.writes[1].ops[0].set).toEqual({backdrop_blur:8,fill});
  await page.getByLabel('Title',{exact:true}).fill('Preserved');
  await page.getByRole('button',{name:'Save node'}).click();
  await expect.poll(()=>state.writes.length).toBe(3);
  expect(state.writes[2].ops[0].set).toEqual({title:'Preserved'});
});

test('conic creation, bounded validation and clearing backdrop blur', async ({page})=>{
  const state=await setup(page); await pick(page);
  await page.getByRole('button',{name:'Replace fill with conic gradient'}).click();
  await expect(page.getByLabel('Gradient type, layer 1')).toHaveValue('angular');
  await page.getByLabel('Backdrop blur (px)').fill('65');
  await page.getByRole('button',{name:'Save node'}).click();
  await expect(page.getByRole('alert')).toContainText('0 to 64'); expect(state.writes).toHaveLength(0);
  await page.getByLabel('Backdrop blur (px)').fill('');
  await page.getByLabel('Position, layer 1 stop 1',{exact:true}).fill('1');
  await page.getByLabel('Position, layer 1 stop 2',{exact:true}).fill('0');
  await page.getByRole('button',{name:'Save node'}).click(); await expect(page.getByRole('alert')).toContainText('ascending');
  await page.getByLabel('Position, layer 1 stop 1',{exact:true}).fill('0');
  await page.getByLabel('Position, layer 1 stop 2',{exact:true}).fill('1');
  await page.getByLabel('Colour or token, layer 1 stop 1',{exact:true}).fill('#000000');
  await page.getByLabel('Colour or token, layer 1 stop 2',{exact:true}).fill('#ffffff');
  await expect(page.getByRole('button',{name:'Remove stop 1, layer 1',exact:true})).toBeDisabled();
  for(let i=0;i<6;i++) await page.getByRole('button',{name:'Add stop, layer 1',exact:true}).click();
  await expect(page.getByRole('button',{name:'Add stop, layer 1',exact:true})).toBeDisabled();
  for(let i=8;i>2;i--) await page.getByRole('button',{name:`Remove stop ${i}, layer 1`,exact:true}).click();
  await page.getByRole('button',{name:'Save node'}).click();
  await expect.poll(()=>state.writes.length).toBe(1);
  expect(state.writes[0].ops[0].set).toEqual({backdrop_blur:null,fill:{gradient:'angular',stops:[[0,'#000000'],[1,'#ffffff']]}});
});


test('smart transition and motion keys preserve metadata with CAS undo',async({page})=>{
  const state=await setup(page);state.nodes[0].meta={purpose:'retained'};await pick(page);
  await page.getByLabel('Motion key',{exact:true}).fill('shared-card');
  await page.getByLabel('Destination frame').selectOption('frame');
  await page.getByLabel('Transition',{exact:true}).selectOption('smart-animate');
  await page.getByLabel('Motion easing',{exact:true}).selectOption('ease-in');
  await page.getByRole('button',{name:'Save node'}).click();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();
  expect(state.writes[0].ops[0].set).toEqual({meta:{purpose:'retained',motion_key:'shared-card'},link_to:{target:'frame',trigger:'tap',transition:'smart-animate',easing:'ease-in',duration_ms:800}});
  await page.getByRole('button',{name:'Undo',exact:true}).click();await expect(page.getByRole('button',{name:'Redo',exact:true})).toBeEnabled();
  expect(state.writes[1].ops[0].set).toEqual({meta:{purpose:'retained'},link_to:null});
});
