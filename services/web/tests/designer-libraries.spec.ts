import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const sourceId='cd'.repeat(16);
const pin={library_id:'kit',source_id:sourceId,source_slug:'source',source_version:7,namespace:'kit',counts:{components:1,tokens:1}};
async function setup(page:Page,options:{existing?:boolean;role?:string;historical?:boolean}={}){
  await mockApi(page);await page.addInitScript(()=>{
    const listeners:((e:{data:string})=>void)[]=[];
    class Source{addEventListener(name:string,callback:(e:{data:string})=>void){if(name==='design')listeners.push(callback);}close(){}}
    Object.assign(window,{EventSource:Source,libraryEvent:(version:number)=>listeners.forEach((fn)=>fn({data:JSON.stringify({slug:'demo',type:'version',version})}))});
  });
  let version=options.existing?3:1,imported=!!options.existing,failAfter=false,ownEvent=false,previewConflict=false,truncatedConflict=false,cas=false,writeConflict=false;
  let held:Promise<void>|null=null;
  const writes:{path:string;body:Record<string,unknown>}[]=[],previews:Record<string,unknown>[]=[],sourceReads:string[]=[];
  const receipts=new Map<string,number>();
  const node={id:'box',type:'rect',title:'Box',x:0,y:0,w:100,h:80};
  await page.route('**/api/whoami',(r)=>r.fulfill({json:{role:options.role || 'admin'}}));
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname==='/api/ui-designer/docs/source'){sourceReads.push(url.search);return r.fulfill({json:{id:sourceId,doc:{}}});}
    if(url.pathname.endsWith('/access'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version,versions:Array.from({length:version},(_,i)=>({version:version-i,author:'kyle'}))}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:[node]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'box',type:'rect',authored:node,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"/>'});
    if(url.pathname.endsWith('/edit')){Object.assign(node,r.request().postDataJSON().ops[0].set);version++;return r.fulfill({json:{version}});}
    if(url.pathname.endsWith('/libraries'))return r.fulfill({json:{version,libraries:imported?[pin]:[]}});
    if(url.pathname.endsWith('/preview')){const body=r.request().postDataJSON();previews.push(body);if(held)await held;return r.fulfill({json:{library_id:'kit',version,source_version:body.source_version,counts:{updated:1,conflicts:truncatedConflict?1:0},added:[],updated:['kit-button'],removed:[],conflicts:previewConflict?[{kind:'local-modified',id:'kit-button',message:'The local component was edited.'}]:[],truncated:truncatedConflict}});}
    if(url.pathname.endsWith('/libraries/import')||url.pathname.endsWith('/apply')){
      const body=r.request().postDataJSON();writes.push({path:url.pathname,body});
      const prior=receipts.get(body.request_id);if(prior)return r.fulfill({json:{version:prior,library_id:'kit',replayed:true}});
      if(held)await held;
      if(writeConflict)return r.fulfill({status:409,json:{detail:{code:'UD-LIBRARY-CONFLICT',message:'Local definitions changed.',conflicts:[{kind:'local-modified',id:'kit-button',message:'This component was locally edited.'}]}}});
      if(cas){cas=false;version++;return r.fulfill({status:409,json:{detail:{code:'UD-VERSION-CONFLICT',message:'Another edit arrived first.'}}});}
      version++;imported=true;receipts.set(body.request_id,version);
      if(ownEvent)await page.evaluate((v)=>(window as unknown as {libraryEvent:(n:number)=>void}).libraryEvent(v),version);
      if(failAfter){failAfter=false;return r.fulfill({status:502,json:{detail:'Proxy failed after commit.'}});}
      return r.fulfill({json:{version,library_id:'kit'}});
    }
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  await page.goto(`/ui-designer/demo${options.historical?'?version=1':''}`);
  await page.getByRole('button',{name:'rect Box',exact:true}).click();
  await page.getByRole('button',{name:'Libraries',exact:true}).click();
  return {writes,previews,sourceReads,failAfterCommit:()=>{failAfter=true;},ownEvent:()=>{ownEvent=true;},conflictPreview:()=>{previewConflict=true;},truncateConflict:()=>{truncatedConflict=true;},cas:()=>{cas=true;},writeConflict:()=>{writeConflict=true;},hold:(value:Promise<void>)=>{held=value;}};
}
async function fillImport(page:Page){
  await page.getByLabel('Source document slug',{exact:true}).fill('source');await page.getByLabel('Retained source version',{exact:true}).fill('7');await page.getByLabel('Namespace',{exact:true}).fill('kit');
  await page.getByLabel('Component IDs · comma separated',{exact:true}).fill('button, button');await page.getByLabel('Component set IDs · comma separated',{exact:true}).fill('buttons');await page.getByLabel('Token IDs (for example color.brand) · comma separated',{exact:true}).fill('color.brand');
}

test('explicit import resolves source identity once, clears undo and pins copied library',async({page})=>{
  const state=await setup(page);await page.getByLabel('Width',{exact:true}).fill('120');await page.getByRole('button',{name:'Save node'}).click();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();
  await fillImport(page);await page.getByRole('button',{name:'Import library',exact:true}).click();
  await expect(page.getByText('Library kit saved at destination v3.')).toBeVisible();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeDisabled();
  expect(state.sourceReads).toHaveLength(1);expect(state.sourceReads[0]).toContain('version=7');
  expect(state.writes[0].body).toMatchObject({expected_version:2,library:{source_id:sourceId,source_version:7,namespace:'kit',components:['button'],component_sets:['buttons'],tokens:['color.brand']}});
  expect(String(state.writes[0].body.request_id)).toMatch(/^[0-9a-f]{32}$/);await expect(page.getByText('source · source v7')).toBeVisible();
});

test('uncertain committed import locks editor and retries same body despite own SSE',async({page})=>{
  const state=await setup(page);state.failAfterCommit();state.ownEvent();await fillImport(page);await page.getByRole('button',{name:'Import library',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry library save'})).toBeVisible();await expect(page.getByRole('button',{name:'Save node'})).toBeDisabled();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeDisabled();await expect(page.getByLabel('Version',{exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Retry library save'}).click();await expect(page.getByText('Library kit saved at destination v2.')).toBeVisible();
  expect(state.writes[0]).toEqual(state.writes[1]);expect(state.sourceReads).toHaveLength(1);await expect(page.getByLabel('Version',{exact:true})).toBeEnabled();
});

test('updates require preview and apply exact source and destination pins',async({page})=>{
  const state=await setup(page,{existing:true});await expect(page.getByRole('button',{name:'Apply library update'})).toHaveCount(0);
  await page.getByLabel('New source version for kit').fill('9');await page.getByRole('button',{name:'Preview update kit'}).click();
  await expect(page.getByText('Based on destination v3.')).toBeVisible();await page.getByRole('button',{name:'Apply library update'}).click();
  await expect(page.getByText('Library kit saved at destination v4.')).toBeVisible();
  expect(state.previews).toEqual([{source_version:9}]);expect(state.writes[0].body).toMatchObject({source_version:9,expected_version:3});
});

test('local modification conflicts block apply and CAS failures never rebase',async({page})=>{
  const state=await setup(page,{existing:true});state.conflictPreview();await page.getByLabel('New source version for kit').fill('9');await page.getByRole('button',{name:'Preview update kit'}).click();
  await expect(page.getByRole('button',{name:'Apply library update'})).toBeDisabled();await expect(page.getByText('The local component was edited.',{exact:false})).toBeVisible();expect(state.writes).toHaveLength(0);
  await page.getByLabel('New source version for kit').fill('10');await expect(page.getByRole('button',{name:'Apply library update'})).toHaveCount(0);
});

test('historical and reader library views cannot mutate',async({page})=>{
  await setup(page,{existing:true,historical:true});await expect(page.getByRole('button',{name:'Import library',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Preview update kit'})).toBeDisabled();
  await setup(page,{existing:true,role:'reader'});await expect(page.getByRole('button',{name:'Import library',exact:true})).toBeDisabled();
});

test('late writes cannot update a different document scope',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));await fillImport(page);await page.getByRole('button',{name:'Import library',exact:true}).click();
  await expect(page.getByText('Working on the library…')).toBeVisible();await page.getByRole('link',{name:'UI Designer',exact:true}).last().click();await page.getByRole('link',{name:'Other',exact:true}).click();release();
  await expect(page.getByRole('heading',{name:'UI Designer / other'})).toBeVisible();await expect(page.getByText('Library kit saved',{exact:false})).toHaveCount(0);
});


test('flattened structured write conflicts remain visible and CAS updates are not retried',async({page})=>{
  const state=await setup(page);state.writeConflict();await fillImport(page);await page.getByRole('button',{name:'Import library',exact:true}).click();
  await expect(page.getByRole('list',{name:'Library conflicts'})).toContainText('kit-button: This component was locally edited.');expect(state.writes).toHaveLength(1);
  const next=await setup(page,{existing:true});await page.getByLabel('New source version for kit').fill('9');await page.getByRole('button',{name:'Preview update kit'}).click();
  await expect(page.getByRole('button',{name:'Apply library update'})).toBeEnabled();next.cas();await page.getByRole('button',{name:'Apply library update'}).click();
  await expect(page.getByRole('alert')).toHaveText('Another edit arrived first.');await expect(page.getByRole('button',{name:'Apply library update'})).toHaveCount(0);expect(next.writes).toHaveLength(1);
});


test('truncated conflict details still block applying when counts report a conflict',async({page})=>{
  const state=await setup(page,{existing:true});state.truncateConflict();
  await page.getByLabel('New source version for kit').fill('9');await page.getByRole('button',{name:'Preview update kit'}).click();
  await expect(page.getByRole('alert')).toContainText('Local changes conflict with this update.');
  await expect(page.getByRole('button',{name:'Apply library update'})).toBeDisabled();expect(state.writes).toHaveLength(0);
});
