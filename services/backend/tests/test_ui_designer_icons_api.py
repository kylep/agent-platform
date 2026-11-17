"""Pinned icon edits preserve CAS and replay original inputs before catalog access."""
import sys
from types import SimpleNamespace

import pytest

from .test_ui_designer_api import P, SLUG, edit, mk

SOURCE = {"name": "Lucide", "version": "1.73.0", "commit": "a" * 40, "license": "ISC"}
SUMMARY = {"id": "circle", "name": "Circle", "tags": ["shape"], "categories": ["shapes"]}
OP = {"op": "icon", "icon": "circle", "revision": 1, "parent": "p", "id": "placed"}


@pytest.fixture
def catalog(monkeypatch):
    state = SimpleNamespace(calls=0, offline=False)

    def plan(doc, op):
        state.calls += 1
        if state.offline:
            raise AssertionError("receipt replay must not read catalog")
        return {"ops": [{"op": "insert", "parent": op["parent"], "node": {
            "id": op["id"], "type": "frame", "w": 24, "h": 24, "children": [
                {"id": "icon-path", "type": "path", "d": "M0 0H24V24H0Z"}]}},
            {"op": "define_component", "from": op["id"], "id": "icon-circle"}],
            "undo_ops": [{"op": "remove", "ids": [op["id"], "icon-circle"]}], "warnings": []}

    def search(**kwargs):
        return {"revision": 1, "source": SOURCE, "icons": [SUMMARY], "total": 1, "next_cursor": None}

    def detail(icon_id, revision):
        return {**SUMMARY, "revision": revision, "source": SOURCE, "view_box": [0, 0, 24, 24], "d": "M0 0H24", "paths": [{"d": "M0 0H24", "fill": False}]}

    monkeypatch.setitem(sys.modules, "agentplatform.ui_designer.icons", SimpleNamespace(
        plan_icon=plan, search_icons=search, get_icon=detail))
    return state


async def test_catalog_discovery_and_closed_response(admin_client, catalog):
    response = await admin_client.get(f"{P}/icons?q=shape&limit=1")
    assert response.status_code == 200, response.text
    assert response.json()["icons"] == [SUMMARY]
    detail = await admin_client.get(f"{P}/icons/circle?revision=1")
    assert detail.status_code == 200 and detail.json()["view_box"] == [0, 0, 24, 24]
    assert (await admin_client.get(f"{P}/icons/circle")).status_code == 422
    assert (await admin_client.get(f"{P}/icons?limit=101")).status_code == 422


async def test_icon_requires_receipt_and_replays_before_catalog_with_exact_undo(admin_client, catalog):
    await mk(admin_client, doc={"schema": 1, "pages": [{"id": "p", "children": []}]})
    missing = await edit(admin_client, 1, [OP])
    assert missing.status_code == 422 and catalog.calls == 0
    saved = await edit(admin_client, 1, [OP], request_id="icon-one")
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.json()["undo_ops"]
    catalog.offline = True
    replay = await edit(admin_client, 1, [OP], request_id="icon-one")
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert catalog.calls == 1
    reused = await edit(admin_client, 1, [{**OP, "revision": 2}], request_id="icon-one")
    assert reused.status_code == 409 and reused.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    undo = await edit(admin_client, 2, saved.json()["undo_ops"], request_id="undo-icon")
    assert undo.status_code == 200, undo.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}?depth=5")).json()["doc"]
    assert not current.get("components") and not current["pages"][0].get("children")


async def test_stale_or_mixed_icon_refused_before_catalog(admin_client, catalog):
    await mk(admin_client, doc={"schema": 1, "pages": [{"id": "p", "children": []}]})
    response = await edit(admin_client, 1, [OP, {"op": "update", "id": "p", "set": {"title": "x"}}], request_id="mixed")
    assert response.status_code == 422 and catalog.calls == 0
    assert (await edit(admin_client, 1, [{"op": "update", "id": "p", "set": {"title": "x"}}])).status_code == 200
    stale = await edit(admin_client, 1, [OP], request_id="stale-icon")
    assert stale.status_code == 409 and catalog.calls == 0


async def test_real_catalog_insert_reuses_native_definition_and_replays(admin_client):
    await mk(admin_client, doc={"schema": 1, "pages": [{"id": "p", "children": []}]})
    detail = await admin_client.get(f"{P}/icons/arrow-right?revision=1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["paths"] and detail.json()["source"]["commit"]
    first = await edit(admin_client, 1, [{**OP, "icon": "arrow-right", "size": 48}], request_id="real-icon-1")
    assert first.status_code == 200, first.text
    second = await edit(admin_client, 2, [{**OP, "icon": "arrow-right", "size": 48, "id": "again"}], request_id="real-icon-2")
    assert second.status_code == 200, second.text
    current = (await admin_client.get(f"{P}/docs/{SLUG}?depth=5")).json()["doc"]
    assert len(current["components"]) == 1
    nodes = current["pages"][0]["children"]
    assert nodes[0]["of"] == nodes[1]["of"] and nodes[0]["w"] == 48
    replay = await edit(admin_client, 1, [{**OP, "icon": "arrow-right", "size": 48}], request_id="real-icon-1")
    assert replay.status_code == 200 and replay.json()["replayed"]
