import base64
import copy
import io
import json
import shutil
from pathlib import Path
import pytest
from PIL import Image
import app as renderer
from test_app import client  # noqa: F401
from agentplatform.ui_designer import motion
from agentplatform.ui_designer.errors import UiDesignerError


def document():
    def frame(id, x, color, child, dx):
        return {
            "id": id,
            "type": "frame",
            "x": x,
            "w": 240,
            "h": 160,
            "fill": "#ffffff",
            "children": [
                {
                    "id": child,
                    "type": "rect",
                    "x": dx,
                    "y": 40,
                    "w": 50,
                    "h": 50,
                    "fill": color,
                    "meta": {"motion_key": "card"},
                }
            ],
        }

    return {
        "schema": 1,
        "pages": [
            {
                "id": "p",
                "children": [
                    frame("before", 0, "#ff0000", "red", 20),
                    frame("after", 300, "#0000ff", "blue", 150),
                ],
            }
        ],
    }


def request(doc=None, **kw):
    return renderer._motion_req(
        {
            "doc": doc or document(),
            "from_node": "before",
            "to_node": "after",
            "easing": "linear",
            **kw,
        }
    )


def test_manifest_normalizes_offsets_matches_explicit_keys_and_endpoints(tmp_path):
    m = renderer.job_motion_preview(renderer.Config(asset_dir=str(tmp_path)), request())
    motion.validate_manifest(m)
    assert (m["width"], m["height"], m["frames"]) == (240, 160, 19)
    layer = next(l for l in m["layers"] if l["id"] == "key:card")
    assert layer["from"]["x"] == 20 and layer["to"]["x"] == 150
    start = motion.render_frame(m, 0)
    end = motion.render_frame(m, 18)
    mid = motion.render_frame(m, 9)
    assert start.getpixel((20, 40)) == (255, 0, 0, 255) and end.getpixel((150, 40)) == (
        0,
        0,
        255,
        255,
    )
    assert mid.getpixel((85, 40)) == (128, 0, 128, 255)
    assert mid.getpixel((84, 40)) == (255, 255, 255, 255)
    assert base64.b64decode(m["from_image"]) != base64.b64decode(m["to_image"])


def test_manifest_transparent_content_crossfade_does_not_lose_alpha(tmp_path):
    doc = document()
    for frame in doc["pages"][0]["children"]:
        frame.pop("fill")
        frame["children"][0]["x"] = 20
        frame["children"][0]["opacity"] = 0.5
    m = renderer.job_motion_preview(
        renderer.Config(asset_dir=str(tmp_path)), request(doc)
    )
    pixel = motion.render_frame(m, 9).getpixel((30, 50))
    assert pixel == (128, 0, 128, 128)


def test_component_relative_matching_and_complex_fallback(tmp_path):
    doc = document()
    doc["components"] = {
        "button": {
            "id": "button",
            "type": "frame",
            "w": 50,
            "h": 50,
            "children": [
                {"id": "label", "type": "rect", "w": 20, "h": 20, "fill": "#ff0000"}
            ],
        }
    }
    for frame, ident in zip(doc["pages"][0]["children"], ["first", "second"]):
        frame["children"] = [{"id": ident, "type": "instance", "of": "button"}]
    m = renderer.job_motion_preview(
        renderer.Config(asset_dir=str(tmp_path)), request(doc)
    )
    assert any(
        l["id"] == "relative:label" and l["from"] and l["to"] for l in m["layers"]
    )
    doc["pages"][0]["children"][0]["blur"] = 2
    m = renderer.job_motion_preview(
        renderer.Config(asset_dir=str(tmp_path)), request(doc)
    )
    assert [l["id"] for l in m["layers"]] == ["frame-dissolve"] and "whole-frame" in m[
        "warnings"
    ][0]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["pages"][0]["children"][0]["children"].append(
            {
                "id": "duplicate",
                "type": "rect",
                "w": 2,
                "h": 2,
                "meta": {"motion_key": "card"},
            }
        ),
        lambda d: d["pages"][0]["children"][0]["children"][0]["meta"].update(
            motion_key=3
        ),
    ],
)
def test_ambiguous_explicit_keys_refused(tmp_path, change):
    doc = document()
    change(doc)
    with pytest.raises(UiDesignerError, match="motion_key"):
        renderer.job_motion_preview(
            renderer.Config(asset_dir=str(tmp_path)), request(doc)
        )


@pytest.mark.parametrize(
    "options",
    [
        {"fps": True},
        {"duration_ms": 199},
        {"duration_ms": 5001},
        {"max_edge": 721},
        {"easing": "evil()"},
        {"from_node": "https://example.com"},
        {"script": "x"},
    ],
)
def test_requests_closed_bounded(options):
    with pytest.raises(renderer.BadRequest):
        request(**options)


@pytest.mark.parametrize("format", ["mp4", "gif"])
def test_real_pinned_encoder_produces_decodable_endpoints(tmp_path, format):
    if shutil.which("node") is None:
        pytest.skip("Node is required for the pinned encoder")
    cfg = renderer.Config(asset_dir=str(tmp_path / "assets"))
    out = renderer.job_motion_export(
        cfg, {**request(), "format": format, "max_bytes": 8 << 20}
    )
    assert out["ok"], out
    assert (
        out["data"][4:8] == b"ftyp"
        if format == "mp4"
        else out["data"].startswith(b"GIF89a")
    )
    assert (out["width"], out["height"], out["frames"]) == (240, 160, 19)
    if format == "gif":
        im = Image.open(io.BytesIO(out["data"]))
        assert im.n_frames == 19
        assert im.convert("RGB").getpixel((20, 40)) == (255, 0, 0)
        im.seek(18)
        assert im.convert("RGB").getpixel((150, 40)) == (0, 0, 255)


async def test_http_motion_preview_and_export_worker(client):
    doc = document()
    response = await client.post(
        "/motion/preview", json={"doc": doc, "from_node": "before", "to_node": "after"}
    )
    assert response.status == 200, await response.text()
    m = await response.json()
    motion.validate_manifest(m)
    response = await client.post(
        "/motion/export",
        json={"doc": doc, "from_node": "before", "to_node": "after", "format": "gif"},
    )
    assert response.status == 200, await response.text()
    assert base64.b64decode((await response.json())["data"]).startswith(b"GIF89a")


def test_manifest_cache_does_not_alias_and_invalidation_is_canonical(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    req = request()
    first = renderer.job_motion_preview(cfg, req)
    first["layers"].clear()
    assert renderer.job_motion_preview(cfg, req)["layers"]
    changed = renderer.job_motion_preview(cfg, {**req, "easing": "ease-in"})
    assert changed["easing"] == "ease-in"


def test_incremental_raster_work_guard_stops_before_next_raster(tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "MAX_RASTER_PIXELS", 80000)
    calls = []

    def raster(source, w, h):
        calls.append((w, h))
        return renderer._raster(source, w, h, None, str(tmp_path))

    with pytest.raises(UiDesignerError, match="raster work"):
        motion.prepare(document(), "before", "after", raster=raster)
    assert calls == [(240, 160), (240, 160)]


async def test_http_disconnect_kills_encoder_group_cleans_tmp_and_recovers(
    tmp_path, monkeypatch
):
    import asyncio, os, tempfile, subprocess
    from aiohttp.test_utils import TestClient, TestServer

    directory = tmp_path / "encoder"
    directory.mkdir()
    marker = tmp_path / "child.json"
    (directory / "encode.mjs").write_text(
        "import fs from 'node:fs';import {spawn} from 'node:child_process';const p=spawn(process.execPath,['-e','setInterval(()=>{},1000)']);fs.writeFileSync("
        + json.dumps(str(marker))
        + ",JSON.stringify({child:p.pid,node:process.pid}));setInterval(()=>{},1000);"
    )
    monkeypatch.setenv("UI_MOTION_ENCODER_DIR", str(directory))
    c = TestClient(
        TestServer(
            renderer.create_app(
                renderer.Config(workers=1, asset_dir=str(tmp_path / "assets"))
            )
        )
    )
    await c.start_server()
    try:
        task = asyncio.create_task(
            c.post(
                "/motion/export",
                json={
                    "doc": document(),
                    "from_node": "before",
                    "to_node": "after",
                    "format": "mp4",
                },
            )
        )
        for _ in range(200):
            if marker.exists():
                break
            await asyncio.sleep(0.05)
        assert marker.exists()
        processes = json.loads(marker.read_text())
        worker = c.app[renderer.MOTION_POOL].all[0].proc.pid
        root = Path(tempfile.gettempdir()) / f"ui-motion-worker-{worker}"
        assert root.exists()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(100):
            if not root.exists():
                break
            await asyncio.sleep(0.05)
        assert not root.exists()
        for pid in processes.values():
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
            ).stdout.strip()
            assert not state or state.startswith("Z")
        response = await c.post(
            "/motion/preview",
            json={"doc": document(), "from_node": "before", "to_node": "after"},
        )
        assert response.status == 200
    finally:
        await c.close()
