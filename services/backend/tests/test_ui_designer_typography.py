import copy
import io
import xml.etree.ElementTree as ET

import pytest
import resvg_py
from PIL import Image,ImageChops

from agentplatform.ui_designer.text import layout_text,make_style,transform_runs
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.svg import paint
from agentplatform.ui_designer.export_formats import html_document


def doc(**props):
    return {'schema':1,'pages':[{'id':'p','children':[{'id':'text','type':'text','text':'Typography',
        'w':220,'h':100,'style':{'size':16},**props}]}]}


def text(doc): return layout_document(doc)['pages'][0]['children'][0]['text']


def test_unicode_case_expansions_and_context_preserve_run_styles():
    a,b=make_style({'size':16}),make_style({'size':24,'weight':700})
    runs=[('stra',a),('ße',b)]
    transformed=transform_runs(runs,'uppercase')
    assert transformed==[('STRA',a),('SSE',b)]
    assert runs==[('stra',a),('ße',b)]
    assert transform_runs([('Ο',a),('Σ',b)],'lowercase')==[('ο',a),('ς',b)]
    assert transform_runs(runs,'none') is runs


def test_capitalize_carries_word_state_across_runs_and_internal_apostrophes():
    st=make_style(None)
    result=transform_runs([('hel',st),("lo don't ",st),('world',st)],'capitalize')
    assert [v for v,_ in result]==['Hel',"lo Don't ",'World']
    assert transform_runs([("we're o’neill --ßeta\nélan",st)],'capitalize')[0][0]=="We're O’neill --SSeta\nÉlan"


def test_render_copy_preserves_authored_rich_text_and_colors():
    source=doc(text=[{'text':'stra','color':'#ff0000'},{'text':'ße','color':'#0000ff'}],text_case='uppercase')
    before=copy.deepcopy(source)
    result=text(source)
    assert result['lines'][0]['text']=='STRASSE'
    assert [r['color'] for r in result['lines'][0]['runs']]==['#ff0000','#0000ff']
    assert source==before


def test_paragraph_final_lines_single_words_and_blank_paragraphs_not_justified():
    st=make_style({'size':16})
    result=layout_text([('alpha beta gamma delta epsilon\nshort end',st)],90,justify=True)
    assert result.lines[0].w==90
    assert result.lines[-2].text.endswith('epsilon') and result.lines[-2].w<90
    assert result.lines[-1].text=='short end' and result.lines[-1].w<90
    single=layout_text([('abcdefghijklmnop',st)],25,justify=True)
    plain=layout_text([('abcdefghijklmnop',st)],25)
    assert [line.w for line in single.lines]==[line.w for line in plain.lines]
    blank=layout_text([('x\n\ny',st)],90,justify=True)
    assert [line.text for line in blank.lines]==['x','','y']


def test_ellipsis_not_stretched_and_justify_cache_cannot_mutate_legacy_layout():
    runs=[('one two three four five six seven eight nine',make_style({'size':16}))]
    plain=layout_text(runs,90,2)
    saved=[(ln.w,[copy.deepcopy(f.glyphs) for f in ln.frags]) for ln in plain.lines]
    justified=layout_text(runs,90,2,justify=True)
    assert justified.truncated and justified.lines[-1].text.endswith('…')
    assert justified.lines[-1].frags[0].glyphs==plain.lines[-1].frags[0].glyphs
    assert layout_text(runs,90,2) is plain
    assert [(ln.w,[f.glyphs for f in ln.frags]) for ln in plain.lines]==saved


def test_fixed_box_vertical_alignment_and_no_negative_overflow_offset():
    top=text(doc())['lines'][0]['y']
    center=text(doc(vertical_align='center'))['lines'][0]['y']
    bottom=text(doc(vertical_align='bottom'))['lines'][0]['y']
    assert center-top==pytest.approx((bottom-top)/2,abs=.001)
    assert bottom-top==pytest.approx(80.8,abs=.001)
    assert text(doc(h=5,vertical_align='bottom'))['lines'][0]['y']==top
    assert text(doc(h='hug',vertical_align='bottom'))['lines'][0]['y']==top


def test_fill_height_vertical_alignment():
    source={'schema':1,'pages':[{'id':'p','children':[{'id':'frame','type':'frame','w':200,'h':100,
        'layout':{'dir':'column'},'children':[{'id':'t','type':'text','w':'fill','h':'fill','text':'Fill',
            'vertical_align':'bottom','style':{'size':16}}]}]}]}
    resolved=layout_document(source)['pages'][0]['children'][0]['children'][0]
    assert resolved['h']==100 and resolved['text']['lines'][0]['y']>80


@pytest.mark.parametrize('decoration,below',[('underline',True),('line-through',False)])
def test_decorations_use_per_run_font_metrics_and_colors(decoration,below):
    source=doc(text=[{'text':'Large ','style':{'size':24},'color':'#ff0000'},
                     {'text':'small','style':{'size':12},'color':'#0000ff'}],decoration=decoration)
    resolved=layout_document(source);node=resolved['pages'][0]['children'][0]
    baseline=node['text']['lines'][0]['y']
    root=ET.fromstring(paint(resolved,'text').svg)
    rectangles=root.findall('.//{http://www.w3.org/2000/svg}rect')
    assert len(rectangles)==2
    assert all((float(r.attrib['y'])>baseline) is below for r in rectangles)
    assert all(float(r.attrib['height'])>=.5 and float(r.attrib['width'])>0 for r in rectangles)
    assert node['text']['decoration']==decoration


def test_explicit_defaults_preserve_resolved_and_svg_bytes():
    original=doc();defaulted=doc(vertical_align='top',text_case='none',decoration='none')
    assert layout_document(original)==layout_document(defaulted)
    assert paint(layout_document(original),'text').svg==paint(layout_document(defaulted),'text').svg


@pytest.mark.parametrize('props',[{'align':'justify'},{'vertical_align':'center'},
                                  {'text_case':'uppercase'},{'decoration':'underline'}])
def test_html_new_typography_uses_faithful_outlined_svg_fallback(props):
    source=doc(**props)
    data,_,warnings=html_document(source,layout_document(source),'text',{},1<<20)
    assert b'<svg' in data and any('SVG fallback' in w for w in warnings)
    assert not any('browser fonts' in w for w in warnings)


def test_decoration_changes_actual_png_pixels(tmp_path):
    def raster(source):
        p=paint(layout_document(source),'text')
        raw=resvg_py.svg_to_bytes(svg_string=p.svg,width=220,height=100,resources_dir=str(tmp_path),
            skip_system_fonts=True,font_dirs=[])
        return Image.open(io.BytesIO(raw)).convert('RGBA')
    difference=ImageChops.difference(raster(doc()),raster(doc(decoration='underline')))
    assert difference.getchannel('A').getbbox() is not None
