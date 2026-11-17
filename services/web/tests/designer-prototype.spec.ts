import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
import type { RNode, UiPrototypeLink } from '../src/lib/uiDesigner';

function documentTree(link: UiPrototypeLink = { target:'finish' }) {
  const start: RNode = { id:'start',type:'frame',title:'Start',x:100,y:200,w:200,h:120,children:[
    {id:'next',type:'rect',title:'Next',x:110,y:210,w:40,h:30,link_to:link},
    {id:'hidden',type:'rect',x:110,y:210,w:40,h:30,visible:false,link_to:{target:'finish'}},
    {id:'outside',type:'rect',x:500,y:500,w:40,h:30,link_to:{target:'finish'}},
  ] };
  const finish: RNode = {id:'finish',type:'frame',title:'Finish',x:400,y:200,w:200,h:120,children:[]};
  return {pages:[{id:'page',children:[start,finish]}]};
}
async function mockPrototype(page:Page, tree = documentTree()) {
  await mockApi(page); const requests: string[]=[]; let head=7, failImage=false;
  await page.route('**/api/ui-designer/**', async (r)=>{
    const url=new URL(r.request().url()); requests.push(url.pathname+url.search);
    if(url.pathname.endsWith('/access'))return r.fallback();
    if(url.pathname.endsWith('/versions')) return r.fulfill({json:{version:head,versions:[{version:head,author:'kyle'}]}});
    if(url.pathname.endsWith('/resolved')) return r.fulfill({json:tree});
    if(url.pathname.endsWith('/comments')) return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/render')) return failImage ? r.fulfill({status:500,body:'failed'}) : r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"/>'});
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  return {requests,setHead:(v:number)=>{head=v;},failImage:()=>{failImage=true;}};
}
const image=(page:Page,title:string)=>page.getByRole('img',{name:`Prototype frame ${title}`,exact:true});
const next=(page:Page)=>page.getByRole('button',{name:'Go to finish from Next'});

test('viewer pins one version, crops hotspots, supports navigation and mobile keyboard',async({page})=>{
  const state=await mockPrototype(page);await page.goto('/ui-designer/demo/play?mode=light');
  await expect(next(page)).toBeVisible(); state.setHead(8);
  expect(await next(page).evaluate((el)=>({left:el.style.left,top:el.style.top}))).toEqual({left:'5%',top:'8.33333%'});
  await expect(page.locator('.ud-prototype-hotspot')).toHaveCount(1);
  await next(page).focus();await page.keyboard.press('Enter');
  await expect(image(page,'Finish')).toBeVisible();await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Back',exact:true}).click();await expect(next(page)).toBeVisible();
  await page.getByRole('button',{name:'Restart',exact:true}).click();await expect(next(page)).toBeVisible();
  expect(state.requests.filter((p)=>p.endsWith('/versions'))).toHaveLength(1);
  expect(state.requests.filter((p)=>p.includes('/render?')||p.includes('/resolved?')).every((p)=>p.includes('version=7')&&p.includes('mode=light'))).toBe(true);
  await expect(page.getByRole('heading',{name:'Prototype · demo · v7'})).toBeVisible();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('hover and delay triggers honor motion and delay does not respond to clicks',async({page})=>{
  await mockPrototype(page,documentTree({target:'finish',trigger:'after-delay',delay_ms:300,transition:'slide',duration_ms:200}));
  await page.goto('/ui-designer/demo/play?version=3&frame=start');await expect(image(page,'Start')).toHaveAttribute('data-loaded','true');
  await expect(page.locator('.ud-prototype-hotspot')).toHaveCount(0);
  await page.locator('.ud-prototype-stage').click({position:{x:20,y:20}});await expect(image(page,'Start')).toBeVisible();
  await expect(image(page,'Finish')).toBeVisible();await expect(image(page,'Finish')).toHaveAttribute('data-transition','slide');
  await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();
  await mockPrototype(page,documentTree({target:'finish',trigger:'hover',transition:'dissolve',duration_ms:200}));
  await page.goto('/ui-designer/demo/play?version=3');await expect(next(page)).toBeVisible();await next(page).hover();
  await expect(image(page,'Finish')).toHaveAttribute('data-transition','dissolve');
  await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();
});

test('reduced motion is instant and expanded component links remain interactive',async({page})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  const tree=documentTree({target:'finish',transition:'dissolve',duration_ms:1500});tree.pages[0].children[0].children![0].id='instance/next';
  await mockPrototype(page,tree);await page.goto('/ui-designer/demo/play?version=2');await next(page).click();
  await expect(image(page,'Finish')).toHaveAttribute('data-transition','instant');await expect(page.getByText('Reduced motion:',{exact:false})).toBeVisible();
});

test('automatic chains pause, self links do not storm and restart resets loading',async({page})=>{
  const tree=documentTree({target:'finish',trigger:'after-delay',delay_ms:100});
  tree.pages[0].children[1].link_to={target:'start',trigger:'after-delay',delay_ms:100};
  const state=await mockPrototype(page,tree);await page.goto('/ui-designer/demo/play?version=4');
  await expect(page.getByRole('button',{name:'Resume playback'})).toBeVisible({timeout:10000});
  expect(state.requests.filter((p)=>p.includes('/render?')).length).toBeLessThanOrEqual(26);
  await page.getByRole('button',{name:'Restart',exact:true}).click();await expect(page.getByRole('button',{name:'Resume playback'})).toHaveCount(0);
  await mockPrototype(page,documentTree({target:'start',trigger:'after-delay',delay_ms:100}));
  await page.goto('/ui-designer/demo/play?version=4');await expect(next(page)).toHaveCount(0);await expect(image(page,'Start')).toBeVisible();
  await page.waitForTimeout(250);await expect(image(page,'Start')).toBeVisible();
});

test('clip rectangles and paint order, hotspot cap, invalid inputs and image failures',async({page})=>{
  const tree=documentTree();tree.pages[0].children[0].children!.push({id:'clipper',type:'frame',x:150,y:200,w:20,h:20,clip:true,children:[{id:'clipchild',type:'rect',x:160,y:210,w:100,h:100,link_to:{target:'finish'}}]});
  const state=await mockPrototype(page,tree);await page.goto('/ui-designer/demo/play?version=7');
  const clipped=page.getByRole('button',{name:'Go to finish from clipchild'});await expect(clipped).toBeVisible();
  expect(await clipped.evaluate((e)=>e.style.width)).toBe('5%');
  state.failImage();await page.getByLabel('Prototype frame').selectOption('finish');await expect(page.getByRole('alert')).toContainText('Could not render');
  const huge=documentTree();huge.pages[0].children[0].children=Array.from({length:1001},(_,i)=>({id:`n-${i}`,type:'rect',x:110,y:210,w:5,h:5,link_to:{target:'finish'}}));
  await mockPrototype(page,huge);await page.goto('/ui-designer/demo/play?version=7');await expect(page.getByRole('alert')).toContainText('1,000');
  await page.goto('/ui-designer/demo/play?version=9007199254740992');await expect(page.getByRole('alert')).toContainText('valid version');
  await page.goto('/ui-designer/demo/play?mode=evil');await expect(page.getByRole('alert')).toContainText('dark or light');
});

test('leaving a loading viewer discards late document responses',async({page})=>{
  await mockPrototype(page);let release:()=>void=()=>{};const held=new Promise<void>((r)=>{release=r;});
  await page.route('**/api/ui-designer/docs/slow/resolved?*',async(r)=>{await held;await r.fulfill({json:documentTree()});});
  await page.goto('/ui-designer/slow/play?version=3');await expect(page.getByText('Loading prototype…')).toBeVisible();
  await page.getByRole('link',{name:'Exit prototype'}).click();
  await page.getByRole('link',{name:'UI Designer',exact:true}).last().click();await page.getByRole('link',{name:'Other',exact:true}).click();
  release();await expect(page.getByRole('heading',{name:'UI Designer / other'})).toBeVisible();await expect(page.getByRole('img',{name:'Prototype frame Start'})).toHaveCount(0);
});


test('slow image transition starts after load and restart waits before automatic playback',async({page})=>{
  const state=await mockPrototype(page,documentTree({target:'finish',transition:'dissolve',duration_ms:200}));
  let release:()=>void=()=>{};const held=new Promise<void>((r)=>{release=r;});
  await page.route('**/api/ui-designer/docs/demo/render?*',async(r)=>{
    if(new URL(r.request().url()).searchParams.get('node')==='finish')await held;
    await r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"/>'});
  });
  await page.goto('/ui-designer/demo/play?version=7');await next(page).click();
  await expect(image(page,'Finish')).toHaveAttribute('data-loaded','false');
  await page.waitForTimeout(250);await expect(image(page,'Finish')).toHaveAttribute('data-transition','instant');
  await expect(page.locator('.ud-prototype-previous')).toHaveCount(1);
  release();await expect(image(page,'Finish')).toHaveAttribute('data-transition','dissolve');
  await expect(page.getByRole('button',{name:'Back',exact:true})).toBeEnabled();await expect(page.locator('.ud-prototype-previous')).toHaveCount(0);
  await page.screenshot({path:'/tmp/ui-designer-prototype-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/ui-designer-prototype-mobile.png',fullPage:true});
  expect(state.requests.filter((p)=>p.endsWith('/versions'))).toHaveLength(0);
});
