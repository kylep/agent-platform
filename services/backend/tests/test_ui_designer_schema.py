"""UI Designer document schema, ids, tokens, expansion budget and ops (design 46 T1)."""
import copy

import pytest

from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.ops import apply_ops

SHA = "a" * 64

DOC = {
    "schema": 1, "title": "Spectator UI", "modes": ["dark", "light"],
    "tokens": {
        "color": {"bg": {"dark": "#0b0f14", "light": "#ffffff"}, "fg": "#e6edf3",
                  "accent": "#5ad1ff"},
        "space": {"xs": 4, "s": 8, "m": 16, "l": 32},
        "radius": {"card": 12},
        "type": {"body": {"family": "Inter", "size": 14, "weight": 400, "line": 1.4},
                 "hud": {"family": "JetBrains Mono", "size": 20, "weight": 600}}},
    "components": {
        "score-pill": {"type": "frame", "layout": {"dir": "row", "gap": "$space.s", "pad": [4, 12]},
                       "fill": "$color.accent", "radius": 999,
                       "children": [{"id": "label", "type": "text", "text": "0",
                                     "style": "$type.hud", "color": "$color.bg"}]}},
    "pages": [{"id": "spectator", "title": "Spectator", "children": [
        {"id": "hud", "type": "frame", "title": "HUD 1080p", "w": 1920, "h": 1080,
         "fill": "$color.bg",
         "layout": {"dir": "column", "pad": "$space.l", "gap": "$space.m", "align": "stretch"},
         "children": [
             {"id": "topbar", "type": "frame", "h": 64,
              "layout": {"dir": "row", "justify": "space-between", "align": "center"},
              "children": [
                  {"id": "match-title", "type": "text", "text": "Match 12", "style": "$type.body",
                   "color": "$color.fg", "w": "hug"},
                  {"id": "red-score", "type": "instance", "of": "score-pill",
                   "overrides": {"label.text": "7"}},
                  {"id": "blue-score", "type": "instance", "of": "score-pill",
                   "overrides": {"label.text": "5", "fill": "#ff5a5a"}}]},
             {"id": "stage", "type": "frame", "w": "fill", "h": "fill", "fill": "#000000",
              "radius": "$radius.card"}]}]}],
}


def doc():
    return copy.deepcopy(DOC)


def codes(d):
    return [i.code for i in S.collect_issues(d)]


def edit(d, ops, v=1, expected=1, **kw):
    return apply_ops(d, ops, version=v, expected_version=expected, **kw)


def err(d, ops, **kw):
    with pytest.raises(UiDesignerError) as e:
        edit(d, ops, **kw)
    return e.value


def test_the_design_example_is_valid():
    S.validate_document(doc())
    assert S.lint(doc()) == []


def test_unknown_keys_are_refused_with_paths_and_all_at_once():
    d = doc()
    d["bogus"] = 1
    d["pages"][0]["children"][0]["children"][1]["wat"] = 2
    d["pages"][0]["children"][0]["w"] = -3
    got = {(i.code, i.path) for i in S.collect_issues(d)}
    assert ("UD-UNKNOWN-KEY", "$.bogus") in got
    assert ("UD-UNKNOWN-KEY", "$.pages[0].children[0].children[1].wat") in got
    assert ("UD-BOUNDS", "$.pages[0].children[0].w") in got


def test_validation_reports_through_one_ud_invalid():
    d = doc()
    d["schema"] = 2
    with pytest.raises(UiDesignerError) as e:
        S.validate_document(d)
    assert e.value.code == "UD-INVALID" and e.value.as_dict()["errors"][0]["path"] == "$.schema"
    assert {"path", "code", "message", "fix"} <= set(e.value.as_dict()["errors"][0])


@pytest.mark.parametrize("path,key,val,code", [
    ("hud", "fill", "red", "UD-FORMAT"),
    ("hud", "fill", "$color.nope", "UD-TOKEN-UNKNOWN"),
    ("hud", "fill", "$space.s", "UD-TOKEN-KIND"),
    ("hud", "fill", "$bad", "UD-FORMAT"),
    ("hud", "w", float("inf"), "UD-TYPE"),
    ("hud", "w", 100001, "UD-TYPE"),
    ("hud", "opacity", 2, "UD-BOUNDS"),
    ("hud", "radius", [1, 2], "UD-BOUNDS"),
    ("hud", "layout", {"dir": "diagonal"}, "UD-VALUE"),
    ("hud", "shadow", {"color": "#000", "blur": 65}, "UD-BOUNDS"),
    ("hud", "fill", {"gradient": "linear", "stops": [[0, "#000"]]}, "UD-BOUNDS"),
    ("hud", "stroke", {"width": 1}, "UD-MISSING"),
    ("match-title", "style", {"family": "Comic Sans", "size": 12}, "UD-FONT"),
    ("match-title", "text", "x" * 4097, "UD-LIMIT"),
    ("match-title", "text", [{"text": "a", "color": "nope"}], "UD-FORMAT"),
    ("match-title", "max_lines", 0, "UD-BOUNDS"),
])
def test_property_checks(path, key, val, code):
    d = doc()
    S.index_nodes(d)[path].node[key] = val
    assert code in codes(d)


def test_node_types_have_closed_keys():
    d = doc()
    S.index_nodes(d)["stage"].node["text"] = "no"  # text belongs to text nodes
    assert "UD-UNKNOWN-KEY" in codes(d)
    d = doc()
    S.index_nodes(d)["stage"].node["type"] = "blob"
    assert "UD-VALUE" in codes(d)


def test_ids_are_validated_and_unique_across_pages_nodes_and_components():
    d = doc()
    d["pages"][0]["children"][0]["children"][1]["id"] = "hud"
    assert "UD-DUPLICATE-ID" in codes(d)
    d = doc()
    d["pages"][0]["children"][0]["id"] = "Bad_ID"
    assert "UD-ID" in codes(d)
    d = doc()
    d["pages"][0]["children"][0]["id"] = "label"  # collides with a component child
    assert "UD-DUPLICATE-ID" in codes(d)
    d = doc()
    d["components"]["score-pill"]["id"] = "other"
    assert "UD-ID" in codes(d)


def test_id_generation_from_title_or_type_with_suffixes():
    taken = {"hud"}
    assert S.generate_id(taken, "HUD", "frame") == "hud-2"
    assert S.generate_id(taken, "HUD", "frame") == "hud-3"
    assert S.generate_id(set(), None, "text") == "text"
    assert S.generate_id(set(), "!!!", "rect") == "rect"
    long = S.generate_id({"a" * 48}, "a" * 80, "frame")
    assert len(long) <= 48 and S.ID_RE.match(long)
    n = {"type": "frame", "title": "Top Bar", "children": [{"type": "text"}, {"type": "text"}]}
    S.assign_ids(n, set())
    assert [n["id"], n["children"][0]["id"], n["children"][1]["id"]] == ["top-bar", "text", "text-2"]


def test_normalize_fills_pages_nodes_and_component_roots():
    raw = {"schema": 1, "components": {"chip": {"type": "rect"}},
           "pages": [{"title": "Home", "children": [{"type": "rect"}]}]}
    out = S.normalize_document(raw)
    assert out["pages"][0]["id"] == "home" and out["components"]["chip"]["id"] == "chip"
    S.validate_document(out)
    assert "id" not in raw["pages"][0]


def test_token_resolution_by_mode_and_literals_are_left_alone():
    d = doc()
    d["pages"][0]["children"][0]["children"][0]["children"][0]["text"] = "$color.bg"
    dark = S.resolve_tokens(d, "dark")
    light = S.resolve_tokens(d, "light")
    hud = S.index_nodes(dark)["hud"].node
    assert hud["fill"] == "#0b0f14" and S.index_nodes(light)["hud"].node["fill"] == "#ffffff"
    assert hud["layout"]["pad"] == 32 and hud["layout"]["gap"] == 16
    assert S.index_nodes(dark)["match-title"].node["text"] == "$color.bg"
    assert S.index_nodes(dark)["match-title"].node["style"]["family"] == "Inter"
    assert S.index_nodes(dark)["red-score"].node["overrides"] == {"label.text": "7"}
    assert S.resolve_token("$color.bg", d["tokens"], "nope", d["modes"]) == "#0b0f14"


def test_mode_keys_must_be_declared():
    d = doc()
    d["tokens"]["color"]["bg"]["sepia"] = "#aa8"
    assert "UD-MODE" in codes(d)
    d = doc()
    del d["modes"]
    assert "UD-MODE" in codes(d)


def test_instances_resolve_components_and_overrides():
    d = doc()
    S.index_nodes(d)["red-score"].node["of"] = "ghost"
    assert "UD-COMPONENT-UNKNOWN" in codes(d)
    for bad in ({"nobody.text": "x"}, {"label.layout": {}}, {"label.text": 5}, {"label.id": "z"}):
        d = doc()
        S.index_nodes(d)["red-score"].node["overrides"] = bad
        assert "UD-OVERRIDE" in codes(d) or "UD-TYPE" in codes(d), bad


def test_limits_node_count_pages_and_document_size():
    d = doc()
    d["pages"][0]["children"] = [{"id": f"n{i}", "type": "rect"} for i in range(S.MAX_NODES + 1)]
    assert any(i.code == "UD-LIMIT" for i in S.collect_issues(d))
    d = doc()
    d["pages"] = [{"id": f"p{i}"} for i in range(S.MAX_PAGES + 1)]
    assert "UD-LIMIT" in codes(d)
    d = doc()
    d["pages"][0]["children"][0]["meta"] = {"k": "x" * 2049}
    assert "UD-LIMIT" in codes(d)
    d = doc()
    d["pages"][0]["children"] = [{"id": f"t{i}", "type": "text", "text": "x" * 4000}
                                 for i in range(300)]
    assert codes(d) == ["UD-LIMIT"]


def test_path_data_is_restricted():
    d = doc()
    d["pages"][0]["children"].append({"id": "p1", "type": "path", "d": "M0 0 L10 10 Z"})
    assert codes(d) == []
    d["pages"][0]["children"][-1]["d"] = "M0 0 <script>"
    assert "UD-FORMAT" in codes(d)


def test_shadow_budget():
    d = doc()
    d["pages"][0]["children"] = [{"id": f"s{i}", "type": "rect", "shadow": {"color": "#000"}}
                                 for i in range(S.MAX_SHADOWS + 1)]
    assert "UD-LIMIT" in codes(d)


def _chain(depth):
    """components c0 .. c<depth>: each holds one instance of the next."""
    comps = {f"c{i}": {"type": "frame", "children": [
        {"id": f"i{i}", "type": "instance", "of": f"c{i + 1}"}]} for i in range(depth)}
    comps[f"c{depth}"] = {"type": "rect"}
    return {"schema": 1, "components": comps, "pages": [
        {"id": "p", "children": [{"id": "top", "type": "instance", "of": "c0"}]}]}


def test_expansion_depth_cap_is_six():
    ok = S.normalize_document(_chain(5))  # page instance + 5 nested = depth 6
    S.validate_document(ok)
    bad = S.normalize_document(_chain(6))
    with pytest.raises(UiDesignerError) as e:
        S.validate_document(bad)
    assert e.value.code == "UD-EXPANSION" and e.value.detail["reason"] == "depth"


def test_self_containing_component_is_refused_even_if_unused():
    d = {"schema": 1, "components": {"a": {"type": "frame", "children": [
        {"id": "again", "type": "instance", "of": "a"}]}}, "pages": []}
    with pytest.raises(UiDesignerError) as e:
        S.validate_document(S.normalize_document(d))
    assert e.value.code == "UD-EXPANSION"


def test_expansion_node_cap():
    # 3 levels of 30-wide fan-out: 27,000 leaves.
    comps = {"leaf": {"type": "rect"},
             "mid": {"type": "frame", "children": [{"id": f"m{i}", "type": "instance", "of": "leaf"}
                                                   for i in range(30)]},
             "big": {"type": "frame", "children": [{"id": f"b{i}", "type": "instance", "of": "mid"}
                                                   for i in range(30)]}}
    d = {"schema": 1, "components": comps, "pages": [{"id": "p", "children": [
        {"id": f"t{i}", "type": "instance", "of": "big"} for i in range(30)]}]}
    with pytest.raises(UiDesignerError) as e:
        S.validate_document(S.normalize_document(d))
    assert e.value.code == "UD-EXPANSION" and e.value.detail["reason"] == "nodes"
    d["pages"][0]["children"] = d["pages"][0]["children"][:20]  # 20*(1+30*(1+30))=18,620
    S.validate_document(S.normalize_document(d))
    assert S.check_expansion(S.normalize_document(d)) == 20 * (1 + 30 * 31)


def test_unknown_assets_refused_only_when_a_placement_set_is_given():
    d = doc()
    d["pages"][0]["children"].append({"id": "pic", "type": "image", "asset": SHA, "fit": "cover"})
    S.validate_document(d)
    S.validate_document(d, known_assets={SHA})
    with pytest.raises(UiDesignerError) as e:
        S.validate_document(d, known_assets=set())
    assert e.value.code == "UD-ASSET-UNKNOWN" and e.value.detail["assets"][0]["asset"] == SHA
    d["pages"][0]["children"][-1]["asset"] = "https://evil/x.png"
    assert "UD-FORMAT" in codes(d)


# --- ops -------------------------------------------------------------------

def test_every_batch_is_one_version_and_input_is_untouched():
    d = doc()
    before = copy.deepcopy(d)
    r = edit(d, [{"op": "set_text", "id": "match-title", "text": "Final"},
                 {"op": "update", "id": "stage", "set": {"fill": "#111", "radius": None}}])
    assert r.version == 2 and d == before
    assert S.index_nodes(r.doc)["match-title"].node["text"] == "Final"
    stage = S.index_nodes(r.doc)["stage"].node
    assert stage["fill"] == "#111" and "radius" not in stage


def test_cas_conflict_answers_with_current_version_and_ops_since():
    since = [{"op": "set_text", "id": "x", "text": "y"}]
    e = err(doc(), [{"op": "set_text", "id": "match-title", "text": "a"}], v=4, expected=2,
            ops_since=lambda n: since if n == 2 else [])
    assert e.code == "UD-VERSION-CONFLICT"
    assert e.detail == {"current_version": 4, "ops_since": since}
    assert err(doc(), [{"op": "tokens", "set": {}}], expected=None).codes == ["UD-MISSING"]


def test_failed_batch_applies_nothing():
    d = doc()
    e = err(d, [{"op": "set_text", "id": "match-title", "text": "ok"},
                {"op": "update", "id": "stage", "set": {"fill": "nope"}}])
    assert e.code == "UD-INVALID" and e.issues[0].path == "$.pages[0].children[0].children[1].fill"
    assert S.index_nodes(d)["match-title"].node["text"] == "Match 12"


def test_batch_shape_errors():
    assert err(doc(), []).codes == ["UD-TYPE"]
    assert err(doc(), [{"op": "nuke"}]).codes == ["UD-OP-UNKNOWN"]
    assert set(err(doc(), [{"op": "remove"}, ]).codes) == {"UD-MISSING"}
    assert err(doc(), [{"op": "remove", "ids": ["stage"], "extra": 1}]).codes == ["UD-OP-FIELD"]
    assert err(doc(), [{"op": "set_text", "id": "nope", "text": "x"}]).codes == ["UD-OP-TARGET"]


def test_insert_generates_ids_and_honours_index():
    r = edit(doc(), [{"op": "insert", "parent": "hud", "index": 0, "node": {
        "type": "frame", "title": "Banner", "children": [{"type": "text", "text": "hi"}]}}])
    hud = S.index_nodes(r.doc)["hud"].node
    assert hud["children"][0]["id"] == "banner" and hud["children"][0]["children"][0]["id"] == "text"
    r = edit(r.doc, [{"op": "insert", "parent": "spectator", "node": {"type": "rect"}}], v=2, expected=2)
    assert r.doc["pages"][0]["children"][-1]["id"] == "rect"
    assert err(doc(), [{"op": "insert", "parent": "hud", "index": 9, "node": {"type": "rect"}}]).codes \
        == ["UD-OP-INDEX"]
    assert err(doc(), [{"op": "insert", "parent": "match-title", "node": {"type": "rect"}}]).codes \
        == ["UD-OP-TARGET"]
    assert "UD-DUPLICATE-ID" in err(doc(), [{"op": "insert", "parent": "hud", "node": {
        "id": "stage", "type": "rect"}}]).codes


def test_update_refuses_children_id_type():
    for k in ("children", "id", "type"):
        assert err(doc(), [{"op": "update", "id": "hud", "set": {k: 1}}]).codes == ["UD-OP-FIELD"]
    r = edit(doc(), [{"op": "update", "id": "spectator", "set": {"title": "Home"}}])
    assert r.doc["pages"][0]["title"] == "Home"


def test_remove_subtree_and_instance_keeps_component():
    r = edit(doc(), [{"op": "remove", "ids": ["topbar", "match-title", "red-score"]}])
    ids = set(S.index_nodes(r.doc))
    assert "topbar" not in ids and "red-score" not in ids and "score-pill" in ids
    assert [w["code"] for w in r.warnings] == ["UD-WARN-COMPONENT-UNUSED"]
    assert "UD-COMPONENT-UNKNOWN" in err(doc(), [{"op": "remove", "ids": ["score-pill"]}]).codes


def test_move_reorders_reparents_and_refuses_cycles():
    r = edit(doc(), [{"op": "move", "id": "stage", "parent": "hud", "index": 0}])
    assert [c["id"] for c in S.index_nodes(r.doc)["hud"].node["children"]] == ["stage", "topbar"]
    r = edit(doc(), [{"op": "move", "id": "match-title", "parent": "hud"}])
    assert [c["id"] for c in S.index_nodes(r.doc)["hud"].node["children"]][-1] == "match-title"
    assert err(doc(), [{"op": "move", "id": "hud", "parent": "topbar"}]).codes == ["UD-OP-CYCLE"]
    assert err(doc(), [{"op": "move", "id": "hud", "parent": "hud"}]).codes == ["UD-OP-CYCLE"]
    assert err(doc(), [{"op": "move", "id": "spectator", "parent": "hud"}]).codes == ["UD-OP-TARGET"]


def test_replace_keeps_id_when_omitted():
    r = edit(doc(), [{"op": "replace", "id": "stage", "node": {"type": "rect", "fill": "#222"}}])
    n = S.index_nodes(r.doc)["stage"].node
    assert n["type"] == "rect" and n["fill"] == "#222"
    r = edit(doc(), [{"op": "replace", "id": "topbar", "node": {
        "type": "frame", "children": [{"id": "match-title", "type": "text", "text": "same ids ok"}]}}])
    assert "red-score" not in S.index_nodes(r.doc)


def test_set_text_only_on_text_nodes():
    assert err(doc(), [{"op": "set_text", "id": "stage", "text": "x"}]).codes == ["UD-OP-TARGET"]
    r = edit(doc(), [{"op": "set_text", "id": "match-title", "text": [{"text": "A"}, {"text": "B"}]}])
    assert S.index_nodes(r.doc)["match-title"].node["text"][1]["text"] == "B"


def test_tokens_deep_merge_and_in_use_removal():
    r = edit(doc(), [{"op": "tokens", "set": {"color": {"accent": "#ff00aa", "warn": "#f90"},
                                              "space": {"xl": 64}}}])
    assert r.doc["tokens"]["color"]["accent"] == "#ff00aa" and r.doc["tokens"]["color"]["fg"]
    r = edit(doc(), [{"op": "tokens", "set": {"color": {"bg": {"light": "#eee"}}}}])
    assert r.doc["tokens"]["color"]["bg"] == {"dark": "#0b0f14", "light": "#eee"}
    e = err(doc(), [{"op": "tokens", "set": {"color": {"fg": None}}}])
    assert e.codes == ["UD-TOKEN-IN-USE"] and e.issues[0].value == "$color.fg"
    assert e.issues[0].path == "$.pages[0].children[0].children[0].children[0].color"
    edit(doc(), [{"op": "tokens", "set": {"space": {"xs": None}}}])  # unused: fine


def test_define_component_lifts_and_replaces_with_instance():
    d = doc()
    r = edit(d, [{"op": "define_component", "id": "top-bar", "from": "topbar"}])
    idx = S.index_nodes(r.doc)
    assert idx["top-bar"].kind == "component" and idx["top-bar"].node["layout"]["justify"]
    inst = idx["topbar"].node
    assert inst == {"id": "topbar", "type": "instance", "of": "top-bar", "h": 64}
    assert idx["match-title"].in_component == "top-bar"
    assert err(doc(), [{"op": "define_component", "id": "hud", "from": "topbar"}]).codes == ["UD-DUPLICATE-ID"]
    assert err(doc(), [{"op": "define_component", "id": "x", "from": "label"}]).codes == ["UD-OP-TARGET"]
    assert err(doc(), [{"op": "define_component", "id": "x", "from": "red-score"}]).codes == ["UD-OP-TARGET"]


def test_set_doc_normalizes_and_validates_then_later_ops_see_it():
    new = {"schema": 1, "pages": [{"title": "Fresh", "children": [{"type": "text", "text": "a"}]}]}
    r = edit(doc(), [{"op": "set_doc", "doc": new},
                     {"op": "set_text", "id": "text", "text": "b"}])
    assert r.doc["pages"][0]["id"] == "fresh" and r.doc["pages"][0]["children"][0]["text"] == "b"
    assert err(doc(), [{"op": "set_doc", "doc": {"schema": 1}}]).codes == ["UD-MISSING"]


def test_set_doc_and_insert_check_image_placements():
    img = {"type": "image", "asset": SHA}
    assert err(doc(), [{"op": "insert", "parent": "hud", "node": img}],
               known_assets=set()).code == "UD-ASSET-UNKNOWN"
    edit(doc(), [{"op": "insert", "parent": "hud", "node": img}], known_assets={SHA})


def test_edits_that_break_expansion_are_refused():
    ops = [{"op": "insert", "parent": "score-pill",
            "node": {"type": "instance", "of": "score-pill"}}]
    assert err(doc(), ops).code == "UD-EXPANSION"


def test_lint_warnings():
    d = doc()
    S.index_nodes(d)["match-title"].node["x"] = 5
    S.index_nodes(d)["topbar"].node["h"] = "hug"
    S.index_nodes(d)["match-title"].node["h"] = "fill"
    del d["tokens"]["color"]["bg"]["light"]
    d["components"]["orphan"] = {"type": "rect"}
    w = {x["code"] for x in S.lint(S.normalize_document(d))}
    assert w == {"UD-WARN-XY-IN-LAYOUT", "UD-WARN-FILL-IN-HUG", "UD-WARN-TOKEN-MODE",
                 "UD-WARN-COMPONENT-UNUSED"}
