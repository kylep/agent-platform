"""The artifacts REST surface (docs/design/23 T2).

The store's rules are tested in test_artifact_store.py; what is held down here
is the seam — the owner is the token's participant and never the body's, an
agent writes only from its own run, deleting is the owner's or an editor's,
the two upload shapes land the same row — and the serving headers, which are
the whole of the browser-facing trust boundary: nosniff always, inline only
for the four rasters, a thumb only for an image.
"""
import base64
import io
import json

from PIL import Image

from agentplatform.db import Artifact
from agentplatform.events import TOPIC_ARTIFACTS_EVENTS

from .test_artifact_store import bomb_png, jpeg_bytes, png_bytes
from .test_relay_api import _agent_token, _human_token, _seed, token_client  # noqa: F401
from .test_wiki_api import _run_id

EDIT_GRANT = "mcp__platform__agents_edit"


async def upload(client, data: bytes, name="pic.png", mime="image/png", *,
                 headers=None, **fields) -> dict:
    form = {k: (json.dumps(v) if not isinstance(v, str) else v) for k, v in fields.items()}
    r = await client.post("/api/artifacts", files={"file": (name, data, mime)},
                          data=form, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


async def _agent_headers(sf, seed_agent, agent_store, name: str, *, run: bool = True,
                         grants=("mcp__platform__relay", "mcp__platform__artifacts")) -> dict:
    await _seed(seed_agent, agent_store, name, platform_tools=list(grants))
    return await _agent_token(sf, name, run_id=await _run_id(sf, name) if run else None)


# --- creating ------------------------------------------------------------------

async def test_multipart_png_upload(admin_client, producer):
    data = png_bytes(64, 32)
    a = await upload(admin_client, data, tags=["ops", "ops"])
    assert a["kind"] == "image" and a["mime"] == "image/png"
    assert (a["width"], a["height"], a["size"]) == (64, 32, len(data))
    assert len(a["sha256"]) == 64 and a["tags"] == ["ops"]
    assert a["owner"] == "user:admin" and a["source"] == "upload"
    assert a["run_id"] is None and a["deleted_at"] is None
    assert a["thumb_url"] == f"/api/artifacts/{a['id']}/thumb"
    assert a["content_url"] == f"/api/artifacts/{a['id']}/content"
    assert "thumb" not in a
    evs = [e for e in producer.envelopes if e["type"] == "artifacts.event"]
    assert len(evs) == 1 and evs[0]["data"]["event"] == "created"
    assert (TOPIC_ARTIFACTS_EVENTS, a["id"]) == producer.published[-1][:2]


async def test_json_upload_shapes(admin_client):
    r = await admin_client.post("/api/artifacts", json={
        "name": "notes.md", "mime": "text/markdown", "text": "# hi\n", "tags": ["doc"]})
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["mime"], a["kind"], a["size"], a["name"]) == ("text/markdown", "file", 5, "notes.md")

    r = await admin_client.post("/api/artifacts", json={
        "name": "pic.png", "content_b64": base64.b64encode(png_bytes()).decode()})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "image"

    # Plain text with no claim is text/plain; garbage is a 400.
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x"})
    assert r.status_code == 201 and r.json()["mime"] == "text/plain"
    r = await admin_client.post("/api/artifacts", json={"name": "a", "content_b64": "%%%"})
    assert r.status_code == 400, r.text
    r = await admin_client.post("/api/artifacts", json={"name": "a"})
    assert r.status_code == 422, r.text
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x",
                                                        "content_b64": "eA=="})
    assert r.status_code == 422, r.text
    # `generated` is the generate route's alone.
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x",
                                                        "source": "generated"})
    assert r.status_code == 422, r.text
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x",
                                                        "source": "tool",
                                                        "meta": {"tool": "fetch"}})
    assert r.status_code == 201 and r.json()["source"] == "tool"


async def test_the_body_never_names_the_owner(admin_client):
    a = await upload(admin_client, b"data", "x.bin", "application/octet-stream",
                     owner="agent:evil", source="generated")
    assert a["owner"] == "user:admin" and a["source"] == "upload"
    r = await admin_client.post("/api/artifacts", json={
        "name": "a", "text": "x", "owner": "agent:evil"})
    assert r.status_code == 201 and r.json()["owner"] == "user:admin"


async def test_an_agent_writes_as_itself_from_its_run(sf, seed_agent, agent_store,
                                                     token_client, admin_client):
    no_run = await _agent_headers(sf, seed_agent, agent_store, "pai", run=False)
    r = await token_client.post("/api/artifacts", json={"name": "a", "text": "x"},
                                headers=no_run)
    assert r.status_code == 403, r.text

    with_run = await _agent_headers(sf, seed_agent, agent_store, "news")
    a = await upload(token_client, png_bytes(), headers=with_run)
    assert a["owner"] == "agent:news" and a["run_id"]
    # ...and reads what it wrote, as does a human reader.
    r = await token_client.get(f"/api/artifacts/{a['id']}", headers=with_run)
    assert r.status_code == 200 and r.json()["owner"] == "agent:news"
    reader = await _human_token(sf, "kyle", "reader")
    assert (await token_client.get("/api/artifacts", headers=reader)).status_code == 200
    assert (await token_client.post("/api/artifacts", json={"name": "a", "text": "x"},
                                    headers=reader)).status_code == 403
    assert (await token_client.get("/api/artifacts")).status_code == 401


async def test_a_derived_artifact_names_its_parent(admin_client):
    parent = await upload(admin_client, png_bytes())
    r = await admin_client.post("/api/artifacts", json={
        "name": "marked.png", "content_b64": base64.b64encode(png_bytes(8, 8)).decode(),
        "meta": {"parent_id": parent["id"], "operation": "markup"}})
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "derived" and r.json()["meta"]["parent_id"] == parent["id"]
    r = await admin_client.post("/api/artifacts", json={
        "name": "m.png", "text": "x", "source": "derived", "meta": {"parent_id": "nope"}})
    assert r.status_code == 400, r.text


# --- the caps ------------------------------------------------------------------

async def test_size_total_and_bomb_caps(admin_client):
    r = await admin_client.post("/api/artifacts", files={
        "file": ("big.bin", b"x" * (8 * 1024 * 1024 + 1), "application/octet-stream")})
    assert r.status_code == 413, r.text
    r = await admin_client.post("/api/artifacts", files={"file": ("b.png", bomb_png(), "image/png")})
    assert r.status_code == 413, r.text
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x",
                                                        "meta": {"k": "v" * 9000}})
    assert r.status_code == 400, r.text

    admin_client._transport.app.state.settings.artifacts_total_max_bytes = 10
    r = await admin_client.post("/api/artifacts", json={"name": "a", "text": "x" * 11})
    assert r.status_code == 507, r.text
    assert "cap" in r.json()["detail"]


# --- reading -------------------------------------------------------------------

async def test_content_and_thumb_headers_for_a_raster(admin_client):
    data = jpeg_bytes(700, 300)
    a = await upload(admin_client, data, "photo.jpg", "image/jpeg")
    r = await admin_client.get(a["content_url"])
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.headers["content-disposition"] == 'inline; filename="photo.jpg"'

    r = await admin_client.get(a["thumb_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.headers["content-disposition"].startswith("inline")
    thumb = Image.open(io.BytesIO(r.content))
    assert thumb.size == (512, 219)


async def test_html_claiming_png_and_svg_are_served_as_attachments(admin_client):
    html = b"<html><script>alert(1)</script></html>"
    a = await upload(admin_client, html, "evil.png", "image/png")
    assert a["mime"] == "application/octet-stream" and a["kind"] == "file"
    r = await admin_client.get(a["content_url"])
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == 'attachment; filename="evil.png"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert a["thumb_url"] is None
    assert (await admin_client.get(f"/api/artifacts/{a['id']}/thumb")).status_code == 404

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    a = await upload(admin_client, svg, "lo go/é.svg", "image/svg+xml")
    r = await admin_client.get(a["content_url"])
    assert r.headers["content-disposition"] == 'attachment; filename="lo go_.svg"'

    a = await upload(admin_client, b"a,b\n", "t.csv", "text/csv")
    r = await admin_client.get(a["content_url"])
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith("attachment")


async def test_list_filters_and_pages(admin_client, sf, seed_agent, agent_store, token_client):
    made = []
    for i in range(3):
        made.append(await upload(admin_client, png_bytes(), f"i{i}.png", tags=[f"t{i}"]))
    made.append(await upload(admin_client, b"blob", "f.bin", "application/octet-stream",
                             tags=["t0"]))
    agent = await _agent_headers(sf, seed_agent, agent_store, "pai")
    made.append(await upload(token_client, b"agent blob", "a.bin",
                             "application/octet-stream", headers=agent))
    ids = [m["id"] for m in made]

    r = await admin_client.get("/api/artifacts", params={"limit": 2})
    assert r.status_code == 200 and [a["id"] for a in r.json()] == ids[4:2:-1]
    assert all("thumb_url" in a and "content_url" in a for a in r.json())
    r = await admin_client.get("/api/artifacts", params={"limit": 2, "before": ids[3]})
    assert [a["id"] for a in r.json()] == ids[2:0:-1]
    r = await admin_client.get("/api/artifacts", params={"kind": "file"})
    assert [a["id"] for a in r.json()] == [ids[4], ids[3]]
    r = await admin_client.get("/api/artifacts", params={"owner": "agent:pai"})
    assert [a["id"] for a in r.json()] == [ids[4]]
    r = await admin_client.get("/api/artifacts", params={"tag": "t0"})
    assert [a["id"] for a in r.json()] == [ids[3], ids[0]]
    r = await admin_client.get("/api/artifacts", params={"q": "I1"})
    assert [a["id"] for a in r.json()] == [ids[1]]
    r = await admin_client.get("/api/artifacts", params={"source": "upload", "limit": 200})
    assert len(r.json()) == 5
    assert (await admin_client.get("/api/artifacts", params={"limit": 500})).status_code == 422
    assert (await admin_client.get("/api/artifacts", params={"before": "nope"})).status_code == 422
    assert (await admin_client.get("/api/artifacts", params={"kind": "video"})).status_code == 422


async def test_stats(admin_client):
    await upload(admin_client, b"12345", "a.bin", "application/octet-stream")
    await upload(admin_client, b"123", "b.bin", "application/octet-stream")
    r = await admin_client.get("/api/artifacts/stats")
    assert r.status_code == 200, r.text
    st = r.json()
    assert (st["count"], st["bytes"]) == (2, 8)
    assert st["total_cap"] == 2 * 1024 ** 3


async def test_unknown_is_404_everywhere(admin_client):
    for path in ("/api/artifacts/nope", "/api/artifacts/nope/content", "/api/artifacts/nope/thumb"):
        assert (await admin_client.get(path)).status_code == 404
    assert (await admin_client.patch("/api/artifacts/nope", json={"name": "x"})).status_code == 404
    assert (await admin_client.delete("/api/artifacts/nope")).status_code == 404


# --- changing ------------------------------------------------------------------

async def test_patch_name_and_tags(admin_client):
    a = await upload(admin_client, b"d", "old.bin", "application/octet-stream")
    r = await admin_client.patch(f"/api/artifacts/{a['id']}", json={"name": "new name.bin",
                                                                   "tags": ["x"]})
    assert r.status_code == 200 and (r.json()["name"], r.json()["tags"]) == ("new name.bin", ["x"])
    r = await admin_client.patch(f"/api/artifacts/{a['id']}", json={"tags": ["y" * 41]})
    assert r.status_code == 400, r.text
    r = await admin_client.patch(f"/api/artifacts/{a['id']}", json={"tags": [str(i) for i in range(21)]})
    assert r.status_code == 400, r.text
    r = await admin_client.patch(f"/api/artifacts/{a['id']}", json={})
    assert r.status_code == 200 and r.json()["name"] == "new name.bin"


async def test_delete_is_owner_editor_or_admin(admin_client, sf, seed_agent, agent_store,
                                               token_client, producer):
    mine = await upload(admin_client, b"d", "a.bin", "application/octet-stream")
    owner = await _agent_headers(sf, seed_agent, agent_store, "pai")
    theirs = await upload(token_client, b"d", "b.bin", "application/octet-stream", headers=owner)
    other = await _agent_headers(sf, seed_agent, agent_store, "news")
    editor = await _agent_headers(sf, seed_agent, agent_store, "ops",
                                  grants=("mcp__platform__relay", "mcp__platform__artifacts",
                                          EDIT_GRANT))

    # Another agent may not; the owner, an agents_edit holder and admin may.
    r = await token_client.delete(f"/api/artifacts/{mine['id']}", headers=other)
    assert r.status_code == 403, r.text
    r = await token_client.delete(f"/api/artifacts/{theirs['id']}", headers=owner)
    assert r.status_code == 200 and r.json()["deleted_at"]
    r = await token_client.delete(f"/api/artifacts/{mine['id']}", headers=editor)
    assert r.status_code == 200, r.text
    third = await upload(admin_client, b"d", "c.bin", "application/octet-stream")
    assert (await admin_client.delete(f"/api/artifacts/{third['id']}")).status_code == 200

    # Deleted hides from the list, the metadata and the bytes; the row stays.
    r = await admin_client.get("/api/artifacts")
    assert r.json() == []
    for path in ("", "/content", "/thumb"):
        assert (await admin_client.get(f"/api/artifacts/{mine['id']}{path}")).status_code == 404
    assert (await admin_client.delete(f"/api/artifacts/{mine['id']}")).status_code == 404
    async with sf() as s:
        assert (await s.get(Artifact, mine["id"])).deleted_at is not None
    deleted = [e["data"] for e in producer.envelopes
               if e["type"] == "artifacts.event" and e["data"]["event"] == "deleted"]
    assert [d["agent"] for d in deleted] == ["pai", "ops", None]
    assert (await admin_client.get("/api/artifacts/stats")).json()["count"] == 0


async def test_patch_is_owner_editor_or_admin(admin_client, sf, seed_agent, agent_store,
                                              token_client):
    owner = await _agent_headers(sf, seed_agent, agent_store, "pai")
    theirs = await upload(token_client, b"d", "b.bin", "application/octet-stream", headers=owner)
    other = await _agent_headers(sf, seed_agent, agent_store, "news")
    editor = await _agent_headers(sf, seed_agent, agent_store, "ops",
                                  grants=("mcp__platform__relay", "mcp__platform__artifacts",
                                          EDIT_GRANT))

    r = await token_client.patch(f"/api/artifacts/{theirs['id']}", json={"name": "stolen"},
                                 headers=other)
    assert r.status_code == 403, r.text
    r = await token_client.patch(f"/api/artifacts/{theirs['id']}", json={"name": "mine"},
                                 headers=owner)
    assert r.status_code == 200 and r.json()["name"] == "mine"
    r = await token_client.patch(f"/api/artifacts/{theirs['id']}", json={"tags": ["edited"]},
                                 headers=editor)
    assert r.status_code == 200 and r.json()["tags"] == ["edited"]
    r = await admin_client.patch(f"/api/artifacts/{theirs['id']}", json={"name": "admin"})
    assert r.status_code == 200 and r.json()["name"] == "admin"
    assert (await admin_client.get(f"/api/artifacts/{theirs['id']}")).json()["name"] == "admin"


# --- the body bound, before any parse ---------------------------------------------

async def test_a_content_length_past_the_bound_is_refused_unparsed(admin_client, monkeypatch):
    from agentplatform.api import artifacts as artifacts_api

    async def never(*a, **k):
        raise AssertionError("the body was parsed")
    monkeypatch.setattr(artifacts_api, "_parse_create", never)
    r = await admin_client.post("/api/artifacts", content=b"x",
                                headers={"Content-Length": str(20 * 1024 * 1024),
                                         "Content-Type": "application/json"})
    assert r.status_code == 413, r.text


async def test_a_chunked_body_past_the_bound_is_refused(admin_client):
    sent = []

    async def chunks():
        # Well past the bound (8 MiB × 4/3 + 64 KiB), 1 MiB at a time; the
        # route must stop reading before the generator runs dry.
        for _ in range(40):
            sent.append(1)
            yield b"x" * (1024 * 1024)
    r = await admin_client.post("/api/artifacts", content=chunks(),
                                headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    assert len(sent) < 40
