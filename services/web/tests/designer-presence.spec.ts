import {expect,test,type Page} from '@playwright/test';
import {mockApi} from './mock-api';
const docId='d'.repeat(32),remoteId='a'.repeat(32);
async function setup(page:Page,role='operator'){
  await mockApi(page);await page.route('**/api/whoami',r=>r.fulfill({json:{principal:'owner',role,agent:null}}));
  await page.addInitScript(()=>{const streams:{url:string;listeners:Record<string,((event:{data:string})=>void)[]>;closed:boolean}[]=[];class Events{url:string;listeners:Record<string,((event:{data:string})=>void)[]>={};closed=false;constructor(url:string){this.url=url;streams.push(this);}addEventListener(name:string,listener:(event:{data:string})=>void){(this.listeners[name]??=[]).push(listener);}close(){this.closed=true;}}Object.assign(window,{EventSource:Events,presenceEvent:(name:string,value:unknown)=>streams.filter(stream=>!stream.closed && stream.url.endsWith('/presence/events')).forEach(stream=>stream.listeners[name]?.forEach(listener=>listener({data:JSON.stringify(value)}))),accessEvent:()=>streams.filter(stream=>!stream.closed && stream.url==='/api/ui-designer/events').forEach(stream=>stream.listeners.design?.forEach(listener=>listener({data:JSON.stringify({type:'access',slug:'demo'})}))),presenceStreamCount:()=>streams.filter(stream=>!stream.closed && stream.url.endsWith('/presence/events')).length});});
  let refusal=0;let allowed=true,backoff=false,stale=false,remoteExpiry=45000,serverTime=Date.now(),sequence=1;
  const writes:{url:string;body:Record<string,any>;at:number}[]=[],leaves:string[]=[],reads:string[]=[];
  const remote=()=>({participant:'user:alex',label:'Alex',client_session:remoteId,state:'editing',version:7,page_id:'page',node_id:'box',cursor:{x:150,y:230},sequence,expires_at:new Date(serverTime+remoteExpiry).toISOString()});
  const roster=()=>({document_id:stale?'e'.repeat(32):docId,server_now:new Date(serverTime).toISOString(),sessions:[remote()]});
  await page.route('**/api/ui-designer/**',async r=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access'))return allowed?r.fulfill({json:{document_id:docId,owner:'user:owner',visibility:'restricted',readers:[],editors:[],access_version:1,can_read:true,can_write:role==='operator',can_manage_sharing:false}}):r.fulfill({status:404,json:{detail:'Unavailable'}});
    if(url.pathname.endsWith('/presence/events'))return r.fulfill({contentType:'text/event-stream',body:''});
    if(url.pathname.includes('/presence')){
      if(r.request().method()==='DELETE'){leaves.push(r.request().url());return r.fulfill({json:{ok:true}});}
      if(r.request().method()==='PUT'){writes.push({url:r.request().url(),body:r.request().postDataJSON(),at:Date.now()});if(refusal)return r.fulfill({status:refusal,json:{detail:'Presence paused'}});if(backoff){backoff=false;return r.fulfill({status:429,headers:{'Retry-After':'2'},json:{detail:'Slow down'}});}return r.fulfill({json:roster()});}
      reads.push(url.pathname);return r.fulfill({json:roster()});
    }
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:7,versions:[{version:7,author:'owner'},{version:3,author:'owner'}]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:[{id:'box',title:'Box',type:'rect',x:100,y:200,w:200,h:120}]},{id:'other',title:'Other',children:[{id:'other-box',type:'rect',x:0,y:0,w:100,h:100}]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/fonts'))return r.fulfill({json:{fonts:[],version:7}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'box',type:'rect',authored:{id:'box',type:'rect',x:100,y:200,w:200,h:120},version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120"/>'});
    return r.fulfill({status:404,json:{}});
  });
  return{writes,leaves,reads,roster,refuse:(status:number)=>{refusal=status;},deny:()=>{allowed=false;},backoff:()=>{backoff=true;},stale:()=>{stale=true;},expire:()=>{remoteExpiry=-1;serverTime+=46000;},newer:()=>{sequence++;serverTime+=1000;},event:(name='presence',value=roster())=>page.evaluate(({name,value})=>(window as unknown as {presenceEvent:(name:string,value:unknown)=>void}).presenceEvent(name,value),{name,value})};
}
test('presence shows people and world cursors, converts local zoom/pan and throttles without edits',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await expect(page.getByRole('region',{name:'People in this design'})).toContainText('Alex · editing');
  await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toBeVisible();await expect.poll(()=>state.writes.length).toBeGreaterThan(0);
  const first=state.writes[0];expect(first.body).toMatchObject({document_id:docId,version:7,page_id:'page',state:'viewing'});expect(first.url.split('/').at(-1)).toMatch(/^[a-f0-9]{32}$/);
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  const stage=page.locator('.ud-stage');const box=await stage.boundingBox();if(!box)throw Error('Missing canvas stage');
  await page.mouse.move(box.x+50*1.25,box.y+30*1.25);await expect.poll(()=>state.writes.some(write=>write.body.cursor && Math.abs(write.body.cursor.x-150)<1 && Math.abs(write.body.cursor.y-230)<1)).toBe(true);
  const viewport=await page.getByLabel('Design canvas',{exact:true}).boundingBox();if(!viewport)throw Error('Missing viewport');
  await page.mouse.move(viewport.x+400,viewport.y+300);await page.mouse.down();await page.mouse.move(viewport.x+440,viewport.y+320);await page.mouse.up();
  const moved=await stage.boundingBox();if(!moved)throw Error('Missing moved stage');await page.mouse.move(moved.x+60*1.25,moved.y+40*1.25);
  await expect.poll(()=>state.writes.some(write=>write.body.cursor && Math.abs(write.body.cursor.x-160)<1 && Math.abs(write.body.cursor.y-240)<1)).toBe(true);
  const cursorWrites=state.writes.filter(write=>write.body.cursor);for(let i=1;i<cursorWrites.length;i++)expect(cursorWrites[i].at-cursorWrites[i-1].at).toBeGreaterThanOrEqual(450);
  await page.getByRole('button',{name:'rect Box',exact:true}).click();await expect.poll(()=>state.writes.some(write=>write.body.state==='editing' && write.body.node_id==='box')).toBe(true);
  expect(state.writes.every(write=>!('ops' in write.body))).toBe(true);
});
test('historical viewers stay viewing and peer cursors are scoped by version and page',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo?version=3');await expect(page.getByRole('region',{name:'People in this design'})).toContainText('v7');await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'rect Box',exact:true}).click();await expect.poll(()=>state.writes.length).toBeGreaterThan(0);expect(state.writes.every(write=>write.body.state==='viewing')).toBe(true);
  await page.getByLabel('Version',{exact:true}).selectOption('');await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Other',exact:true}).click();await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toHaveCount(0);await expect.poll(()=>state.writes.some(write=>write.body.page_id==='other' && write.body.cursor===null)).toBe(true);
});
test('roster sequence rejects stale movement and local expiry removes peers without leave',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toBeVisible();
  const old=state.roster();state.newer();const fresh=state.roster();fresh.sessions[0].cursor.x=180;await state.event('presence',fresh);await expect(page.locator('[data-presence-session]')).toHaveAttribute('data-presence-x','180');
  await state.event('presence',old);await expect(page.locator('[data-presence-session]')).toHaveAttribute('data-presence-x','180');
  const expires={...fresh,server_now:new Date(Date.parse(fresh.server_now)+1000).toISOString(),sessions:[{...fresh.sessions[0],sequence:3,expires_at:new Date(Date.parse(fresh.server_now)+1200).toISOString()}]};await state.event('presence',expires);await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toHaveCount(0,{timeout:3000});
});
test('presence honors 429 Retry-After, overflow resync and hidden leave then new session',async({page})=>{
  const state=await setup(page);state.backoff();await page.goto('/ui-designer/demo');await expect.poll(()=>state.writes.length).toBe(1);await page.waitForTimeout(700);expect(state.writes).toHaveLength(1);
  await expect.poll(()=>state.writes.length,{timeout:4000}).toBe(2);expect(state.writes[1].at-state.writes[0].at).toBeGreaterThanOrEqual(1900);
  const before=state.reads.length;await page.waitForTimeout(1000);await state.event('overflow',{} as ReturnType<typeof state.roster>);await expect.poll(()=>state.reads.length).toBeGreaterThan(before);
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});await expect.poll(()=>state.leaves.length).toBe(1);expect(state.leaves[0]).toContain('document_id='+docId);await expect(page.getByRole('region',{name:'People in this design'})).toHaveCount(0);
  await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});await expect.poll(()=>new Set(state.writes.map(write=>write.url)).size).toBe(2);
});
test('stale UUID roster is never displayed and ACL revoke closes presence and clears peers',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await expect(page.getByRole('img',{name:'Alex cursor',exact:true})).toBeVisible();
  state.stale();const changed=state.roster();changed.sessions[0].label='Stale private actor';await state.event('presence',changed);await expect(page.getByText('Stale private actor',{exact:false})).toHaveCount(0);
  state.deny();await page.evaluate(()=>(window as unknown as {accessEvent:()=>void}).accessEvent());await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');await expect(page.getByRole('region',{name:'People in this design'})).toHaveCount(0);expect(await page.evaluate(()=>(window as unknown as {presenceStreamCount:()=>number}).presenceStreamCount())).toBe(0);
});


for(const status of [401,403,409])test(`presence ${status} pauses PUTs across revalidation until the selected tuple changes`,async({page})=>{
  const state=await setup(page);state.refuse(status);await page.goto('/ui-designer/demo');await expect.poll(()=>state.writes.length).toBe(1);await page.waitForTimeout(1200);expect(state.writes).toHaveLength(1);
  state.refuse(0);await page.getByRole('button',{name:'Other',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(2);expect(state.writes[1].body.page_id).toBe('other');
});
