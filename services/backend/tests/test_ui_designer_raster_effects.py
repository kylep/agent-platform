"""Pixel and resource contracts for continuous paints and true backdrop roots."""
import copy
import io

import pytest
import resvg_py
from PIL import Image

from agentplatform.ui_designer import raster_effects as effects, svg
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.errors import UiDesignerError


def scene(children, **root):
    return {"schema": 1, "pages": [{"id": "p", "children": [{"id": "root", "type": "frame",
        "w": 512, "h": 256, "fill": "#fff", "children": children, **root}]}]}


def rect(id, **props):
    return {"id": id, "type": "rect", **props}


def render(doc, node="root", **kwargs):
    resolved = layout_document(doc)
    painted = svg.paint(resolved, node, **kwargs)
    png = bytes(resvg_py.svg_to_bytes(svg_string=painted.svg, width=round(painted.w), height=round(painted.h),
                                     skip_system_fonts=True, font_dirs=[]))
    return Image.open(io.BytesIO(png)).convert("RGBA"), painted


def fixture():
    return scene([
        rect("black", w=128, h=256, fill="#000"),
        {"id": "clip", "type": "frame", "w": 256, "h": 256, "clip": True, "children": [
            rect("glass", x=96, y=48, w=64, h=160, backdrop_blur=24)]},
        rect("sweep", x=280, y=16, w=200, h=200, fill={"gradient": "angular",
             "stops": [[0, "#ff0000"], [1, "#0000ff"]]})])


def test_conic_continuity_nonzero_origin_and_backdrop_crop_context():
    im, painted = render(fixture())
    assert 80 < im.getpixel((127, 128))[0] < 180
    assert im.getpixel((127, 20))[:3] == (0, 0, 0)
    assert len({im.getpixel((x, 66)) for x in range(382, 392)}) >= 6
    crop, _ = render(fixture(), "glass")
    assert crop.getpixel((31, 80)) == im.getpixel((127, 128))
    assert 'data:image/png;base64,' in painted.svg


def test_conic_cardinals_rotation_transparency_and_seam():
    stops = [[0, "#f00"], [.25, "#0f0"], [.5, "#00f"], [.75, "#ff0"], [1, "#f00"]]
    im, _ = render(scene([rect("c", w=200, h=100, fill={"gradient": "angular", "stops": stops})]))
    for point, expected in [((100, 5), (255, 0, 0)), ((195, 50), (0, 255, 0)),
                            ((100, 95), (0, 0, 255)), ((5, 50), (255, 255, 0))]:
        assert max(abs(a-b) for a,b in zip(im.getpixel(point)[:3], expected)) < 12
    rotated, _ = render(scene([rect("c", x=20, y=20, w=100, h=100, rotate=90,
        fill={"gradient": "angular", "stops": stops})]))
    assert rotated.getpixel((115, 70))[0] > 240  # red start rotates from top to right
    transparent = scene([rect("c", w=100, h=100, fill={"gradient": "angular",
        "stops": [[0, "#ff000000"], [1, "#ff0000ff"]]})], fill="#00000000")
    alpha, _ = render(transparent)
    assert alpha.getpixel((50, 1))[3] < 5 and alpha.getpixel((49, 1))[3] > 245
    assert 55 < alpha.getpixel((98, 50))[3] < 75


def test_masked_rotated_backdrop_and_opacity_are_applied_once():
    children = [rect("bar", w=50, h=100, fill="#000"),
                rect("glass", x=30, y=20, w=40, h=60, backdrop_blur=16)]
    group = {"id": "group", "type": "frame", "w": 100, "h": 100,
             "rotate": 90, "fill": "#fff", "mask": "alpha", "children": [
                 rect("mask", w=100, h=100, fill="#ffffff80"), *children]}
    im, _ = render(scene([group], w=100, h=100))
    assert 130 < im.getpixel((50, 49))[0] < 230
    assert 115 < im.getpixel((50, 10))[0] < 145  # half-alpha mask exactly once
    assert im.getpixel((50, 95))[:3] == (255, 255, 255)
    opacity = copy.deepcopy(group)
    opacity.pop("mask")
    opacity["children"] = children
    opacity["opacity"] = .5
    half, _ = render(scene([opacity], w=100, h=100))
    assert 130 < half.getpixel((50, 49))[0] < 230


def test_backdrop_transparent_edges_do_not_gain_black_matte():
    doc = scene([rect("red", x=0, y=0, w=50, h=100, fill="#f00"),
                 rect("glass", x=30, y=0, w=40, h=100, backdrop_blur=16)],
                fill="#00000000", w=100, h=100)
    im, _ = render(doc)
    red, green, blue, alpha = im.getpixel((53, 50))
    assert red > 245 and green == blue == 0 and 20 < alpha < 125


def test_effect_limits_and_early_node_crop():
    resolved = layout_document(fixture())
    with pytest.raises(UiDesignerError, match="aggregate"):
        effects.paint_effects(resolved, "root", max_work_pixels=100)
    with pytest.raises(UiDesignerError, match="live"):
        effects.paint_effects(resolved, "root", max_live_bytes=100)
    doc = fixture()
    doc["pages"][0]["children"][0]["children"].append(rect("huge", x=10000, w=100000, h=100000,
        fill={"gradient": "angular", "stops": [[0, "#000"], [1, "#fff"]]}))
    im, _ = render(doc, "glass")
    assert im.size == (64, 160)


def test_unaffected_documents_retain_legacy_svg_path():
    doc = scene([rect("r", w=20, h=20, fill="#f00")])
    resolved = layout_document(doc)
    assert not effects.required(resolved, "root")
    painted = svg.paint(resolved, "root")
    assert 'data:image/png' not in painted.svg and '<rect' in painted.svg


def test_request_wide_budget_accumulates_across_paints():
    resolved = layout_document(fixture())
    budget = effects.new_budget()
    budget.work_limit = 500000
    effects.paint_effects(resolved, "root", effect_budget=budget)
    with pytest.raises(UiDesignerError, match="aggregate"):
        effects.paint_effects(resolved, "root", effect_budget=budget)
