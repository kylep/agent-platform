import {expect,test,type Page} from '@playwright/test';
import {mockApi} from './mock-api';
import type {DesignerAccess} from '../src/lib/useDesignerAccess';
async function setup(page:Page,kind='owner'){
  await mockApi(page);await page.route('**/api/whoami',r=>r.fulfill({json:{principal:'kyle',role:'operator',agent:null}}));
  await page.addInitScript(()=>{const streams:{listener?:(event:{data:string})=>void}[]=[];class Events{listener?:(event:{data:string})=>void;constructor(){streams.push(this);}addEventListener(name:string,listener:(event:{data:string})=>void){if(name==='design')this.listener=listener;}close(){this.listener=undefined;}}Object.assign(window,{EventSource:Events,sharingEvent:()=>streams.forEach(stream=>stream.listener?.({data:JSON.stringify({type:'access',slug:'demo',access_version:2})}))});});
  let head=7;let allowed=true,failed=false,conflict=false,ownEvent=false;const receipts=new Map<string,DesignerAccess>();
  let access:DesignerAccess={document_id:'d'.repeat(32),owner:'user:kyle',visibility:'workspace',readers:[],editors:[],access_version:1,can_read:true,can_write:kind!=='reader',can_manage_sharing:kind==='owner'};
  const writes:Record<string,unknown>[]=[],reads:string[]=[];const node={id:'text',type:'text',title:'Greeting',text:'Private content',x:0,y:0,w:100,h:40};
  await page.route('**/api/ui-designer/**',async r=>{
    const url=new URL(r.request().url());
    if(url.pathname==='/api/ui-designer/sharing-choices')return r.fulfill({json:{principals:[{principal:'user:kyle',label:'Kyle',kind:'user'},{principal:'user:alex',label:'Alex',kind:'user'},{principal:'agent:artist',label:'Artist',kind:'agent'}],teams:[{principal:'team:team-id',id:'team-id',name:'Design team',slug:'design'}],truncated:false}});
    if(url.pathname.endsWith('/access')){
      if(!allowed)return r.fulfill({status:404,json:{detail:'Not found'}});
      if(r.request().method()==='GET')return r.fulfill({json:access});
      const body=r.request().postDataJSON();writes.push(body);
      if(receipts.has(body.request_id))return r.fulfill({json:receipts.get(body.request_id)});
      if(conflict){conflict=false;access={...access,access_version:access.access_version+1,visibility:'restricted'};return r.fulfill({status:409,json:{detail:{code:'UD-VERSION-CONFLICT',message:'Access changed'}}});}
      access={...access,visibility:body.visibility,readers:body.readers,editors:body.editors,access_version:access.access_version+1};receipts.set(body.request_id,access);
      if(ownEvent)await page.evaluate(()=>(window as unknown as {sharingEvent:()=>void}).sharingEvent());
      if(failed){failed=false;return r.fulfill({status:502,json:{detail:'Proxy failure'}});}
      return r.fulfill({json:access});
    }
    reads.push(url.pathname);
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:head,versions:[{version:head,author:'kyle'},{version:3,author:'kyle'}]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',children:[{id:'frame',title:'Screen',type:'frame',x:0,y:0,w:100,h:40,children:[node]}]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/fonts'))return r.fulfill({json:{fonts:[],version:7}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'text',type:'text',authored:node,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"/>'});
    return r.fulfill({status:404,json:{}});
  });
  return {writes,reads,advance:()=>{head++;},access:()=>access,deny:()=>{allowed=false;},fail:()=>{failed=true;ownEvent=true;},conflict:()=>{conflict=true;},event:()=>page.evaluate(()=>(window as unknown as {sharingEvent:()=>void}).sharingEvent())};
}
async function open(page:Page){await page.getByRole('button',{name:'Sharing',exact:true}).click();await expect(page.getByRole('heading',{name:'Sharing',exact:true})).toBeVisible();}
test('owner shares with readable team labels using independent access CAS across historical versions',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await open(page);
  await page.getByLabel('Document visibility').selectOption('restricted');await page.getByLabel('Share with person or team').selectOption('team:team-id');await page.getByLabel('New sharing permission').selectOption('editor');await page.getByRole('button',{name:'Add access',exact:true}).click();
  await expect(page.getByRole('list',{name:'Shared access'})).toContainText('Design team (team)');await page.getByRole('button',{name:'Save sharing',exact:true}).click();
  await expect(page.getByText('Sharing saved.',{exact:true})).toBeVisible();expect(state.writes[0]).toMatchObject({expected_access_version:1,visibility:'restricted',readers:[],editors:['team:team-id']});expect(state.writes[0].request_id).toMatch(/^[a-f0-9]{32}$/);
  await expect(page.getByLabel('Version',{exact:true})).toHaveValue('');await page.getByLabel('Version',{exact:true}).selectOption('3');
  await expect(page.getByLabel('Document visibility')).toHaveValue('restricted');await expect(page.getByLabel('Document visibility')).toBeEnabled();expect(state.access().access_version).toBe(2);expect(state.writes).toHaveLength(1);
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
for(const kind of ['editor','reader'])test(`${kind} uses content capability and cannot manage sharing`,async({page})=>{
  await setup(page,kind);await page.goto('/ui-designer/demo');await open(page);await expect(page.getByLabel('Document visibility')).toBeDisabled();
  await page.getByRole('button',{name:'text Greeting',exact:true}).click();
  if(kind==='editor'){await page.getByLabel('Title',{exact:true}).fill('Changed');await expect(page.getByRole('button',{name:'Save node',exact:true})).toBeEnabled();}else await expect(page.getByLabel('Title',{exact:true})).toBeDisabled();
});
test('revoked access clears cached content and missing initial access never fetches document data',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await expect(page.getByRole('button',{name:'text Greeting',exact:true})).toBeVisible();state.deny();await state.event();
  await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');await expect(page.getByRole('button',{name:'text Greeting',exact:true})).toHaveCount(0);await expect(page.locator('.ud-canvas img')).toHaveCount(0);
  const before=state.reads.length;await page.reload();await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');expect(state.reads.length).toBe(before);
});
test('unknown sharing outcome survives own access event and retries exact receipt without locking editor',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await open(page);state.fail();await page.getByLabel('Document visibility').selectOption('restricted');await page.getByRole('button',{name:'Save sharing',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry sharing save'})).toBeVisible();await expect(page.getByLabel('Document visibility')).toBeDisabled();
  await page.getByRole('button',{name:'text Greeting',exact:true}).click();await page.getByLabel('Title',{exact:true}).fill('Allowed content draft');await expect(page.getByRole('button',{name:'Save node',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Retry sharing save'}).click();await expect(page.getByText('Sharing saved.',{exact:true})).toBeVisible();expect(state.writes).toHaveLength(2);expect(state.writes[0]).toEqual(state.writes[1]);expect(state.access().access_version).toBe(2);
});
test('conflicting sharing save refreshes current access and requires explicit review and save',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await open(page);state.conflict();await page.getByLabel('Document visibility').selectOption('restricted');await page.getByRole('button',{name:'Save sharing',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Sharing changed elsewhere');await expect(page.getByLabel('Document visibility')).toHaveValue('restricted');expect(state.writes).toHaveLength(1);
  await page.getByLabel('Share with person or team').selectOption('user:alex');await page.getByRole('button',{name:'Add access',exact:true}).click();await page.getByRole('button',{name:'Save sharing',exact:true}).click();await expect(page.getByText('Sharing saved.',{exact:true})).toBeVisible();expect(state.writes[1].expected_access_version).toBe(2);expect(state.writes[1].request_id).not.toBe(state.writes[0].request_id);
});


test('prototype access invalidation preserves its original pin and revocation removes images',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo/play');await expect(page.getByRole('img',{name:'Prototype frame Screen',exact:true})).toBeVisible();
  state.advance();await state.event();await expect(page.getByRole('heading',{name:'Prototype · demo · v7',exact:true})).toBeVisible();
  expect(state.reads.filter((path)=>path.endsWith('/versions'))).toHaveLength(1);
  state.deny();await state.event();await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');await expect(page.getByRole('img',{name:'Prototype frame Screen',exact:true})).toHaveCount(0);
});


test('a forbidden page render rechecks access and clears the document without an SSE event',async({page})=>{
  const state=await setup(page);await page.route('**/render?*',r=>{state.deny();return r.fulfill({status:404,body:'Not found'});});
  await page.goto('/ui-designer/demo');await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');await expect(page.locator('.ud-canvas img')).toHaveCount(0);
});
test('document list refreshes after an access event and removes revoked titles',async({page})=>{
  const state=await setup(page);let available=true;await page.route('**/api/ui-designer/docs',r=>r.fulfill({json:{docs:available ? [{slug:'demo',title:'Private board',owner:'kyle'}]:[]}}));
  await page.goto('/ui-designer');await expect(page.getByRole('link',{name:'Private board',exact:true})).toBeVisible();available=false;await state.event();await expect(page.getByRole('link',{name:'Private board',exact:true})).toHaveCount(0);
});
