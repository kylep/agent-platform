import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const finding=(index=0)=>({code:'UD-LINT-CONTRAST',severity:'warning',node_id:'box',mode:'dark',message:`Contrast needs visual review ${index}.`,suggestion:'Choose a darker text colour.',approximate:true});
const result=()=>({version:3,rules_version:'lint-1',findings:[finding()],total:1,by_severity:{warning:1,info:0,error:0},next_cursor:null as string|null,truncated:false});
async function setup(page:Page,availableModes=['dark','light']){
  await mockApi(page);const queries:URLSearchParams[]=[];let value=result(),held:Promise<void>|null=null,failed=false;
  const box={id:'box',type:'text',title:'Greeting',x:10,y:20,w:100,h:80,text:'Hello'};
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access') || url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:3,versions:[3,1].map((version)=>({version,author:'kyle'}))}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:[box]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'box',type:'text',authored:box,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"/>'});
    if(/^\/api\/ui-designer\/docs\/[^/]+$/.test(url.pathname))return r.fulfill({json:{doc:{modes:availableModes}}});
    if(url.pathname.endsWith('/lint')){queries.push(url.searchParams);if(held)await held;return failed?r.fulfill({status:502,json:{detail:'Design check unavailable.'}}):r.fulfill({json:{...value,version:Number(url.searchParams.get('version'))}});}
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'Findings',exact:true}).click();
  return {queries,setResult:(next:ReturnType<typeof result>)=>{value=next;},hold:(promise:Promise<void>)=>{held=promise;},fail:()=>{failed=true;}};
}

test('findings are explicit pinned advisory checks with codes suggestions and node selection',async({page})=>{
  const state=await setup(page);expect(state.queries).toHaveLength(0);
  await page.getByRole('button',{name:'Run lint',exact:true}).click();
  await expect(page.getByText('Showing 1 of 1 findings.')).toBeVisible();await expect(page.getByText('UD-LINT-CONTRAST',{exact:true})).toBeVisible();
  await expect(page.getByText('Approximate — review visually',{exact:false})).toBeVisible();await expect(page.getByText('Suggested change: Choose a darker text colour.')).toBeVisible();
  expect(Object.fromEntries(state.queries[0])).toEqual({version:'3',limit:'50',mode:'dark'});
  await page.getByRole('button',{name:'box',exact:true}).click();await expect(page.getByLabel('Text',{exact:true})).toHaveValue('Hello');
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('node severity all modes and historical version filters are mutually correct',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'text Greeting',exact:true}).click();
  await page.getByLabel('Findings scope').selectOption('selection');await page.getByLabel('Findings modes').selectOption('all');await page.getByLabel('Finding severity').selectOption('info');
  await page.getByLabel('Version',{exact:true}).selectOption('1');
  state.setResult({...result(),findings:[],total:0});await page.getByRole('button',{name:'Run lint',exact:true}).click();
  await expect(page.getByText('No findings for these filters.')).toBeVisible();
  expect(Object.fromEntries(state.queries[0])).toEqual({version:'1',limit:'50',node:'box',all_modes:'true',severity:'info'});
  await expect(page.getByText('does not establish accessibility compliance',{exact:false})).toBeVisible();
});

test('pagination is explicit with display and engine caps disclosed',async({page})=>{
  const state=await setup(page);state.setResult({...result(),total:2,next_cursor:'next-1'});
  await page.getByRole('button',{name:'Run lint',exact:true}).click();await expect(page.getByRole('button',{name:'Load more findings'})).toBeVisible();
  state.setResult({...result(),findings:[finding(1)],total:2,truncated:true});await page.getByRole('button',{name:'Load more findings'}).click();
  await expect(page.getByText('Showing 2 of 2 findings.')).toBeVisible();expect(state.queries[1].get('cursor')).toBe('next-1');
  await expect(page.getByText('Additional issues may exist.',{exact:false})).toBeVisible();
  state.setResult({...result(),findings:Array.from({length:200},(_,i)=>finding(i)),total:400,next_cursor:'more'});await page.getByRole('button',{name:'Run lint',exact:true}).click();
  await expect(page.getByText('Showing the first 200 findings.',{exact:false})).toBeVisible();await expect(page.getByRole('button',{name:'Load more findings'})).toHaveCount(0);
});

test('loading filters disable and stale mode results are discarded, errors can retry',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));
  await page.getByRole('button',{name:'Run lint',exact:true}).click();await expect(page.getByLabel('Finding severity')).toBeDisabled();
  await page.evaluate(()=>{document.documentElement.dataset.theme='light';});await expect(page.getByRole('button',{name:'Run lint',exact:true})).toBeEnabled();release();
  await expect(page.getByRole('list',{name:'Findings list'})).toHaveCount(0);
  state.fail();await page.getByRole('button',{name:'Run lint',exact:true}).click();await expect(page.getByRole('alert')).toHaveText('Design check unavailable.');
  expect(state.queries.at(-1)?.get('mode')).toBe('light');
});

test('leaving the document aborts findings and never populates a new scope',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));await page.getByRole('button',{name:'Run lint',exact:true}).click();
  await page.getByRole('link',{name:'UI Designer',exact:true}).last().click();await page.getByRole('link',{name:'Other',exact:true}).click();release();
  await expect(page.getByRole('heading',{name:'UI Designer / other'})).toBeVisible();await expect(page.getByRole('list',{name:'Findings list'})).toHaveCount(0);
});


test('documents without modes omit mode and off-canvas findings are labeled',async({page})=>{
  const state=await setup(page,[]);state.setResult({...result(),findings:[{...finding(),node_id:'component-definition'}]});
  await page.getByRole('button',{name:'Run lint',exact:true}).click();
  await expect(page.getByText('component-definition (not available on this canvas)')).toBeVisible();
  expect(state.queries[0].has('mode')).toBe(false);expect(state.queries[0].has('all_modes')).toBe(false);
});
