"""Constrained layout, instances, modes, warnings and the benchmark gate (design 46 T2)."""
import copy
import json
import os
import time
from pathlib import Path

import pytest

from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import (contrast_ratio, document_warnings, iter_nodes,
                                              layout_document)
from tests.test_ui_designer_schema import DOC
from tests.ui_designer_fixtures import big_doc

GOLDEN = Path(__file__).parent / "fixtures" / "ui_designer"
BODY = {"family": "Inter", "size": 14, "weight": 400, "line": 1.4}
LINE_H = 19.6


def make(children, **extra):
    d = {"schema": 1, "title": "t", "tokens": {}, "components": {}, "pages": [
        {"id": "p", "title": "P", "children": children}]}
    d.update(extra)
    S.validate_document(d)
    return d


def boxes(doc, mode=None):
    r = layout_document(doc, mode)
    return {n["id"]: n for n in iter_nodes(r)}


def xywh(n):
    return (n["x"], n["y"], n["w"], n["h"])


def rect(id_, w, h, **kw):
    return {"id": id_, "type": "rect", "w": w, "h": h, "fill": "#ff0000", **kw}


def frame(id_, children, layout=None, **kw):
    n = {"id": id_, "type": "frame", "children": children, **kw}
    if layout is not None:
        n["layout"] = layout
    return n


def text(id_, s, **kw):
    return {"id": id_, "type": "text", "text": s, "style": BODY, "color": "#000000", **kw}


# --- flow layout -----------------------------------------------------------

def test_row_gap_pad_start():
    b = boxes(make([frame("f", [rect("a", 50, 30), rect("b", 50, 30), rect("c", 50, 30)],
                           {"dir": "row", "gap": 10, "pad": 20}, w=300, h=100)]))
    assert [xywh(b[i]) for i in "abc"] == [(20, 20, 50, 30), (80, 20, 50, 30), (140, 20, 50, 30)]


def test_column_pad_tuple_and_hug_size():
    b = boxes(make([frame("f", [rect("a", 50, 30), rect("b", 70, 10)],
                          {"dir": "column", "gap": 5, "pad": [4, 8, 6, 2]})]))
    assert xywh(b["f"]) == (0, 0, 2 + 70 + 8, 4 + 30 + 5 + 10 + 6)
    assert xywh(b["a"]) == (2, 4, 50, 30) and xywh(b["b"]) == (2, 39, 70, 10)


@pytest.mark.parametrize("justify,xs", [("center", [95, 155]), ("end", [190, 250]),
                                        ("space-between", [0, 250]), ("start", [0, 60])])
def test_justify(justify, xs):
    b = boxes(make([frame("f", [rect("a", 50, 10), rect("b", 50, 10)],
                          {"dir": "row", "gap": 10, "justify": justify}, w=300, h=10)]))
    assert [b["a"]["x"], b["b"]["x"]] == xs


@pytest.mark.parametrize("align,ys", [("start", [0, 0]), ("center", [20, 35]), ("end", [40, 70]),
                                      ("stretch", [0, 0])])
def test_cross_align(align, ys):
    b = boxes(make([frame("f", [rect("a", 20, 60), rect("b", 20, 30)],
                          {"dir": "row", "align": align}, w=100, h=100)]))
    assert [b["a"]["y"], b["b"]["y"]] == ys if align != "stretch" else True
    if align == "stretch":   # numeric sizes are never stretched
        assert (b["a"]["h"], b["b"]["h"]) == (60, 30)


def test_stretch_sizes_unspecified_cross_and_fill():
    b = boxes(make([frame("f", [{"id": "a", "type": "rect", "w": 20, "fill": "#000000"},
                                {"id": "b", "type": "rect", "w": 20, "h": "fill", "fill": "#000000"}],
                          {"dir": "row", "align": "stretch", "pad": 5}, w=100, h=100)]))
    assert (b["a"]["h"], b["b"]["h"]) == (90, 90)


def test_fill_shares_remaining_main_axis():
    b = boxes(make([frame("f", [rect("a", 100, 10), rect("b", "fill", 10), rect("c", "fill", 10)],
                          {"dir": "row", "gap": 10}, w=320, h=10)]))
    assert [(b[i]["x"], b[i]["w"]) for i in "abc"] == [(0, 100), (110, 100), (220, 100)]


def test_hug_frame_wraps_content_and_fill_in_hug_resolves_hug():
    doc = make([frame("f", [rect("a", 40, 10), rect("b", "fill", 10)], {"dir": "row", "gap": 4},
                      w="hug")])
    b = boxes(doc)
    assert (b["f"]["w"], b["b"]["w"]) == (44, 0)   # a leaf fill with nothing to fill under hug is 0
    assert "UD-WARN-FILL-IN-HUG" in [w["code"] for w in document_warnings(doc)]


def test_fill_cross_in_hug_parent_two_pass_text_wraps_at_sibling_width():
    doc = make([frame("f", [rect("bar", 120, 8),
                            text("t", "alpha beta gamma delta epsilon zeta eta theta", w="fill")],
                      {"dir": "column"}, w="hug")])
    b = boxes(doc)
    assert b["f"]["w"] == 120 and b["t"]["w"] == 120
    assert len(layout_document(doc)["pages"][0]["children"][0]["children"][1]["text"]["lines"]) >= 3
    assert b["t"]["h"] > LINE_H * 2


def test_wrap_breaks_lines_at_main_size():
    b = boxes(make([frame("f", [rect(f"r{i}", 40, 20) for i in range(5)],
                          {"dir": "row", "gap": 10, "wrap": True}, w=100)]))
    assert [(b[f"r{i}"]["x"], b[f"r{i}"]["y"]) for i in range(5)] == [
        (0, 0), (50, 0), (0, 30), (50, 30), (0, 60)]
    assert b["f"]["h"] == 80   # hug height: three lines of 20 and two gaps


def test_invisible_children_are_not_laid_out():
    b = boxes(make([frame("f", [rect("a", 50, 10), rect("gone", 50, 10, visible=False),
                                rect("b", 50, 10)], {"dir": "row", "gap": 10})]))
    assert b["b"]["x"] == 60 and b["gone"]["w"] == 0 and b["gone"]["visible"] is False


def test_absolute_child_is_out_of_flow_and_does_not_hug():
    b = boxes(make([frame("f", [rect("a", 50, 30), rect("o", 200, 200, absolute=True, x=7, y=9)],
                          {"dir": "row", "pad": 10})]))
    assert xywh(b["f"]) == (0, 0, 70, 50)
    assert xywh(b["o"]) == (7, 9, 200, 200)


def test_free_frame_places_at_xy_and_hugs_bbox():
    b = boxes(make([frame("f", [rect("a", 10, 10, x=5, y=7), rect("b", 30, 20, x=40, y=50)])], ))
    assert xywh(b["f"]) == (0, 0, 70, 70)
    assert xywh(b["b"]) == (40, 50, 30, 20)


def test_free_frame_fill_child_fills_the_box():
    b = boxes(make([frame("f", [rect("a", "fill", "fill")], w=80, h=60)]))
    assert xywh(b["a"]) == (0, 0, 80, 60)


def test_nested_positions_are_document_coordinates():
    b = boxes(make([frame("o", [frame("i", [rect("r", 5, 5)], {"dir": "row", "pad": 3})],
                          {"dir": "row", "pad": 10}, x=100, y=200)]))
    assert xywh(b["r"]) == (113, 213, 5, 5)


def test_mask_child_is_out_of_flow():
    b = boxes(make([frame("f", [rect("m", 30, 30), rect("a", 20, 20)], {"dir": "row", "pad": 4},
                          mask="alpha")]))
    assert xywh(b["m"]) == (0, 0, 30, 30) and b["a"]["x"] == 4


# --- text ------------------------------------------------------------------

def test_text_is_measured_at_the_width_it_is_given():
    s = "The quick brown fox jumps over the lazy dog"
    wide = boxes(make([frame("f", [text("t", s, w="fill")], {"dir": "column"}, w=400)]))["t"]
    narrow = boxes(make([frame("f", [text("t", s, w="fill")], {"dir": "column"}, w=100)]))["t"]
    assert wide["h"] == pytest.approx(LINE_H) and narrow["h"] > 3 * LINE_H
    assert narrow["w"] == 100


def test_hug_text_in_row_is_unbounded_and_one_line():
    b = boxes(make([frame("f", [text("t", "a fairly long label that stays on one line")],
                          {"dir": "row"}, w=60)]))
    assert b["t"]["h"] == pytest.approx(LINE_H) and b["t"]["w"] > 60


def test_text_max_lines_ellipsis():
    r = layout_document(make([frame("f", [text("t", "one two three four five six seven eight nine ten",
                                                w=90, max_lines=2)])]))
    t = next(n for n in iter_nodes(r) if n["id"] == "t")
    assert t["text"]["truncated"] and len(t["text"]["lines"]) == 2
    assert t["text"]["lines"][1]["text"].endswith("…")
    assert t["text"]["lines"][1]["w"] <= 90 + 1e-6


def test_explicit_newlines_and_align():
    r = layout_document(make([text("t", "ab\nabcdef", w=100, align="right")]))
    t = r["pages"][0]["children"][0]
    l0, l1 = t["text"]["lines"]
    assert t["h"] == pytest.approx(2 * LINE_H)
    assert l0["x"] == pytest.approx(100 - l0["w"]) and l1["x"] == pytest.approx(100 - l1["w"])
    assert l1["y"] > l0["y"]


def test_mixed_runs_have_own_style_and_color():
    r = layout_document(make([text("t", [{"text": "plain "}, {"text": "BIG", "style": {"size": 28},
                                                              "color": "#ff0000"}])]))
    runs = r["pages"][0]["children"][0]["text"]["lines"][0]["runs"]
    assert [x["size"] for x in runs] == [14, 28] and runs[1]["color"] == "#ff0000"
    assert runs[0]["color"] == "#000000"


def test_glyph_table_covers_every_glyph_drawn():
    r = layout_document(make([text("t", "Hello, world")]))
    drawn = {(run["face"], g[0]) for n in iter_nodes(r) if "text" in n
             for ln in n["text"]["lines"] for run in ln["runs"] for g in run["glyphs"]}
    assert drawn
    for face, gid in drawn:
        tab = r["glyphs"][face]
        assert tab["upem"] > 0
    assert any(p.startswith("M") for p in r["glyphs"]["Inter:400"]["paths"].values())


def test_text_overflow_warning():
    fits = make([text("t", "short", w=100, h=30)])
    over = make([text("t", "this text is far too long for a box that small", w=100, h=20)])
    wide = make([text("t", "Supercalifragilisticexpialidocious", w=40, h=200)])
    assert "UD-WARN-TEXT-OVERFLOW" not in [w["code"] for w in document_warnings(fits)]
    ws = [w for w in document_warnings(over) if w["code"] == "UD-WARN-TEXT-OVERFLOW"]
    assert ws and ws[0]["id"] == "t" and ws[0]["path"] == "$.pages[0].children[0]"
    assert "UD-WARN-TEXT-OVERFLOW" in [w["code"] for w in document_warnings(over)]
    # an unbreakable word is split between glyphs, which is not an overflow of the box
    assert "UD-WARN-TEXT-OVERFLOW" not in [w["code"] for w in document_warnings(wide)]


# --- contrast --------------------------------------------------------------

def test_contrast_ratio_extremes():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21)
    assert contrast_ratio((10, 20, 30), (10, 20, 30)) == pytest.approx(1)


def test_contrast_warning_uses_nearest_filled_ancestor():
    doc = make([frame("outer", [frame("inner", [text("t", "hello", color="#777777")], fill="#808080")],
                      fill="#000000")])
    w = [x for x in document_warnings(doc) if x["code"] == "UD-WARN-CONTRAST"]
    assert len(w) == 1 and w[0]["id"] == "t"   # grey on grey, not grey on the black outer frame
    ok = make([frame("outer", [text("t", "hello", color="#ffffff")], fill="#000000")])
    assert not [x for x in document_warnings(ok) if x["code"] == "UD-WARN-CONTRAST"]
    none = make([text("t", "hello", color="#ffffff")])   # no filled ancestor: nothing to compare with
    assert not [x for x in document_warnings(none) if x["code"] == "UD-WARN-CONTRAST"]


def test_contrast_is_checked_per_mode():
    doc = make([frame("f", [text("t", "hello", color="$color.fg")], fill="$color.bg")],
               modes=["dark", "light"],
               tokens={"color": {"bg": {"dark": "#000000", "light": "#ffffff"}, "fg": "#e6edf3"}})
    w = [x for x in document_warnings(doc) if x["code"] == "UD-WARN-CONTRAST"]
    assert [x["mode"] for x in w] == ["light"]


def test_alpha_fill_composites_over_ancestor():
    doc = make([frame("o", [frame("i", [text("t", "hello", color="#ffffff")], fill="#00000000")],
                      fill="#ffffff")])
    assert [x["id"] for x in document_warnings(doc) if x["code"] == "UD-WARN-CONTRAST"] == ["t"]


# --- rotation --------------------------------------------------------------

def test_rotation_reports_bounds_but_does_not_feed_layout():
    b = boxes(make([frame("f", [rect("a", 100, 50, rotate=90), rect("b", 20, 20)],
                          {"dir": "row", "gap": 10})]))
    assert xywh(b["a"]) == (0, 0, 100, 50) and b["b"]["x"] == 110   # siblings unaffected
    assert b["a"]["bounds"] == pytest.approx([25, -25, 50, 100], abs=1e-6)
    assert "bounds" not in b["b"] and b["f"]["w"] == 130


def test_rotation_45_bounds_and_descendants_inherit_the_transform():
    b = boxes(make([frame("f", [rect("r", 10, 10, x=0, y=0)], w=100, h=100, rotate=45)]))
    assert b["f"]["bounds"][2] == pytest.approx(141.421, abs=1e-3)
    assert "bounds" in b["r"] and b["r"]["bounds"][2] == pytest.approx(14.142, abs=1e-3)


# --- instances and modes ---------------------------------------------------

def test_instances_expand_with_prefixed_ids_overrides_and_own_props():
    b = boxes(DOC, "dark")
    assert {"red-score", "red-score/label", "blue-score", "blue-score/label"} <= set(b)
    assert b["red-score"]["of"] == "score-pill" and "of" not in b["hud"]
    red = layout_document(DOC, "dark")
    lab = next(n for n in iter_nodes(red) if n["id"] == "red-score/label")
    assert lab["text"]["lines"][0]["text"] == "7"
    blue = next(n for n in iter_nodes(red) if n["id"] == "blue-score")
    assert blue["fill"] == "#ff5a5a"          # root override
    assert b["red-score"]["h"] == pytest.approx(8 + 20 * 1.2)  # hud 20px, line 1.2 default + 4+4 pad


def test_modes_resolve_tokens():
    d, l = layout_document(DOC, "dark"), layout_document(DOC, "light")
    fd = next(n for n in iter_nodes(d) if n["id"] == "hud")["fill"]
    fl = next(n for n in iter_nodes(l) if n["id"] == "hud")["fill"]
    assert (fd, fl) == ("#0b0f14", "#ffffff")
    assert d["mode"] == "dark" and layout_document(DOC)["mode"] == "dark"   # default: first mode
    assert not any(isinstance(v, str) and v.startswith("$") for n in iter_nodes(d)
                   for k, v in n.items() if k in ("fill", "radius"))


def test_nested_instances_and_instance_size_override():
    doc = make([{"id": "i", "type": "instance", "of": "outer", "w": 200}],
               components={
                   "inner": frame("inner", [rect("dot", 10, 10)], {"dir": "row", "pad": 2}, w=50),
                   "outer": frame("outer", [{"id": "n", "type": "instance", "of": "inner"},
                                            {"id": "n2", "type": "instance", "of": "inner",
                                             "overrides": {"dot.fill": "#00ff00"}}],
                                  {"dir": "row", "gap": 6, "pad": 1})})
    b = boxes(doc)
    assert {"i", "i/n", "i/n/dot", "i/n2", "i/n2/dot"} <= set(b)
    assert b["i"]["w"] == 200 and (b["i/n"]["x"], b["i/n2"]["x"]) == (1, 57)
    assert b["i/n2/dot"]["fill"] == "#00ff00" and b["i/n/dot"]["fill"] == "#ff0000"
    assert b["i/n/dot"]["x"] == 3


def test_self_containing_component_is_an_expansion_error():
    doc = {"schema": 1, "title": "t", "tokens": {}, "components": {
        "c": {"type": "frame", "children": [{"id": "again", "type": "instance", "of": "c"}]}},
        "pages": [{"id": "p", "title": "P", "children": [{"id": "i", "type": "instance", "of": "c"}]}]}
    with pytest.raises(UiDesignerError) as e:
        layout_document(doc)
    assert e.value.code == "UD-EXPANSION"


def test_component_warning_paths_point_into_the_component():
    doc = make([{"id": "i", "type": "instance", "of": "c", "overrides": {"t.text": "x " * 40}}],
               components={"c": frame("c", [text("t", "hi", w=60, h=20)], {"dir": "row"})})
    w = [x for x in document_warnings(doc) if x["code"] == "UD-WARN-TEXT-OVERFLOW"]
    assert w and w[0]["id"] == "i/t" and w[0]["path"] == '$.components.c.children[0]'


# --- resolved tree ---------------------------------------------------------

def test_resolved_tree_is_json_and_deterministic():
    a = layout_document(DOC, "dark")
    json.dumps(a)
    assert a == layout_document(copy.deepcopy(DOC), "dark")
    assert a["stats"]["nodes"] == sum(1 for _ in iter_nodes(a)) == S.check_expansion(DOC)


def _golden(name: str, resolved: dict) -> None:
    resolved = {k: v for k, v in resolved.items() if k != "glyphs"}
    path = GOLDEN / f"{name}.json"
    text_ = json.dumps(resolved, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GOLDEN") or not path.exists():
        path.write_text(text_)
    assert json.loads(path.read_text()) == json.loads(text_), \
        f"{path.name} changed; rerun with UPDATE_GOLDEN=1 if intended"


GALLERY = make([
    frame("card", [
        frame("head", [{"id": "dot", "type": "ellipse", "w": 12, "h": 12, "fill": "$color.accent"},
                       text("title", "Round 3 standings", w="fill", color="$color.fg"),
                       {"id": "pill", "type": "instance", "of": "chip", "overrides": {"label.text": "LIVE"}}],
              {"dir": "row", "gap": 8, "align": "center"}, w="fill"),
        text("body", "Red leads by two points after a late push through the centre lane.", w="fill",
             color="$color.fg", max_lines=2),
        frame("grid", [rect(f"c{i}", 48, 32, fill="$color.accent") for i in range(7)],
              {"dir": "row", "gap": 6, "wrap": True}, w="fill"),
        rect("badge", 40, 40, absolute=True, x=250, y=-8, rotate=15, radius=[4, 4, 4, 4]),
        text("foot", [{"text": "Updated "}, {"text": "now", "style": {"weight": 700}}], w="fill",
             align="center", color="$color.fg"),
    ], {"dir": "column", "gap": 10, "pad": 16, "align": "stretch"}, w=280, x=20, y=20,
        fill="$color.panel", radius=12)],
    modes=["dark", "light"],
    tokens={"color": {"fg": {"dark": "#e6edf3", "light": "#111111"}, "accent": "#5ad1ff",
                      "panel": {"dark": "#161b22", "light": "#f0f2f5"}}},
    components={"chip": frame("chip", [text("label", "NEW", color="#000000")],
                              {"dir": "row", "pad": [2, 8]}, fill="#ffd24d", radius=999)})


@pytest.mark.parametrize("name,doc,mode", [("spectator-dark", DOC, "dark"), ("spectator-light", DOC, "light"),
                                           ("gallery-dark", GALLERY, "dark"), ("gallery-light", GALLERY, "light")])
def test_golden_resolved_trees(name, doc, mode):
    _golden(name, layout_document(doc, mode))


def test_gallery_golden_facts_not_just_a_snapshot():
    b = boxes(GALLERY, "dark")
    assert xywh(b["card"]) == (20, 20, 280, b["card"]["h"])
    assert b["title"]["w"] == pytest.approx(280 - 32 - 12 - 8 - b["pill"]["w"] - 8)   # fill after dot, chip, gaps
    assert b["body"]["h"] == pytest.approx(2 * LINE_H)                                 # max_lines 2
    assert b["grid"]["h"] == 32 * 2 + 6                                                # 7 chips of 48+6 over 248px: 4 + 3
    assert xywh(b["badge"]) == (270, 12, 40, 40) and "bounds" in b["badge"]
    assert b["card"]["h"] == pytest.approx(32 + b["head"]["h"] + b["body"]["h"] + b["grid"]["h"]
                                           + b["foot"]["h"] + 3 * 10)   # the absolute badge adds nothing


# --- benchmark gate ----------------------------------------------------------

@pytest.mark.performance
def test_benchmark_20k_expanded_nodes_under_250ms():
    doc = big_doc()
    S.validate_document(doc)
    assert S.check_expansion(doc) >= 19_000
    layout_document(doc, "dark")       # warm the process caches a real renderer keeps (fonts, shaping)
    best = min(_timed(doc) for _ in range(5))
    print(f"\nbenchmark: {best * 1000:.0f} ms for 20k expanded nodes")
    assert best < 0.250, f"layout took {best * 1000:.0f} ms (gate: 250 ms)"


def _timed(doc) -> float:
    t = time.perf_counter()
    r = layout_document(doc, "dark")
    dt = time.perf_counter() - t
    assert r["stats"]["nodes"] >= 19_000
    return dt
