import base64
import copy
import hashlib
import io
import re
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

import app as renderer
from test_app import DOC, client  # noqa: F401


def request(format='svg', **kwargs):
    return renderer._export_req({'doc': DOC, 'mode': 'dark', 'node': 'card', 'format': format, **kwargs})


def test_self_contained_svg_embeds_only_trusted_raster_bytes(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    buf = io.BytesIO(); Image.new('RGB', (10, 10), '#abcdef').save(buf, 'PNG')
    raw = buf.getvalue(); sha = hashlib.sha256(raw).hexdigest()
    doc = copy.deepcopy(DOC)
    doc['pages'][0]['children'][0]['children'].append({'id': 'pic', 'type': 'image', 'asset': sha, 'w': 10, 'h': 10})
    req = request(); req['doc'] = doc
    assert renderer.job_export(cfg, req)['code'] == 'MISSING-ASSETS'
    assert renderer.job_asset(cfg, {'sha': sha, 'data': raw})['ok']
    out = renderer.job_export(cfg, req)
    root = ET.fromstring(out['data'])
    hrefs = [v for n in root.iter() for k, v in n.attrib.items() if k.endswith('href')]
    assert hrefs and all(h.startswith(('#', 'data:image/png;base64,')) for h in hrefs)
    assert any(h.startswith('data:image/png;base64,') for h in hrefs)
    assert b'http://' not in out['data'].replace(b'http://www.w3.org/2000/svg', b'')


def test_pdf_raster_media_box_and_white_transparency(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    doc = {'schema': 1, 'pages': [{'id': 'p', 'children': [{'id': 'card', 'type': 'frame', 'w': 120, 'h': 50}]}]}
    req = request('pdf'); req.update(doc=doc, mode=None)
    out = renderer.job_export(cfg, req)
    assert out['data'].startswith(b'%PDF-')
    assert re.search(rb'/MediaBox\s*\[\s*0\s+0\s+90(?:\.0)?\s+37\.5\s*\]', out['data'])
    jpeg = re.search(rb'\xff\xd8.*?\xff\xd9', out['data'], re.S).group()
    with Image.open(io.BytesIO(jpeg)) as raster:
        assert raster.size == (120, 50)
        assert min(raster.getpixel((0, 0))) >= 250
    assert any('white' in warning for warning in out['warnings'])


def test_html_basic_dom_uses_token_variables_and_escapes_literals(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    req = request('html')
    req['doc'] = copy.deepcopy(DOC)
    req['doc']['title'] = '</title><script>alert(1)</script>'
    req['doc']['pages'][0]['children'][0]['children'][0]['text'] = '<script>&"'
    out = renderer.job_export(cfg, req)
    html = out['data'].decode()
    assert html.startswith('<!doctype html>')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '--color-bg:#102030' in html and 'background:var(--color-bg)' in html
    assert 'font-size:var(--type-body-size)' in html
    assert 'data-node="card"' in html and 'data-node="label"' in html
    assert 'Content-Security-Policy' in html
    assert any('browser fonts' in warning for warning in out['warnings'])


def test_complex_html_uses_outlined_svg_fallback(tmp_path):
    req = request('html'); req['doc'] = copy.deepcopy(DOC)
    req['doc']['pages'][0]['children'][0]['shadow'] = {'color': '#000000', 'blur': 5}
    out = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)
    assert b'<svg' in out['data']
    assert any('SVG fallback' in warning for warning in out['warnings'])


@pytest.mark.parametrize('format', ['svg', 'pdf', 'html'])
def test_output_cap_is_enforced_without_returning_partial_content(tmp_path, format):
    from agentplatform.ui_designer.errors import UiDesignerError
    with pytest.raises(UiDesignerError) as exc:
        renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), request(format, max_bytes=16))
    assert exc.value.code == 'UD-IMAGE-TOO-LARGE'


@pytest.mark.parametrize('extra', [{'format': 'js'}, {'scale': True}, {'max_bytes': 0}, {'max_bytes': 9 << 20}, {'script': 'x'}])
def test_export_request_is_closed_and_bounded(extra):
    body = {'doc': DOC, 'format': 'svg', **extra}
    with pytest.raises(renderer.BadRequest): renderer._export_req(body)


async def test_export_http_base64_transport(client):
    response = await client.post('/export', json={'doc': DOC, 'mode': 'dark', 'node': 'card', 'format': 'svg'})
    assert response.status == 200, await response.text()
    out = await response.json()
    assert base64.b64decode(out['data']).startswith(b'<svg')
    assert (out['width'], out['height']) == (120, 50)
    assert out['mime'] == 'image/svg+xml'


def test_html_instance_override_does_not_reintroduce_authored_tokens(tmp_path):
    doc = {'schema': 1, 'tokens': {'color': {'ink': '#112233'},
           'type': {'body': {'family': 'Inter', 'size': 16, 'weight': 400}}},
           'components': {'base': {'type': 'frame', 'w': 100, 'h': 40, 'children': [
               {'id': 'label', 'type': 'text', 'text': 'Label', 'color': '$color.ink', 'style': '$type.body'}]}},
           'pages': [{'id': 'p', 'children': [{'id': 'card', 'type': 'instance', 'of': 'base',
               'overrides': {'label.color': '#ff0000', 'label.style': {'size': 24}}}]}]}
    req = request('html'); req.update(doc=doc, mode=None)
    html = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)['data'].decode()
    assert 'color:#ff0000' in html and 'color:var(--color-ink)' not in html
    assert 'font-size:24px' in html and 'font-size:var(--type-body-size)' not in html


def test_html_mode_color_tokens_and_type_token_usage(tmp_path):
    doc = copy.deepcopy(DOC)
    doc['modes'] = ['dark', 'light']
    doc['tokens']['color']['bg'] = {'dark': '#102030', 'light': '#fefefe'}
    req = request('html'); req.update(doc=doc, mode='light')
    html = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)['data'].decode()
    assert '--color-bg:#fefefe' in html and 'background:var(--color-bg)' in html
    assert 'font-size:var(--type-body-size)' in html


def test_html_node_limit_applies_before_emit(tmp_path):
    doc = copy.deepcopy(DOC)
    doc['pages'][0]['children'][0]['children'] = [
        {'id': f'r{i}', 'type': 'rect', 'w': 1, 'h': 1} for i in range(200)]
    req = request('html'); req['doc'] = doc
    from agentplatform.ui_designer.errors import UiDesignerError
    with pytest.raises(UiDesignerError, match='200 expanded nodes'):
        renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)


def test_pdf_reports_actual_raster_pixels_at_scale(tmp_path):
    out = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), request('pdf', scale=3))
    assert (out['width'], out['height']) == (360, 150)
    assert out['box'][2:] == [120, 50]


def test_html_child_under_rotated_ancestor_uses_svg_fallback(tmp_path):
    doc = copy.deepcopy(DOC)
    doc['pages'][0]['children'][0]['rotate'] = 35
    req = request('html'); req.update(doc=doc, node='label')
    out = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)
    assert b'<svg' in out['data']
    assert any('SVG fallback' in warning for warning in out['warnings'])


@pytest.mark.parametrize('family,fallback', [('Inter', 'sans-serif'), ('JetBrains Mono', 'monospace')])
@pytest.mark.parametrize('token', [False, True])
def test_html_font_family_has_generic_fallback(tmp_path, family, fallback, token):
    doc = copy.deepcopy(DOC)
    style = {'family': family, 'size': 16, 'weight': 400}
    doc['tokens']['type']['body'] = style
    doc['pages'][0]['children'][0]['children'][0]['style'] = '$type.body' if token else style
    req = request('html'); req['doc'] = doc
    output = renderer.job_export(renderer.Config(asset_dir=str(tmp_path)), req)['data'].decode()
    expected = f'font-family:var(--type-body-family),{fallback};' if token else f'font-family:&quot;{family}&quot;,{fallback};'
    assert expected in output
