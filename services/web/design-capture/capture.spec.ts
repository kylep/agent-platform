import {expect, test} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {mockApi} from '../tests/mock-api.ts';
import {captureScene} from './extract.ts';
import {prepareCapture} from './fixtures.ts';
const inventory = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8')) as {
  routes: {slug:string; group:string; title:string; path:string; source:string; heading?:string; probe?:string; scenario?:string}[];
};

const output = fileURLToPath(new URL('../../../.ap/site-design-capture/captures/', import.meta.url));
const maxHeight = 6000;

for (const route of inventory.routes) {
  test(`${route.group}: ${route.title} [${route.slug}]`, async ({page}) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => localStorage.setItem('theme', 'dark'));
    await page.clock.setFixedTime(new Date());
    const unmatched = await mockApi(page);
    await prepareCapture(page, route);
    // Captures are entirely local, including their API fixtures.
    await page.route('**/*', async intercepted => {
      const url = new URL(intercepted.request().url());
      if (['data:', 'blob:'].includes(url.protocol) || url.origin === 'http://127.0.0.1:6009') {
        return intercepted.fallback();
      }
      return intercepted.abort('blockedbyclient');
    });
    await page.goto(route.path, {waitUntil:'domcontentloaded'});
    if (route.heading) {
      await expect(page.getByRole('heading', {name:new RegExp(route.heading, 'i')}).first()).toBeVisible();
    }
    if (route.probe) await expect(page.locator('body')).toContainText(new RegExp(route.probe, 'i'));
    await page.evaluate(async () => { await document.fonts.ready; });
    // Allow fixture effects and local images to finish without waiting on SSE.
    await page.waitForTimeout(300);
    expect(errors, 'page exceptions').toEqual([]);
    expect(unmatched, 'missing fixture reads').toEqual([]);
    const scene = await page.evaluate(captureScene, {fullPage:true, maxHeight, maxPrimitives:12_000});
    for (const primitive of scene.primitives) {
      if (primitive.kind !== 'image') continue;
      const dataUrl = await page.evaluate(async src => {
        if (src.startsWith('data:')) return src;
        if (!src.startsWith('blob:') && new URL(src, location.href).origin !== location.origin) return null;
        const response = await fetch(src);
        if (!response.ok) return null;
        const blob = await response.blob();
        if (blob.size > 8 * 1024 * 1024) return null;
        return await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result));
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        });
      }, primitive.src);
      if (dataUrl) Object.assign(primitive, {dataUrl});
    }
    await mkdir(output, {recursive:true});
    await writeFile(`${output}/${route.slug}.json`, JSON.stringify({route, mode:'dark', scene}, null, 2));
    await page.screenshot({path:`${output}/${route.slug}.png`,
      clip:{x:0,y:0,width:scene.width,height:scene.height}, fullPage:true, animations:'disabled'});
    expect(scene.primitives.length, 'editable visual content').toBeGreaterThan(3);
  });
}
