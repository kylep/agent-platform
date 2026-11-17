import copy
import hashlib
import io
import math

import pytest
import resvg_py
from PIL import Image
from fontTools.svgLib.path import parse_path
import pathops

import app as renderer
from agentplatform.ui_designer.vector_geometry import boolean_geometry,validate_path
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.svg import paint,AssetInfo
from test_app import client  # noqa: F401


def rect(x=0,y=0,w=100,h=100,**extra): return {'type':'rect','x':x,'y':y,'w':w,'h':h,**extra}


def area(geometry):
    p=pathops.Path();parse_path(geometry['d'],p.getPen());return abs(p.area)


@pytest.mark.parametrize('kind,expected',[('union',15000),('subtract',5000),('intersect',5000),('exclude',10000)])
def test_vetted_booleans_have_expected_geometry_area(kind,expected):
    result=boolean_geometry([rect(),rect(50)],kind)
    assert area(result['geometry'])==pytest.approx(expected)
    validate_path(result['geometry']['d'])
    import re
    assert set(re.findall(r'[A-DF-Za-df-z]',result['geometry']['d'])) <= set('MLCZ')


def test_path_dimensions_do_not_scale_local_d_and_rotation_is_baked():
    path={'type':'path','x':10,'y':20,'w':10,'h':10,'d':'M0 0H20V20H0Z'}
    result=boolean_geometry([path,rect(20,20,10,20)],'intersect')['geometry']
    assert (result['x'],result['y'],result['w'],result['h'])==(20,20,10,20)
    rotated=boolean_geometry([rect(w=20,h=10,rotate=90),rect(-20,-20,100,100)],'intersect')['geometry']
    assert (rotated['x'],rotated['y'],rotated['w'],rotated['h'])==pytest.approx((5,-5,10,20))


def test_nonzero_hole_orientation_survives_rasterization(tmp_path):
    result=boolean_geometry([rect(),{'type':'ellipse','x':25,'y':25,'w':50,'h':50}],'subtract')['geometry']
    assert area(result)==pytest.approx(10000-math.pi*25**2,rel=.001)
    doc={'schema':1,'pages':[{'id':'p','children':[{'id':'result','type':'path','fill':'#f00',
         **{k:v for k,v in result.items() if k!='empty'}}]}]}
    svg=paint(layout_document(doc),'result').svg
    raw=resvg_py.svg_to_bytes(svg_string=svg,width=100,height=100,resources_dir=str(tmp_path),skip_system_fonts=True,font_dirs=[])
    image=Image.open(io.BytesIO(raw)).convert('RGBA')
    assert image.getpixel((50,50))[3]==0 and image.getpixel((5,5))[3]==255


def test_empty_result_and_empty_operand_remain_valid():
    empty=boolean_geometry([rect(),rect(200)],'intersect')['geometry']
    assert empty['empty'] and empty['d']=='' and empty['w']==empty['h']==0
    result=boolean_geometry([{'type':'path',**{k:v for k,v in empty.items() if k!='empty'}},rect()], 'union')['geometry']
    assert area(result)==10000


def test_open_contour_uses_svg_fill_implicit_close():
    result=boolean_geometry([{'type':'path','x':0,'y':0,'w':100,'h':100,'d':'M0 0H100V100'},rect()], 'intersect')['geometry']
    assert area(result)==5000


@pytest.mark.parametrize('path',['M0 0 L','M0 0L','M0 0C','M0 0A','M0 0junk','M0 0 L1e 0','M0,,0','M0 0,','M1e999 0L1 1Z'])
def test_strict_parser_never_silently_ignores_bad_geometry(path):
    with pytest.raises(UiDesignerError): validate_path(path)


@pytest.mark.parametrize('path',['m0 0h10v10h-10z','M0 0Q10 20 20 0T40 0Z','M0 0A10 10 0 0110 20Z','M0 0A0 10 0 0 1 10 0L10 10Z'])
def test_standard_relative_quadratic_compact_arc_and_zero_radius_are_supported(path):
    validate_path(path)
    assert boolean_geometry([{'type':'path','x':0,'y':0,'w':100,'h':100,'d':path},rect()], 'union')['geometry']['d']


def test_segment_coordinate_and_operand_bounds_before_native_work():
    too_many='M0 0'+'L1 1'*2050+'Z'
    with pytest.raises(UiDesignerError): boolean_geometry([{'type':'path','x':0,'y':0,'w':10,'h':10,'d':too_many},rect()], 'union')
    with pytest.raises(UiDesignerError): boolean_geometry([rect()]*9,'union')
    with pytest.raises(UiDesignerError): boolean_geometry([rect(x=100000),rect()], 'union')
    with pytest.raises(UiDesignerError): boolean_geometry([rect(w='fill'),rect()],'union')


@pytest.mark.parametrize('fit',['cover','contain','fill'])
def test_path_image_paint_clips_actual_translated_shape(tmp_path,fit):
    source=io.BytesIO();Image.new('RGB',(10,10),'#ff0000').save(source,'PNG')
    raw=source.getvalue();sha=hashlib.sha256(raw).hexdigest();(tmp_path/'source.png').write_bytes(raw)
    doc={'schema':1,'pages':[{'id':'p','children':[{'id':'canvas','type':'frame','w':100,'h':100,'children':[
        {'id':'path','type':'path','x':20,'y':30,'w':40,'h':40,'d':'M0 0H40L0 40Z',
         'fill':{'image':sha,'fit':fit}}]}]}]}
    svg=paint(layout_document(doc),'canvas',assets={sha:AssetInfo('source.png',10,10)}).svg
    assert 'transform="translate(20 30)"' in svg
    png=resvg_py.svg_to_bytes(svg_string=svg,width=100,height=100,resources_dir=str(tmp_path),skip_system_fonts=True,font_dirs=[])
    image=Image.open(io.BytesIO(png)).convert('RGBA')
    assert image.getpixel((25,35))==(255,0,0,255)
    assert image.getpixel((55,65))[3]==0
    assert image.getpixel((5,5))[3]==0


async def test_worker_boolean_http_has_closed_bounded_geometry(client):
    response=await client.post('/boolean',json={'kind':'union','operands':[rect(),rect(50)]})
    assert response.status==200,await response.text()
    body=await response.json()
    assert body['ok'] and body['renderer_version']
    assert set(body['geometry'])=={'x','y','w','h','d','empty'}
    assert area(body['geometry'])==15000
    refused=await client.post('/boolean',json={'kind':'union','operands':[rect(),rect()],'script':'x'})
    assert refused.status==400


def test_rotated_path_image_and_nonzero_hole_clip_correctly(tmp_path):
    stream=io.BytesIO();Image.new('RGB',(10,10),'#ff0000').save(stream,'PNG')
    raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest();(tmp_path/'source.png').write_bytes(raw)
    node={'id':'path','type':'path','x':20,'y':30,'w':40,'h':40,'rotate':90,
          'd':'M0 0H40L0 40Z','fill':{'image':sha,'fit':'cover'}}
    def render(node):
        doc={'schema':1,'pages':[{'id':'p','children':[{'id':'canvas','type':'frame','w':100,'h':100,'children':[node]}]}]}
        source=paint(layout_document(doc),'canvas',assets={sha:AssetInfo('source.png',10,10)}).svg
        return Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_string=source,width=100,height=100,
             resources_dir=str(tmp_path),skip_system_fonts=True,font_dirs=[]))).convert('RGBA')
    image=render(node)
    assert image.getpixel((55,35))==(255,0,0,255) and image.getpixel((25,65))[3]==0
    node.pop('rotate');node['d']='M0 0H40V40H0Z M10 10V30H30V10Z'
    node['fill']=[{'image':sha},{'color':'#0000ff','opacity':.25}]
    image=render(node)
    assert image.getpixel((40,50))[3]==0
    assert image.getpixel((25,35))[:3]==pytest.approx((191,0,64),abs=1)
