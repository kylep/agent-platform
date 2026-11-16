"""The `ui_designer` broker tool (docs/design/46 T6): each action's request to
the API, pictures from `_with_pictures`, the 45 s timeout and the audit row.
Fakes come from `test_artifacts_tool.py` (a `_request` table).

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q -k ui_designer"""
import asyncio
import base64
import inspect
import json

import pytest
from test_artifacts_tool import api, images_of, text_of  # noqa: F401
from test_relay_tool import broker, caller, published  # noqa: F401

DOCS = "/api/ui-designer/docs"
PNG = b"\x89PNG" + b"p" * 30
ART = "ab" * 16


def ud(**kw):
    return asyncio.run(broker.ui_designer(**kw))


def timeout_of(api, path):
    return [t for p, t in api.timeouts if p == path][-1]


def test_list_is_one_line_per_document(api):
    api.replies[DOCS] = (200, json.dumps({"docs": [
        {"slug": "hud", "title": "HUD", "version": 3, "owner": "agent:pai", "updated_at": "t"}]}))
    assert ud(action="list", q="h", limit=5) == "hud · HUD · v3 · agent:pai · t"
    assert api[-1] == ("GET", DOCS, {"q": "h", "limit": 5}, None)
    assert timeout_of(api, DOCS) == broker._UI_DESIGNER_TIMEOUT == 45


def test_create_forwards_request_id(api):
    api.replies[DOCS] = (200, json.dumps({"slug": "hud", "version": 1, "text": "created"}))
    out = ud(action="create", slug="hud", title="HUD", reason="start", request_id="r-1")
    assert out.startswith("created hud v1")
    assert api[-1][3] == {"slug": "hud", "title": "HUD", "reason": "start", "request_id": "r-1"}


def test_outline_returns_text_and_cursor(api):
    api.replies[f"{DOCS}/hud/outline"] = (200, json.dumps(
        {"text": "# hud v1", "next_after": "n7"}))
    out = ud(action="outline", slug="hud", page="p1")
    assert out.startswith("# hud v1") and "after=n7" in out
    assert api[-1][2]["page"] == "p1"


def test_get_named_version_uses_versions_route(api):
    ud(action="get", slug="hud", version=2, node="n1", resolve=True)
    assert api[-1][:2] == ("GET", f"{DOCS}/hud/versions/2")
    assert api[-1][2]["resolve"] == "true"


def test_edit_attaches_snapshots_as_images(api):
    api.replies[f"{DOCS}/hud/edit"] = (200, json.dumps({
        "slug": "hud", "version": 4, "text": "ok", "warnings": [
            {"code": "overflow", "node": "t1", "message": "text overflows"}],
        "snapshots": [{"node": "hud", "mime": "image/png", "width": 100, "height": 50,
                       "data": base64.b64encode(PNG).decode()}]}))
    out = ud(action="edit", slug="hud", ops=[{"op": "x"}], expected_version=3,
             reason="r", request_id="r-2", snapshot=["hud"])
    assert "edited hud v4" in text_of(out) and "warning: overflow t1 text overflows" in text_of(out)
    assert "snapshot hud 100×50" in text_of(out)
    assert [base64.b64decode(i.data) for i in images_of(out)] == [PNG]
    body = api[-1][3]
    assert body["snapshot"] == ["hud"] and body["expected_version"] == 3
    assert timeout_of(api, f"{DOCS}/hud/edit") == 45


def test_edit_replay_says_so_and_has_no_pictures(api):
    api.replies[f"{DOCS}/hud/edit"] = (200, json.dumps(
        {"slug": "hud", "version": 4, "replayed": True, "snapshots": []}))
    out = ud(action="edit", slug="hud", ops=[{}], expected_version=3, request_id="r-2")
    assert isinstance(out, str) and "replayed" in out


def test_edit_validates_arguments_without_calling(api):
    assert ud(action="edit", slug="hud", expected_version=1).startswith("error:")
    assert ud(action="edit", slug="hud", ops=[{}]).startswith("error:")
    assert ud(action="edit", slug="hud", ops=[{}], expected_version=1,
              snapshot=list("abcde")).startswith("error:")
    assert ud(action="get", slug="../x").startswith("error:")
    assert len(api) == 0


def test_snapshot_renders_each_node_as_an_image(api):
    api.replies[f"{DOCS}/hud/render"] = (200, PNG, "image/png")
    out = ud(action="snapshot", slug="hud", nodes=["a", "b"], scale=2)
    assert len(images_of(out)) == 2
    assert api[-1][2]["scale"] == 2 and api[-1][2]["annotate"] is None


def test_snapshot_refusal_is_text(api):
    api.replies[f"{DOCS}/hud/render"] = (429, '{"detail": "UD-BUDGET-PIXELS"}')
    out = ud(action="snapshot", slug="hud", nodes=["a"])
    assert isinstance(out, str) and "UD-BUDGET-PIXELS" in out


def test_history_and_restore(api):
    api.replies[f"{DOCS}/hud/versions"] = (200, json.dumps({
        "slug": "hud", "version": 5, "next_before": 2,
        "versions": [{"first_version": 3, "last_version": 5, "author": "agent:pai",
                      "reasons": ["a", "b"]}]}))
    out = ud(action="history", slug="hud")
    assert "hud is at v5" in out and "v3-v5 · agent:pai · a / b" in out and "before=2" in out
    api.replies[f"{DOCS}/hud/restore"] = (200, json.dumps({"slug": "hud", "version": 6}))
    assert ud(action="restore", slug="hud", version=2, expected_version=5,
              request_id="r-3").startswith("restored v2 as hud v6")
    assert api[-1][3] == {"version": 2, "expected_version": 5, "request_id": "r-3"}
    assert ud(action="restore", slug="hud", version=2).startswith("error:")


def test_place_returns_the_sha(api):
    api.replies[f"{DOCS}/hud/place"] = (200, json.dumps(
        {"sha256": "f" * 64, "bytes": 2048, "mime": "image/png"}))
    assert "sha256 " + "f" * 64 in ud(action="place", slug="hud", artifact_id=ART)
    assert api[-1][3] == {"artifact_id": ART}
    assert ud(action="place", slug="hud", artifact_id="nope").startswith("error:")


def test_unknown_action_and_api_refusal(api):
    assert ud(action="nope").startswith("error: action must be one of")
    api.replies[f"{DOCS}/hud/outline"] = (404, '{"detail": "unknown document"}')
    assert ud(action="outline", slug="hud").startswith("error:")


def test_with_pictures_keeps_text_only_a_string_and_caps_at_four():
    assert broker._with_pictures(["a"], []) == "a"
    out = broker._with_pictures(["a"], [(PNG, "image/png", f"c{i}") for i in range(6)])
    assert len(images_of(out)) == 4


def test_audit_row_and_signature(api, published):
    api.replies[f"{DOCS}/hud/outline"] = (200, json.dumps({"text": "x"}))
    ud(action="outline", slug="hud")
    data = published[-1][1]["data"]
    assert (data["tool"], data["action"], data["decision"]) == ("ui_designer", "outline", "allow")
    assert list(inspect.signature(broker.ui_designer).parameters)[:2] == ["action", "slug"]
