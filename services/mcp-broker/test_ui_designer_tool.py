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
        "versions": [{"first_version": 3, "version": 5, "author": "agent:pai",
                      "reasons": ["a", "b"]}]}))
    out = ud(action="history", slug="hud")
    assert "hud is at v5" in out and "v3\u2013v5 (3 writes) · agent:pai · a / b" in out and "before=2" in out
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


def test_history_folded_row_shows_range_and_never_a_bare_slash(api):
    api.replies[f"{DOCS}/hud/versions"] = (200, json.dumps({
        "slug": "hud", "version": 3, "next_before": None,
        "versions": [{"first_version": 2, "version": 3, "author": "agent:pai",
                      "label": None, "reasons": ["", ""]},
                     {"first_version": 1, "version": 1, "author": "agent:pai", "reasons": [""]}]}))
    lines = ud(action="history", slug="hud").splitlines()
    assert lines[0] == "hud is at v3"
    assert lines[1] == "v2\u2013v3 (2 writes) \u00b7 agent:pai"
    assert lines[2] == "v1 \u00b7 agent:pai"
    assert all(" / " not in line and not line.endswith("/") for line in lines)


def test_docstring_carries_the_cheat_sheet():
    doc = inspect.getdoc(broker.ui_designer)
    assert "set_doc" in doc and "there is no add_page op" in doc and "layout is an OBJECT" in doc


def test_inspect_forwards_node_and_mode(api):
    api.replies[f"{DOCS}/hud/inspect"] = (200, json.dumps(
        {"id": "t1", "type": "text", "text": "hi", "tokens": ["$color.ink"]}))
    out = ud(action="inspect", slug="hud", node="t1", mode="dark")
    assert json.loads(out)["tokens"] == ["$color.ink"]
    assert api[-1][:3] == ("GET", f"{DOCS}/hud/inspect", {"node": "t1", "mode": "dark", "version": None})
    assert ud(action="inspect", slug="hud").startswith("error: action='inspect' requires node")


def test_comments_list_add_resolve(api):
    cid = "c" * 32
    api.replies[f"{DOCS}/hud/comments"] = (200, json.dumps({"comments": [
        {"id": cid, "author": "user:kyle", "node_id": "t1", "x": 1, "y": 2, "body": "bigger",
         "resolved_at": None}]}))
    out = ud(action="comments", slug="hud", op="list")
    assert out == f"{cid} user:kyle@t1 (1,2): bigger"
    api.replies[f"{DOCS}/hud/comments"] = (200, json.dumps({"id": cid, "replayed": False}))
    out = ud(action="comments", slug="hud", op="add", body="ok", node="t1", request_id="r9")
    assert out == f"comment {cid} added to hud"
    assert api[-1][3] == {"body": "ok", "node_id": "t1", "request_id": "r9"}
    api.replies[f"{DOCS}/hud/comments/{cid}/resolve"] = (200, json.dumps({"id": cid, "resolved": True}))
    assert ud(action="comments", slug="hud", op="resolve", comment_id=cid) == f"resolved thread {cid}"
    assert "requires body" in ud(action="comments", slug="hud", op="add")
    assert "requires comment_id" in ud(action="comments", slug="hud", op="resolve", comment_id="x")
    assert "list|add|resolve" in ud(action="comments", slug="hud", op="zap")


def test_history_diff(api):
    api.replies[f"{DOCS}/hud/diff"] = (200, json.dumps(
        {"from_version": 1, "to_version": 3, "text": "v2 r: insert(root)", "truncated": False}))
    out = ud(action="history", slug="hud", op="diff", from_version=1, to_version=3)
    assert out == "hud v1 -> v3\nv2 r: insert(root)"
    assert api[-1][2] == {"from": 1, "to": 3}
    assert "requires from_version" in ud(action="history", slug="hud", op="diff")


def test_export_reports_the_card(api):
    api.replies[f"{DOCS}/hud/export"] = (200, json.dumps({
        "version": 2, "name": "hud.png", "width": 400, "height": 300, "size": 2048,
        "artifact_id": ART, "card": f"[[artifact:{ART}]]", "url": f"/artifacts/{ART}",
        "card_posted": True}))
    out = ud(action="export", slug="hud", node="root", scale=2, name="hud")
    assert f"[[artifact:{ART}]]" in out and "posted to #design" in out and "hud.png" in out
    assert api[-1][3] == {"node": "root", "name": "hud", "scale": 2, "format": "png"}


def test_export_format_is_forwarded_to_api(api):
    ud(action='export', slug='hud', node='root', format='html')
    assert api[-1][3]['format'] == 'html'
    assert api[-1][3]['node'] == 'root'


def test_history_visual_diff_is_image_content_not_base64_text(api):
    png=b"\x89PNG\r\n\x1a\n"+b'p'*30
    encoded=base64.b64encode(png).decode()
    api.replies[f'{DOCS}/hud/visual-diff']=(200,json.dumps({
        'image':encoded,'base_image':encoded,'mime':'image/png','width':20,'height':10,
        'changed_pixels':20,'total_pixels':200,'box':[0,0,20,10],
        'regions':[{'x':0,'y':0,'w':2,'h':10,'node_ids':['button']}],
        'node_ids':['button'],'truncated':False,'warnings':[]}))
    out=ud(action='history',slug='hud',op='visual_diff',from_version=1,to_version=2,node='root',tolerance=3)
    assert images_of(out)[0].data==encoded
    assert encoded not in text_of(out)
    assert '20/200 changed pixels' in text_of(out) and 'button' in text_of(out)
    assert api[-1]==('POST',f'{DOCS}/hud/visual-diff',None,{
        'from_version':1,'to_version':2,'scale':1,'tolerance':3,'node':'root'})
    assert 'requires from_version' in ud(action='history',slug='hud',op='visual_diff')
    assert 'integer from 0 to 255' in ud(action='history',slug='hud',op='visual_diff',from_version=1,to_version=2,tolerance=True)


def test_lint_read_only_pinned_page_and_cursor(api):
    payload={'slug':'hud','version':2,'rules_version':'lint-1','findings':[
        {'code':'UD-LINT-CONTRAST','severity':'warning','node_id':'text','mode':None,
         'message':'Low contrast','suggestion':'Increase contrast','approximate':True}],
        'total':2,'by_severity':{'warning':2},'next_cursor':'opaque-next','truncated':False}
    api.replies[f'{DOCS}/hud/lint']=(200,json.dumps(payload))
    out=ud(action='lint',slug='hud',version=2,node='root',all_modes=True,severity='warning',limit=20,after='opaque-prev')
    assert json.loads(out)==payload
    assert api[-1]==('GET',f'{DOCS}/hud/lint',{'version':2,'node':'root','all_modes':'true',
        'severity':'warning','limit':20,'cursor':'opaque-prev'},None)
    assert 'explicit retained version' in ud(action='lint',slug='hud')
    assert 'choose mode' in ud(action='lint',slug='hud',version=2,all_modes=True,mode='dark')


def test_library_actions_use_small_specs_and_receipt_inputs(api):
    spec={'source_id':'a'*32,'source_version':2,'namespace':'shared','components':['button']}
    ud(action='library',op='import',slug='hud',library=spec,expected_version=4,request_id='lib-import')
    assert api[-1]==('POST',f'{DOCS}/hud/libraries/import',None,{
        'library':spec,'expected_version':4,'request_id':'lib-import'})
    ud(action='library',op='preview',slug='hud',library={'library_id':'shared','source_version':3})
    assert api[-1]==('POST',f'{DOCS}/hud/libraries/shared/preview',None,{'source_version':3})
    ud(action='library',op='apply',slug='hud',library={'library_id':'shared','source_version':3},expected_version=5,request_id='lib-apply')
    assert api[-1][3]=={'source_version':3,'expected_version':5,'request_id':'lib-apply'}
    ud(action='library',slug='hud',version=2)
    assert api[-1]==('GET',f'{DOCS}/hud/libraries',{'version':2},None)
    assert 'expected_version and request_id' in ud(action='library',op='import',slug='hud',library=spec)
    assert 'library_id must' in ud(action='library',op='preview',slug='hud',library={'library_id':'../../secret','source_version':3})


def test_templates_catalogue_and_explicit_create_receipt(api):
    ud(action='templates')
    assert api[-1]==('GET','/api/ui-designer/templates',None,None)
    ud(action='create',slug='starter',template={'id':'desk-buddy','revision':1},request_id='template-one')
    assert api[-1]==('POST',DOCS,None,{'slug':'starter','title':'','request_id':'template-one',
        'template_id':'desk-buddy','template_revision':1})
    assert 'request_id' in ud(action='create',slug='starter',template={'id':'desk-buddy','revision':1})
    assert 'choose doc or template' in ud(action='create',slug='starter',template={'id':'desk-buddy'})


def test_icons_search_and_pinned_detail_without_document_slug(api):
    ud(action='icons', q='arrow', limit=24, after='page-two')
    assert api[-1] == ('GET', '/api/ui-designer/icons',
                       {'q': 'arrow', 'limit': 24, 'cursor': 'page-two'}, None)
    ud(action='icons', icon={'id': 'arrow-right', 'revision': 1})
    assert api[-1] == ('GET', '/api/ui-designer/icons/arrow-right', {'revision': 1}, None)
    before = len(api)
    assert 'error:' in ud(action='icons', icon={'id': '../bad', 'revision': 1})
    assert 'error:' in ud(action='icons', icon={'id': 'arrow', 'revision': True})
    assert len(api) == before


def test_rapid_ui_tool_rejects_writes_and_other_documents_before_api(api,monkeypatch,published):
    async def whoami():
        return {'agent':'pai','run_id':'r1','tools':['mcp__platform__ui_designer'],
                'rapid':True,'rapid_ui_slug':'hud'}
    monkeypatch.setattr(broker,'_whoami',whoami)
    for action,slug in [('edit','hud'),('list',None),('get','other'),('export','hud')]:
        assert 'only read its UI sink' in ud(action=action,slug=slug)
    assert not api
    assert published[-1][1]['data']['decision']=='deny:rapid-scope'
    ud(action='outline',slug='hud')
    assert api[-1][0:2]==('GET',f'{DOCS}/hud/outline')
