import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';

async function setup(page:Page, role='admin') {
  await mockApi(page);
  const writes: Record<string,unknown>[]=[]; let failed=false; let held:Promise<void>|null=null;
  const artifact='ab'.repeat(16);
  const box={id:'box',type:'rect',title:'Box',x:10,y:20,w:100,h:80,fill:'#aabbcc'};
  await page.route('**/api/whoami',(r)=>r.fulfill({json:{role}}));
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access') || url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:9,versions:[{version:9,author:'kyle'},{version:3,author:'kyle'}]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:[box]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'box',type:'rect',authored:box,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"/>'});
    if(url.pathname.endsWith('/export')){
      const body=r.request().postDataJSON();writes.push(body);
      if(held)await held;
      if(failed){failed=false;return r.fulfill({status:503,json:{detail:'Export renderer unavailable.'}});}
      return r.fulfill({json:{artifact_id:artifact,name:`design.${body.format}`,url:'javascript:bad()',width:100,height:80,mime:'text/html',warnings:['<script> is exported as text.']}});
    }
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  return {writes,artifact,fail:()=>{failed=true;},hold:(promise:Promise<void>)=>{held=promise;}};
}

for(const format of ['png','svg','pdf','html'])test(`exports ${format} with explicit version and safe artifact links`,async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');
  await expect(page.getByLabel('Export format')).toHaveValue('png');
  await page.getByLabel('Export format').selectOption(format);
  await page.getByRole('button',{name:'Export design',exact:true}).click();
  await expect(page.getByRole('link',{name:'Open artifact'})).toHaveAttribute('href',`/artifacts/${state.artifact}`);
  await expect(page.getByRole('link',{name:'Download export'})).toHaveAttribute('href',`/api/artifacts/${state.artifact}/content`);
  expect(state.writes).toEqual([{format,node:'page',version:9,mode:'dark',scale:1}]);
  await expect(page.locator('iframe')).toHaveCount(0);await expect(page.getByText('<script> is exported as text.')).toBeVisible();
  if(format==='html')await expect(page.getByText('Static HTML and CSS;',{exact:false})).toBeVisible();
});

test('selected node and historical version remain explicit in export payload',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo?version=3');
  await page.getByRole('button',{name:'rect Box',exact:true}).click();
  await page.getByLabel('Export scope').selectOption('selection');
  await page.getByLabel('Export format').selectOption('svg');
  await page.getByRole('button',{name:'Export design',exact:true}).click();
  await expect(page.getByRole('link',{name:'Open artifact'})).toBeVisible();
  expect(state.writes[0]).toMatchObject({node:'box',version:3,format:'svg'});
  await expect(page.getByRole('button',{name:'Save node'})).toBeDisabled();
});

test('exports guard concurrent clicks, show failures, and retry',async({page})=>{
  const state=await setup(page);state.fail();let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));
  await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'Export design',exact:true}).click();
  await expect(page.getByRole('button',{name:'Exporting…'})).toBeDisabled();await expect(page.getByLabel('Export format')).toBeDisabled();
  release();await expect(page.getByRole('alert')).toHaveText('Export renderer unavailable.');
  await page.getByRole('button',{name:'Export design',exact:true}).click();await expect(page.getByRole('link',{name:'Open artifact'})).toBeVisible();
  expect(state.writes).toHaveLength(2);
});

test('reader exports disabled and late response cannot reach another document',async({page})=>{
  await setup(page,'reader');await page.goto('/ui-designer/demo');await expect(page.getByRole('button',{name:'Export design',exact:true})).toBeDisabled();
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));
  await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'Export design',exact:true}).click();
  await page.getByRole('link',{name:'UI Designer',exact:true}).last().click();await page.getByRole('link',{name:'Other',exact:true}).click();
  release();await expect(page.getByRole('heading',{name:'UI Designer / other'})).toBeVisible();
  await expect(page.getByRole('link',{name:'Open artifact'})).toHaveCount(0);
});
