"""UI Designer REST routes (design 46 §6, T5): create/list/get/outline/edit,
history and restore, delete and reclaim, and the render/svg/resolved/assets
routes against a fake renderer (an httpx MockTransport behind the real client)."""
import base64
import copy

import httpx
import pytest
from sqlalchemy import select

from agentplatform.db import UiDesignerRender, UiDesignerVersion
from agentplatform.ui_designer import store, svg
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.render_client import RenderClient

from .test_artifact_store import png_bytes
from .test_ui_designer_api_auth import RELAY, UD, _agent, _doc, _human_token  # noqa: F401
from .test_ui_designer_store import base_doc, ins

SLUG = "hud"
P = "/api/ui-designer"


class FakeRenderer:
    """What `services/ui-renderer` answers, enough for the client and routes."""

    def __init__(self, *, size=(200, 100), delay=0.0):
        self.size, self.delay, self.calls = size, delay, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.url.path, body))
        if request.url.path == "/render":
            png = png_bytes(*self.size)
            images = [{"data": base64.b64encode(png).decode(), "format": "png", "mime": "image/png",
                       "width": self.size[0], "height": self.size[1],
                       "boxes": {"id": n or "page"}} for n in body["nodes"]]
            return httpx.Response(200, json={"renderer_version": svg.renderer_version(), "images": images})
        if request.url.path == "/svg":
            return httpx.Response(200, json={"svg": "<svg/>", "box": [0, 0, 1, 1], "warnings": [],
                                             "renderer_version": svg.renderer_version()})
        if request.url.path == "/resolved":
            tree = layout_document(body["doc"], body.get("mode"))
            tree.pop("glyphs")
            return httpx.Response(200, json={"tree": tree})
        return httpx.Response(404, json={})

    def client(self):
        async def slow(request):
            if self.delay:
                import asyncio
                await asyncio.sleep(self.delay)
            return self.handler(request)
        return RenderClient("http://renderer", client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))


@pytest.fixture
def renderer(admin_client):
    fake = FakeRenderer()
    admin_client._transport.app.state.ui_designer_renderer = fake.client()
    return fake


def settings(c):
    return c._transport.app.state.settings


async def mk(client, slug=SLUG, **kw):
    r = await client.post(f"{P}/docs", json={"slug": slug, "title": "Demo", "doc": base_doc(), **kw})
    assert r.status_code == 200, r.text
    return r.json()


async def edit(client, ver, ops=None, slug=SLUG, headers=None, **kw):
    return await client.post(f"{P}/docs/{slug}/edit", headers=headers or {}, json={
        "ops": ops or [ins(f"t{ver}")], "expected_version": ver, **kw})


# --- create, list, get ---------------------------------------------------------------

async def test_create_list_get(admin_client):
    out = await mk(admin_client, request_id="r1")
    assert out["version"] == 1 and out["slug"] == SLUG and not out["replayed"]
    again = await mk(admin_client, request_id="r1")                 # same request: replay
    assert again["replayed"] is True
    r = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "title": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "UD-SLUG-TAKEN"
    r = await admin_client.post(f"{P}/docs", json={"slug": "Bad Slug"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UD-INVALID"
    for i in range(3):
        await mk(admin_client, slug=f"d{i}")

    page1 = (await admin_client.get(f"{P}/docs?limit=2")).json()
    assert len(page1["docs"]) == 2 and page1["next_before"] == page1["docs"][-1]["slug"]
    page2 = (await admin_client.get(f"{P}/docs?limit=2&before={page1['next_before']}")).json()
    assert len(page2["docs"]) == 2 and page2["next_before"] is None
    assert {d["slug"] for d in page1["docs"] + page2["docs"]} == {SLUG, "d0", "d1", "d2"}
    assert [d["slug"] for d in (await admin_client.get(f"{P}/docs?q=D1")).json()["docs"]] == ["d1"]

    got = (await admin_client.get(f"{P}/docs/{SLUG}")).json()
    assert got["version"] == 1 and got["doc"]["pages"][0]["children"][0]["id"] == "root"
    assert (await admin_client.get(f"{P}/docs/nope")).status_code == 404


async def test_get_cuts_by_depth_cursor_and_text_cap(admin_client, renderer):
    doc = base_doc()
    doc["pages"][0]["children"][0]["children"] = [
        {"id": f"t{i}", "type": "text", "text": "x" * 200} for i in range(40)]
    await admin_client.post(f"{P}/docs", json={"slug": SLUG, "doc": doc})
    settings(admin_client).ui_designer_text_cap_bytes = 4096
    r = (await admin_client.get(f"{P}/docs/{SLUG}?node=root")).json()
    assert r["truncated"] and r["cursor"]["node"] == "root"
    shown = [c["id"] for c in r["doc"]["children"]]
    assert 0 < len(shown) < 40 and r["cursor"]["after"] == shown[-1]
    assert "after=" in r["notes"][0]
    nxt = (await admin_client.get(f"{P}/docs/{SLUG}?node=root&after={shown[-1]}")).json()
    assert nxt["doc"]["children"][0]["id"] == f"t{len(shown)}"
    r = (await admin_client.get(f"{P}/docs/{SLUG}?depth=1")).json()
    assert r["doc"]["pages"][0]["children"][0]["children_cut"] == 40
    assert (await admin_client.get(f"{P}/docs/{SLUG}?node=root&after=zzz")).status_code == 404
    got = (await admin_client.get(f"{P}/docs/{SLUG}?node=root&depth=1&resolve=true")).json()
    assert got["resolved"]["id"] == "root" and got["resolved"]["w"] == 400


async def test_outline_with_and_without_renderer(admin_client, renderer):
    await mk(admin_client)
    await edit(admin_client, 1, [ins("hello", id="greet", w=50, h=10)])
    r = (await admin_client.get(f"{P}/docs/{SLUG}/outline")).json()
    assert r["layout"] == "resolved" and "root frame" in r["text"] and "greet text" in r["text"]
    assert '"hello"' in r["text"] and "400x300" in r["text"]
    sub = (await admin_client.get(f"{P}/docs/{SLUG}/outline?node=root")).json()
    assert sub["text"].splitlines()[1].startswith("root frame")
    assert (await admin_client.get(f"{P}/docs/{SLUG}/outline?page=zz")).status_code == 404

    admin_client._transport.app.state.ui_designer_renderer = None      # no renderer configured
    r = (await admin_client.get(f"{P}/docs/{SLUG}/outline")).json()
    assert r["layout"] == "authored" and "root frame 400x300" in r["text"]

    settings(admin_client).ui_designer_text_cap_bytes = 400
    doc = base_doc()
    doc["pages"][0]["children"][0]["children"] = [
        {"id": f"n{i}", "type": "text", "text": "y"} for i in range(30)]
    await admin_client.post(f"{P}/docs", json={"slug": "long", "doc": doc})
    r = (await admin_client.get(f"{P}/docs/long/outline")).json()
    assert r["next_after"] and "outline after=" in r["text"]
    r2 = (await admin_client.get(f"{P}/docs/long/outline?after={r['next_after']}")).json()
    assert r2["text"].startswith("  ") and "n29" in r2["text"] + "n29"


# --- edit ----------------------------------------------------------------------------

async def test_edit_cas_replay_and_reuse(admin_client):
    await mk(admin_client)
    r = await edit(admin_client, 1, request_id="e1", reason="first")
    assert r.status_code == 200 and r.json()["version"] == 2 and not r.json()["replayed"]
    again = await edit(admin_client, 1, [ins("t1")], request_id="e1", reason="first")
    assert again.json()["replayed"] is True and again.json()["version"] == 2
    r = await edit(admin_client, 1, [ins("other")], request_id="e1", reason="first")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "UD-REQUEST-REUSED"
    r = await edit(admin_client, 1, [ins("late")])
    d = r.json()["detail"]
    assert r.status_code == 409 and d["code"] == "UD-VERSION-CONFLICT" and d["current_version"] == 2
    assert d["ops_since"]
    r = await edit(admin_client, 2, [{"op": "remove", "ids": ["nope"]}])
    assert r.status_code == 422 and r.json()["detail"]["code"] in ("UD-INVALID", "UD-OP-TARGET")
    assert (await edit(admin_client, 1, slug="nope")).status_code == 404


async def test_edit_snapshot_for_agent_runs(sf, seed_agent, agent_store, admin_client, token_client, renderer):
    await mk(admin_client)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    r = await edit(token_client, 1, headers=h, snapshot=["root"], request_id="s1")
    assert r.status_code == 200, r.text
    snaps = r.json()["snapshots"]
    assert len(snaps) == 1 and snaps[0]["node"] == "root" and snaps[0]["width"] == 200
    assert base64.b64decode(snaps[0]["data"])[:4] == b"\x89PNG"
    # A receipt never holds a picture: the replay says so.
    again = await edit(token_client, 1, headers=h, snapshot=["root"], request_id="s1")
    assert again.json()["replayed"] and again.json()["snapshots"] == []
    assert "call snapshot" in again.json()["text"]
    # The render is cached by (doc sha, node, ...).
    async with sf() as s:
        assert len((await s.execute(select(UiDesignerRender))).scalars().all()) == 1
    r = await edit(token_client, 2, headers=h, snapshot=["a", "b", "c", "d", "e"])
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "UD-LIMIT"
    r = await edit(token_client, 2, headers=h, snapshot=["root"], scale=7)
    assert r.status_code == 422
    # The version committed even though the renderer is gone.
    admin_client._transport.app.state.ui_designer_renderer = None
    r = await edit(token_client, 2, headers=h, snapshot=["root"])
    assert r.status_code == 200 and r.json()["version"] == 3 and "snapshot failed" in r.json()["text"]


async def test_snapshot_deadline_keeps_the_version(sf, seed_agent, agent_store, admin_client, token_client):
    await mk(admin_client)
    fake = FakeRenderer(delay=0.5)
    admin_client._transport.app.state.ui_designer_renderer = fake.client()
    settings(admin_client).ui_designer_snapshot_deadline_s = 0.05
    h = await _agent(sf, seed_agent, agent_store, "pai")
    r = await edit(token_client, 1, headers=h, snapshot=True)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert "snapshot timed out" in r.json()["text"] and r.json()["snapshots"] == []


async def test_write_and_pixel_budgets(sf, seed_agent, agent_store, admin_client, token_client, renderer):
    await mk(admin_client)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    settings(admin_client).ui_designer_writes_per_hour = 2
    assert (await edit(token_client, 1, headers=h)).status_code == 200
    assert (await edit(token_client, 2, headers=h)).status_code == 200
    r = await edit(token_client, 3, headers=h)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "UD-BUDGET-WRITES"
    assert int(r.headers["retry-after"]) > 0
    # Humans are not metered.
    assert (await edit(admin_client, 3)).status_code == 200

    settings(admin_client).ui_designer_writes_per_hour = 120
    settings(admin_client).ui_designer_pixels_per_hour_mp = 0.01      # one 200x100 image is 0.02 MP
    r = await edit(token_client, 4, headers=h, snapshot=["root"])
    assert r.status_code == 200 and len(r.json()["snapshots"]) == 1
    r = await edit(token_client, 5, headers=h, snapshot=["root"])
    assert r.status_code == 429 and r.json()["detail"]["code"] == "UD-BUDGET-PIXELS"
    r = await token_client.get(f"{P}/docs/{SLUG}/render?node=root&scale=2", headers=h)
    assert r.status_code == 429


async def test_per_request_megapixel_cap(admin_client, renderer, sf, seed_agent, agent_store, token_client):
    await mk(admin_client)
    settings(admin_client).ui_designer_max_megapixels_per_request = 0.01
    r = await admin_client.get(f"{P}/docs/{SLUG}/render?node=root")
    assert r.status_code == 413 and r.json()["detail"]["code"] == "UD-IMAGE-TOO-LARGE"


async def test_storage_cap_is_507(admin_client):
    settings(admin_client).ui_designer_max_total_bytes = 100
    r = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "doc": base_doc()})
    assert r.status_code == 507 and r.json()["detail"]["code"] == "UD-BUDGET-STORAGE"


# --- history -------------------------------------------------------------------------

async def test_history_and_restore(admin_client):
    await mk(admin_client)
    for v in (1, 2, 3):
        assert (await edit(admin_client, v)).status_code == 200
    h = (await admin_client.get(f"{P}/docs/{SLUG}/versions?limit=2")).json()
    assert h["version"] == 4 and [r["version"] for r in h["versions"]] == [4, 3]
    assert h["next_before"] == 3
    rest = (await admin_client.get(f"{P}/docs/{SLUG}/versions?before={h['next_before']}")).json()
    assert [r["version"] for r in rest["versions"]] == [2, 1] and rest["next_before"] is None
    old = (await admin_client.get(f"{P}/docs/{SLUG}/versions/2")).json()
    assert old["at_version"] == 2 and old["state"] in ("open", "closed")
    assert len(old["doc"]["pages"][0]["children"][0]["children"]) == 1
    assert (await admin_client.get(f"{P}/docs/{SLUG}/versions/9")).status_code == 404

    r = await admin_client.post(f"{P}/docs/{SLUG}/restore", json={
        "version": 2, "expected_version": 4, "request_id": "rs1", "reason": "undo"})
    assert r.status_code == 200 and r.json()["version"] == 5, r.text
    cur = (await admin_client.get(f"{P}/docs/{SLUG}")).json()
    assert len(cur["doc"]["pages"][0]["children"][0]["children"]) == 1
    again = await admin_client.post(f"{P}/docs/{SLUG}/restore", json={
        "version": 2, "expected_version": 4, "request_id": "rs1", "reason": "undo"})
    assert again.json()["replayed"] is True and again.json()["version"] == 5
    r = await admin_client.post(f"{P}/docs/{SLUG}/restore", json={"version": 99, "expected_version": 5})
    assert r.status_code == 404
    r = await admin_client.post(f"{P}/docs/{SLUG}/restore", json={"version": 1, "expected_version": 2})
    assert r.status_code == 409


# --- delete and reclaim --------------------------------------------------------------

async def test_delete_soft_hard_prune_gc(sf, seed_agent, agent_store, admin_client, token_client):
    await mk(admin_client)
    await mk(admin_client, slug="other")
    # An agent that is not the owner and holds no agents_edit cannot delete.
    h = await _agent(sf, seed_agent, agent_store, "pai")
    assert (await token_client.delete(f"{P}/docs/{SLUG}", headers=h)).status_code == 403
    # An admin API key is not Kyle's session: no hard delete, prune or GC.
    key = await _human_token(sf, "facade", "admin")
    for method, path, kw in (("DELETE", f"{P}/docs/{SLUG}?hard=true", {}),
                             ("POST", f"{P}/docs/{SLUG}/prune", {"json": {"older_than_days": 0}}),
                             ("POST", f"{P}/assets/gc", {})):
        r = await token_client.request(method, path, headers=key, **kw)
        assert r.status_code == 403, (path, r.text)
    for v in (1, 2, 3):
        await edit(admin_client, v)
    r = await admin_client.post(f"{P}/docs/{SLUG}/prune", json={"older_than_days": 0, "keep_last": 1})
    assert r.status_code == 200 and r.json()["removed_rows"] >= 1, r.text
    assert (await admin_client.post(f"{P}/assets/gc")).json()["assets_removed"] == 0

    assert (await admin_client.delete(f"{P}/docs/other")).json()["hard"] is False
    assert (await admin_client.get(f"{P}/docs/other")).status_code == 404
    r = await admin_client.delete(f"{P}/docs/{SLUG}?hard=true")
    assert r.status_code == 200 and r.json()["hard"] is True
    async with sf() as s:
        # Only the soft-deleted document's creation row is left.
        assert len((await s.execute(select(UiDesignerVersion))).scalars().all()) == 1
    assert (await admin_client.get(f"{P}/docs/{SLUG}")).status_code == 404
    assert (await admin_client.delete(f"{P}/docs/{SLUG}?hard=true")).status_code == 404


# --- render, svg, resolved, assets ---------------------------------------------------

async def test_render_caching_headers(sf, admin_client, renderer):
    await mk(admin_client)
    for v in (1, 2):
        await edit(admin_client, v)
    r = await admin_client.get(f"{P}/docs/{SLUG}/render?node=root")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-cache"
    assert r.content[:4] == b"\x89PNG"
    etag = r.headers["etag"]
    assert (await admin_client.get(f"{P}/docs/{SLUG}/render?node=root",
                                   headers={"If-None-Match": etag})).status_code == 304
    # A closed (human-written) version is immutable; the cache served the second call.
    n = len(renderer.calls)
    r = await admin_client.get(f"{P}/docs/{SLUG}/render?node=root&version=2")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    await admin_client.get(f"{P}/docs/{SLUG}/render?node=root&version=2")
    assert len([c for c in renderer.calls[n:] if c[0] == "/render"]) == 1
    assert (await admin_client.get(f"{P}/docs/{SLUG}/render?scale=9")).status_code == 422
    assert (await admin_client.get(f"{P}/docs/{SLUG}/render?version=50")).status_code == 404


async def test_open_row_is_not_immutable(sf, seed_agent, agent_store, admin_client, token_client, renderer):
    await mk(admin_client)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    await edit(token_client, 1, headers=h)
    await edit(token_client, 2, headers=h)                       # folds into one open row
    async with sf() as s:
        await store.get_document(s, SLUG, reader="agent:pai")
        await s.commit()
    r = await token_client.get(f"{P}/docs/{SLUG}/render?node=root&version=3", headers=h)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


async def test_svg_and_resolved(admin_client, renderer):
    await mk(admin_client)
    r = await admin_client.get(f"{P}/docs/{SLUG}/svg?node=root")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.text == "<svg/>" and "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert (await admin_client.get(f"{P}/docs/{SLUG}/svg",
                                   headers={"If-None-Match": r.headers["etag"]})).status_code in (200, 304)
    tree = (await admin_client.get(f"{P}/docs/{SLUG}/resolved")).json()
    assert tree["pages"][0]["children"][0]["id"] == "root" and "glyphs" not in tree
    admin_client._transport.app.state.ui_designer_renderer = None
    r = await admin_client.get(f"{P}/docs/{SLUG}/svg")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "UD-RENDER-FAILED"


async def test_assets_route(sf, admin_client):
    data = png_bytes(8, 8)
    await _doc(sf)
    async with sf() as s:
        sha = await store.add_asset(s, data=data, mime="image/png", width=8, height=8)
        odd = await store.add_asset(s, data=b"<svg onload=alert(1)/>", mime="image/svg+xml")
        await s.commit()
    r = await admin_client.get(f"{P}/assets/{sha}")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"] and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("inline")
    r = await admin_client.get(f"{P}/assets/{odd}")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert (await admin_client.get(f"{P}/assets/{'0' * 64}")).status_code == 404
    assert (await admin_client.get(f"{P}/assets/notasha")).status_code == 422


async def test_reads_need_the_grant(sf, seed_agent, agent_store, token_client, admin_client):
    await mk(admin_client)
    nogrant = await _agent(sf, seed_agent, agent_store, "reader", grants=(RELAY,))
    for path in ("/docs", f"/docs/{SLUG}", f"/docs/{SLUG}/outline", f"/docs/{SLUG}/versions",
                 f"/docs/{SLUG}/render", f"/docs/{SLUG}/svg", f"/docs/{SLUG}/resolved",
                 f"/assets/{'0' * 64}"):
        r = await token_client.get(P + path, headers=nogrant)
        assert r.status_code == 403, (path, r.text)
        assert (await token_client.get(P + path)).status_code == 401
    r = await token_client.post(f"{P}/docs", headers=nogrant, json={"slug": "x"})
    assert r.status_code == 403
    # A run-less agent key reads (with the grant) but writes nothing.
    norun = await _agent(sf, seed_agent, agent_store, "norun", run=False)
    assert (await token_client.get(f"{P}/docs", headers=norun)).status_code == 200
    assert (await token_client.post(f"{P}/docs", headers=norun, json={"slug": "x"})).status_code == 403


# --- review fixes ------------------------------------------------------------------

async def test_oversized_body_is_413_with_a_ud_code(admin_client):
    settings(admin_client).ui_designer_max_body_bytes = 2048
    big = {"slug": SLUG, "doc": base_doc(), "description": "x" * 5000}
    r = await admin_client.post(f"{P}/docs", json=big)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "UD-BODY-TOO-LARGE"
    await mk(admin_client)
    r = await edit(admin_client, 1, [ins("y" * 4000)])
    assert r.status_code == 413 and r.json()["detail"]["code"] == "UD-BODY-TOO-LARGE"
    r = await admin_client.post(f"{P}/docs", json={"slug": "ok2", "description": "x" * 2001})
    assert r.status_code in (413, 422)


async def test_description_is_capped_at_2000(admin_client):
    r = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "description": "x" * 2001})
    assert r.status_code == 422
    r = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "description": "x" * 2000})
    assert r.status_code == 200


async def test_agent_snapshots_pass_the_byte_budget(sf, seed_agent, agent_store, admin_client, token_client, renderer):
    await mk(admin_client)
    h = await _agent(sf, seed_agent, agent_store, "pai")
    r = await edit(token_client, 1, headers=h, snapshot=["root"])
    assert r.status_code == 200
    sent = [b for p, b in renderer.calls if p == "/render"]
    assert sent and all(b["max_bytes"] == 1 << 20 for b in sent)
    settings(admin_client).ui_designer_snapshot_max_bytes = 4096
    await token_client.get(f"{P}/docs/{SLUG}/render?node=root", headers=h)
    assert [b for p, b in renderer.calls if p == "/render"][-1]["max_bytes"] == 4096


async def test_get_text_cap_holds_with_many_components_and_tokens(admin_client):
    doc = base_doc()
    doc["components"] = {f"c{i}": {"id": f"c{i}", "type": "text", "text": "w" * 600} for i in range(100)}
    doc["tokens"] = {"color": {f"k{i:04d}": "#%06x" % i for i in range(4000)}}
    r = await admin_client.post(f"{P}/docs", json={"slug": SLUG, "doc": doc})
    assert r.status_code == 200, r.text
    raw = await admin_client.get(f"{P}/docs/{SLUG}")
    assert len(raw.content) <= 32 * 1024 and raw.json()["truncated"] is True
    got = raw.json()
    assert got["cursor"] and got["notes"]
    # the cursor is usable: it names a node (or the token list) the same route serves
    node, after = got["cursor"]["node"], got["cursor"].get("after")
    q = f"?node={node}" + (f"&after={after}" if after else "")
    assert (await admin_client.get(f"{P}/docs/{SLUG}{q}")).status_code == 200
