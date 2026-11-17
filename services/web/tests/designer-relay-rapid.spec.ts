import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aV9kAAAAASUVORK5CYII=','base64');
async function relay(page:Page,body:string,privateDoc=false){
  await mockApi(page);let version=9,denied=privateDoc;const renders:string[]=[];
  await page.addInitScript(()=>{
    const streams:{url:string;listeners:Record<string,((event:{data:string})=>void)[]>;closed:boolean}[]=[];
    class Stream{url:string;listeners:Record<string,((event:{data:string})=>void)[]>={};closed=false;constructor(url:string){this.url=url;streams.push(this);}addEventListener(name:string,callback:(event:{data:string})=>void){(this.listeners[name]??=[]).push(callback);}close(){this.closed=true;}}
    Object.assign(window,{EventSource:Stream,designStreamCount:()=>streams.filter((s)=>s.url==='/api/ui-designer/events'&&!s.closed).length,designEmit:(event:unknown)=>streams.filter((s)=>!s.closed&&s.url==='/api/ui-designer/events').forEach((s)=>s.listeners.design?.forEach((fn)=>fn({data:JSON.stringify(event)})))});
  });
  await page.route('**/api/relay/channels/rc1/messages?*',(r)=>r.fulfill({json:[{id:'design-msg',channel_id:'rc1',author:'user:kyle',kind:'text',body,card:null,reply_to:null,thread_root:null,run_id:null,hop:0,mentions:[],created_at:new Date().toISOString(),edited_at:null,face:null,reactions:[]}]}));
  await page.route('**/api/ui-designer/docs/*/versions',(r)=>denied?r.fulfill({status:403,json:{detail:'private'}}):r.fulfill({json:{version,versions:[]}}));
  await page.route('**/api/ui-designer/docs/*/render?*',(r)=>{renders.push(r.request().url());return r.fulfill({contentType:'image/png',body:png});});
  await page.goto('/relay?channel=rc1');
  return{renders,setVersion:(value:number)=>{version=value;},deny:()=>{denied=true;}};
}
async function emit(page:Page,event:unknown){await page.evaluate((value)=>(window as unknown as {designEmit:(event:unknown)=>void}).designEmit(value),event);}

test('Relay design references keep code protected and share one live preview stream',async({page})=>{
  const state=await relay(page,'Review [[ui:demo#box]] and [[ui:demo/other]]. `[[ui:quoted#box]]` [link [[ui:inside]]](https://example.com)');
  await expect(page.getByAltText('Latest design preview for demo node box')).toBeVisible();await expect(page.getByAltText('Latest design preview for demo node other')).toBeVisible();
  await expect(page.locator('[aria-label="Design quoted node box"]')).toHaveCount(0);expect(await page.evaluate(()=>(window as unknown as {designStreamCount:()=>number}).designStreamCount())).toBe(1);
  expect(state.renders.every((url)=>url.includes('max_edge=320')&&url.includes('version=9'))).toBe(true);
  await expect(page.getByRole('link',{name:'UI Designer · demo #box',exact:true})).toHaveAttribute('href','/ui-designer/demo?node=box');
  state.setVersion(10);await emit(page,{type:'version',slug:'demo',version:10});await expect.poll(()=>state.renders.some((url)=>url.includes('version=10'))).toBe(true);
  state.setVersion(1);await emit(page,{type:'deleted',slug:'demo'});await emit(page,{type:'version',slug:'demo',version:1});await expect.poll(()=>state.renders.some((url)=>url.includes('version=1&'))).toBe(true);
});

test('Relay unavailable designs are gentle and per-message previews are bounded',async({page})=>{
  const state=await relay(page,Array.from({length:12},(_,i)=>`[[ui:design-${i}]]`).join(' '),true);
  await page.locator('.ud-reference').first().scrollIntoViewIfNeeded();await expect(page.getByText('Design unavailable or private.').first()).toBeVisible();
  await expect(page.locator('.ud-reference')).toHaveCount(12);expect(state.renders.length).toBeLessThanOrEqual(8);
});

async function rapid(page:Page){
  await mockApi(page);await page.route('**/api/agents',(r)=>r.fulfill({json:[{name:'pai',agent_type:'persona',enabled:true,system:false}]}));let chats:Record<string,any>[]=[];const bodies:Record<string,any>[]=[];
  await page.route('**/api/rapid-chats?*',(r)=>r.fulfill({json:chats}));
  await page.route('**/api/rapid-chats/*/turns?*',(r)=>r.fulfill({json:[]}));
  await page.route('**/api/rapid-chats',async(r)=>{const body=r.request().postDataJSON();bodies.push(body);const chat={id:'rapid-1',...body,state:'completed',areas:[],started_at:null,ended_at:null,duration_minutes:60,max_turns:200,turns_total:0,last_quota:null,last_error:null,end_reason:null,created_at:new Date().toISOString(),output_ref:{kind:body.sink.kind,slug:body.sink.slug,version:2},summary:null};chats=[chat];await r.fulfill({json:chat});});
  await page.goto('/rapid-chats');await page.getByRole('button',{name:'New rapid-chat'}).click();await page.getByLabel('Title',{exact:true}).fill('Design sprint');await page.getByLabel('Goal',{exact:true}).fill('Design a useful screen.');await page.getByRole('checkbox',{name:'pai',exact:true}).check();
  return bodies;
}

test('Rapid Chat UI Designer output is replace-only and links its pinned document',async({page})=>{
  const bodies=await rapid(page);await page.getByLabel('Output kind').selectOption('ui_designer');await expect(page.getByLabel('Mode',{exact:true})).toBeDisabled();await expect(page.getByLabel('Mode',{exact:true})).toHaveValue('replace');await page.getByLabel('UI Designer output slug').fill('sprint-design');await page.getByRole('button',{name:'Launch',exact:true}).click();
  await expect(page.getByRole('link',{name:'sprint-design',exact:true})).toHaveAttribute('href','/ui-designer/sprint-design?version=2');expect(bodies[0].sink).toEqual({kind:'ui_designer',slug:'sprint-design',mode:'replace'});
});

test('Rapid Chat wiki output keeps append mode and its existing wiki link',async({page})=>{
  const bodies=await rapid(page);await page.getByLabel('Wiki output slug').fill('sprint-notes');await page.getByLabel('Mode',{exact:true}).selectOption('append');await page.getByRole('button',{name:'Launch',exact:true}).click();await expect(page.getByRole('link',{name:'sprint-notes',exact:true})).toHaveAttribute('href','/wiki/sprint-notes');expect(bodies[0].sink).toEqual({kind:'wiki',slug:'sprint-notes',mode:'append'});
});
