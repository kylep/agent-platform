import base64
import copy
import io
import json

import pytest
from PIL import Image

import app as renderer
from agentplatform.ui_designer import visual_diff
from agentplatform.ui_designer.errors import UiDesignerError
from test_app import client  # noqa: F401


def document(x=0, color='#ff0000', present=True):
    children = [{'id': 'box', 'type': 'frame', 'x': x, 'w': 10, 'h': 10, 'fill': color}] if present else []
    return {'schema': 1, 'pages': [{'id': 'p', 'children': children}]}


def request(before, after, **kwargs):
    return renderer._diff_req({'before_doc':before, 'after_doc':after, 'node':'box', **kwargs})


def test_moved_node_uses_world_union_instead_of_normalizing_crops(tmp_path):
    out = renderer.job_diff(renderer.Config(asset_dir=str(tmp_path)), request(document(), document(20)))
    assert out['box'] == [0, 0, 30, 10]
    assert (out['width'], out['height'], out['changed_pixels'], out['total_pixels']) == (30, 10, 200, 300)
    assert out['node_ids'] == ['box']
    assert out['regions'][0]['node_ids'] == ['box']


def test_union_negative_fractional_origin_is_pixel_aligned(tmp_path):
    out = renderer.job_diff(renderer.Config(asset_dir=str(tmp_path)), request(document(-3.5), document(-1.5), scale=2))
    assert out['box'] == [-3.5, 0, 12, 10]
    assert (out['width'], out['height']) == (24, 20)
    assert out['changed_pixels'] == 160


@pytest.mark.parametrize('added', [True, False])
def test_added_removed_node_has_blank_other_canvas(tmp_path, added):
    empty, full = document(present=False), document()
    out = renderer.job_diff(renderer.Config(asset_dir=str(tmp_path)), request(empty if added else full, full if added else empty))
    assert out['changed_pixels'] == out['total_pixels'] == 100
    assert out['node_ids'] == ['box']


def test_missing_from_both_versions_refused(tmp_path):
    with pytest.raises(UiDesignerError):
        renderer.job_diff(renderer.Config(asset_dir=str(tmp_path)), request(document(present=False), document(present=False)))


def test_premultiplied_comparison_ignores_hidden_rgb_but_counts_alpha():
    clear_red = Image.new('RGBA', (1,1), (255,0,0,0))
    clear_blue = Image.new('RGBA', (1,1), (0,0,255,0))
    assert visual_diff.compare(clear_red, clear_blue, [0,0,1,1], 1, [])['changed_pixels'] == 0
    alpha = Image.new('RGBA', (1,1), (0,0,0,1))
    assert visual_diff.compare(clear_red, alpha, [0,0,1,1], 1, [])['changed_pixels'] == 1
    assert visual_diff.compare(clear_red, alpha, [0,0,1,1], 1, [], tolerance=1)['changed_pixels'] == 0


def test_tolerance_is_exact_premultiplied_channel_delta(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    before, after = document(color='#000000'), document(color='#010000')
    assert renderer.job_diff(cfg, request(before, after))['changed_pixels'] == 100
    assert renderer.job_diff(cfg, request(before, after, tolerance=1))['changed_pixels'] == 0


def test_unchanged_semtransparent_presentation_pngs_are_opaque_same_white_matte():
    image = Image.new('RGBA', (5,5), (255,0,0,128))
    out = visual_diff.compare(image,image,[0,0,5,5],1,[])
    assert out['changed_pixels'] == 0
    base = Image.open(io.BytesIO(out['base_image']))
    overlay = Image.open(io.BytesIO(out['image']))
    assert base.getchannel('A').getextrema() == overlay.getchannel('A').getextrema() == (255,255)
    assert base.getpixel((0,0)) == (255,127,127,255)
    # Both are opaque: fading one over the other never compounds source alpha.
    combined = Image.blend(base, overlay, .5)
    assert combined.getpixel((0,0))[0] == 255


def test_regions_metadata_and_pixel_counts_stay_bounded():
    before = Image.new('RGBA', (2048,2048))
    after = before.copy()
    for y in range(32):
        for x in range(32):
            if (x+y)%2 == 0: after.putpixel((x*64,y*64),(255,0,0,255))
    nodes = [(f'n{i:03d}-'+'x'*40,[0,0,2048,2048]) for i in range(200)]
    out = visual_diff.compare(before,after,[0,0,2048,2048],1,nodes)
    assert out['changed_pixels'] == 512
    assert len(out['regions']) <= 200 and len(out['node_ids']) <= 200 and out['truncated']
    assert len(json.dumps({k:v for k,v in out.items() if k not in ('image','base_image')}, separators=(',',':')).encode()) <= 64*1024


def test_union_and_combined_output_bounds():
    with pytest.raises(UiDesignerError): visual_diff.union_box([[0,0,10000,10]],1)
    with pytest.raises(UiDesignerError): visual_diff.union_box([[0,0,4096,4096]],1)
    im = Image.new('RGBA',(10,10))
    with pytest.raises(UiDesignerError): visual_diff.compare(im,im,[0,0,10,10],1,[],max_bytes=100)


@pytest.mark.parametrize('extra',[{'tolerance':True},{'tolerance':256},{'scale':True},{'max_bytes':0},{'script':'x'}])
def test_diff_arguments_closed_and_bounded(extra):
    with pytest.raises(renderer.BadRequest):
        renderer._diff_req({'before_doc':document(),'after_doc':document(),**extra})


async def test_diff_http_two_png_base64_transport(client):
    response = await client.post('/diff', json={'before_doc':document(),'after_doc':document(20),'node':'box'})
    assert response.status == 200, await response.text()
    out = await response.json()
    for field in ('image','base_image'):
        with Image.open(io.BytesIO(base64.b64decode(out[field]))) as png:
            assert png.size == (30,10)
    assert out['changed_pixels'] == 200


def test_successful_diff_cache_reuses_rasters_and_invalidates_options(tmp_path, monkeypatch):
    renderer._DIFF_CACHE.clear()
    original = renderer._raster
    calls = []
    def raster(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(renderer, '_raster', raster)
    cfg = renderer.Config(asset_dir=str(tmp_path))
    req = request(document(), document(20))
    first = renderer.job_diff(cfg, req)
    renderer.job_diff(cfg, req)
    assert len(calls) == 2
    first['regions'].clear()  # caller mutations cannot corrupt the cache
    assert renderer.job_diff(cfg, req)['regions']
    renderer.job_diff(cfg, {**req, 'tolerance': 1})
    assert len(calls) == 4
    renderer.job_diff(cfg, request(document(), document(25)))
    assert len(calls) == 6
    for offset in range(30,35): renderer.job_diff(cfg, request(document(), document(offset)))
    assert len(renderer._DIFF_CACHE) <= 4
    assert sum(item[1] for item in renderer._DIFF_CACHE.values()) <= 32 << 20


def test_missing_assets_and_failures_are_not_cached(tmp_path, monkeypatch):
    renderer._DIFF_CACHE.clear()
    calls = []
    def missing(*args):
        calls.append(True)
        return {'ok':False,'code':'MISSING-ASSETS','missing':['a'*64]}
    monkeypatch.setattr(renderer, '_job_diff_uncached', missing)
    cfg = renderer.Config(asset_dir=str(tmp_path))
    req = request(document(),document(1))
    renderer.job_diff(cfg,req); renderer.job_diff(cfg,req)
    assert len(calls) == 2 and not renderer._DIFF_CACHE
