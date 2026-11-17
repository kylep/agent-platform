import { expect,test,type Page } from '@playwright/test';
import { mockApi } from './mock-api';
const face={sha256:'a'.repeat(64),family:'Example OFL',weight:500,license:'OFL-1.1',license_text:'SIL OPEN FONT LICENSE Version 1.1 — example verified license',glyphs:200,units_per_em:1000};
const fontFile={name:'example.ttf',mimeType:'font/ttf',buffer:Buffer.from([0,1,0,0,0,0,0,0])};
async function setup(page:Page,role='admin'){
  await mockApi(page);await page.route('**/api/whoami',r=>r.fulfill({json:{role}}));
  let version=1,installed=false,failed=false,conflict=false;const receipts=new Map<string,number>();const uploads:Record<string,unknown>[]=[],edits:Record<string,unknown>[]=[];
  const text={id:'text',type:'text',title:'Greeting',text:[{text:'Hello',style:{weight:700}}],x:0,y:0,w:100,h:40,style:{family:'Inter',size:20,line:1.4,weight:700,letter:0.5} as Record<string,unknown>};
  await page.route('**/api/ui-designer/**',async r=>{
    const url=new URL(r.request().url());
    if(url.pathname.endsWith('/versions'))return r.fulfill({json:{version,versions:[{version:1,author:'kyle'},{version:2,author:'kyle'},{version:3,author:'kyle'}]}});
    if(url.pathname.endsWith('/resolved'))return r.fulfill({json:{pages:[{id:'page',children:[text]}]}});
    if(url.pathname.endsWith('/comments'))return r.fulfill({json:{comments:[]}});
    if(url.pathname.endsWith('/render'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"/>'});
    if(url.pathname.endsWith('/inspect'))return r.fulfill({json:{id:'text',type:'text',text:'Hello',authored:text,version:Number(url.searchParams.get('version'))}});
    if(url.pathname.endsWith('/fonts')){
      if(r.request().method()==='GET')return r.fulfill({json:{version:Number(url.searchParams.get('version')),fonts:installed && url.searchParams.get('version')!=='1' ? [face]:[]}});
      const body=r.request().postDataJSON();uploads.push(body);if(receipts.has(body.request_id))return r.fulfill({json:{version:receipts.get(body.request_id),replayed:true,fonts:[face]}});
      if(conflict){conflict=false;version++;return r.fulfill({status:409,json:{detail:{code:'UD-VERSION-CONFLICT',message:'Changed'}}});}
      installed=true;version++;receipts.set(body.request_id,version);if(failed){failed=false;return r.fulfill({status:502,json:{detail:'Proxy failure'}});}
      return r.fulfill({json:{version,fonts:[face],warnings:[]}});
    }
    if(url.pathname.endsWith('/edit')){const body=r.request().postDataJSON();edits.push(body);for(const op of body.ops)Object.assign(text,op.set);version++;return r.fulfill({json:{version,warnings:[]}});}
    return r.fulfill({status:404,json:{}});
  });
  return {uploads,edits,text,getVersion:()=>version,fail:()=>{failed=true;},conflict:()=>{conflict=true;},install:()=>{installed=true;version=2;}};
}
async function openFonts(page:Page){await page.getByRole('button',{name:'Fonts',exact:true}).click();await expect(page.getByRole('heading',{name:'Document fonts'})).toBeVisible();}
test('font upload pins CAS, lists license and custom family edits preserve typography and rich text',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await openFonts(page);
  await page.getByLabel('Font file',{exact:true}).setInputFiles(fontFile);await page.getByRole('button',{name:'Upload font',exact:true}).click();
  await expect(page.getByRole('list',{name:'Installed document fonts'})).toContainText('Example OFL');
  expect(state.uploads[0]).toMatchObject({expected_version:1,data_base64:fontFile.buffer.toString('base64')});expect(state.uploads[0].request_id).toMatch(/^[a-f0-9]{32}$/);
  await page.getByText('License for Example OFL 500',{exact:true}).click();await expect(page.getByText(face.license_text,{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Discard draft and reload inspector'}).click();
  await page.getByRole('button',{name:'text Greeting',exact:true}).click();
  await page.getByLabel('Text font family',{exact:true}).selectOption('Example OFL');await expect(page.getByLabel('Text font weight',{exact:true})).toHaveValue('700');
  await page.getByRole('button',{name:'Save node',exact:true}).click();await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeEnabled();
  expect(state.edits[0]).toMatchObject({expected_version:2,ops:[{op:'update',id:'text',set:{style:{family:'Example OFL',size:20,line:1.4,weight:700,letter:0.5}}}]});
  expect(state.text.text).toEqual([{text:'Hello',style:{weight:700}}]);
  await page.getByRole('button',{name:'Undo',exact:true}).click();await expect(page.getByRole('button',{name:'Redo',exact:true})).toBeEnabled();
  expect(state.edits[1]).toMatchObject({ops:[{set:{style:{family:'Inter',size:20,line:1.4,weight:700,letter:0.5}}}]});
});
test('invalid file size/type stays local and uncertain upload retries exact bytes and receipt',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await openFonts(page);
  await page.getByLabel('Font file',{exact:true}).setInputFiles({...fontFile,name:'font.woff'});await page.getByRole('button',{name:'Upload font',exact:true}).click();await expect(page.getByRole('alert')).toContainText('TTF or OTF');
  await page.getByLabel('Font file',{exact:true}).setInputFiles({...fontFile,buffer:Buffer.alloc(1024*1024+1)});await page.getByRole('button',{name:'Upload font',exact:true}).click();await expect(page.getByRole('alert')).toContainText('1 MiB');expect(state.uploads).toHaveLength(0);
  await page.getByLabel('Font file',{exact:true}).setInputFiles(fontFile);state.fail();await page.getByRole('button',{name:'Upload font',exact:true}).click();
  await expect(page.getByRole('button',{name:'Retry font upload'})).toBeVisible();await expect(page.getByLabel('Version',{exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Close fonts'})).toBeDisabled();
  await page.getByRole('button',{name:'Retry font upload'}).click();await expect(page.getByRole('list',{name:'Installed document fonts'})).toBeVisible();
  expect(state.uploads).toHaveLength(2);expect(state.uploads[0]).toEqual(state.uploads[1]);expect(state.getVersion()).toBe(2);
});
test('CAS conflict refreshes latest and requires an explicit new upload',async({page})=>{
  const state=await setup(page);await page.goto('/ui-designer/demo');await openFonts(page);state.conflict();
  await page.getByLabel('Font file',{exact:true}).setInputFiles(fontFile);await page.getByRole('button',{name:'Upload font',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Review the latest version');await expect(page.getByRole('button',{name:'Upload font',exact:true})).toBeDisabled();expect(state.uploads).toHaveLength(1);
  await page.getByRole('button',{name:'Discard draft and reload inspector'}).click();await page.getByRole('button',{name:'Upload font',exact:true}).click();
  await expect(page.getByRole('list',{name:'Installed document fonts'})).toBeVisible();expect(state.uploads[1].expected_version).toBe(2);expect(state.uploads[1].request_id).not.toBe(state.uploads[0].request_id);
});
for(const role of ['reader','admin'])test(`font history is read-only for ${role}`,async({page})=>{
  const state=await setup(page,role);state.install();await page.goto(`/ui-designer/demo${role==='admin'?'?version=1':''}`);await openFonts(page);
  await expect(page.getByLabel('Font file',{exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Upload font',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'text Greeting',exact:true}).click();await expect(page.getByLabel('Text font family',{exact:true})).toBeDisabled();
});


test('typography token family stays intact when another property is edited',async({page})=>{
  const state=await setup(page);Object.assign(state.text,{style:'$type.body'});await page.goto('/ui-designer/demo');
  await page.getByRole('button',{name:'text Greeting',exact:true}).click();
  await expect(page.getByLabel('Text font family',{exact:true})).toBeDisabled();
  await page.getByLabel('Title',{exact:true}).fill('Kept token');await page.getByRole('button',{name:'Save node',exact:true}).click();
  await expect.poll(()=>state.edits.length).toBe(1);expect(state.edits[0]).toMatchObject({ops:[{set:{title:'Kept token'}}]});expect(state.text.style).toBe('$type.body');
});
