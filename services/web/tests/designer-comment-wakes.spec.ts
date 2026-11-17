import {expect,test,type Page} from '@playwright/test';
import {mockApi} from './mock-api';
const docId='d'.repeat(32),commentId='a'.repeat(32),runId='f'.repeat(32),privateReason='Private documents cannot start comment runs yet.';
async function setup(page:Page,restricted=false,role='operator'){
  await mockApi(page);await page.route('**/api/whoami',r=>r.fulfill({json:{principal:'owner',role,agent:null}}));
  await page.addInitScript(()=>{const listeners:((event:{data:string})=>void)[]=[];class Events{addEventListener(name:string,listener:(event:{data:string})=>void){if(name==='design')listeners.push(listener);}close(){}}Object.assign(window,{EventSource:Events,commentEvent:(type:string)=>listeners.forEach(listener=>listener({data:JSON.stringify({type,slug:'demo',document_id:'d'.repeat(32)})}))});});
  let comments:Record<string,any>[]=[],fail=false,omit=false,denied=false,wrongRead=false;
  let held:Promise<void>|null=null;const writes:Record<string,any>[]=[],receipts=new Set<string>(),reads:string[]=[];
  let notifications:Record<string,any>[]=[{agent:restricted?'pai':'artist',status:restricted?'skipped':'queued',...(restricted?{reason:privateReason}:{})}];
  await page.route('**/api/ui-designer/**',async r=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/access'))return denied?r.fulfill({status:404,json:{detail:'Unavailable'}}):r.fulfill({json:{document_id:docId,owner:'user:owner',visibility:restricted?'restricted':'workspace',readers:[],editors:[],access_version:1,can_read:true,can_write:role==='operator',can_manage_sharing:true}});
    if(url.pathname.endsWith('/versions')){reads.push('versions');return r.fulfill({json:{version:7,versions:[{version:7,author:'owner'}]}});}
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',children:[{id:'box',title:'Box',type:'rect',x:0,y:0,w:100,h:100},{id:'other',title:'Other',type:'rect',x:120,y:0,w:100,h:100}]}]}});
    if(url.pathname.endsWith('/fonts'))return r.fulfill({json:{fonts:[],version:7}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:url.searchParams.get('node'),type:'rect',authored:{id:url.searchParams.get('node'),type:'rect',w:100,h:100},version:7}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="100"/>'});
    if(url.pathname.endsWith('/comments')){
      if(r.request().method()==='GET'){
        reads.push('comments');if(held)await held;
        if(wrongRead){wrongRead=false;return r.fulfill({json:{document_id:'e'.repeat(32),comments:[{id:commentId,body:'Wrong document private content',notifications:[]}]}});}
        return r.fulfill({json:{document_id:docId,comments:comments.map(comment=>({...comment,notifications:omit?[]:notifications}))}});
      }
      const body=r.request().postDataJSON();writes.push(body);
      if(receipts.has(body.request_id))return r.fulfill({json:{document_id:docId,id:commentId,comment:null,notifications,replayed:true}});
      receipts.add(body.request_id);const comment={id:commentId,body:body.body,node_id:body.node_id,parent_id:body.parent_id,author:'user:owner',resolved_at:null,resolved_by:null,created_at:new Date().toISOString(),notifications};comments.push(comment);
      if(held)await held;if(fail){fail=false;return r.fulfill({status:502,json:{detail:'Proxy failed after commit'}});}
      return r.fulfill({json:{document_id:docId,id:commentId,comment,notifications,replayed:false}});
    }
    return r.fulfill({status:404,json:{}});
  });
  return {writes,reads,count:()=>comments.length,fail:()=>{fail=true;},omit:()=>{omit=true;},deny:()=>{denied=true;},wrongRead:()=>{wrongRead=true;},hold:(value:Promise<void>|null)=>{held=value;},statuses:(value:Record<string,any>[])=>{notifications=value;},event:(type='comment-wake')=>page.evaluate(type=>(window as unknown as {commentEvent:(type:string)=>void}).commentEvent(type),type)};
}
test('named comment queues with selected context and wake SSE refreshes honest run-created link only',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'rect Box',exact:true}).click();
  await page.getByLabel('Comment text',{exact:true}).fill('@artist Please review this box.');await page.getByRole('button',{name:'Add comment',exact:true}).click();
  await expect(page.getByRole('list',{name:'Agent notification status'})).toHaveText('@artist · Queued');
  expect(state.writes[0]).toMatchObject({expected_document_id:docId,node_id:'box',parent_id:null,body:'@artist Please review this box.'});expect(state.writes[0].request_id).toMatch(/^[a-f0-9]{32}$/);
  const headReads=state.reads.filter(value=>value==='versions').length;
  state.statuses([{agent:'artist',status:'run_created',run_id:runId}]);await state.event();
  await expect(page.getByRole('link',{name:'Run created',exact:true})).toHaveAttribute('href','/runs/'+runId);expect(state.reads.filter(value=>value==='versions')).toHaveLength(headReads);await expect(page.getByText('Queued',{exact:true})).toHaveCount(0);
});
test('private skip is explicit and an uncertain applied comment retries identical body/node without duplicate wake',async({page})=>{
  const state=await setup(page,true);state.fail();state.omit();await page.goto('/ui-designer/demo');await page.getByRole('button',{name:'rect Box',exact:true}).click();
  await page.getByLabel('Comment text',{exact:true}).fill('@pai Review privately.');await page.getByRole('button',{name:'Add comment',exact:true}).click();await expect(page.getByRole('button',{name:'Retry comment',exact:true})).toBeVisible();
  await expect(page.getByLabel('Comment text',{exact:true})).toBeDisabled();await page.getByRole('button',{name:'rect Other',exact:true}).click();
  await page.getByRole('button',{name:'Retry comment',exact:true}).click();await expect(page.getByRole('list',{name:'Agent notification status'})).toContainText('Not notified: '+privateReason);
  expect(state.writes).toHaveLength(2);expect(state.writes[0]).toEqual(state.writes[1]);expect(state.writes[1].node_id).toBe('box');expect(state.count()).toBe(1);
});
test('rapid duplicate clicks post once and malicious comment, agent, reason and run IDs stay escaped',async({page})=>{
  const state=await setup(page);state.statuses([{agent:'bad<script>',status:'skipped',reason:'<img src=x onerror=alert(1)>'},{agent:'other',status:'run_created',run_id:'javascript:alert(1)'}]);await page.goto('/ui-designer/demo');
  await page.getByLabel('Comment text',{exact:true}).fill('<script>window.hacked=true</script> @bad');await page.getByRole('button',{name:'Add comment',exact:true}).evaluate(button=>{button.click();button.click();});
  await expect(page.locator('.ud-comment p').first()).toHaveText('<script>window.hacked=true</script> @bad');expect(state.writes).toHaveLength(1);await expect(page.locator('.ud-comments img, .ud-comments script')).toHaveCount(0);await expect(page.getByRole('link',{name:'Run created',exact:true})).toHaveCount(0);expect(await page.evaluate(()=>(window as unknown as {hacked?:boolean}).hacked)).toBeUndefined();
});
test('late comment list cannot cross an ACL revocation and wrong UUID contents are rejected',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await expect(page.getByRole('button',{name:'rect Box',exact:true})).toBeVisible();state.wrongRead();await state.event();await expect(page.getByText('Wrong document private content',{exact:true})).toHaveCount(0);
  let release!:()=>void;state.hold(new Promise<void>(resolve=>{release=resolve;}));await state.event();state.deny();await state.event('access');release();await expect(page.getByRole('alert')).toHaveText('This design is unavailable or private.');await expect(page.getByRole('region',{name:'Comments',exact:true})).toHaveCount(0);
});
test('read-only comment controls cannot post',async({page})=>{
  const state=await setup(page,false,'reader');await page.goto('/ui-designer/demo');await expect(page.getByLabel('Comment text',{exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Add comment',exact:true})).toBeDisabled();expect(state.writes).toEqual([]);
});
