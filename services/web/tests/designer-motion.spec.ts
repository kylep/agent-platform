import { expect,test,type Page } from '@playwright/test';
import { mockApi } from './mock-api';
import { motionEase } from '../src/lib/uiMotion';
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';
const manifest={renderer_version:'test',motion_version:'motion-1',width:100,height:100,box:[0,0,100,100],duration_ms:400,fps:24,frames:10,easing:'ease-in-out',from_image:png,to_image:png,assets:{a:png,b:png},layers:[{id:'shared',from:{x:0,y:0,w:20,h:20,rotate:0,opacity:1,asset:'a'},to:{x:70,y:70,w:30,h:30,rotate:90,opacity:0.5,asset:'b'}}],warnings:['Complex effects use a frame dissolve.']};
async function setup(page:Page,role='admin'){
  await mockApi(page);await page.route('**/api/whoami',r=>r.fulfill({json:{role}}));
  const previews:Record<string,unknown>[]=[],exports:Record<string,unknown>[]=[];let held:Promise<void>|null=null,fail=false;
  const tree={pages:[{id:'page',title:'Main',children:[{id:'start',title:'Start',type:'frame',x:0,y:0,w:100,h:100,children:[{id:'next',title:'Next',type:'rect',x:0,y:0,w:30,h:30,link_to:{target:'finish',transition:'smart-animate',duration_ms:400,easing:'ease-out'}}]},{id:'finish',title:'Finish',type:'frame',x:120,y:0,w:100,h:100,link_to:{target:'start'}}]}]};
  await page.route('**/api/ui-designer/**',async r=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:7,versions:[{version:7,author:'kyle'},{version:3,author:'kyle'}]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:tree});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/png',body:Buffer.from(png,'base64')});
    if(url.pathname.endsWith('/motion/preview')){const body=r.request().postDataJSON();previews.push(body);if(held)await held;return r.fulfill({json:{...manifest,duration_ms:body.duration_ms,easing:body.easing}});}
    if(url.pathname.endsWith('/motion/export')){const body=r.request().postDataJSON();exports.push(body);if(fail){fail=false;return r.fulfill({status:502,json:{detail:'Proxy unavailable'}});}return r.fulfill({json:{artifact_id:'ab'.repeat(16),name:`motion.${body.format}`,url:'https://untrusted.invalid/',warnings:['Silent animation.']}});}
    return r.fulfill({status:404,json:{}});
  });
  return {previews,exports,hold:(promise:Promise<void>)=>{held=promise;},fail:()=>{fail=true;}};
}
test('smart playback uses pinned canonical manifest and disables hotspots until complete',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo/play?version=3&mode=light');
  await page.getByRole('button',{name:'Go to finish from Next'}).click();
  await expect(page.getByLabel('Motion preview')).toBeVisible();
  await expect(page.locator('.ud-prototype-hotspot')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();
  expect(state.previews).toEqual([{version:3,mode:'light',from_node:'start',to_node:'finish',duration_ms:400,easing:'ease-out',fps:24,max_edge:720}]);
  await expect(page.getByRole('list',{name:'Motion notes'})).toContainText('Complex effects');
  await expect(page.getByRole('button',{name:'Go to start from Finish'})).toBeVisible();
});
test('reduced motion skips smart request and stale manifest cannot replace restart',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});const state=await setup(page);
  await page.goto('/ui-designer/demo/play?version=3');await page.getByRole('button',{name:'Go to finish from Next'}).click();
  await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();expect(state.previews).toHaveLength(0);
  await page.emulateMedia({reducedMotion:'no-preference'});await page.getByRole('button',{name:'Restart',exact:true}).click();
  let release!:()=>void;state.hold(new Promise<void>(resolve=>{release=resolve;}));
  await page.getByRole('button',{name:'Go to finish from Next'}).click();await expect.poll(()=>state.previews.length).toBe(1);
  await page.getByRole('button',{name:'Restart',exact:true}).click();release();
  await expect(page.getByRole('button',{name:'Go to finish from Next'})).toBeVisible();await expect(page.getByLabel('Motion preview')).toHaveCount(0);
});
test('motion export pins inputs and retries uncertain outcome with same receipt, safe links and mobile layout',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo?version=3');
  await page.getByLabel('Motion duration (ms)').fill('600');await page.getByLabel('Motion format').selectOption('gif');
  state.fail();await page.getByRole('button',{name:'Export motion',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry motion export'})).toBeVisible();await expect(page.getByLabel('Motion duration (ms)')).toBeDisabled();
  await page.getByRole('button',{name:'Retry motion export'}).click();
  await expect(page.getByRole('link',{name:'Download motion'})).toHaveAttribute('href',`/api/artifacts/${'ab'.repeat(16)}/content`);
  expect(state.exports).toHaveLength(2);expect(state.exports[0]).toEqual(state.exports[1]);expect(state.exports[0]).toMatchObject({version:3,mode:'dark',from_node:'start',to_node:'finish',duration_ms:600,format:'gif'});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('reader can preview but not export, and invalid duration is local',async({page})=>{
  const state=await setup(page,'reader');await page.goto('/ui-designer/demo?version=3');
  await expect(page.getByRole('button',{name:'Export motion',exact:true})).toBeDisabled();
  await page.getByLabel('Motion duration (ms)').fill('10');await page.getByRole('button',{name:'Preview motion',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('200 to 5000');expect(state.previews).toHaveLength(0);
});


test('invalid motion image falls back safely without following an external image URL',async({page})=>{
  await setup(page);
  await page.route('**/motion/preview',r=>r.fulfill({json:{...manifest,from_image:'https://untrusted.invalid/image.png'}}));
  const external:string[]=[];page.on('request',request=>{if(request.url().includes('untrusted.invalid'))external.push(request.url());});
  await page.goto('/ui-designer/demo/play?version=3');await page.getByRole('button',{name:'Go to finish from Next'}).click();
  await expect(page.getByRole('list',{name:'Motion notes'})).toContainText('Showing the destination frame instead');
  await expect(page.getByRole('button',{name:'Go to start from Finish'})).toBeVisible();expect(external).toEqual([]);
});
test('leaving an export scope ignores a late successful receipt',async({page})=>{
  await setup(page);let release!:()=>void;const wait=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/motion/export',async r=>{await wait;await r.fulfill({json:{artifact_id:'ab'.repeat(16),name:'old.mp4'}});});
  await page.goto('/ui-designer/demo?version=3');await page.getByRole('button',{name:'Export motion',exact:true}).click();
  await expect(page.getByText('Exporting motion…',{exact:true})).toBeVisible();
  await page.getByLabel('Version',{exact:true}).selectOption('7');release();
  await expect(page.getByRole('heading',{name:'Motion',exact:true})).toBeVisible();
  await expect(page.getByRole('link',{name:'Download motion'})).toHaveCount(0);
});


test('motion easing matches the canonical polynomial timeline',()=>{
  expect(motionEase(.25,'linear')).toBe(.25);
  expect(motionEase(.25,'ease-in')).toBe(.0625);
  expect(motionEase(.25,'ease-out')).toBe(.4375);
  expect(motionEase(.25,'ease-in-out')).toBe(.125);
  expect(motionEase(.75,'ease-in-out')).toBe(.875);
  expect(motionEase(-1,'linear')).toBe(0);expect(motionEase(2,'linear')).toBe(1);
});
