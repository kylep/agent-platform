import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV9kAAAAASUVORK5CYII=';
const receipt=()=>({image:png,base_image:png,mime:'image/png',width:200,height:120,box:[-10,20,200,120],changed_pixels:24,total_pixels:24000,regions:[{x:0,y:30,w:10,h:10,node_ids:['box']}],node_ids:['box'],truncated:false,warnings:[]});
async function setup(page:Page){
  await mockApi(page);const posts:Record<string,unknown>[]=[];let result=receipt(), failure=false, held:Promise<void>|null=null;
  const box={id:'box',type:'rect',title:'Box',x:0,y:20,w:100,h:80};
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:3,versions:[3,2,1].map((version)=>({version,author:'kyle'}))}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:[box]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'box',type:'rect',authored:box,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"/>'});
    if(url.pathname.endsWith('/diff'))return r.fulfill({json:{chunks:[{version:3,ops:[{op:'update',id:'box'}]}],truncated:false}});
    if(url.pathname.endsWith('/visual-diff')){
      posts.push(r.request().postDataJSON());if(held)await held;
      return failure?r.fulfill({status:502,json:{detail:'Visual renderer unavailable.'}}):r.fulfill({json:result});
    }
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'Compare',exact:true}).click();
  return {posts,setResult:(value:ReturnType<typeof receipt>)=>{result=value;},fail:()=>{failure=true;},hold:(promise:Promise<void>)=>{held=promise;}};
}

test('visual compare retains side by side history and opacity is entirely local',async({page})=>{
  const state=await setup(page);await expect(page.getByRole('img',{name:'Version 2',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Compare visually'}).click();
  await expect(page.getByText('24 of 24,000 pixels changed at tolerance 0.')).toBeVisible();
  await expect(page.getByRole('img',{name:'Aligned target version'})).toHaveAttribute('src',`data:image/png;base64,${png}`);
  const overlay=page.getByRole('img',{name:'Visual differences from v2 to v3'});await expect(overlay).toBeVisible();
  await page.getByLabel('Heatmap opacity').fill('25');await expect(overlay).toHaveCSS('opacity','0.25');
  expect(state.posts).toEqual([{from_version:2,to_version:3,node:'page',mode:'dark',scale:1,tolerance:0}]);
  await expect(page.getByRole('img',{name:'Changed regions'}).locator('rect')).toHaveAttribute('x','0');
  await page.getByLabel('Show changed regions').uncheck();await expect(page.getByRole('img',{name:'Changed regions'})).toHaveCount(0);
  await page.getByRole('button',{name:'box',exact:true}).click();await expect(page.getByLabel('Width',{exact:true})).toHaveValue('100');
  await page.getByText('Document edits',{exact:true}).click();await expect(page.getByText('v3: update box')).toBeVisible();
});

test('explicit versions selection tolerance and unchanged results are clear',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'rect Box',exact:true}).click();
  await page.getByLabel('Compare scope',{exact:true}).selectOption('selection');
  await page.getByLabel('Compare from',{exact:true}).selectOption('1');await page.getByLabel('Compare to',{exact:true}).selectOption('2');
  await page.getByLabel('Visual difference tolerance').fill('12');state.setResult({...receipt(),changed_pixels:0,regions:[],node_ids:[]});
  await page.getByRole('button',{name:'Compare visually'}).click();await expect(page.getByText('No visual differences at tolerance 12.')).toBeVisible();
  expect(state.posts[0]).toEqual({from_version:1,to_version:2,node:'box',mode:'dark',scale:1,tolerance:12});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('invalid tolerance and image are rejected and renderer errors remain actionable',async({page})=>{
  const state=await setup(page);await page.getByLabel('Visual difference tolerance').fill('256');await page.getByRole('button',{name:'Compare visually'}).click();
  await expect(page.getByRole('alert')).toHaveText('Tolerance must be an integer from 0 to 255.');expect(state.posts).toHaveLength(0);
  await page.getByLabel('Visual difference tolerance').fill('0');state.setResult({...receipt(),image:'<svg/onload=alert(1)>'});await page.getByRole('button',{name:'Compare visually'}).click();
  await expect(page.getByRole('alert')).toHaveText('The visual comparison returned an invalid image.');
  state.fail();await page.getByRole('button',{name:'Compare visually'}).click();await expect(page.getByRole('alert')).toHaveText('Visual renderer unavailable.');
});

test('inflight controls disable and stale scope results cannot appear',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));
  await page.getByRole('button',{name:'Compare visually'}).click();await expect(page.getByRole('button',{name:'Comparing…'})).toBeDisabled();
  await expect(page.getByLabel('Compare from',{exact:true})).toBeDisabled();
  await page.evaluate(()=>{document.documentElement.dataset.theme='light';});
  await expect(page.getByRole('button',{name:'Compare visually'})).toBeEnabled();release();
  await expect(page.getByRole('img',{name:'Visual differences from v2 to v3'})).toHaveCount(0);
  await page.getByRole('button',{name:'Compare visually'}).click();await expect(page.getByRole('img',{name:'Visual differences from v2 to v3'})).toBeVisible();
  expect(state.posts.at(-1)?.mode).toBe('light');
});

test('document navigation cancels a pending compare without leaking its result',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));
  await page.getByRole('button',{name:'Compare visually'}).click();
  await page.getByRole('link',{name:'UI Designer',exact:true}).last().click();await page.getByRole('link',{name:'Other',exact:true}).click();release();
  await expect(page.getByRole('heading',{name:'UI Designer / other'})).toBeVisible();await expect(page.getByRole('img',{name:'Visual differences from v2 to v3'})).toHaveCount(0);
});
