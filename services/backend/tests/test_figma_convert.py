"""Figma converter tests. The real desk-buddy dump (tests/fixtures/figma/desk-buddy.json)
is used when present; the synthetic dump below always runs."""
import json
import math
import pathlib

import pytest

from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.figma_convert import _EXT, convert

FIX = pathlib.Path(__file__).parent / "fixtures" / "figma" / "desk-buddy.json"
WHITE = {"r": 1, "g": 1, "b": 1}


def solid(c=WHITE, **k):
    return dict({"type": "SOLID", "color": c}, **k)


def rect(i, x=0, y=0, w=10, h=10, **k):
    return dict({"id": i, "name": "box " + i, "type": "RECTANGLE", "width": w, "height": h,
                 "relativeTransform": [[1, 0, x], [0, 1, y]], "fills": [solid()]}, **k)


def synthetic():
    comp = {"id": "2:1", "name": "icon/dot", "type": "COMPONENT", "width": 24, "height": 24,
            "relativeTransform": [[1, 0, 0], [0, 1, -400]],
            "children": [{"id": "2:2", "name": "Vector", "type": "VECTOR", "width": 24, "height": 24,
                          "relativeTransform": [[1, 0, 0], [0, 1, 0]], "fills": [solid({"r": 0, "g": 0, "b": 0})],
                          "fillGeometry": [{"windingRule": "NONZERO", "data": "M 0 0 L 24 0 L 24 24 Z"}]}]}
    inst = {"id": "3:2", "name": "icon/dot", "type": "INSTANCE", "width": 24, "height": 24, "componentId": "2:1",
            "componentName": "icon/dot", "relativeTransform": [[1, 0, 5], [0, 1, 6]],
            "children": [dict(comp["children"][0], fills=[solid({"r": 1, "g": 0, "b": 0})])]}
    text = {"id": "3:3", "name": "Title", "type": "TEXT", "width": 100, "height": 20, "characters": "Hello world",
            "relativeTransform": [[1, 0, 0], [0, 1, 40]], "textAutoResize": "WIDTH_AND_HEIGHT",
            "textAlignHorizontal": "LEFT", "fills": [],
            "segments": [{"start": 0, "end": 6, "fontFamily": "Comic Neue", "fontStyle": "Medium", "fontSize": 20,
                          "fontWeight": 500, "lineHeight": {"unit": "PIXELS", "value": 24},
                          "letterSpacing": {"unit": "PIXELS", "value": 0}, "fills": [solid()]},
                         {"start": 6, "end": 11, "fontFamily": "JetBrains Mono", "fontStyle": "Bold", "fontSize": 20,
                          "fontWeight": 700, "lineHeight": {"unit": "AUTO"},
                          "letterSpacing": {"unit": "PIXELS", "value": 0}, "fills": [solid({"r": 1, "g": 0, "b": 0})]}]}
    rotated = rect("3:4", x=200, y=100, w=100, h=50, relativeTransform=[[0, -1, 200], [1, 0, 100]])
    masked = [dict(rect("3:5", w=20, h=20), isMask=True, maskType="ALPHA"), rect("3:6", w=40, h=40)]
    stack = rect("3:7", fills=[solid({"r": 1, "g": 0, "b": 0}), solid({"r": 0, "g": 0, "b": 1}, opacity=0.5)],
                 effects=[{"type": "DROP_SHADOW", "visible": True, "radius": 8, "color": {"r": 0, "g": 0, "b": 0, "a": .5},
                           "offset": {"x": 0, "y": 2}}, {"type": "LAYER_BLUR", "visible": True, "radius": 3}],
                 strokes=[solid()], strokeWeight=2, strokeAlign="INSIDE", dashPattern=[4, 4], blendMode="MULTIPLY")
    al = {"id": "3:8", "name": "row", "type": "FRAME", "width": 200, "height": 40, "layoutMode": "HORIZONTAL",
          "itemSpacing": 8, "paddingLeft": 4, "paddingRight": 4, "paddingTop": 4, "paddingBottom": 4,
          "primaryAxisAlignItems": "SPACE_BETWEEN", "counterAxisAlignItems": "CENTER",
          "relativeTransform": [[1, 0, 0], [0, 1, 200]],
          "children": [rect("3:9", layoutSizingHorizontal="FILL", layoutSizingVertical="FIXED")]}
    screen = {"id": "4:1", "name": "Home", "type": "FRAME", "width": 480, "height": 320, "clipsContent": True,
              "relativeTransform": [[1, 0, 0], [0, 1, 0]], "fills": [solid({"r": .1, "g": .1, "b": .1})],
              "children": [inst, text, rotated, *masked, stack, al]}
    return {"name": "t", "document": {"id": "0:1", "name": "Page 1", "type": "CANVAS", "children": [comp, screen]}}


def walk(n):
    yield n
    for c in n.get("children", []):
        yield from walk(c)


def test_synthetic_validates_and_maps():
    doc, rep = convert(synthetic())
    S.validate_document(doc)
    assert list(doc["components"]) == ["icon-dot"]
    home = doc["pages"][0]["children"][0]
    assert home["w"] == 480 and home["clip"] is True
    by = {n["meta"]["figma"]["id"]: n for n in walk(home) if "meta" in n}
    assert by["3:2"]["type"] == "instance" and by["3:2"]["of"] == "icon-dot"
    assert by["3:2"]["overrides"] == {"vector.fill": "#ff0000"}
    runs = by["3:3"]["text"]
    assert runs[0]["text"] == "Hello " and by["3:3"]["w"] == "hug"
    assert by["3:8"]["layout"] == {"dir": "row", "gap": 8, "pad": 4, "justify": "space-between", "align": "center"}
    assert by["3:9"]["w"] == "fill" and "x" not in by["3:9"]
    assert any("font Comic Neue" in k for k in rep["approximated"]["TEXT"])
    assert rep["converted"]["VECTOR"] >= 1


def test_rotation_matches_figma_bounds():
    doc, _ = convert(synthetic())
    n = next(n for n in walk(doc["pages"][0]["children"][0]) if n.get("meta", {}).get("figma", {}).get("id") == "3:4")
    assert n["rotate"] == 90
    assert (n["x"], n["y"]) == (125, 125)  # design 46 worked example


def test_mask_regrouped_and_reported():
    doc, rep = convert(synthetic())
    g = next(n for n in walk(doc["pages"][0]["children"][0]) if n.get("mask") == "alpha")
    assert len(g["children"]) == 4  # the mask and every following sibling
    assert rep["approximated"]["RECTANGLE"]["mask regrouped under a masked frame"] == 1


def test_stacked_paints_and_effects_reported_or_mapped():
    doc, rep = convert(synthetic())
    n = next(n for n in walk(doc["pages"][0]["children"][0]) if n.get("meta", {}).get("figma", {}).get("id") == "3:7")
    assert isinstance(n["fill"], (str, list))
    assert "shadow" in n
    # whatever the schema cannot express yet is counted, never silently lost
    for feat, key in (("layer blur", "blur"), ("dashed stroke", "dash"), ("blend mode MULTIPLY", "blend")):
        assert (feat in rep["unmapped"]) != bool(_EXT[key]), feat


@pytest.mark.skipif(not FIX.exists(), reason="desk-buddy dump not captured")
def test_desk_buddy_fixture():
    doc, rep = convert(json.loads(FIX.read_text()))
    S.validate_document(doc)
    assert len(doc["pages"][0]["children"]) >= 10
    assert len(doc["components"]) == 8
    assert not rep["dropped"] or sum(rep["dropped"].values()) < 5


def _find(doc, fid):
    for pg in doc["pages"]:
        for top in pg["children"]:
            for n in walk(top):
                if n.get("meta", {}).get("figma", {}).get("id") == fid:
                    return n


def test_page_id_never_collides_with_a_node_id():
    d = synthetic()
    d["document"]["children"].append(rect("9:1", name="Page 1"))
    d["document"]["children"][-1]["name"] = "Page 1"
    doc, _ = convert(d)
    S.validate_document(doc)
    ids = [doc["pages"][0]["id"]] + [n["id"] for n in walk({"children": doc["pages"][0]["children"]}) if "id" in n]
    assert len(ids) == len(set(ids))


def test_budgets_are_clamped_and_reported():
    d = synthetic()
    sh = {"type": "DROP_SHADOW", "visible": True, "radius": 200, "spread": 100,
          "color": {"r": 0, "g": 0, "b": 0, "a": 1}, "offset": {"x": 0, "y": 0}}
    d["document"]["children"].append(rect(
        "9:2", fills=[solid({"r": i / 20, "g": 0, "b": 0}) for i in range(12)], effects=[sh] * 40))
    doc, rep = convert(d)
    S.validate_document(doc)
    app = rep["approximated"]["RECTANGLE"]
    assert any("fill stack trimmed" in k for k in app)
    assert any("shadow spread clamped" in k for k in app)
    assert any("shadow blur clamped" in k for k in app)
    assert any("beyond the 32 per document" in k for k in rep["unmapped"]) or not _EXT["shadow_list"]


def test_instance_root_fill_override():
    d = synthetic()
    comp = d["document"]["children"][0]
    comp["fills"] = [solid({"r": 1, "g": 0, "b": 0})]
    inst = dict(comp, id="3:20", name="blue", type="INSTANCE", componentId="2:1", componentName="icon/dot",
                fills=[solid({"r": 0, "g": 0, "b": 1})])
    d["document"]["children"][1]["children"].append(inst)
    doc, _ = convert(d)
    S.validate_document(doc)
    assert _find(doc, "3:20")["overrides"]["fill"] == "#0000ff"


def test_rest_shared_fill_and_text_styles_use_first_referenced_value():
    dump={'name':'REST styles','styles':{
        'S:fill':{'name':'Brand / Primary','styleType':'FILL'},
        'S:text':{'name':'Heading','styleType':'TEXT'},
        'S:unused':{'name':'Unused','styleType':'FILL'}},
        'document':{'id':'page','type':'CANVAS','name':'Main','children':[
            rect('a',fills=[solid({'r':1,'g':0,'b':0})],styles={'fill':'S:fill'}),
            rect('b',fills=[solid({'r':0,'g':0,'b':1})],styles={'fill':'S:fill'}),
            {'id':'t','type':'TEXT','name':'Title','width':80,'height':25,'characters':'Hi!',
             'style':{'fontFamily':'Inter','fontSize':20,'fontWeight':600,'lineHeightPx':24,'letterSpacing':1},
             'styles':{'text':'S:text','fill':'S:fill'},'fills':[solid({'r':1,'g':0,'b':0})],
             'characterStyleOverrides':[0,0,1],'styleOverrideTable':{'1':{'fontWeight':700}}}]}}
    doc,report=convert(dump)
    a,b,text=doc['pages'][0]['children']
    assert a['fill']==b['fill'] and a['fill'].startswith('$color.brand-primary-')
    assert len(doc['tokens']['color'])==1
    assert list(doc['tokens']['color'].values())==['#ff0000']
    assert text['style'].startswith('$type.heading-')
    style=next(iter(doc['tokens']['type'].values()))
    assert style=={'family':'Inter','size':20,'weight':600,'line':1.2,'letter':1}
    assert text['text'][1]['style']['weight']==700
    assert not S.collect_issues(doc)


def test_unsupported_shared_style_is_reported_without_changing_node_paint():
    dump={'document':{'id':'p','type':'CANVAS','children':[
        rect('a',styles={'effect':'shadow-style'},effects=[])]}}
    doc,report=convert(dump)
    assert any('shared effect style' in key for key in report['unmapped'])
