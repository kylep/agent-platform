"""Repeated plain instances preserve their own geometry, appearance, and warnings."""
import pytest

from agentplatform.ui_designer import schema
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import iter_nodes, layout_document


def instance(id_, **fields):
    return {"id": id_, "type": "instance", "of": "card", **fields}


def document(children, component):
    return {"schema": 1, "pages": [{"id": "home", "children": children}],
            "components": {"card": {"id": "card", "type": "frame", **component}}}


def test_repeated_instances_resolve_in_their_own_parent_context():
    def panel(id_, width, x, fill, **fields):
        return {"id": id_, "type": "frame", "w": width, "x": x, "fill": fill,
                "layout": {"dir": "column"}, "children": [instance(f"{id_}-card")], **fields}

    doc = document([
        panel("narrow", 70, 0, "#000000"),
        panel("wide", 240, 100, "#ffffff"),
        panel("turned", 70, 360, "#000000", rotate=90),
    ], {
        "w": "fill", "layout": {"dir": "column"},
        "children": [{"id": "label", "type": "text", "w": "fill", "h": 18, "color": "#000000",
                      "text": "Repeated status details", "style": {"size": 14}}],
    })
    schema.validate_document(doc)
    result = layout_document(doc)
    nodes = {node["id"]: node for node in iter_nodes(result)}
    narrow = nodes["narrow-card/label"]
    wide = nodes["wide-card/label"]
    turned = nodes["turned-card/label"]
    assert (narrow["w"], wide["w"], turned["w"]) == (70, 240, 70)
    assert len(narrow["text"]["lines"]) > len(wide["text"]["lines"]) == 1
    assert "bounds" not in narrow and "bounds" not in wide
    assert turned["bounds"] == [386.0, -26.0, 18.0, 70.0]
    assert wide["text"]["lines"][0]["x"] == 100

    warnings = result["warnings"]
    for code, text in (("UD-WARN-TEXT-OVERFLOW", "needs"),
                       ("UD-WARN-CONTRAST", "contrast 1.00:1")):
        matches = [warning for warning in warnings if warning["code"] == code]
        assert [warning["id"] for warning in matches] == ["narrow-card/label", "turned-card/label"]
        for warning in matches:
            assert warning["path"] == "$.components.card.children[0]"
            assert repr(warning["id"]) in warning["message"]
            assert text in warning["message"]


def test_plain_and_overridden_instances_observe_document_changes():
    doc = document([
        instance("before"),
        instance("override", overrides={"label.text": "Override", "label.color": "#ff0000"}),
        instance("after"),
    ], {"children": [{"id": "label", "type": "text", "text": "Original", "color": "#222222"}]})
    schema.validate_document(doc)

    def labels():
        return {node["id"]: (node["text"]["lines"][0]["text"],
                            node["text"]["lines"][0]["runs"][0]["color"])
                for node in iter_nodes(layout_document(doc)) if node["type"] == "text"}

    assert labels() == {"before/label": ("Original", "#222222"), "override/label": ("Override", "#ff0000"),
                        "after/label": ("Original", "#222222")}
    doc["components"]["card"]["children"][0].update(text="Revised", color="#663399")
    assert labels() == {"before/label": ("Revised", "#663399"), "override/label": ("Override", "#ff0000"),
                        "after/label": ("Revised", "#663399")}


def test_every_repeated_instance_counts_toward_the_expansion_budget():
    # Eight rendered nodes per placement hit the expansion cap while keeping the
    # authored node count low enough for a valid document at the boundary.
    leaf_count = 7
    per_instance = 1 + leaf_count
    repeats = schema.MAX_EXPANDED_NODES // per_instance
    placements = [instance(f"card-{i}") for i in range(repeats)]
    doc = document(placements, {
        "children": [{"id": f"mark-{i}", "type": "rect", "w": 8, "h": 8} for i in range(leaf_count)],
    })
    schema.validate_document(doc)
    result = layout_document(doc)
    assert result["stats"]["nodes"] == per_instance * repeats
    assert len(result["pages"][0]["children"]) == repeats
    assert result["pages"][0]["children"][-1]["children"][-1]["id"] == f"card-{repeats - 1}/mark-{leaf_count - 1}"

    placements.append(instance("one-too-many"))
    with pytest.raises(UiDesignerError) as raised:
        layout_document(doc)
    assert raised.value.code == "UD-EXPANSION"
    assert raised.value.detail == {"reason": "nodes", "limit": schema.MAX_EXPANDED_NODES}


def test_reused_instances_do_not_share_mutable_rendered_styles_and_links():
    doc = document([
        instance("first"), instance("second"),
        {"id": "destination", "type": "frame", "w": 8, "h": 8},
    ], {
        "w": 20, "h": 20, "link_to": {"target": "destination"},
        "children": [{
            "id": "fill", "type": "rect", "w": 8, "h": 8,
            "fill": {"gradient": "linear", "stops": [[0, "#fff"], [1, "#000"]]},
        }],
    })
    schema.validate_document(doc)
    nodes = {node["id"]: node for node in iter_nodes(layout_document(doc))}
    nodes["first"]["link_to"]["target"] = "new"
    nodes["first/fill"]["fill"]["stops"][0][1] = "#f00"
    assert nodes["second"]["link_to"]["target"] == "destination"
    assert nodes["second/fill"]["fill"]["stops"][0][1] == "#fff"
    assert doc["components"]["card"]["children"][0]["fill"]["stops"][0][1] == "#fff"
