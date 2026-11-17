import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
type Shape={id:string;type:string;title:string;[key:string]:unknown};
const sha='ab'.repeat(32);
async function setup(page:Page,options:{role?:string;historical?:boolean}={}){
  await mockApi(page);let version=1,fail=false,reject=false;const writes:Record<string,any>[]=[],assets:string[]=[];
  const shapes:Shape[]=[{id:'a',type:'rect',title:'First',x:10,y:10,w:80,h:80,fill:'red'},{id:'b',type:'ellipse',title:'Second',x:40,y:10,w:80,h:80},{id:'path',type:'path',title:'Curve',x:70,y:45,w:120,h:100,rotate:30,d:'M 0 0 C 10 10 20 20 40 40',fill:[{color:'red',opacity:.5}]}];
  const receipts=new Map<string,unknown>();
  await page.route('**/api/whoami',(r)=>r.fulfill({json:{role:options.role||'admin'}}));
  await page.route('**/api/ui-designer/**',async(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access') || url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version,versions:Array.from({length:version},(_,i)=>({version:version-i,author:'kyle'}))}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',title:'Main',children:shapes}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/inspect')){const shape=shapes.find((s)=>s.id===url.searchParams.get('node'));return r.fulfill({json:{id:shape?.id,type:shape?.type,authored:shape,version:Number(url.searchParams.get('version'))}});}
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"/>'});
    if(url.pathname.endsWith('/assets')){assets.push(url.search);return r.fulfill({json:{assets:[{sha256:sha,mime:'image/png',width:120,height:80}],next_cursor:null}});}
    if(url.pathname.endsWith('/edit')){
      const body=r.request().postDataJSON();writes.push(body);if(receipts.has(body.request_id))return r.fulfill({json:{...receipts.get(body.request_id) as object,replayed:true}});
      if(reject){reject=false;return r.fulfill({status:422,json:{detail:{code:'UD-INVALID',message:'The path geometry was refused.'}}});}
      let inverse:unknown;
      for(const op of body.ops){
        if(op.op==='boolean'){
          const first=shapes.find((s)=>s.id===op.ids[0])!,second=shapes.find((s)=>s.id===op.ids[1])!;inverse=[{op:'replace',id:first.id,node:structuredClone(first)},{op:'insert',parent:'page',node:structuredClone(second),index:1}];first.type='path';first.d='M 0 0 L 80 0 L 80 80 Z';shapes.splice(shapes.indexOf(second),1);
        }else if(op.op==='insert')shapes.splice(op.index??shapes.length,0,structuredClone(op.node));
        else if(op.op==='remove'){for(const id of op.ids){const at=shapes.findIndex((s)=>s.id===id);if(at>=0)shapes.splice(at,1);}}
        else if(op.op==='replace'){const at=shapes.findIndex((s)=>s.id===op.id);shapes.splice(at,1,structuredClone(op.node));}
        else Object.assign(shapes.find((s)=>s.id===op.id)!,op.set);
      }
      version++;const receipt={version,undo_ops:inverse,warnings:[]};receipts.set(body.request_id,receipt);
      if(fail){fail=false;return r.fulfill({status:502,json:{detail:'Proxy failed after commit.'}});}return r.fulfill({json:receipt});
    }
    return r.fulfill({status:404,json:{}});
  });
  await page.goto(`/ui-designer/demo${options.historical?'?version=1':''}`);await expect(page.getByRole('button',{name:'rect First',exact:true})).toBeVisible();
  return{shapes,writes,assets,fail:()=>{fail=true;},reject:()=>{reject=true;}};
}
async function pick(page:Page,name='path Curve'){await page.getByRole('button',{name,exact:true}).click();await expect(page.getByRole('button',{name:'Save node'})).toBeVisible();}

test('pen creates a closed cubic path and coordinate controls change one point before one insert',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'New path',exact:true}).click();const svg=page.getByRole('group',{name:'Path point editor'});
  await svg.click({position:{x:40,y:50}});await page.getByLabel('New path segment').selectOption('C');await svg.click({position:{x:120,y:120}});
  await page.getByLabel('Selected path point').selectOption('1');await page.getByLabel('First control X',{exact:true}).fill('33');await page.getByRole('button',{name:'Apply coordinates'}).click();
  await page.getByLabel('Closed path',{exact:true}).check();await page.getByRole('button',{name:'Save path',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(1);
  const op=state.writes[0].ops[0];expect(op.op).toBe('insert');expect(op.parent).toBe('page');expect(op.node.w).toBeGreaterThan(0);expect(op.node.h).toBeGreaterThan(0);expect(op.node.d).toMatch(/^M .* C 33 .* Z$/);
  await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Undo',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(2);expect(state.writes[1].ops[0]).toEqual({op:'remove',ids:[op.node.id]});
});

test('local cubic handles and raw edits preserve node placement and validation keeps the draft editable',async({page})=>{
  const state=await setup(page);await pick(page);const source=String(state.shapes[2].d);await page.getByLabel('Selected path point').selectOption('1');
  await page.getByLabel('First control X',{exact:true}).fill('25');await page.getByRole('button',{name:'Apply coordinates'}).click();
  await page.locator('circle[aria-label="Second control 2"]').focus();await page.keyboard.press('Shift+ArrowRight');
  state.reject();await page.getByRole('button',{name:'Save path',exact:true}).click();await expect(page.getByText('The path geometry was refused.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Save path',exact:true})).toBeEnabled();await expect(page.getByLabel('SVG path data')).not.toHaveValue(source);
  await page.getByRole('button',{name:'Save path',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(2);
  expect(state.writes[1].ops).toEqual([{op:'update',id:'path',set:{d:'M 0 0 C 25 10 30 20 40 40'}}]);expect(state.shapes[2]).toMatchObject({x:70,y:45,rotate:30,w:120,h:100});
});

test('unsupported imported commands stay untouched until explicit draft reset',async({page})=>{
  const state=await setup(page);state.shapes[2].d='M 0 0 Q 10 10 20 0';await pick(page);
  await expect(page.getByText('Its original data is preserved.',{exact:false})).toBeVisible();await expect(page.getByLabel('SVG path data')).toHaveValue('M 0 0 Q 10 10 20 0');expect(state.writes).toHaveLength(0);
  await page.getByRole('button',{name:'Reset to editable draft'}).click();expect(state.shapes[2].d).toBe('M 0 0 Q 10 10 20 0');await page.getByRole('button',{name:'Save path',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(1);expect(state.writes[0].ops[0].set.d).toBe('M 0 0 L 100 0 L 100 100 Z');
});

test('ordered boolean subtract gets exact server undo after unknown commit and redo stays boolean',async({page})=>{
  const state=await setup(page);await pick(page,'rect First');await page.getByLabel('Boolean operand Second',{exact:true}).check();await page.getByLabel('Boolean operation').selectOption('subtract');state.fail();
  await page.getByRole('button',{name:'Apply boolean'}).click();await expect(page.getByRole('button',{name:'Retry save'})).toBeVisible();await page.getByRole('button',{name:'Retry save'}).click();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();
  expect(state.writes[0]).toEqual(state.writes[1]);expect(state.writes[0].ops).toEqual([{op:'boolean',kind:'subtract',ids:['a','b']}]);
  await page.getByRole('button',{name:'Undo',exact:true}).click();await expect(page.getByRole('button',{name:'Redo',exact:true})).toBeEnabled();expect(state.shapes.map((s)=>s.id)).toEqual(['a','b','path']);expect(state.shapes[0].type).toBe('rect');
  await page.getByRole('button',{name:'Redo',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(4);expect(state.writes[3].ops).toEqual(state.writes[0].ops);
});

test('path image fill only uses scoped placed metadata and keeps path geometry',async({page})=>{
  const state=await setup(page);await pick(page);await page.getByRole('button',{name:'Load placed images'}).click();await page.getByLabel('Path placed image').selectOption(sha);await page.getByLabel('Path image fit').selectOption('contain');
  await page.getByRole('button',{name:'Apply image fill'}).click();await expect.poll(()=>state.writes.length).toBe(1);expect(state.writes[0].ops).toEqual([{op:'update',id:'path',set:{fill:{image:sha,fit:'contain'}}}]);expect(state.shapes[2]).toMatchObject({x:70,y:45,rotate:30,d:'M 0 0 C 10 10 20 20 40 40'});expect(state.assets[0]).toContain('version=1');
});

test('new path context changes cancel the draft and historical controls cannot write',async({page})=>{
  await setup(page);await pick(page,'rect First');await page.getByRole('button',{name:'New path',exact:true}).click();await expect(page.getByRole('region',{name:'Draw a new path'})).toBeVisible();await pick(page);
  await expect(page.getByRole('region',{name:'Draw a new path'})).toHaveCount(0);await expect(page.getByText('The new path draft was closed',{exact:false})).toBeVisible();
  await setup(page,{historical:true});await pick(page);await expect(page.getByRole('button',{name:'New path',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Save path',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Apply boolean'})).toBeDisabled();
});


test('dragging an anchor maps screen coordinates back into local path coordinates',async({page})=>{
  const state=await setup(page);await pick(page);const anchor=page.locator('circle[aria-label="Anchor 2"]');await anchor.scrollIntoViewIfNeeded();
  const points=await anchor.evaluate((element)=>{const circle=element as SVGCircleElement,matrix=circle.getScreenCTM()!;const start=new DOMPoint(circle.cx.baseVal.value,circle.cy.baseVal.value).matrixTransform(matrix),end=new DOMPoint(circle.cx.baseVal.value+5,circle.cy.baseVal.value+7).matrixTransform(matrix);return{x:start.x,y:start.y,ex:end.x,ey:end.y};});
  await page.mouse.move(points.x,points.y);await page.mouse.down();await page.mouse.move(points.ex,points.ey);await page.mouse.up();
  await page.getByRole('button',{name:'Save path',exact:true}).click();await expect.poll(()=>state.writes.length).toBe(1);
  expect(state.writes[0].ops[0].set).toEqual({d:'M 0 0 C 10 10 20 20 45 47'});expect(state.shapes[2]).toMatchObject({x:70,y:45,rotate:30});
});

test('new raw path fallback preserves supported server commands without quiet conversion',async({page})=>{
  const state=await setup(page);await page.getByRole('button',{name:'New path',exact:true}).click();
  const raw='M 0 0 Q 20 40 80 0 Z';await page.getByLabel('SVG path data').fill(raw);await page.getByRole('button',{name:'Save path',exact:true}).click();
  await expect.poll(()=>state.writes.length).toBe(1);expect(state.writes[0].ops[0].node.d).toBe(raw);expect(state.writes[0].ops[0].node.w).toBeGreaterThan(0);
});
