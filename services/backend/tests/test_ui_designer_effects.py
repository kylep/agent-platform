"""Figma-equivalent effects (stacked fills, shadows, blurs, blend, extra gradients,
stroke options): validation, and PNG pixel checks at a few points through resvg."""
import io

import pytest
import resvg_py
from PIL import Image

from agentplatform.ui_designer import svg
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.schema import (MAX_BACKDROPS, MAX_BLURS, MAX_PAINTS, MAX_SHADOWS, MAX_STEPPED,
                                              collect_issues, normalize_document, validate_document)

HEX = "#%02x%02x%02x"


def doc_of(children, w=100, h=100, bg="#ffffff"):
    return normalize_document({"schema": 1, "pages": [{"id": "p", "children": [
        {"id": "root", "type": "frame", "w": w, "h": h, "fill": bg, "children": children}]}]})


def render(children, **kw):
    d = doc_of(children, **kw)
    validate_document(d)
    painted = svg.paint(layout_document(d, "dark"), "root")
    png = resvg_py.svg_to_bytes(svg_string=painted.svg, width=round(painted.w), height=round(painted.h),
                                skip_system_fonts=True, font_dirs=[])
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA"), painted.svg


def px(im, x, y):
    return im.getpixel((x, y))[:3]


def near(a, b, tol=6):
    return all(abs(i - j) <= tol for i, j in zip(a, b))


def rect(id_, x, y, w, h, **kw):
    return {"id": id_, "type": "rect", "x": x, "y": y, "w": w, "h": h, "absolute": True, **kw}


def issues_at(node):
    d = doc_of([node])
    return {(i.code if hasattr(i, "code") else i["code"]) for i in collect_issues(d)}


# --- validation ------------------------------------------------------------

def test_old_shapes_still_validate():
    for node in (rect("a", 0, 0, 5, 5, fill="#fff", shadow={"color": "#000", "blur": 4}),
                 rect("b", 0, 0, 5, 5, fill={"gradient": "radial", "stops": [[0, "#fff"], [1, "#000"]]},
                      stroke={"color": "#000", "width": 2, "align": "inside"})):
        assert not issues_at(node)


@pytest.mark.parametrize("prop,val,code", [
    ("blend", "pass-through", "UD-VALUE"),
    ("blur", 65, "UD-BOUNDS"),
    ("backdrop_blur", -1, "UD-BOUNDS"),
    ("fill", [], "UD-BOUNDS"),
    ("fill", ["#fff"] * (MAX_PAINTS + 1), "UD-BOUNDS"),
    ("fill", {"color": "#fff", "gradient": "linear", "stops": [[0, "#fff"], [1, "#000"]]}, "UD-VALUE"),
    ("fill", {"color": "#fff", "opacity": 2}, "UD-BOUNDS"),
    ("fill", {"image": "nope"}, "UD-FORMAT"),
    ("fill", {"gradient": "conic", "stops": [[0, "#fff"], [1, "#000"]]}, "UD-VALUE"),
    ("shadow", [{"color": "#000", "inset": 1}], "UD-TYPE"),
    ("shadow", [{"color": "#000", "spread": 500}], "UD-BOUNDS"),
    ("shadow", [], "UD-BOUNDS"),
    ("stroke", {"color": "#000", "dash": []}, "UD-BOUNDS"),
    ("stroke", {"color": "#000", "dash": [0]}, "UD-BOUNDS"),
    ("stroke", {"color": "#000", "cap": "flat"}, "UD-VALUE"),
    ("stroke", {"color": "#000", "sides": [1, 2]}, "UD-BOUNDS"),
])
def test_bad_effects_are_refused(prop, val, code):
    assert code in issues_at(rect("n", 0, 0, 9, 9, **{prop: val}))


def test_effect_budgets():
    many = [rect(f"s{i}", 0, 0, 5, 5, shadow=[{"color": "#000"}] * 2) for i in range(MAX_SHADOWS // 2 + 1)]
    assert "UD-LIMIT" in {i.code if hasattr(i, "code") else i["code"] for i in collect_issues(doc_of(many))}
    blurs = [rect(f"b{i}", 0, 0, 5, 5, blur=2) for i in range(MAX_BLURS + 1)]
    assert "UD-LIMIT" in {i.code if hasattr(i, "code") else i["code"] for i in collect_issues(doc_of(blurs))}
    bd = [rect(f"d{i}", 0, 0, 5, 5, backdrop_blur=2) for i in range(MAX_BACKDROPS + 1)]
    assert "UD-LIMIT" in {i.code if hasattr(i, "code") else i["code"] for i in collect_issues(doc_of(bd))}


def test_image_fill_must_be_placed_and_not_overridden():
    sha = "a" * 64
    d = doc_of([rect("pic", 0, 0, 20, 20, fill=[{"image": sha}])])
    with pytest.raises(UiDesignerError) as e:
        validate_document(d, known_assets=set())
    assert e.value.code == "UD-ASSET-UNKNOWN"
    validate_document(d, known_assets={sha})
    d["components"] = {"c": {"id": "c", "type": "rect", "w": 5, "h": 5}}
    d["pages"][0]["children"][0]["children"].append(
        {"id": "i", "type": "instance", "of": "c", "overrides": {"fill": {"image": sha}}})
    assert "UD-OVERRIDE" in {i.code if hasattr(i, "code") else i["code"] for i in collect_issues(d)}


def test_assets_used_sees_fill_images():
    sha = "b" * 64
    r = layout_document(doc_of([rect("pic", 0, 0, 20, 20, fill=[{"color": "#fff"}, {"image": sha}])]), "dark")
    assert svg.assets_used(r) == [sha]


# --- fills -----------------------------------------------------------------

def test_stacked_fills_with_opacity():
    im, _ = render([rect("r", 10, 10, 80, 80, fill=["#ff0000", {"color": "#0000ff", "opacity": 0.5}])])
    assert near(px(im, 50, 50), (128, 0, 128), 3)
    assert px(im, 5, 5) == (255, 255, 255)


def test_gradient_paint_opacity():
    im, _ = render([rect("r", 0, 0, 100, 100, fill={"gradient": "linear", "angle": 90, "opacity": 0.5,
                                                    "stops": [[0, "#000000"], [1, "#000000"]]})])
    assert near(px(im, 50, 50), (128, 128, 128), 3)


def test_angular_gradient_quadrants():
    stops = [[0, "#ff0000"], [0.25, "#00ff00"], [0.5, "#0000ff"], [0.75, "#ffff00"], [1, "#ff0000"]]
    im, _ = render([rect("r", 0, 0, 100, 100, fill={"gradient": "angular", "stops": stops})])
    # starts at 12 o'clock going clockwise: just right of top is red, 3 o'clock green, 6 blue, 9 yellow
    assert near(px(im, 52, 10), (255, 0, 0), 40)
    assert near(px(im, 90, 50), (0, 255, 0), 30)
    assert near(px(im, 50, 90), (0, 0, 255), 30)
    assert near(px(im, 10, 50), (255, 255, 0), 30)


def test_diamond_gradient_centre_and_corner():
    im, _ = render([rect("r", 0, 0, 100, 100, fill={"gradient": "diamond",
                                                    "stops": [[0, "#ffffff"], [1, "#000000"]]})])
    assert px(im, 50, 50)[0] > 235
    assert px(im, 2, 2)[0] < 5            # corners are past the last stop
    assert 90 < px(im, 25, 50)[0] < 170   # halfway along an axis
    assert abs(px(im, 25, 50)[0] - px(im, 50, 25)[0]) <= 4


# --- strokes ---------------------------------------------------------------

def test_dash_and_cap():
    im, _ = render([rect("r", 10, 10, 80, 80, stroke={"color": "#000000", "width": 6, "dash": [10, 10],
                                                      "align": "center"})])
    row = [px(im, x, 10)[0] for x in range(12, 52)]
    assert min(row) < 20 and max(row) > 235       # dashes alternate ink and gap
    im2, _ = render([rect("r", 10, 10, 80, 80, stroke={"color": "#000000", "width": 6, "dash": [0, 12],
                                                       "cap": "round", "align": "center"})])
    assert min(px(im2, x, 10)[0] for x in range(12, 52)) < 60  # zero-length dashes + round cap = dots


def test_sides_widths():
    im, _ = render([rect("r", 10, 10, 80, 80, stroke={"color": "#ff0000", "sides": [0, 0, 10, 0]})])
    assert near(px(im, 50, 85), (255, 0, 0), 3)      # bottom edge inked
    assert px(im, 50, 12) == (255, 255, 255)         # top edge not
    assert px(im, 12, 50) == (255, 255, 255)
    assert px(im, 50, 50) == (255, 255, 255)


# --- shadows ---------------------------------------------------------------

def test_legacy_single_shadow_uses_dropshadow_filter():
    _, s = render([rect("r", 30, 30, 30, 30, fill="#0000ff", shadow={"x": 4, "y": 4, "blur": 0, "color": "#ff0000"})])
    assert "feDropShadow" in s


def test_drop_shadow_with_spread_and_offset():
    im, _ = render([rect("r", 30, 30, 30, 30, fill="#0000ff",
                         shadow=[{"x": 10, "y": 10, "blur": 0, "spread": 4, "color": "#ff0000"},
                                 {"x": 0, "y": 0, "blur": 0, "spread": 0, "color": "#00ff00"}])])
    assert near(px(im, 45, 45), (0, 0, 255), 3)              # body on top
    assert near(px(im, 68, 68), (255, 0, 0), 3)              # shadow reaches 30+30+10+4-... beyond body
    assert px(im, 80, 80) == (255, 255, 255)
    assert px(im, 22, 22) == (255, 255, 255)                 # spread offset leaves top-left clear


def test_inner_shadow_only_inside():
    im, _ = render([rect("r", 20, 20, 60, 60, fill="#ffffff",
                         shadow=[{"x": 0, "y": 8, "blur": 0, "color": "#000000", "inset": True},
                                 {"x": 0, "y": 0, "blur": 0, "spread": 0, "color": "#00000000"}])])
    assert near(px(im, 50, 22), (0, 0, 0), 3)       # top band inside the shape is shadowed
    assert px(im, 50, 50) == (255, 255, 255)
    assert px(im, 50, 10) == (255, 255, 255)        # nothing leaks outside


# --- blur, backdrop, blend -------------------------------------------------

def test_layer_blur_softens_edges():
    sharp, _ = render([rect("r", 30, 30, 40, 40, fill="#000000")])
    soft, s = render([rect("r", 30, 30, 40, 40, fill="#000000", blur=12)])
    assert px(sharp, 28, 50)[0] == 255
    assert 10 < px(soft, 28, 50)[0] < 245 and px(soft, 50, 50)[0] < 40


def test_backdrop_blur_blurs_what_is_behind_and_clips_to_shape():
    kids = [rect("bar", 0, 0, 50, 100, fill="#000000"),
            rect("glass", 30, 30, 40, 40, backdrop_blur=16, fill="#ffffff10", radius=0)]
    im, _ = render(kids)
    assert px(im, 10, 50) == (0, 0, 0)               # outside the glass: sharp
    edge = px(im, 49, 50)[0]
    assert 40 < edge < 215                           # the black/white edge under the glass is blurred
    assert px(im, 90, 50) == (255, 255, 255)


def test_backdrop_blur_under_rotated_ancestor_lines_up():
    kids = [{"id": "tilt", "type": "frame", "x": 0, "y": 0, "w": 100, "h": 100, "absolute": True, "rotate": 90,
             "children": [rect("bar", 0, 0, 50, 100, fill="#000000", absolute=True),
                          rect("glass", 30, 30, 40, 40, backdrop_blur=16, fill="#ffffff10")]}]
    im, s = render(kids)
    # rotate 90 about the centre: the bar (left half) lands on the top half; the glass blurs its own edge
    assert px(im, 50, 10)[0] < 20
    assert px(im, 50, 90)[0] > 235
    assert 40 < px(im, 50, 49)[0] < 215


def test_blend_modes():
    kids = [rect("under", 0, 0, 100, 100, fill="#808080"),
            rect("top", 20, 20, 60, 60, fill="#c08040", blend="multiply")]
    im, _ = render(kids)
    assert near(px(im, 50, 50), (96, 64, 32), 3)
    assert near(px(im, 5, 5), (128, 128, 128), 2)
    im2, _ = render([rect("under", 0, 0, 100, 100, fill="#808080"),
                     rect("top", 20, 20, 60, 60, fill="#c08040", blend="screen")])
    assert px(im2, 50, 50)[0] > 128 + 40


def test_effects_are_deterministic():
    kids = [rect("r", 10, 10, 50, 50, fill=[{"gradient": "angular", "stops": [[0, "#f00"], [1, "#00f"]]}],
                 shadow=[{"x": 2, "y": 2, "blur": 4, "color": "#000"}, {"x": 0, "y": 1, "color": "#ffffff88", "inset": True}],
                 blur=1, blend="overlay")]
    assert render(kids)[1] == render(kids)[1]


# --- budgets over the expanded tree, stepped gradients, image paints on paths ---

def _codes(d):
    try:
        validate_document(d)
    except UiDesignerError as e:
        return e.code, str(e)
    return None, ""


def test_effect_budgets_count_instances():
    d = doc_of([{"id": f"i{k}", "type": "instance", "of": "c"} for k in range(MAX_BLURS + 1)])
    d["components"] = {"c": {"id": "c", "type": "rect", "w": 5, "h": 5, "blur": 1}}
    code, _ = _codes(d)
    assert code == "UD-EXPANSION"
    d2 = doc_of([{"id": f"i{k}", "type": "instance", "of": "c"} for k in range(MAX_STEPPED + 1)])
    d2["components"] = {"c": {"id": "c", "type": "rect", "w": 5, "h": 5,
                              "fill": {"gradient": "angular", "stops": [[0, "#fff"], [1, "#000"]]}}}
    assert _codes(d2)[0] == "UD-EXPANSION"


def test_stepped_gradient_budget_and_cache_in_painter():
    from agentplatform.ui_designer.schema import MAX_STEPPED
    g = {"gradient": "diamond", "stops": [[0, "#fff"], [1, "#000"]]}
    # same gradient on the same box: one pattern, however many uses
    d = doc_of([rect(f"r{i}", 0, 0, 10, 10, fill=g) for i in range(10)])
    s = svg.paint(layout_document(d, "dark"), "root").svg
    assert s.count("<pattern") == 1
    d = doc_of([rect(f"r{i}", 0, 0, 10, 10, fill=g) for i in range(MAX_STEPPED + 1)])
    with pytest.raises(UiDesignerError):
        svg.paint(layout_document(d, "dark"), "root")


def test_image_fill_on_path_is_rejected():
    n = {"id": "pa", "type": "path", "d": "M0 0L5 5", "fill": {"image": "c" * 64}}
    assert "UD-VALUE" in issues_at(n)
