"""Resize journals literal geometry and provides bounded exact inverse updates."""

import copy
import json
import pytest
from agentplatform.ui_designer import schema as S, store
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.errors import UiDesignerError


def document(children=None):
    return {
        "schema": 1,
        "pages": [
            {
                "id": "p",
                "children": [
                    {
                        "id": "parent",
                        "type": "frame",
                        "w": 80,
                        "h": 100,
                        "max_w": 90,
                        "children": children
                        or [
                            {
                                "id": "child",
                                "type": "rect",
                                "w": 80,
                                "h": 20,
                                "max_w": 90,
                                "constraints": {"horizontal": "stretch"},
                            }
                        ],
                    }
                ],
            }
        ],
    }


def parent(doc):
    return doc["pages"][0]["children"][0]


def test_clamped_resize_exact_undo_redo_and_literal_forward_replay():
    original = document()
    request = [{"op": "resize", "id": "parent", "set": {"w": 140, "title": "Resized"}}]
    resized = apply_ops(original, request, version=1, expected_version=1)
    assert (
        parent(resized.doc)["w"] == 140
    )  # Authored request stays raw; delta uses effective 80 -> 90.
    assert parent(resized.doc)["children"][0]["w"] == 90
    assert all(op["op"] == "update" for op in resized.ops)
    restored = apply_ops(resized.doc, resized.undo_ops, version=2, expected_version=2)
    assert json.dumps(restored.doc, sort_keys=True) == json.dumps(
        original, sort_keys=True
    )
    replayed = apply_ops(original, resized.ops, version=1, expected_version=1)
    assert json.dumps(replayed.doc, sort_keys=True) == json.dumps(
        resized.doc, sort_keys=True
    )
    redone = apply_ops(restored.doc, request, version=3, expected_version=3)
    assert redone.doc == resized.doc and redone.undo_ops == resized.undo_ops
    literal = apply_ops(
        original,
        [{"op": "update", "id": "parent", "set": {"w": 140}}],
        version=1,
        expected_version=1,
    )
    assert parent(literal.doc)["children"][0]["w"] == 80
    assert original == document()


@pytest.mark.parametrize(
    "pin,expected",
    [
        ("start", (10, 20)),
        ("end", (60, 20)),
        ("center", (35, 20)),
        ("stretch", (10, 70)),
        ("scale", (15, 30)),
    ],
)
def test_each_pin_mode(pin, expected):
    doc = document(
        [
            {
                "id": "child",
                "type": "rect",
                "x": 10,
                "w": 20,
                "h": 10,
                "constraints": {"horizontal": pin},
            }
        ]
    )
    parent(doc).update(w=100, max_w=200)
    result = apply_ops(
        doc,
        [{"op": "resize", "id": "parent", "set": {"w": 150}}],
        version=1,
        expected_version=1,
    )
    child = parent(result.doc)["children"][0]
    assert (child.get("x", 0), child["w"]) == expected


def test_nested_geometry_and_absent_positions_restore_exactly():
    doc = document(
        [
            {
                "id": "nested",
                "type": "frame",
                "w": 50,
                "h": 40,
                "max_w": 70,
                "constraints": {"horizontal": "stretch"},
                "children": [
                    {
                        "id": "leaf",
                        "type": "rect",
                        "w": 10,
                        "h": 10,
                        "constraints": {"horizontal": "end"},
                    }
                ],
            }
        ]
    )
    parent(doc).update(w=100, max_w=200)
    result = apply_ops(
        doc,
        [{"op": "resize", "id": "parent", "set": {"w": 150}}],
        version=1,
        expected_version=1,
    )
    nested = parent(result.doc)["children"][0]
    assert nested["w"] == 70 and nested["children"][0]["x"] == 20
    restored = apply_ops(result.doc, result.undo_ops, version=2, expected_version=2).doc
    assert restored == doc and "x" not in parent(restored)["children"][0]["children"][0]


@pytest.mark.parametrize(
    "changes",
    [
        {"w": 120, "min_w": True},
        {"w": 120, "min_w": "bad"},
        {"w": 120, "min_w": 50, "max_w": 40},
        {"w": 120, "constraints": []},
        {"w": 120, "layout": {"dir": "grid", "columns": True}},
    ],
)
def test_invalid_parent_fields_are_structured_before_geometry_math(changes):
    doc = document()
    with pytest.raises(UiDesignerError) as caught:
        apply_ops(
            doc,
            [{"op": "resize", "id": "parent", "set": changes}],
            version=1,
            expected_version=1,
        )
    assert caught.value.code == "UD-INVALID" and doc == document()


def test_resize_batch_and_unknown_geometry_and_zero_scale_refused():
    doc = document()
    with pytest.raises(UiDesignerError):
        apply_ops(
            doc,
            [
                {"op": "resize", "id": "parent", "set": {"w": 100}},
                {"op": "update", "id": "child", "set": {"x": 3}},
            ],
            version=1,
            expected_version=1,
        )
    parent(doc)["children"][0]["w"] = "hug"
    with pytest.raises(UiDesignerError, match="numeric child"):
        apply_ops(
            doc,
            [{"op": "resize", "id": "parent", "set": {"w": 100}}],
            version=1,
            expected_version=1,
        )
    parent(doc).update(w=0, max_w=200)
    parent(doc)["children"][0].update(w=10, constraints={"horizontal": "scale"})
    with pytest.raises(UiDesignerError, match="positive original"):
        apply_ops(
            doc,
            [{"op": "resize", "id": "parent", "set": {"w": 100}}],
            version=1,
            expected_version=1,
        )


def test_resize_inverse_operation_budget_is_atomic():
    doc = document(
        [
            {
                "id": f"child-{i}",
                "type": "rect",
                "w": 1,
                "h": 1,
                "constraints": {"horizontal": "end"},
            }
            for i in range(128)
        ]
    )
    before = copy.deepcopy(doc)
    with pytest.raises(UiDesignerError, match="128 literal updates"):
        apply_ops(
            doc,
            [{"op": "resize", "id": "parent", "set": {"w": 100}}],
            version=1,
            expected_version=1,
        )
    assert doc == before


async def test_store_receipt_replay_contains_inverse_and_oversize_receipt_is_atomic(
    sf, monkeypatch
):
    async with sf() as session:
        await store.create_document(
            session, slug="resize", owner="user:test", doc=document()
        )
        await session.commit()
    kwargs = {
        "ops": [{"op": "resize", "id": "parent", "set": {"w": 140}}],
        "expected_version": 1,
        "author": "user:test",
        "scope": "user:test",
        "request_id": "resize-one",
    }
    async with sf() as session:
        first = await store.edit_document(session, "resize", **kwargs)
        await session.commit()
    async with sf() as session:
        repeated = await store.edit_document(session, "resize", **kwargs)
        assert repeated["replayed"] and repeated["undo_ops"] == first["undo_ops"]
        monkeypatch.setattr(store, "MAX_RECEIPT_BYTES", 128)
        with pytest.raises(UiDesignerError, match="receipt budget"):
            await store.edit_document(
                session,
                "resize",
                ops=[{"op": "resize", "id": "parent", "set": {"w": 80}}],
                expected_version=2,
                author="user:test",
                scope="user:test",
                request_id="oversized",
            )
        await session.rollback()
    async with sf() as session:
        assert (await store.get_document(session, "resize"))["version"] == 2


@pytest.mark.parametrize('layout',[
    {'dir':'grid','columns':0},{'dir':'grid','columns':13},{'dir':'grid','columns':True},
    {'dir':'grid','row_gap':-1},{'dir':'grid','column_gap':'$space.gap'},
    {'dir':'grid','wrap':False},{'dir':'grid','justify':'start'},
    {'dir':'row','columns':2},{'dir':'column','row_gap':2},
])
def test_grid_schema_rejects_conflicting_or_invalid_controls(layout):
    doc=document();parent(doc)['layout']=layout
    assert S.collect_issues(doc)


def test_bounds_and_constraints_are_closed_and_instance_bounds_merge():
    doc=document();parent(doc)['layout']={'dir':'grid','columns':2,'row_gap':4,'column_gap':8,'pad':[4,8],'align':'stretch'}
    S.validate_document(doc)
    doc['components']={'base':{'id':'base','type':'frame','min_w':50,'max_w':100}}
    doc['pages'][0]['children'].append({'id':'inst','type':'instance','of':'base','min_w':101})
    assert any(issue.code=='UD-VALUE' for issue in S.collect_issues(doc))
    doc['pages'][0]['children'][-1]={'id':'inst','type':'instance','of':'base',
        'overrides':{'constraints':{'horizontal':'end'}}}
    assert any(issue.code=='UD-OVERRIDE' for issue in S.collect_issues(doc))


async def test_folded_resize_history_replays_without_running_pin_algorithm(sf,monkeypatch):
    from sqlalchemy import select
    from agentplatform.db import UiDesignerVersion
    from agentplatform.ui_designer import constraints
    async with sf() as session:
        await store.create_document(session,slug='folded-resize',owner='agent:test',author='agent:test',run_id='run',doc=document())
        await session.commit()
    snapshots={}
    for version,width in [(1,140),(2,80)]:
        async with sf() as session:
            result=await store.edit_document(session,'folded-resize',ops=[{'op':'resize','id':'parent','set':{'w':width}}],
                expected_version=version,author='agent:test',run_id='run')
            snapshots[result['version']]=result['doc']
            await session.commit()
    def future_algorithm(*args,**kwargs):
        raise AssertionError('history replay must not recompute implicit pins')
    monkeypatch.setattr(constraints,'resize_children',future_algorithm)
    async with sf() as session:
        rows=(await session.execute(select(UiDesignerVersion).order_by(UiDesignerVersion.version))).scalars().all()
        assert len(rows)==2 and rows[-1].first_version==2 and rows[-1].version==3
        assert all(op['op']=='update' for chunk in rows[-1].chunks for op in chunk['ops'])
        for version in (2,3):
            assert (await store.get_document(session,'folded-resize',version=version))['doc']==snapshots[version]
