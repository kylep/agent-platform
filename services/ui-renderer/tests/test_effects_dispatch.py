"""Every renderer format shares effects dispatch and verified page-context assets."""
import base64
import copy
import hashlib
import io
import json

import pytest
from PIL import Image, ImageChops
import app as renderer
from test_app import client  # noqa: F401


def scene():
    return {'schema':1,'pages':[{'id':'p','children':[{'id':'card','type':'frame','w':100,'h':80,'children':[
        {'id':'shape','type':'ellipse','x':10,'y':10,'w':70,'h':60,'fill':{'gradient':'angular','stops':[[0,'#ff0000'],[.5,'#00ff00'],[1,'#0000ff']]}}
    ]}]}]}


def req(doc,node='card',**kw):
    return renderer._render_req({'doc':doc,'node':node,'nodes':[node],**kw})


def pixels(raw):
    image=Image.open(io.BytesIO(raw));image.load();return image.convert('RGBA')


def test_render_svg_standalone_html_and_diff_use_identical_effects(tmp_path):
    cfg=renderer.Config(asset_dir=str(tmp_path));doc=scene()
    render=renderer.job_render(cfg,req(doc))['images'][0]
    svg=renderer.job_svg(cfg,renderer._svg_req({'doc':doc,'node':'card'}))
    assert 'data:image/png;base64,' in svg['svg']
    svg_png=renderer._raster(svg['svg'],100,80,None,str(tmp_path))
    assert ImageChops.difference(pixels(render['data']),pixels(svg_png)).getbbox() is None
    for format in ['svg','html','pdf']:
        export=renderer.job_export(cfg,renderer._export_req({'doc':doc,'node':'card','format':format}))
        assert export['data'] and len(export['data'])<=8<<20
        if format=='svg':
            png=renderer._raster(export['data'].decode(),100,80,None,str(tmp_path))
            assert ImageChops.difference(pixels(render['data']),pixels(png)).getbbox() is None
        if format=='html':assert b'data:image/png;base64,' in export['data']
    diff=renderer.job_diff(cfg,renderer._diff_req({'before_doc':doc,'after_doc':doc,'node':'card'}))
    assert diff['changed_pixels']==0


def backdrop_scene(sha):
    return {'schema':1,'pages':[{'id':'p','children':[{'id':'card','type':'frame','w':100,'h':80,'children':[
        {'id':'preceding','type':'image','w':100,'h':80,'asset':sha},
        {'id':'glass','type':'ellipse','x':15,'y':10,'w':50,'h':40,'backdrop_blur':4,'fill':{'color':'#ffffff','opacity':.2}}
    ]}]}]}


def test_svg_and_crop_request_preceding_context_assets_by_sha(tmp_path):
    cfg=renderer.Config(asset_dir=str(tmp_path));stream=io.BytesIO()
    Image.new('RGB',(20,20),'#ea3745').save(stream,'PNG');raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
    doc=backdrop_scene(sha);request=renderer._svg_req({'doc':doc,'node':'glass'})
    assert renderer.job_svg(cfg,request)=={'ok':False,'code':'MISSING-ASSETS','missing':[sha]}
    assert renderer.job_render(cfg,req(doc,'glass'))['code']=='MISSING-ASSETS'
    assert renderer.job_asset(cfg,{'sha':sha,'data':raw})['ok']
    svg=renderer.job_svg(cfg,request)
    assert 'data:image/png;base64,' in svg['svg'] and 'https://' not in svg['svg']
    render=renderer.job_render(cfg,req(doc,'glass'))['images'][0]
    assert (render['width'],render['height'])==(50,40)
    svg_png=renderer._raster(svg['svg'],50,40,None,str(tmp_path))
    assert ImageChops.difference(pixels(render['data']),pixels(svg_png)).getbbox() is None
    exported=renderer.job_export(cfg,renderer._export_req({'doc':doc,'node':'glass','format':'svg'}))
    assert exported['data'].startswith(b'<svg')


def test_asset_href_is_closed_native_schema_and_asset_cache_hash_only(tmp_path):
    from agentplatform.ui_designer.schema import validate_document
    from agentplatform.ui_designer.errors import UiDesignerError
    for bad in ['data:image/png;base64,AAAA','https://example.com/x','../../etc/passwd']:
        with pytest.raises(UiDesignerError):validate_document(backdrop_scene(bad))
    doc=scene();doc['pages'][0]['children'][0]['children'][0]['href']='https://example.com'
    with pytest.raises(UiDesignerError):validate_document(doc)


def test_context_assets_do_not_include_other_pages(tmp_path):
    from agentplatform.ui_designer.layout import layout_document
    doc=scene();doc['pages'].append({'id':'other','children':[{'id':'other-image','type':'image','asset':'b'*64,'w':20,'h':20}]})
    assert renderer._paint_assets(layout_document(doc),'shape')==[]


def test_rotated_path_mask_and_effect_export_parity(tmp_path):
    cfg=renderer.Config(asset_dir=str(tmp_path));doc=scene()
    card=doc['pages'][0]['children'][0]
    card['rotate']=15
    card['children']=[{'id':'masked','type':'frame','w':90,'h':70,'mask':'alpha','children':[
        {'id':'mask','type':'path','w':90,'h':70,'d':'M0 0L90 0L45 70Z','fill':'#ffffff'},
        {'id':'fill','type':'rect','w':90,'h':70,'fill':{'gradient':'angular','stops':[[0,'#ff0000'],[1,'#0000ff']]}}
    ]}]
    out=renderer.job_render(cfg,req(doc,'masked'))['images'][0]
    svg=renderer.job_export(cfg,renderer._export_req({'doc':doc,'node':'masked','format':'svg'}))
    raster=renderer._raster(svg['data'].decode(),out['width'],out['height'],None,str(tmp_path))
    assert ImageChops.difference(pixels(out['data']),pixels(raster)).getbbox() is None
    assert pixels(raster).getbbox() is not None


def test_large_native_effect_scene_can_render_bounded_small_thumbnail(tmp_path):
    doc=scene();card=doc['pages'][0]['children'][0]
    card.update(w=5000,h=1000)
    card['children'][0].update(w=5000,h=1000,x=0,y=0)
    out=renderer.job_render(renderer.Config(asset_dir=str(tmp_path)),req(doc,scale=3,max_edge=100))['images'][0]
    assert (out['width'],out['height'])==(100,20)


@pytest.mark.parametrize('kind',['render','diff'])
def test_multi_paint_jobs_share_work_budget(tmp_path,monkeypatch,kind):
    from agentplatform.ui_designer import raster_effects
    from agentplatform.ui_designer.errors import UiDesignerError
    monkeypatch.setattr(raster_effects,'MAX_WORK_PIXELS',30000)
    cfg=renderer.Config(asset_dir=str(tmp_path));doc=scene()
    # One paint fits; two paints must not reset the per-request work counter.
    renderer.job_render(cfg,req(doc))
    with pytest.raises(UiDesignerError,match='aggregate'):
        if kind=='render':
            request=req(doc);request['nodes']=['card','card']
            renderer.job_render(cfg,request)
        else:
            renderer._job_diff_uncached(cfg,renderer._diff_req({'before_doc':doc,'after_doc':doc,'node':'card'}))


async def test_native_svg_http_missing_asset_retry_uses_verified_cache(client):
    stream=io.BytesIO();Image.new('RGB',(8,8),'#1090e0').save(stream,'PNG')
    raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest();doc=backdrop_scene(sha)
    body={'doc':doc,'node':'glass'}
    missing=await client.post('/svg',json=body)
    assert missing.status==409 and (await missing.json())['missing']==[sha]
    assert (await client.put('/assets/'+sha,data=raw)).status==200
    response=await client.post('/svg',json=body)
    assert response.status==200
    assert 'data:image/png;base64,' in (await response.json())['svg']
