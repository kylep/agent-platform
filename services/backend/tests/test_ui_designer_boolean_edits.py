"""Boolean authoring plans are native-free, exact and bounded before worker work."""

import copy
import json
import pytest
from agentplatform.ui_designer import schema as S, store
from agentplatform.ui_designer.ops import (
    apply_ops,
    prepare_boolean,
    boolean_literal_ops,
)
from agentplatform.ui_designer.errors import UiDesignerError


def document():
    return {
        "schema": 1,
        "pages": [
            {
                "id": "p",
                "children": [
                    {"id": "before", "type": "rect", "x": 1, "y": 2, "w": 10, "h": 10},
                    {"id": "keep", "type": "rect", "w": 1, "h": 1},
                    {
                        "id": "first",
                        "type": "rect",
                        "x": 20,
                        "y": 2,
                        "w": 10,
                        "h": 10,
                        "rotate": 10,
                        "radius": 2,
                        "max_w": 12,
                        "title": "Style",
                        "fill": "#f00",
                        "stroke": {"color": "#000", "width": 2},
                        "meta": {"text": "literal"},
                    },
                    {
                        "id": "after",
                        "type": "ellipse",
                        "x": 40,
                        "y": 2,
                        "w": 10,
                        "h": 10,
                    },
                ],
            }
        ],
    }


def test_exact_original_order_and_first_style_survive_geometry_replacement():
    doc = document()
    op = {"op": "boolean", "kind": "union", "ids": ["first", "before", "after"]}
    prepared = prepare_boolean(doc, op)
    assert [entry.get("index") for entry in prepared["undo_ops"][1:]] == [0, 3]
    geometry = {"x": 0, "y": 0, "w": 50, "h": 20, "d": "M0 0H50V20H0Z", "empty": False}
    literal = boolean_literal_ops(prepared, geometry)
    result = apply_ops(doc, literal, version=1, expected_version=1)
    children = result.doc["pages"][0]["children"]
    assert [n["id"] for n in children] == ["keep", "first"]
    first = children[1]
    assert (
        first["type"] == "path"
        and first["fill"] == "#f00"
        and first["stroke"] == doc["pages"][0]["children"][2]["stroke"]
    )
    assert first["title"] == "Style" and first["meta"] == {"text": "literal"}
    assert not {"rotate", "radius", "max_w"} & first.keys()
    restored = apply_ops(
        result.doc, prepared["undo_ops"], version=2, expected_version=2
    )
    assert json.dumps(restored.doc, sort_keys=True) == json.dumps(doc, sort_keys=True)
    assert doc == document()


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["pages"][0]["children"][0].update(w="hug"),
        lambda d: d["pages"][0]["children"][0].update(x="$space.x"),
        lambda d: d["pages"][0]["children"][0].update(type="text"),
        lambda d: d["pages"][0]["children"][0].update(
            stroke={"color": "#000", "sides": [1, 2, 3, 4]}
        ),
    ],
)
def test_unresolved_geometry_or_unsupported_first_style_refused(change):
    doc = document()
    change(doc)
    with pytest.raises(UiDesignerError):
        prepare_boolean(
            doc, {"op": "boolean", "kind": "union", "ids": ["before", "first"]}
        )


def test_mask_flow_duplicates_and_large_inverse_are_refused_before_worker():
    doc = document()
    for ids in [["before"], ["before", "before"]]:
        with pytest.raises(UiDesignerError):
            prepare_boolean(doc, {"op": "boolean", "kind": "union", "ids": ids})
    doc["pages"][0]["children"] = [
        {
            "id": "container",
            "type": "frame",
            "layout": {"dir": "row"},
            "children": doc["pages"][0]["children"],
        }
    ]
    with pytest.raises(UiDesignerError, match="absolute"):
        prepare_boolean(
            doc, {"op": "boolean", "kind": "union", "ids": ["before", "first"]}
        )
    container = doc["pages"][0]["children"][0]
    container.pop("layout")
    container["mask"] = "alpha"
    with pytest.raises(UiDesignerError, match="mask child"):
        prepare_boolean(
            doc, {"op": "boolean", "kind": "union", "ids": ["before", "first"]}
        )
    doc = document()
    doc["pages"][0]["children"][0].update(type="path", d="M0 0" + "L1 1 " * 2000)
    with pytest.raises(UiDesignerError, match="undo exceeds"):
        prepare_boolean(
            doc, {"op": "boolean", "kind": "union", "ids": ["before", "first"]}
        )


def test_empty_path_result_is_valid_and_can_be_an_operand():
    doc = document()
    prepared = prepare_boolean(
        doc, {"op": "boolean", "kind": "intersect", "ids": ["before", "first"]}
    )
    result = apply_ops(
        doc,
        boolean_literal_ops(
            prepared, {"x": 0, "y": 0, "w": 0, "h": 0, "d": "", "empty": True}
        ),
        version=1,
        expected_version=1,
    )
    S.validate_document(result.doc)
    again = prepare_boolean(
        result.doc, {"op": "boolean", "kind": "union", "ids": ["before", "after"]}
    )
    assert again["operands"][0]["d"] == "" and again["operands"][0]["w"] == 0


def test_strict_new_path_edits_but_legacy_paths_and_restore_stay_accepted():
    doc = document()
    doc["pages"][0]["children"][0].update(type="path", d="M200000 0L200001 1")
    S.validate_document(doc)  # Original schema semantics remain compatible.
    changed = apply_ops(
        doc,
        [{"op": "update", "id": "keep", "set": {"title": "unrelated"}}],
        version=1,
        expected_version=1,
    )
    assert changed.doc["pages"][0]["children"][0]["d"] == "M200000 0L200001 1"
    restored = apply_ops(
        changed.doc, [{"op": "set_doc", "doc": doc}], version=2, expected_version=2
    )
    assert restored.doc == doc
    with pytest.raises(UiDesignerError):
        apply_ops(
            doc,
            [{"op": "update", "id": "before", "set": {"d": "M0 0L"}}],
            version=1,
            expected_version=1,
        )
    with pytest.raises(UiDesignerError):
        apply_ops(
            doc,
            [
                {
                    "op": "insert",
                    "parent": "p",
                    "node": {"id": "bad", "type": "path", "d": "M0 0L"},
                }
            ],
            version=1,
            expected_version=1,
        )


async def test_receipt_is_rechecked_after_document_lock(sf, monkeypatch):
    async with sf() as session:
        await store.create_document(
            session, slug="race", doc=document(), owner="user:test"
        )
        await session.commit()
    arguments = {
        "ops": [{"op": "update", "id": "keep", "set": {"title": "changed"}}],
        "expected_version": 1,
        "author": "user:test",
        "scope": "user:test",
        "request_id": "same",
    }
    async with sf() as session:
        first = await store.edit_document(session, "race", **arguments)
        await session.commit()
    lookup = store._receipt_lookup
    missed = False

    async def entry_race(*args, **kwargs):
        nonlocal missed
        if not missed:
            missed = True
            return None  # Simulate commit between entry lookup and row lock.
        return await lookup(*args, **kwargs)

    monkeypatch.setattr(store, "_receipt_lookup", entry_race)
    async with sf() as session:
        replay = await store.edit_document(session, "race", **arguments)
        assert replay["replayed"] and replay["version"] == first["version"]


def test_component_leaf_operands_allowed_but_removed_link_target_refused():
    doc = document()
    children = doc['pages'][0]['children']
    doc['components'] = {'shape': {'id': 'shape', 'type': 'frame', 'w': 100, 'h': 100,
                                    'children': children}}
    doc['pages'][0]['children'] = []
    prepared = prepare_boolean(doc, {'op': 'boolean', 'kind': 'union', 'ids': ['before', 'first']})
    result = apply_ops(doc, boolean_literal_ops(prepared, {'x': 0, 'y': 0, 'w': 30, 'h': 10,
                       'd': 'M0 0H30V10H0Z'}), version=1, expected_version=1)
    assert result.doc['components']['shape']['children'][0]['type'] == 'path'
    # Removing a bound child must fail final whole-document validation atomically.
    doc['component_sets'] = {'shapes': {'axes': {}, 'properties': {
        'shown': {'type': 'boolean', 'default': True}}, 'variants': [
        {'when': {}, 'of': 'shape', 'bindings': {'shown': ['first']}}]}}
    S.validate_document(doc)
    with pytest.raises(UiDesignerError):
        apply_ops(doc, boolean_literal_ops(prepared, {'x': 0, 'y': 0, 'w': 30, 'h': 10,
                  'd': 'M0 0H30V10H0Z'}), version=1, expected_version=1)
    assert len(doc['components']['shape']['children']) == 4
