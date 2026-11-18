"""UI Designer renderer (design 46 T4): the painter's SVG and PNGs (golden), the
renderer service's job timeout and memory fence, the downscale ladder, and the
API-side client with its renderer_version-keyed cache.

Goldens: the SVG text must match exactly (it is platform independent); the PNG
is compared to a checked-in file with a small mean-error tolerance, because
resvg's anti-aliasing is allowed to differ in the last bit between CPUs.
`UPDATE_GOLDEN=1 pytest tests/test_ui_designer_render.py` rewrites them."""
import asyncio
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest
import resvg_py
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageChops, ImageStat

from agentplatform.db import UiDesignerRender
from agentplatform.ui_designer import store, svg
from agentplatform.ui_designer.errors import (UD_IMAGE_TOO_LARGE, UD_OP_TARGET, UD_RENDER_FAILED,
                                              UiDesignerError)
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.schema import new_document, normalize_document, validate_document

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ui-renderer"))
import app as renderer  # noqa: E402

from tests.ui_designer_fixtures import big_doc  # noqa: E402

GOLDEN = Path(__file__).parent / "golden" / "ui_designer"
UPDATE = bool(os.environ.get("UPDATE_GOLDEN"))


def png_of(painted, scale=1, res_dir=None):
    return resvg_py.svg_to_bytes(svg_string=painted.svg, width=round(painted.w * scale),
                                 height=round(painted.h * scale), resources_dir=res_dir,
                                 skip_system_fonts=True, font_dirs=[])


def check_golden(name, painted, scale=1, res_dir=None, tol=1.5):
    png = png_of(painted, scale, res_dir)
    svg_path, png_path = GOLDEN / f"{name}.svg", GOLDEN / f"{name}.png"
    if UPDATE or not png_path.exists():
        GOLDEN.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(painted.svg)
        png_path.write_bytes(png)
    assert hashlib.sha256(painted.svg.encode()).hexdigest() == \
        hashlib.sha256(svg_path.read_text().encode()).hexdigest(), f"{name}: SVG changed"
    got, want = Image.open(io.BytesIO(png)).convert("RGBA"), Image.open(png_path).convert("RGBA")
    assert got.size == want.size
    err = sum(ImageStat.Stat(ImageChops.difference(got, want)).mean) / 4
    assert err <= tol, f"{name}: PNG differs from golden (mean error {err:.2f}/255)"


def doc_with(children, validate=True, **extra):
    d = new_document("Golden")
    d["tokens"] = {"color": {"bg": {"dark": "#101820", "light": "#f4f6f8"}, "fg": {"dark": "#ffffff", "light": "#101820"},
                             "accent": "#5ad1ff"},
                   "type": {"hud": {"family": "JetBrains Mono", "size": 18, "weight": 600},
                            "body": {"family": "Inter", "size": 14, "weight": 400}}}
    d["modes"] = ["dark", "light"]
    d["pages"] = [{"id": "p", "title": "P", "children": children}]
    d.update(extra)
    d = normalize_document(d)
    if validate:
        validate_document(d)
    return d


def hud():
    return doc_with([{"id": "hud", "type": "frame", "w": 360, "h": 200, "fill": "$color.bg", "radius": 16,
                      "shadow": {"x": 0, "y": 4, "blur": 12, "color": "#000000aa"},
                      "layout": {"dir": "column", "gap": 10, "pad": 16}, "children": [
        {"id": "title", "type": "text", "text": "Match 12: Red vs Blue", "style": "$type.body", "color": "$color.fg"},
        {"id": "bar", "type": "frame", "h": 36, "w": "fill", "layout": {"dir": "row", "gap": 8, "align": "center"},
         "children": [
             {"id": "pill", "type": "frame", "layout": {"pad": [4, 12]}, "radius": 999, "fill": "$color.accent",
              "stroke": {"color": "#ffffff", "width": 3, "align": "outside"},
              "children": [{"id": "score", "type": "text", "text": "7 - 5", "style": "$type.hud", "color": "#101820"}]},
             {"id": "grad", "type": "rect", "w": 120, "h": 24, "radius": [12, 0, 12, 0],
              "fill": {"gradient": "linear", "angle": 90, "stops": [[0, "#ff5a5a"], [1, "#5a7bff"]]}},
             {"id": "dot", "type": "ellipse", "w": 24, "h": 24, "rotate": 30,
              "fill": {"gradient": "radial", "stops": [[0, "#ffffff"], [1, "#2ecc71cc"]]},
              "stroke": {"color": "#ffffff", "width": 2, "align": "inside"}}]},
        {"id": "clipped", "type": "frame", "w": 120, "h": 40, "clip": True, "radius": 8, "fill": "#2a3440",
         "children": [{"id": "wide", "type": "rect", "x": -20, "y": 10, "w": 200, "h": 14, "absolute": True,
                       "fill": "#ff9f43", "rotate": -8}]}]}])


def test_hud_golden_dark_light_and_annotated():
    d = hud()
    r = layout_document(d, "dark")
    check_golden("hud-dark", svg.paint(r, "hud"), scale=2)
    check_golden("hud-annotated", svg.paint(r, "hud", annotate=True))
    check_golden("hud-light", svg.paint(layout_document(d, "light"), "hud"))


def test_glyphs_are_paths_shared_in_defs_and_no_text_or_scripts():
    s = svg.paint(layout_document(hud(), "dark"), "hud").svg
    assert "<text" not in s and "<script" not in s and "<foreignObject" not in s
    assert s.count("<use ") > s.count("<path id=")  # glyphs are defined once and placed many times


def test_node_not_found_and_page_render():
    r = layout_document(hud(), "dark")
    with pytest.raises(UiDesignerError) as e:
        svg.paint(r, "nope")
    assert e.value.code == UD_OP_TARGET
    page = svg.paint(r, "p")
    assert (page.w, page.h) == (360, 200)
    assert svg.paint(r).svg == page.svg  # no node means the first page


def test_rendering_inside_a_rotated_ancestor_keeps_its_place():
    d = doc_with([{"id": "outer", "type": "frame", "w": 100, "h": 40, "rotate": 90,
                   "children": [{"id": "inner", "type": "rect", "x": 0, "y": 0, "w": 100, "h": 10,
                                 "fill": "#ff0000", "absolute": True}]}])
    # a frame with no layout places children at x/y
    r = layout_document(d, "dark")
    p = svg.paint(r, "inner")
    assert "rotate(90" in p.svg
    im = Image.open(io.BytesIO(png_of(p))).convert("RGBA")
    assert im.getpixel((im.width // 2, im.height // 2))[0] > 200


def mask_doc(kind):
    return doc_with([{"id": "m", "type": "frame", "w": 100, "h": 60, "mask": kind, "children": [
        {"id": "shape", "type": "ellipse", "x": 10, "y": 5, "w": 80, "h": 50, "absolute": True,
         "fill": {"gradient": "linear", "angle": 90, "stops": [[0, "#ffffff00"], [1, "#ffffff"]]}},
        {"id": "content", "type": "rect", "x": 0, "y": 0, "w": 100, "h": 60, "absolute": True, "fill": "#ff0000"}]}])


@pytest.mark.parametrize("kind", ["alpha", "vector", "luminance"])
def test_mask_modes(kind):
    p = svg.paint(layout_document(mask_doc(kind), "dark"), "m")
    im = Image.open(io.BytesIO(png_of(p, 2))).convert("RGBA")
    assert im.getpixel((2, 2))[3] == 0                      # outside the mask shape: nothing
    centre = im.getpixel((im.width // 2, im.height // 2))
    assert centre[0] > 150 and centre[3] > (200 if kind == "vector" else 90)
    left = im.getpixel((30, im.height // 2))
    # alpha and luminance follow the gradient (transparent/black at the left); vector ignores paint
    assert (left[3] > 200) if kind == "vector" else (left[3] < 100)
    check_golden(f"mask-{kind}", p, scale=2)


def png_bytes(w, h, color):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_images_share_one_defs_entry_and_fit_modes(tmp_path):
    # A checked-in source image keeps the content hash stable across PNG encoders.
    raw = (Path(__file__).parent / "fixtures" / "ui_designer" / "image-fit-source.png").read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / f"{sha}.png").write_bytes(raw)
    d = doc_with([{"id": "row", "type": "frame", "w": 200, "h": 60, "fill": "#000000",
                   "layout": {"dir": "row", "gap": 5, "pad": 5}, "children": [
        {"id": "cover", "type": "image", "asset": sha, "fit": "cover", "w": 40, "h": 40, "radius": 6},
        {"id": "contain", "type": "image", "asset": sha, "fit": "contain", "w": 40, "h": 40},
        {"id": "fill", "type": "image", "asset": sha, "fit": "fill", "w": 40, "h": 40}]}])
    r = layout_document(d, "dark")
    p = svg.paint(r, "row", assets={sha: svg.AssetInfo(f"{sha}.png", 40, 20)})
    assert p.svg.count("<image ") == 1 and p.svg.count('href="#im') == 3
    assert svg.assets_used(r) == [sha]
    check_golden("images", p, scale=3, res_dir=str(tmp_path))
    # without dimensions the painter falls back to one inline image per node
    q = svg.paint(r, "row", href=lambda s: f"/api/ui-designer/assets/{s}")
    assert q.svg.count("<image ") == 3 and "preserveAspectRatio" in q.svg


def test_hostile_strings_cannot_break_out_of_attributes():
    d = hud()
    r = layout_document(d, "dark")
    r["pages"][0]["children"][0]["children"][1]["fill"] = '"><script>alert(1)</script>'
    s = svg.paint(r, "hud", href=lambda s: s).svg
    assert "<script" not in s
    with pytest.raises(UiDesignerError):
        svg.safe_prefix('/x"/><script>')
    with pytest.raises(UiDesignerError):
        svg.safe_prefix("/a/../b")


def test_work_caps_are_enforced_by_the_painter():
    kids = [{"id": f"s{i}", "type": "rect", "w": 4, "h": 4, "fill": "#fff",
             "shadow": {"x": 0, "y": 0, "blur": 2, "color": "#000"}} for i in range(33)]
    r = layout_document(doc_with([{"id": "f", "type": "frame", "w": 100, "h": 100, "children": kids}], validate=False), "dark")
    with pytest.raises(UiDesignerError) as e:
        svg.paint(r, "f")
    assert e.value.code == UD_RENDER_FAILED


def test_renderer_version_covers_the_font_set():
    v = svg.renderer_version()
    assert v.startswith(f"p{svg.PAINTER_VERSION}-") and len(v) == len(f"p{svg.PAINTER_VERSION}-") + 16


# --- the service: ladder, jobs, timeout, memory -----------------------------

def noisy_doc(n=60):
    """Text-free but hard to compress: many random-looking small rects."""
    import random
    rnd = random.Random(7)
    kids = [{"id": f"r{i}", "type": "rect", "x": rnd.randint(0, 900), "y": rnd.randint(0, 900),
             "w": rnd.randint(2, 30), "h": rnd.randint(2, 30),
             "fill": "#%06x" % rnd.randint(0, 0xFFFFFF), "absolute": True} for i in range(n * 40)]
    return doc_with([{"id": "noise", "type": "frame", "w": 1000, "h": 1000, "fill": "#000", "children": kids}])


def test_ladder_downscales_then_reencodes_then_gives_up(tmp_path):
    d = noisy_doc()
    r = layout_document(d, "dark")
    p = svg.paint(r, "noise")
    first = renderer.ladder(p.svg, p, 1, 4096, None, None, str(tmp_path))
    assert (first["format"], first["width"]) == ("png", 1000)
    smooth = svg.paint(layout_document(doc_with([{"id": "sm", "type": "frame", "w": 1000, "h": 1000, "fill": {
        "gradient": "radial", "stops": [[0, "#ff5a5a"], [0.5, "#3366ff"], [1, "#101820"]]}}]), "dark"), "sm")
    full = renderer.ladder(smooth.svg, smooth, 1, 4096, None, None, str(tmp_path))
    # at 1000 px there is no smaller PNG tier, so over the bound the ladder re-encodes: WebP, then JPEG
    second = renderer.ladder(smooth.svg, smooth, 1, 4096, len(full["data"]) - 1, None, str(tmp_path))
    assert second["format"] in ("webp", "jpeg") and len(second["data"]) < len(full["data"])
    big = renderer.ladder(p.svg, p, 3, 4096, 10_000_000, None, str(tmp_path))
    assert big["width"] == 3000
    tiered = renderer.ladder(smooth.svg, smooth, 3, 4096, 400_000, None, str(tmp_path))
    assert tiered["width"] <= 1280 and len(tiered["data"]) <= 400_000
    with pytest.raises(UiDesignerError) as e:
        renderer.ladder(p.svg, p, 1, 4096, 500, None, str(tmp_path))
    assert e.value.code == UD_IMAGE_TOO_LARGE and e.value.detail["box"] == [0.0, 0.0, 1000.0, 1000.0]
    huge = svg.paint(layout_document(doc_with([{"id": "w", "type": "frame", "w": 4000, "h": 4000}]), "dark"), "w")
    with pytest.raises(UiDesignerError) as e:   # 10 megapixel output cap
        renderer.ladder(huge.svg, huge, 1, 4096, None, None, str(tmp_path))
    assert e.value.code == UD_RENDER_FAILED


@pytest.fixture
async def service(tmp_path):
    made = []

    async def make(**kw):
        cfg = renderer.Config(asset_dir=str(tmp_path / "assets"), workers=kw.pop("workers", 1), **kw)
        c = TestClient(TestServer(renderer.create_app(cfg)))
        await c.start_server()
        made.append(c)
        return c
    yield make
    for c in made:
        await c.close()


async def test_job_timeout_kills_and_respawns_the_worker(service):
    c = await service(job_timeout_s=0.3)
    body = {"doc": big_doc(rows=40), "mode": "dark", "nodes": ["root"]}   # layout alone takes far longer
    r = await c.post("/render", json=body)
    assert r.status == 504
    j = await r.json()
    assert j["code"] == UD_RENDER_FAILED and "timed out" in j["message"]
    ok = await c.post("/render", json={"doc": hud(), "mode": "dark", "nodes": ["hud"]})  # the respawned worker serves
    assert ok.status == 200


async def test_memory_fence_kills_the_job_not_the_service(service):
    if sys.platform != "linux":
        pytest.skip("RLIMIT_DATA is only enforced on Linux")
    # The 3150 × 3150 output fits the pixel cap. Nested opacity groups force
    # native compositing buffers without exceeding any authored document limit.
    size = {"w": 1050, "h": 1050}
    foreground = {"id": "foreground", "type": "rect", **size,
                  "fill": {"gradient": "linear", "stops": [[0, "#f00"], [1, "#080"]]}}
    layer = {"id": "layer", "type": "frame", **size, "fill": "#000",
             "opacity": .8, "children": [foreground]}
    scene = doc_with([{"id": "composite", "type": "frame", **size, "fill": "#000",
                       "opacity": .8, "children": [layer]}])
    body = {"doc": scene, "mode": "dark", "nodes": ["composite"], "scale": 3}
    control = await service(job_memory_mb=512, job_timeout_s=20)
    assert (await control.post("/render", json=body)).status == 200

    c = await service(job_memory_mb=64, job_timeout_s=20)
    small = {"doc": hud(), "mode": "dark", "nodes": ["hud"]}
    assert (await c.post("/render", json=small)).status == 200
    before = c.app[renderer.POOL].all[0].proc.pid
    response = await c.post("/render", json=body)
    error = await response.json()
    assert response.status == 504 and error["code"] == UD_RENDER_FAILED
    assert "worker exited" in error["message"]
    assert (await c.get("/healthz")).status == 200
    assert (await c.post("/render", json=small)).status == 200
    assert c.app[renderer.POOL].all[0].proc.pid != before


# --- the client and the cache ----------------------------------------------

@pytest.fixture
async def rc(service, tmp_path):
    c = await service()
    import httpx

    class _T(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            resp = await c.request(request.method, request.url.path, data=request.content,
                                   headers={k: v for k, v in request.headers.items() if k.lower() == "content-type"})
            return httpx.Response(resp.status, content=await resp.read(),
                                  headers={"content-type": resp.headers.get("content-type", "")})
    client = RenderClient("http://renderer", client=httpx.AsyncClient(transport=_T()))
    yield client
    await client.aclose()


async def test_client_renders_caches_by_version_and_uploads_missing_assets(sf, rc):
    d = hud()
    sha_doc = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
    async with sf() as s:
        out = await rc.render(s, document_id="d" * 32, doc=d, doc_sha256=sha_doc, mode="dark", nodes=["hud", "title"])
        await s.commit()
    assert [o.node for o in out] == ["hud", "title"] and not any(o.cached for o in out)
    assert out[0].png[:8] == b"\x89PNG\r\n\x1a\n" and out[0].boxes["children"][0]["id"] == "title"
    async with sf() as s:
        again = await rc.render(s, document_id="d" * 32, doc=d, doc_sha256=sha_doc, mode="dark", nodes=["hud", "title"])
        assert all(o.cached for o in again) and again[0].png == out[0].png
        other = await rc.render(s, document_id="d" * 32, doc=d, doc_sha256=sha_doc, mode="dark", nodes=["hud"], scale=2)
        assert not other[0].cached and other[0].width == 2 * out[0].width
        rows = (await s.execute(__import__("sqlalchemy").select(UiDesignerRender.key))).all()
        assert len(rows) == 3
        # a new renderer version is a new key: nothing from the old one is served
        import agentplatform.ui_designer.render_client as rcmod
        orig = rcmod.renderer_version
        rcmod.renderer_version = lambda: "p999-other"
        try:
            miss = await rc.render(s, document_id="d" * 32, doc=d, doc_sha256=sha_doc, mode="dark", nodes=["hud"])
            assert not miss[0].cached
            assert len((await s.execute(__import__("sqlalchemy").select(UiDesignerRender.key))).all()) == 3  # mismatch: not cached
        finally:
            rcmod.renderer_version = orig
    # an image the renderer has never seen travels with the call, once
    png = png_bytes(30, 20, "#e74c3c")
    sha = hashlib.sha256(png).hexdigest()
    async with sf() as s:
        await store.add_asset(s, data=png, mime="image/png", width=30, height=20)
        await s.commit()
    di = doc_with([{"id": "pic", "type": "image", "asset": sha, "w": 30, "h": 20}])
    async with sf() as s:
        got = await rc.render(s, document_id="e" * 32, doc=di, doc_sha256="x" * 64, mode="dark", nodes=["pic"])
    im = Image.open(io.BytesIO(got[0].png)).convert("RGB")
    assert im.getpixel((15, 10)) == (231, 76, 60)
    async with sf() as s:
        with pytest.raises(UiDesignerError) as e:
            await rc.render(s, document_id="e" * 32, doc=doc_with([{"id": "pic", "type": "image", "asset": "0" * 64, "w": 3, "h": 3}]),
                            doc_sha256="y" * 64, mode="dark", nodes=["pic"])
    assert e.value.code == "UD-ASSET-UNKNOWN"


async def test_client_svg_resolved_and_errors(rc):
    s = await rc.svg(doc=hud(), mode="dark", node="hud", asset_prefix="/api/ui-designer/assets/")
    assert s["svg"].startswith("<svg") and s["box"][2:] == [360.0, 200.0]
    tree = await rc.resolved(doc=hud(), mode="dark")
    assert "glyphs" not in tree and "runs" not in json.dumps(tree)
    with pytest.raises(UiDesignerError) as e:
        await rc.svg(doc=hud(), mode="dark", node="nope")
    assert e.value.code == UD_OP_TARGET


async def test_client_reports_an_unreachable_renderer():
    c = RenderClient("http://127.0.0.1:1", timeout_s=1)
    with pytest.raises(UiDesignerError) as e:
        await c.resolved(doc=hud(), mode="dark")
    assert e.value.code == UD_RENDER_FAILED
    await c.aclose()
