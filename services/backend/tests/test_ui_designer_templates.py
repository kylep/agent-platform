import io

import pytest
from PIL import Image
import resvg_py

from agentplatform.ui_designer.templates import list_templates,get_template
from agentplatform.ui_designer.schema import validate_document,image_assets,index_nodes
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.lint import findings
from agentplatform.ui_designer.svg import paint
from agentplatform.ui_designer.errors import UiDesignerError


IDS=['desk-buddy','phone-dashboard','desktop-workspace','game-spectator-hud']


def test_catalog_is_bounded_and_provider_returns_independent_copies():
    catalog=list_templates()
    assert [m['id'] for m in catalog]==IDS
    assert all(set(m)=={'id','revision','title','description','width','height','tags','preview_node'} for m in catalog)
    catalog[0]['title']='mutated'
    assert list_templates()[0]['title']=='Desk Buddy'
    doc=get_template('desk-buddy'); doc['pages'].clear()
    assert get_template('desk-buddy',1)['pages']


@pytest.mark.parametrize('id,revision',[('missing',None),('desk-buddy',0),('desk-buddy',2),('desk-buddy',True),('desk-buddy',1.0),(None,None)])
def test_unknown_id_or_retained_revision_is_structured_not_found(id,revision):
    with pytest.raises(UiDesignerError) as exc: get_template(id,revision)
    assert exc.value.code=='UD-NOT-FOUND'


@pytest.mark.parametrize('meta',list_templates(),ids=IDS)
def test_scene_valid_assets_free_and_no_overflow_or_low_contrast_in_both_modes(meta):
    doc=get_template(meta['id'])
    validate_document(doc)
    assert image_assets(doc)==[]
    assert set(doc['modes'])=={'light','dark'}
    canvas=index_nodes(doc)['canvas'].node
    assert (canvas['w'],canvas['h'])==(meta['width'],meta['height'])
    for mode in doc['modes']:
        tree=layout_document(doc,mode)
        assert 20<=tree['stats']['nodes']<=200
        warnings=[f for f in findings(doc,mode)['findings'] if f['severity']=='warning']
        assert warnings==[],warnings


@pytest.mark.parametrize('meta',list_templates(),ids=IDS)
def test_actual_painter_and_rasterizer_produce_full_size_preview(meta,tmp_path):
    doc=get_template(meta['id'])
    tree=layout_document(doc,doc['modes'][0])
    svg=paint(tree,'canvas').svg
    assert '<image' not in svg and 'http://' not in svg.replace('http://www.w3.org/2000/svg','')
    png=resvg_py.svg_to_bytes(svg_string=svg,width=meta['width'],height=meta['height'],resources_dir=str(tmp_path),
                              skip_system_fonts=True,font_dirs=[])
    with Image.open(io.BytesIO(png)) as image:
        assert image.size==(meta['width'],meta['height'])
        assert image.getbbox() is not None
