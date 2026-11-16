"""The renderer service over HTTP, and the image's file set."""
import base64
import hashlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as renderer

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]

DOC = {"schema": 1, "title": "t", "modes": ["dark"],
       "tokens": {"color": {"bg": "#102030"}, "type": {"body": {"family": "Inter", "size": 16, "weight": 400}}},
       "components": {}, "pages": [{"id": "p", "title": "P", "children": [
           {"id": "card", "type": "frame", "w": 120, "h": 50, "fill": "$color.bg", "radius": 8,
            "layout": {"pad": 8}, "children": [
                {"id": "label", "type": "text", "text": "Hello", "style": "$type.body", "color": "#ffffff"}]}]}]}


@pytest.fixture
async def client(tmp_path):
    c = TestClient(TestServer(renderer.create_app(renderer.Config(workers=1, asset_dir=str(tmp_path / "a")))))
    await c.start_server()
    yield c
    await c.close()


async def test_health_render_and_boxes(client):
    h = await (await client.get("/healthz")).json()
    assert h["ok"] and h["renderer_version"].startswith("p")
    r = await client.post("/render", json={"doc": DOC, "mode": "dark", "nodes": ["card"], "scale": 2})
    assert r.status == 200
    j = await r.json()
    assert j["renderer_version"] == h["renderer_version"]
    im = j["images"][0]
    png = base64.b64decode(im["data"])
    assert Image.open(io.BytesIO(png)).size == (240, 100) == (im["width"], im["height"])
    assert im["boxes"]["children"][0]["id"] == "label"


@pytest.mark.parametrize("body", [
    {"doc": "x"}, {"doc": DOC, "scale": 7}, {"doc": DOC, "nodes": ["a"] * 5}, {"doc": DOC, "background": "red"},
    {"doc": DOC, "max_edge": 1}, {"doc": DOC, "mode": 3}])
async def test_bad_requests_are_400(client, body):
    r = await client.post("/render", json=body)
    assert r.status == 400 and (await r.json())["code"] == "UD-INVALID"


async def test_unknown_node_and_expansion_errors(client):
    r = await client.post("/render", json={"doc": DOC, "mode": "dark", "nodes": ["nope"]})
    assert r.status == 422 and (await r.json())["code"] == "UD-OP-TARGET"


async def test_asset_upload_verifies_hash_downscales_and_rejects_junk(client, tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), "#ff0000").save(buf, "PNG")
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    assert (await client.put("/assets/" + "0" * 64, data=data)).status == 422     # body does not hash to the id
    assert (await client.put("/assets/zzz", data=data)).status == 400
    junk = b"not an image"
    assert (await client.put(f"/assets/{hashlib.sha256(junk).hexdigest()}", data=junk)).status == 422
    r = await client.put(f"/assets/{sha}", data=data)
    assert r.status == 200
    j = await r.json()
    assert j["width"] * j["height"] <= renderer.ASSET_MAX_PIXELS and j["width"] * 2 == pytest.approx(j["height"] * 3, abs=3)


async def test_missing_asset_is_409_then_renders(client):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "#00ff00").save(buf, "PNG")
    sha = hashlib.sha256(buf.getvalue()).hexdigest()
    doc = {**DOC, "pages": [{"id": "p", "title": "P", "children": [
        {"id": "pic", "type": "image", "asset": sha, "w": 10, "h": 10}]}]}
    r = await client.post("/render", json={"doc": doc, "mode": "dark", "nodes": ["pic"]})
    assert r.status == 409 and (await r.json())["missing"] == [sha]
    assert (await client.put(f"/assets/{sha}", data=buf.getvalue())).status == 200
    r = await client.post("/render", json={"doc": doc, "mode": "dark", "nodes": ["pic"]})
    assert r.status == 200


async def test_svg_and_resolved_routes(client):
    s = await (await client.post("/svg", json={"doc": DOC, "mode": "dark", "node": "card"})).json()
    assert s["svg"].startswith("<svg") and "<text" not in s["svg"]
    bad = await client.post("/svg", json={"doc": DOC, "asset_prefix": '/x"><script>'})
    assert bad.status == 400
    t = await (await client.post("/resolved", json={"doc": DOC, "mode": "dark"})).json()
    assert t["tree"]["pages"][0]["children"][0]["id"] == "card" and "glyphs" not in t["tree"]


def test_the_image_file_set_is_enough_to_render(tmp_path):
    """Copy exactly what the Dockerfile copies, run a render with only that on
    the path, and check the database stack never got imported."""
    import shutil
    text = (SERVICE / "Dockerfile").read_text().replace("\\\n", " ")
    for line in text.splitlines():
        m = re.match(r"COPY\s+(.+)\s+(\S+)$", line.strip())
        if not m or m.group(1).startswith("services/ui-renderer/requirements"):
            continue
        srcs, dest = m.group(1).split(), m.group(2)
        into_dir = dest.endswith("/") or dest == "." or len(srcs) > 1
        for src in srcs:
            p = REPO / src
            target = tmp_path / dest
            if p.is_dir():
                shutil.copytree(p, target, dirs_exist_ok=True)
            else:
                target = target / p.name if into_dir else target
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(p.read_bytes())
    prog = (
        "import sys, json; sys.path.insert(0, sys.argv[2]); import app; from app import Config\n"
        f"doc = json.loads({__import__('json').dumps(__import__('json').dumps(DOC))})\n"
        "r = app.job_render(Config(asset_dir=sys.argv[1]), {'doc': doc, 'mode': 'dark', 'nodes': ['card'],"
        " 'scale': 1, 'annotate': True, 'max_edge': 1568, 'max_bytes': None, 'background': None})\n"
        "assert r['ok'] and r['images'][0]['data'][:4] == b'\\x89PNG'\n"
        "bad = [m for m in ('sqlalchemy', 'fastapi', 'asyncpg', 'agentplatform.db', 'agentplatform.ui_designer.store') if m in sys.modules]\n"
        "import agentplatform; assert agentplatform.__file__.startswith(sys.argv[2]), agentplatform.__file__\n"
        "assert not bad, bad\n")
    out = subprocess.run([sys.executable, "-I", "-c", prog, str(tmp_path / "cache"), str(tmp_path)],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


# --- admission -------------------------------------------------------------------

@pytest.fixture
def slow_jobs(monkeypatch):
    import time

    def call(self, kind, req, timeout, start_timeout):
        time.sleep(0.6)
        return {"ok": True, "tree": {}}
    monkeypatch.setattr(renderer._Worker, "call", call)


async def _slow_client(tmp_path, **cfg):
    c = TestClient(TestServer(renderer.create_app(renderer.Config(
        workers=1, asset_dir=str(tmp_path / "a"), **cfg)), handler_cancellation=True))
    await c.start_server()
    return c


async def test_full_queue_is_503_with_retry_after(tmp_path, slow_jobs):
    import asyncio
    c = await _slow_client(tmp_path, queue_max=1, queue_deadline_s=5)
    try:
        post = lambda: c.post("/resolved", json={"doc": DOC})
        running = asyncio.create_task(post())
        await asyncio.sleep(0.15)
        waiting = asyncio.create_task(post())
        await asyncio.sleep(0.15)
        refused = await post()
        assert refused.status == 503 and refused.headers["Retry-After"]
        assert (await refused.json())["code"] == "UD-RENDER-FAILED"
        assert (await running).status == 200 and (await waiting).status == 200
    finally:
        await c.close()


async def test_queue_deadline_is_503_and_cancelled_client_frees_its_place(tmp_path, slow_jobs):
    import asyncio
    c = await _slow_client(tmp_path, queue_max=8, queue_deadline_s=0.2)
    try:
        running = asyncio.create_task(c.post("/resolved", json={"doc": DOC}))
        await asyncio.sleep(0.1)
        late = await c.post("/resolved", json={"doc": DOC})
        assert late.status == 503 and late.headers["Retry-After"]
        assert (await running).status == 200
    finally:
        await c.close()
    c = await _slow_client(tmp_path, queue_max=8, queue_deadline_s=5)
    try:
        pool = c.server.app[renderer.POOL]
        running = asyncio.create_task(c.post("/resolved", json={"doc": DOC}))
        await asyncio.sleep(0.1)
        with pytest.raises(asyncio.TimeoutError):          # the client gives up while queued
            await asyncio.wait_for(c.post("/resolved", json={"doc": DOC}), 0.2)
        await asyncio.sleep(0.1)
        assert pool.waiting == 0                            # cancellation reached the queued handler
        assert (await running).status == 200
    finally:
        await c.close()
