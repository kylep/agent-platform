import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const source={name:'Lucide',version:'1.73.0',commit:'abcd'.repeat(10),license:'ISC + MIT'};
const icons=[{id:'camera',name:'Camera',tags:[],categories:[]},{id:'circle',name:'Circle',tags:[],categories:[]},{id:'filled',name:'Filled sample',tags:[],categories:[]}];
async function setup(page:Page,options:{reader?:boolean;historical?:boolean}={}){
  await mockApi(page);let version=1,fail=false,unsafe=false;const writes:Record<string,any>[]=[],catalogQueries:URLSearchParams[]=[],detailQueries:string[]=[];
  const nodes:Record<string,any>[]=[{id:'frame',type:'frame',title:'Destination frame',x:0,y:0,w:240,h:160,children:[]}];
  const receipts=new Map<string,unknown>();let holdSearch:string|null=null,holdDetail:string|null=null,held:Promise<void>|null=null;
  await page.route('**/api/whoami',(r)=>r.fulfill({json:{role:options.reader?'reader':'admin'}}));
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/icons')){
      catalogQueries.push(url.searchParams);const q=url.searchParams.get('q')||'';if(held&&holdSearch===q)await held;
      const list=q?icons.filter((icon)=>icon.id.includes(q)):url.searchParams.has('cursor')?[icons[2]]:icons.slice(0,2);
      return r.fulfill({json:{revision:1,source,icons:list,total:q?list.length:3,next_cursor:!q&&!url.searchParams.has('cursor')?'next-1':null}});
    }
    const detail=/\/icons\/([^/]+)$/.exec(url.pathname);if(detail){detailQueries.push(url.search);if(held&&holdDetail===detail[1])await held;const icon=icons.find((entry)=>entry.id===detail[1]);return r.fulfill({json:{...icon,revision:1,view_box:[0,0,24,24],d:'M 0 0 L 24 24',paths:unsafe?[{d:'<svg onload=alert(1)>',fill:false}]:[{d:'M 2 2 L 8 2 L 8 8 Z',fill:detail[1]==='filled'},{d:'M 4 4 L 20 20',fill:false}]}});}
    if(url.pathname.endsWith('/access') || url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version,versions:Array.from({length:version},(_,i)=>({version:version-i,author:'kyle'}))}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:nodes}]}});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'frame',type:'frame',authored:nodes[0],version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"/>'});
    if(url.pathname.endsWith('/edit')){
      const body=r.request().postDataJSON();writes.push(body);if(receipts.has(body.request_id))return r.fulfill({json:{...receipts.get(body.request_id) as object,replayed:true}});
      let inverse:unknown;for(const op of body.ops){if(op.op==='icon'){nodes[0].children.push({id:op.id,type:'frame',of:'native-camera',title:'Camera',w:op.size,h:op.size,x:0,y:0,children:[]});inverse=[{op:'remove',ids:[op.id]}];}else if(op.op==='remove')nodes[0].children=nodes[0].children.filter((node:Record<string,unknown>)=>!op.ids.includes(node.id));}
      version++;const receipt={version,undo_ops:inverse};receipts.set(body.request_id,receipt);if(fail){fail=false;return r.fulfill({status:502,json:{detail:'Proxy failed after commit.'}});}return r.fulfill({json:receipt});
    }
    if(url.pathname==='/api/ui-designer/docs')return r.fulfill({json:{docs:[{slug:'other',title:'Other',owner:'kyle'}]}});
    return r.fulfill({status:404,json:{}});
  });
  await page.goto(`/ui-designer/demo${options.historical?'?version=1':''}`);await page.getByRole('button',{name:'frame Destination frame',exact:true}).click();await page.getByRole('button',{name:'Icons',exact:true}).click();
  return{writes,nodes,catalogQueries,detailQueries,fail:()=>{fail=true;},unsafe:()=>{unsafe=true;},hold:(kind:'search'|'detail',value:string,promise:Promise<void>)=>{held=promise;if(kind==='search')holdSearch=value;else holdDetail=value;}};
}

test('icon metadata search pages and attribution remain bounded and explicit',async({page})=>{
  const state=await setup(page);await expect(page.getByRole('button',{name:'Select icon camera',exact:true})).toBeVisible();await page.getByRole('button',{name:'Next icons',exact:true}).click();await expect(page.getByRole('button',{name:'Select icon filled',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Previous icons',exact:true}).click();await expect(page.getByRole('button',{name:'Select icon camera',exact:true})).toBeVisible();
  await page.getByLabel('Search icons',{exact:true}).fill('cam');await page.getByRole('button',{name:'Search icons',exact:true}).click();await expect(page.getByRole('button',{name:'Select icon camera',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Select icon circle',exact:true})).toHaveCount(0);
  expect(state.catalogQueries.every((query)=>query.get('limit')==='24')).toBe(true);await expect(page.getByRole('link',{name:'Lucide license and attribution'})).toHaveAttribute('href','https://lucide.dev/license');
});

test('48px camera inserts via one pinned native icon op and exact server undo survives retry',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'Select icon camera',exact:true}).click();await expect(page.getByRole('img',{name:'Preview Camera',exact:true})).toBeVisible();
  await page.getByLabel('Icon size',{exact:true}).fill('48');await page.getByLabel('Icon colour',{exact:true}).fill('#abcdef');await page.getByLabel('Icon stroke width',{exact:true}).fill('1.5');
  await expect(page.getByText('Destination: Destination frame (frame)')).toBeVisible();state.fail();await page.getByRole('button',{name:'Insert icon',exact:true}).click();await expect(page.getByRole('button',{name:'Retry save'})).toBeVisible();await expect(page.getByRole('button',{name:'Insert icon',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Retry save'}).click();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();expect(state.writes[0]).toEqual(state.writes[1]);
  expect(state.writes[0].ops[0]).toMatchObject({op:'icon',icon:'camera',revision:1,parent:'frame',size:48,color:'#abcdef',stroke_width:1.5});expect(state.detailQueries[0]).toBe('?revision=1');
  await page.getByRole('button',{name:'Undo',exact:true}).click();await expect(page.getByRole('button',{name:'Redo',exact:true})).toBeEnabled();expect(state.nodes[0].children).toHaveLength(0);
  await page.getByRole('button',{name:'Redo',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(4);expect(state.writes[3].ops).toEqual(state.writes[0].ops);
});

test('preview keeps filled element ordering and validates size colour and stroke',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'Next icons',exact:true}).click();await page.getByRole('button',{name:'Select icon filled',exact:true}).click();const preview=page.getByRole('img',{name:'Preview Filled sample',exact:true});await expect(preview.locator('path')).toHaveCount(2);await expect(preview.locator('path').nth(0)).not.toHaveAttribute('fill','none');await expect(preview.locator('path').nth(1)).toHaveAttribute('fill','none');
  await page.getByLabel('Icon size',{exact:true}).fill('129');await page.getByRole('button',{name:'Insert icon',exact:true}).click();await expect(page.getByRole('alert')).toHaveText('Icon size must be from 16 to 128.');
  await page.getByLabel('Icon size',{exact:true}).fill('24');await page.getByLabel('Icon colour',{exact:true}).fill('url(https://example.com/evil)');await page.getByRole('button',{name:'Insert icon',exact:true}).click();await expect(page.getByRole('alert')).toHaveText('Choose a hexadecimal icon colour.');await expect(preview.locator('path').nth(0)).toHaveAttribute('stroke','var(--ds-accent)');expect(state.writes).toHaveLength(0);
});

test('unsafe native previews reject markup and historical views cannot insert',async({page})=>{
  const state=await setup(page);state.unsafe();await page.getByRole('button',{name:'Select icon camera',exact:true}).click();await expect(page.getByRole('alert')).toHaveText('The icon preview contained unsupported data.');await expect(page.getByRole('button',{name:'Insert icon',exact:true})).toHaveCount(0);
  await setup(page,{historical:true});await page.getByRole('button',{name:'Select icon camera',exact:true}).click();await expect(page.getByRole('button',{name:'Insert icon',exact:true})).toBeDisabled();
});

test('stale search and detail replies cannot replace newer selections',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold('detail','camera',new Promise<void>((r)=>{release=r;}));await page.getByRole('button',{name:'Select icon camera',exact:true}).click();await page.getByRole('button',{name:'Select icon circle',exact:true}).click();await expect(page.getByRole('img',{name:'Preview Circle',exact:true})).toBeVisible();release();await expect(page.getByRole('img',{name:'Preview Camera',exact:true})).toHaveCount(0);
  let releaseSearch:()=>void=()=>{};state.hold('search','cam',new Promise<void>((r)=>{releaseSearch=r;}));await page.getByLabel('Search icons',{exact:true}).fill('cam');await page.getByRole('button',{name:'Search icons',exact:true}).click();await page.getByLabel('Search icons',{exact:true}).fill('circle');await page.getByRole('button',{name:'Search icons',exact:true}).click();await expect(page.getByRole('button',{name:'Select icon circle',exact:true})).toBeVisible();releaseSearch();await expect(page.getByRole('button',{name:'Select icon camera',exact:true})).toHaveCount(0);
});
