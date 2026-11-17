import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const templates=[
  {id:'desk-buddy',title:'Desk buddy',description:'Friendly desktop companion.',width:480,height:320},
  {id:'phone-dashboard',title:'Phone dashboard',description:'Stats and tasks.',width:390,height:844},
  {id:'desktop-workspace',title:'Desktop workspace',description:'Workspace starter.',width:1440,height:900},
  {id:'game-spectator-hud',title:'Game spectator HUD',description:'Score and event feed.',width:1920,height:1080},
].map((value)=>({...value,revision:1,tags:['starter'],preview_node:'canvas'}));
async function setup(page:Page,role='admin'){
  await mockApi(page);const bodies:Record<string,unknown>[]=[];let catalogs=0,failed=false,catalogFailed=false,invalid=false,held:Promise<void>|null=null;
  const receipts=new Set<string>();
  await page.route('**/api/whoami',(r)=>r.fulfill({json:{role}}));
  await page.route('**/api/ui-designer/templates',async(r)=>{catalogs++;return catalogFailed?r.fulfill({status:502,json:{detail:'Catalog unavailable.'}}):r.fulfill({json:{templates}});});
  await page.route('**/api/ui-designer/docs',async(r)=>{
    if(r.request().method()==='GET')return r.fulfill({json:{docs:[]}});
    const body=r.request().postDataJSON();bodies.push(body);if(held)await held;
    if(receipts.has(body.request_id))return r.fulfill({json:{slug:body.slug,version:1,replayed:true}});
    if(invalid)return r.fulfill({status:409,json:{detail:{code:'UD-SLUG-TAKEN',message:'This document slug already exists.'}}});
    receipts.add(body.request_id);
    if(failed){failed=false;return r.fulfill({status:502,json:{detail:'Proxy failed after commit.'}});}
    return r.fulfill({json:{slug:body.slug,version:1}});
  });
  await page.route('**/api/ui-designer/docs/*/**',(r)=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/access') || url.pathname.endsWith('/presence') || url.pathname.includes('/presence/'))return r.fallback();
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version:1,versions:[{version:1,author:'kyle'}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[]}});
    return r.fulfill({status:404,json:{}});
  });
  await page.goto('/ui-designer');
  return{bodies,catalogs:()=>catalogs,failAfter:()=>{failed=true;},conflict:()=>{invalid=true;},catalogFail:(value=true)=>{catalogFailed=value;},hold:(value:Promise<void>)=>{held=value;}};
}

for(const template of templates)test(`creates ordinary document from ${template.id} with pinned catalog revision`,async({page})=>{
  const state=await setup(page);await page.getByLabel('Starter template').selectOption(template.id);
  await expect(page.getByRole('region',{name:'Template details'})).toContainText(`${template.width} × ${template.height} px`);
  await page.getByLabel('Document slug',{exact:true}).fill('new-design');await page.getByLabel('Document title (optional)').fill('My starter');
  await page.getByRole('button',{name:'Create document',exact:true}).click();await expect(page).toHaveURL(/\/ui-designer\/new-design$/);
  expect(state.bodies[0]).toMatchObject({slug:'new-design',title:'My starter',template_id:template.id,template_revision:1});
  expect(String(state.bodies[0].request_id)).toMatch(/^[0-9a-f]{32}$/);expect(state.bodies[0]).not.toHaveProperty('doc');
});

test('blank create keeps ordinary API behavior and malformed slug never writes',async({page})=>{
  const state=await setup(page);await page.getByLabel('Document slug',{exact:true}).fill('Bad slug');await page.getByRole('button',{name:'Create document',exact:true}).click();
  await expect(page.getByRole('alert')).toHaveText('Use 1–64 lowercase letters, numbers or hyphens for the document slug.');expect(state.bodies).toHaveLength(0);
  await page.getByLabel('Document slug',{exact:true}).fill('blank');await page.getByRole('button',{name:'Create document',exact:true}).click();await expect(page).toHaveURL(/\/ui-designer\/blank$/);
  await expect(page.getByText('This document has no pages yet.')).toBeVisible();
  expect(state.bodies[0]).not.toHaveProperty('template_id');expect(state.bodies[0]).not.toHaveProperty('template_revision');
});

test('uncertain create locks fields and retries identical receipt without refetching catalog',async({page})=>{
  await page.addInitScript(()=>Object.defineProperty(window.crypto,'randomUUID',{value:undefined}));
  const state=await setup(page);state.failAfter();await page.getByLabel('Starter template').selectOption('desk-buddy');await page.getByLabel('Document slug',{exact:true}).fill('uncertain');
  await page.getByRole('button',{name:'Create document',exact:true}).click();await expect(page.getByRole('button',{name:'Retry create'})).toBeVisible();await expect(page.getByLabel('Starter template')).toBeDisabled();
  await page.getByRole('button',{name:'Retry create'}).click();await expect(page).toHaveURL(/\/ui-designer\/uncertain$/);
  expect(state.bodies[0]).toEqual(state.bodies[1]);expect(state.catalogs()).toBe(1);
});

test('roles gate creation while keeping real import administrator-only, conflicts are clear',async({page})=>{
  await setup(page,'reader');await expect(page.getByRole('region',{name:'Create a design document'})).toHaveCount(0);
  await setup(page,'operator');await expect(page.getByRole('region',{name:'Create a design document'})).toBeVisible();await expect(page.getByRole('region',{name:'Import from Figma'})).toHaveCount(0);
  const state=await setup(page);state.conflict();await page.getByLabel('Document slug',{exact:true}).fill('taken');await page.getByRole('button',{name:'Create document',exact:true}).click();
  await expect(page.getByRole('alert')).toHaveText('This document slug already exists.');await expect(page.getByRole('button',{name:'Create document',exact:true})).toBeEnabled();
});

test('leaving while create runs never navigates a different document unexpectedly',async({page})=>{
  const state=await setup(page);let release:()=>void=()=>{};state.hold(new Promise<void>((r)=>{release=r;}));await page.getByLabel('Document slug',{exact:true}).fill('late');
  await page.getByRole('button',{name:'Create document',exact:true}).click();await expect(page.getByRole('button',{name:'Creating…'})).toBeDisabled();
  await page.getByRole('link',{name:'Wiki',exact:true}).click();release();await expect(page).toHaveURL(/\/wiki$/);await expect(page.getByRole('heading',{name:'UI Designer / late'})).toHaveCount(0);
});


test('catalog errors can reload while ordinary blank creation remains available',async({page})=>{
  const state=await setup(page);state.catalogFail();await page.reload();
  await expect(page.getByRole('alert')).toContainText('Catalog unavailable.');
  await expect(page.getByRole('button',{name:'Create document',exact:true})).toBeEnabled();
  state.catalogFail(false);await page.getByRole('button',{name:'Reload templates'}).click();
  await page.getByLabel('Starter template').selectOption('desk-buddy');await expect(page.getByRole('region',{name:'Template details'})).toContainText('480 × 320');
});
