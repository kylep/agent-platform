"""Bundled fonts, shaping, line breaking and the font-parity check (design 46 T2)."""
import re
from pathlib import Path

import pytest
import resvg_py

from agentplatform.ui_designer import fonts, schema as S
from agentplatform.ui_designer.layout import iter_nodes, layout_document
from agentplatform.ui_designer.text import layout_text, make_style


def style(family="Inter", size=16, weight=400, **kw):
    return make_style({"family": family, "size": size, "weight": weight, **kw})


def test_allowlist_matches_schema_and_files_exist_with_licences():
    assert set(fonts._FILES) == set(S.FONT_FAMILIES)
    for fam in S.FONT_FAMILIES:
        for w in fonts.WEIGHTS:
            assert fonts.get_face(fam, w).path.is_file()
    names = {p.name for p in fonts.FONT_DIR.iterdir()}
    assert {"OFL-Inter.txt", "OFL-JetBrainsMono.txt", "OFL-NotoSans.txt"} <= names
    for lic in fonts.FONT_DIR.glob("OFL-*.txt"):
        assert "SIL OPEN FONT LICENSE" in lic.read_text().upper()


def test_unknown_family_is_refused():
    with pytest.raises(KeyError):
        fonts.get_face("Comic Sans", 400)


@pytest.mark.parametrize("asked,got", [(1, 400), (400, 400), (450, 400), (500, 500), (550, 500), (600, 600),
                                       (650, 600), (700, 700), (900, 700)])
def test_weight_snaps_to_nearest_bundled(asked, got):
    assert fonts.snap_weight(asked) == got
    assert fonts.get_face("Inter", asked).key == f"Inter:{got}"


def test_shaping_applies_kerning_and_monospace_is_monospace():
    st = style()
    ink = lambda s: layout_text([(s, st)]).w
    assert ink("AV") < ink("A") + ink("V") - 0.2          # kerned pair
    mono = style("JetBrains Mono", 20)
    widths = {layout_text([(s, mono)]).w for s in ("iiii", "mmmm", "WiWi")}
    assert len({round(w, 3) for w in widths}) == 1


def test_bold_is_wider_than_regular_and_glyph_outlines_are_y_down():
    assert layout_text([("Hello", style(weight=700))]).w > layout_text([("Hello", style())]).w
    face = fonts.get_face("Inter", 400)
    gid = face.hb_font.get_nominal_glyph(ord("H"))
    d, b = face.glyph_outline(gid)
    assert d.startswith("M") and b[1] < 0 < b[3] + 1 and b[3] <= 1   # cap height above the baseline: y < 0
    assert face.glyph_outline(face.hb_font.get_nominal_glyph(ord(" ")))[0] == ""


def test_line_breaking_greedy_and_spaces_hang():
    st = style(size=14)
    full = layout_text([("alpha beta gamma", st)]).w
    t = layout_text([("alpha beta gamma", st)], full - 1)
    assert [l.text for l in t.lines] == ["alpha beta", "gamma"]
    assert all(l.w <= full - 1 for l in t.lines)
    assert layout_text([("alpha beta gamma", st)], full).lines[0].text == "alpha beta gamma"


def test_overlong_word_breaks_between_glyphs_and_everything_is_kept():
    st = style(size=14)
    word = "Supercalifragilisticexpialidocious"
    t = layout_text([(word, st)], 60)
    assert t.hard_broken and "".join(l.text for l in t.lines) == word
    assert all(l.w <= 60 + 1e-6 for l in t.lines)


def test_max_lines_ellipsis_fits_the_width():
    st = style(size=14)
    t = layout_text([("one two three four five six seven eight nine ten", st)], 100, 2)
    assert t.truncated and len(t.lines) == 2 and t.lines[1].text.endswith("…")
    assert t.lines[1].w <= 100 + 1e-6
    assert not layout_text([("one two", st)], 200, 2).truncated


def test_line_height_scales_with_size_and_line():
    t = layout_text([("a\nb\nc", style(size=10, line=2))])
    assert t.h == pytest.approx(60) and [round(l.top) for l in t.lines] == [0, 20, 40]


def test_emoji_free_unicode_does_not_crash_and_missing_glyph_is_notdef():
    t = layout_text([("naïve café Ωmega Привет", style())], 80)
    assert t.w > 0 and len(t.lines) >= 2
    layout_text([("你好", style())])    # CJK has no glyph: .notdef, never an exception


def test_run_styles_split_into_frags_with_own_faces():
    plain, bold = style(), style(weight=700)
    t = layout_text([("plain ", plain), ("bold", bold)])
    assert [(f.run, f.face) for f in t.lines[0].frags] == [(0, "Inter:400"), (1, "Inter:700")]


# --- font parity: the glyph paths we emit, rasterized by resvg, occupy the
# --- ink box the measurement says they do (tolerance 1 px) ----------------

SCALE = 4


def _svg_for(resolved: dict, width: int, height: int) -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">']
    for n in iter_nodes(resolved):
        for ln in n.get("text", {}).get("lines", []):
            for run in ln["runs"]:
                tab = resolved["glyphs"][run["face"]]
                k = run["size"] / tab["upem"]
                for g in run["glyphs"]:
                    d = tab["paths"].get(str(g[0]))
                    if d:
                        parts.append(f'<path transform="translate({ln["x"] + g[1]:.3f} {ln["y"] + (g[2] if len(g) > 2 else 0):.3f}) '
                                     f'scale({k:.6f})" d="{d}" fill="#000"/>')
    return "".join(parts) + "</svg>"


def _ink_box(png: bytes):
    from io import BytesIO

    from PIL import Image
    im = Image.open(BytesIO(png)).convert("RGBA")
    bbox = im.getchannel("A").point(lambda a: 255 if a > 127 else 0).getbbox()
    return None if bbox is None else tuple(v / SCALE for v in bbox)


@pytest.mark.parametrize("family,weight", [("Inter", 400), ("Inter", 700), ("JetBrains Mono", 600),
                                           ("Noto Sans", 500)])
def test_font_parity_resvg_ink_matches_measured_bounds(family, weight):
    s = "Hamburgefonstiv 0123 AVAW"
    st = {"family": family, "size": 40, "weight": weight}
    doc = {"schema": 1, "title": "t", "tokens": {}, "components": {}, "pages": [{"id": "p", "title": "P", "children": [
        {"id": "t", "type": "text", "text": s, "style": st, "x": 10, "y": 10}]}]}
    r = layout_document(doc)
    node = r["pages"][0]["children"][0]
    W, H = int(node["w"]) + 40, int(node["h"]) + 40
    png = resvg_py.svg_to_bytes(svg_string=_svg_for(r, W, H), width=W * SCALE, height=H * SCALE,
                                skip_system_fonts=True)
    left, top, right, bottom = _ink_box(png)
    # expected ink from the outlines' own bounds and the measured positions
    face = fonts.get_face(family, weight)
    ln = node["text"]["lines"][0]
    k = 40 / face.upem
    xs0, xs1, ys0, ys1 = [], [], [], []
    for g in ln["runs"][0]["glyphs"]:
        b = face.glyph_outline(g[0])[1]
        if b:
            xs0.append(ln["x"] + g[1] + b[0] * k)
            xs1.append(ln["x"] + g[1] + b[2] * k)
            ys0.append(ln["y"] + b[1] * k)
            ys1.append(ln["y"] + b[3] * k)
    assert left == pytest.approx(min(xs0), abs=1) and right == pytest.approx(max(xs1), abs=1)
    assert top == pytest.approx(min(ys0), abs=1) and bottom == pytest.approx(max(ys1), abs=1)
    # and the ink sits inside the measured advance width (a line box may be
    # shorter than a font's content area, so vertical containment is not implied)
    assert node["x"] - 1 <= left and right <= node["x"] + node["w"] + 1
    assert ln["w"] == pytest.approx(node["w"])


# --- regression: line breaking and caches stay linear / bounded ----------------

def test_one_very_long_unbroken_word_breaks_in_linear_time():
    import time
    st = style(size=14)
    word = "x" * 4000
    t0 = time.perf_counter()
    t = layout_text([(word, st)], 40)
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"{dt:.2f}s"
    assert t.hard_broken and "".join(l.text for l in t.lines) == word
    assert sum(len(f.glyphs) for l in t.lines for f in l.frags) == 4000
    assert all(l.w <= 40 + 1e-6 for l in t.lines) and len(t.lines) > 100


def test_long_text_in_a_narrow_box_is_fast_and_ellipsis_of_a_long_word_is_fast():
    import time
    st = style(size=14)
    text = " ".join(["lorem ipsum dolor sit amet"] * 150)       # ~4 KiB
    t0 = time.perf_counter()
    t = layout_text([(text, st)], 30)
    t2 = layout_text([("y" * 4000, st)], 200, 1)
    assert time.perf_counter() - t0 < 1.5
    assert len(t.lines) > 300 and t.hard_broken
    assert t2.truncated and len(t2.lines) == 1 and t2.lines[0].text.endswith("…")
    assert t2.lines[0].w <= 200 + 1e-6


def test_caches_are_bounded_by_stored_glyphs():
    from agentplatform.ui_designer import text as T
    st = style(size=14)
    for i in range(400):
        layout_text([(f"word{i} " + "z" * 900, st)], 300)
    for c in (T._SHAPED, T._ATOMS, T._LAYOUTS):
        assert c.cost <= c.max_cost and c.cost == sum(v[1] for v in c.d.values())
