"""Boolean planning never weakens CAS, durable receipts or asset scope."""
from types import SimpleNamespace

import pytest

from agentplatform.ui_designer import store
from agentplatform.ui_designer.errors import UiDesignerError, UD_RENDER_FAILED
from .test_ui_designer_api import P, SLUG, edit, mk
from .test_artifact_store import png_bytes


def scene():
    return {"schema": 1, "pages": [{"id": "p", "children": [{"id": "root", "type": "frame", "w": 200, "h": 120,
        "children": [{"id": "a", "type": "rect", "w": 100, "h": 100, "fill": "#ff0000"},
                     {"id": "b", "type": "rect", "x": 50, "w": 100, "h": 100, "fill": "#00ff00"}]}]}]}


OP = {"op": "boolean", "kind": "union", "ids": ["a", "b"]}
GEOMETRY = {"geometry": {"x": 0, "y": 0, "w": 150, "h": 100, "d": "M0 0H150V100H0Z", "empty": False}, "warnings": []}


@pytest.fixture
def worker(admin_client):
    state = SimpleNamespace(calls=[], before=None, failure=None)

    async def boolean(kind, operands):
        state.calls.append((kind, operands))
        if state.before:
            await state.before()
        if state.failure:
            raise state.failure
        return GEOMETRY

    admin_client._transport.app.state.ui_designer_renderer = SimpleNamespace(boolean_geometry=boolean)
    return state


async def test_saved_boolean_recipe_replays_before_worker_and_exact_undo(admin_client, worker):
    original = scene()
    await mk(admin_client, doc=original)
    saved = await edit(admin_client, 1, [OP], request_id="boolean-one")
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["version"] == 2 and result["undo_ops"]
    assert len(worker.calls) == 1
    worker.failure = UiDesignerError(UD_RENDER_FAILED, "renderer is offline")
    replay = await edit(admin_client, 1, [OP], request_id="boolean-one")
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] and replay.json()["undo_ops"] == result["undo_ops"]
    assert len(worker.calls) == 1
    reused = await edit(admin_client, 1, [{**OP, "kind": "subtract"}], request_id="boolean-one")
    assert reused.status_code == 409 and reused.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    restored = await edit(admin_client, 2, result["undo_ops"], request_id="boolean-undo")
    assert restored.status_code == 200, restored.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}?depth=5")).json()["doc"]
    assert current["pages"] == original["pages"]


async def test_stale_boolean_fails_before_worker(admin_client, worker):
    await mk(admin_client, doc=scene())
    assert (await edit(admin_client, 1, [{"op": "update", "id": "a", "set": {"title": "New"}}])).status_code == 200
    response = await edit(admin_client, 1, [OP], request_id="stale")
    assert response.status_code == 409 and response.json()["detail"]["current_version"] == 2
    assert worker.calls == []


async def test_concurrent_edit_during_geometry_preserves_new_head(admin_client, worker):
    await mk(admin_client, doc=scene())

    async def other_writer():
        response = await edit(admin_client, 1, [{"op": "update", "id": "a", "set": {"title": "Concurrent"}}], request_id="other-writer")
        assert response.status_code == 200, response.text

    worker.before = other_writer
    response = await edit(admin_client, 1, [OP], request_id="racing-boolean")
    assert response.status_code == 409, response.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}?depth=5")).json()
    children = current["doc"]["pages"][0]["children"][0]["children"]
    assert current["version"] == 2 and len(children) == 2 and children[0]["title"] == "Concurrent"


async def test_renderer_failure_does_not_mutate_document(admin_client, worker):
    await mk(admin_client, doc=scene())
    worker.failure = UiDesignerError(UD_RENDER_FAILED, "bounded worker failed")
    response = await edit(admin_client, 1, [OP], request_id="failed-worker")
    assert response.status_code == 502 and response.json()["detail"]["code"] == UD_RENDER_FAILED, response.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}?depth=5")).json()
    assert current["version"] == 1 and current["doc"]["pages"] == scene()["pages"]


async def test_placed_asset_list_is_scoped_paginated_and_includes_unused(sf, admin_client):
    await mk(admin_client)
    await mk(admin_client, slug="other")
    async with sf() as session:
        expected = []
        for size in (2, 3):
            asset = await store.place_asset(session, SLUG, data=png_bytes(size, size), mime="image/png", placed_by="user:admin", placed_from="test", width=size, height=size)
            expected.append(asset["sha256"])
        foreign = await store.place_asset(session, "other", data=png_bytes(4, 4), mime="image/png", placed_by="user:admin", placed_from="test", width=4, height=4)
        await session.commit()
    response = await admin_client.get(f"{P}/docs/{SLUG}/assets?version=1&limit=1")
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["version"] == 1 and len(first["assets"]) == 1 and first["next_cursor"]
    second = (await admin_client.get(f"{P}/docs/{SLUG}/assets?version=1&limit=1&cursor={first['next_cursor']}")).json()
    actual = [first["assets"][0]["sha256"], second["assets"][0]["sha256"]]
    assert actual == sorted(expected) and foreign["sha256"] not in actual and second["next_cursor"] is None
    assert (await admin_client.get(f"{P}/docs/{SLUG}/assets?version=999")).status_code == 404
