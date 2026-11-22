import base64
import json
from types import SimpleNamespace
import pytest
from sqlalchemy import select, func
from agentplatform.db import UiDesignerDocument, UiDesignerImport
from agentplatform.ui_designer import store
from agentplatform.ui_designer.figma_import_job import FigmaImportWorker


def config():
    return SimpleNamespace(ui_designer_max_total_bytes=64 << 20, ui_designer_max_version_bytes_per_doc=16 << 20)


def result(meta, tree=None):
    return {'ok':True,'meta':{'ok':True,**meta},'files':[] if tree is None else [
        {'name':'tree.json','b64':base64.b64encode(json.dumps(tree).encode()).decode()}]}


async def test_restart_checkpoints_and_atomic_completion(sf):
    calls=[]
    async def fetch(args):
        calls.append(args['mode'])
        if args['mode']=='file_meta':
            return result({'name':'Demo','version':'v1','pages':[{'id':'1:1','name':'Main'}]})
        return result({}, {'styles':{'s':{'name':'Brand','styleType':'FILL'}}, 'document':{'type':'DOCUMENT','children':[{'id':'1:1','type':'CANVAS','name':'Main','children':[
            {'id':'1:2','type':'RECTANGLE','name':'Box','size':{'x':50,'y':40},'styles':{'fill':'s'},'fills':[{'type':'SOLID','color':{'r':1,'g':0,'b':0}}]}]}]}})
    async with sf() as s:
        job,_=await store.start_import(s,slug='imported',file_key='abc',initiated_by='user:kyle')
        ident=job.id
        await s.commit()
    worker=FigmaImportWorker(sf,config(),fetch=fetch)
    await worker.advance(); await worker.advance(); await worker.advance()
    async with sf() as s:
        assert (await s.get(UiDesignerImport,ident)).staged
        assert (await s.execute(select(func.count()).select_from(UiDesignerDocument))).scalar()==0
    await FigmaImportWorker(sf,config(),fetch=fetch).advance()
    assert not await worker.advance()
    async with sf() as s:
        assert (await s.get(UiDesignerImport,ident)).state=='done'
        assert (await s.execute(select(func.count()).select_from(UiDesignerDocument))).scalar()==1
        document = (await s.execute(select(UiDesignerDocument))).scalar_one()
        assert list(document.doc['tokens']['color'].values()) == ['#ff0000']
    assert calls==['file_meta','tree_page']


def test_namespace_preserves_literals_and_remaps_overrides():
    from agentplatform.ui_designer.figma_import_job import namespace
    doc={'pages':[{'id':'page','children':[{'id':'child','type':'text','text':'child','meta':{'figma':{'id':'child'}}},
        {'id':'inst','type':'instance','of':'comp','overrides':{'child.text':'child'}}]}],
         'components':{'comp':{'id':'comp','type':'frame','children':[]}}}
    out=namespace(doc,'p0-')
    text,inst=out['pages'][0]['children']
    assert text['text']=='child' and text['meta']['figma']['id']=='child'
    assert inst['of'] in out['components']
    assert inst['overrides']=={text['id']+'.text':'child'}


async def test_rate_limit_is_durable_and_does_not_starve_other_job(sf):
    async def fetch(args):
        if args['file_key']=='limited':
            return {'ok':True,'meta':{'ok':False,'error':'UD-IMPORT-RATE-LIMITED','retry_after_seconds':3600}}
        return result({'name':'Other','pages':[{'id':'p','name':'P'}]})
    async with sf() as s:
        a,_=await store.start_import(s,slug='a',file_key='limited',initiated_by='user:kyle')
        b,_=await store.start_import(s,slug='b',file_key='good',initiated_by='user:kyle')
        a_id,b_id=a.id,b.id
        await s.commit()
    worker=FigmaImportWorker(sf,config(),fetch=fetch)
    await worker.advance(); await worker.advance()
    async with sf() as s:
        assert (await s.get(UiDesignerImport,a_id)).report['next_attempt_at']
        assert (await s.get(UiDesignerImport,b_id)).pages


async def test_failed_page_is_explicit_and_no_document_created(sf):
    async def fetch(args):
        if args['mode']=='file_meta':
            return result({'name':'Bad','pages':[{'id':'p','name':'P'}]})
        raise RuntimeError('secret upstream details')
    async with sf() as s:
        job,_=await store.start_import(s,slug='bad',file_key='bad',initiated_by='user:kyle')
        ident=job.id
        await s.commit()
    worker=FigmaImportWorker(sf,config(),fetch=fetch)
    for _ in range(3):
        await worker.advance()
    async with sf() as s:
        job=await s.get(UiDesignerImport,ident)
        assert job.state=='failed' and job.pages['p']['state']=='failed'
        assert not job.staged and 'secret' not in job.error
        assert (await s.execute(select(func.count()).select_from(UiDesignerDocument))).scalar()==0


async def test_image_batches_resume_after_rate_limit(sf):
    import io
    from PIL import Image
    image=Image.new('RGB',(1,1)); stream=io.BytesIO(); image.save(stream,format='PNG')
    encoded=base64.b64encode(stream.getvalue()).decode()
    batches=[]
    limited=False
    async def fetch(args):
        nonlocal limited
        if args['mode']=='file_meta':
            return result({'name':'Images','pages':[{'id':'p','name':'P'}]})
        if args['mode']=='tree_page':
            return result({}, {'document':{'type':'CANVAS','id':'p','name':'P','children':[
                {'id':f'r{i}','type':'RECTANGLE','size':{'x':1,'y':1},'fills':[{'type':'IMAGE','imageRef':str(i)}]}
                for i in range(9)]}})
        batches.append(args['image_refs'])
        if len(args['image_refs'])==1 and not limited:
            limited=True
            return {'ok':True,'meta':{'ok':False,'error':'UD-IMPORT-RATE-LIMITED','retry_after_seconds':60}}
        return {'ok':True,'meta':{'ok':True},'files':[
            {'name':f'asset-{i}.bin','b64':encoded,'meta':{'image_ref':ref}} for i,ref in enumerate(args['image_refs'])]}
    async with sf() as s:
        job,_=await store.start_import(s,slug='images',file_key='img',initiated_by='user:kyle')
        ident=job.id; await s.commit()
    worker=FigmaImportWorker(sf,config(),fetch=fetch)
    for _ in range(4):
        await worker.advance()
    async with sf() as s:
        job=await s.get(UiDesignerImport,ident)
        assert len(job.staged['p']['image_assets'])==8
        job.report={k:v for k,v in job.report.items() if k!='next_attempt_at'}
        await s.commit()
    for _ in range(3):
        await worker.advance()
    assert list(map(len,batches))==[8,1,1]
    async with sf() as s:
        assert (await s.get(UiDesignerImport,ident)).state=='done'


def test_namespace_remaps_rich_text_token_colors_but_preserves_text():
    from agentplatform.ui_designer.figma_import_job import namespace
    doc={'tokens':{'color':{'brand':'#f00'},'type':{'heading':{'family':'Inter','size':20}}},
         'pages':[{'id':'p','children':[{'id':'t','type':'text','style':'$type.heading',
             'text':[{'text':'$color.brand','color':'$color.brand'}]}]}]}
    out=namespace(doc,'p0-')
    node=out['pages'][0]['children'][0]
    assert node['style']=='$type.p0-heading'
    assert node['text']==[{'text':'$color.brand','color':'$color.p0-brand'}]
