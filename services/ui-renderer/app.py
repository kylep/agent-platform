"""UI Designer renderer (design 46 section 5): expand, lay out, paint, rasterize.

A small aiohttp service. The API stays I/O only: it validates, stores and
forwards a document here and gets PNGs back. `resvg_py.svg_to_bytes` holds the
GIL and a render can allocate a lot, so each job runs in a separate worker
process under `RLIMIT_DATA` and a wall clock; over either, the worker is killed
and respawned and the caller gets `UD-RENDER-FAILED`. The service holds no
credentials: image bytes arrive from the API on `PUT /assets/<sha256>` (a
`/render` that needs one answers 409 with the missing hashes), and the pod's
NetworkPolicy admits only the API.

    GET  /healthz            {"ok": true, "renderer_version": ...}
    PUT  /assets/{sha}       raw image bytes; verified, downscaled to <= 4 MP
    POST /render             {doc, mode, nodes, scale, annotate, max_edge,
                              max_bytes, background} -> {renderer_version,
                              images: [{node, format, mime, width, height,
                              bytes, data (base64), boxes}], warnings}
    POST /svg                {doc, mode, node, annotate, asset_prefix, assets}
    POST /resolved           {doc, mode} -> the resolved tree (glyph data cut)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import multiprocessing as mp
import os
import re
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

log = logging.getLogger("ui_renderer")

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SCALES = (0.5, 1, 2, 3)
MAX_PIXELS = 10_000_000          # output cap
ASSET_MAX_PIXELS = 4_000_000     # stored asset size after downscale
ASSET_MAX_BYTES = 8 << 20
BODY_MAX = 16 << 20
TIERS = (1280, 1024)             # the downscale ladder, then WebP, then JPEG


@dataclass(frozen=True)
class Config:
    workers: int = 2
    job_timeout_s: float = 10.0
    job_memory_mb: int = 512
    asset_dir: str = "/var/cache/ui-renderer/assets"
    start_timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(workers=int(e("UI_RENDERER_WORKERS", "2")),
                   job_timeout_s=float(e("UI_RENDERER_JOB_TIMEOUT_S", "10")),
                   job_memory_mb=int(e("UI_RENDERER_JOB_MEMORY_MB", "512")),
                   asset_dir=e("UI_RENDERER_ASSET_DIR", "/var/cache/ui-renderer/assets"))


# --- jobs (run inside a worker process) ------------------------------------

def _err(code: str, message: str, **detail: Any) -> dict:
    return {"ok": False, "code": code, "message": message, **detail}


def _asset_path(cfg: Config, sha: str) -> Path:
    return Path(cfg.asset_dir) / f"{sha}.png"


def _asset_info(cfg: Config, sha: str):
    from PIL import Image
    from agentplatform.ui_designer.svg import AssetInfo
    with Image.open(_asset_path(cfg, sha)) as im:
        return AssetInfo(href=f"{sha}.png", w=im.width, h=im.height)


def job_asset(cfg: Config, req: dict) -> dict:
    from PIL import Image, ImageOps
    sha, data = req["sha"], req["data"]
    if hashlib.sha256(data).hexdigest() != sha:
        return _err("UD-INVALID", "body does not hash to the asset id")
    Image.MAX_IMAGE_PIXELS = 25_000_000   # Pillow raises above twice this: 50 MP
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ("PNG", "JPEG", "GIF", "WEBP"):
            return _err("UD-INVALID", f"unsupported image format {im.format}")
        im.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return _err("UD-INVALID", "image above 50 megapixels")
    except Exception as e:  # corrupt or truncated input
        return _err("UD-INVALID", f"unreadable image: {type(e).__name__}")
    im = ImageOps.exif_transpose(im)
    if im.width * im.height > ASSET_MAX_PIXELS:
        f = (ASSET_MAX_PIXELS / (im.width * im.height)) ** 0.5
        im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
    has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    im = im.convert("RGBA" if has_alpha else "RGB")
    out = _asset_path(cfg, sha)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.tmp")
    im.save(tmp, "PNG", compress_level=3)
    os.replace(tmp, out)
    return {"ok": True, "width": im.width, "height": im.height}


def _raster(svg: str, w: int, h: int, background: str | None, res_dir: str) -> bytes:
    import resvg_py
    return resvg_py.svg_to_bytes(svg_string=svg, width=w, height=h, resources_dir=res_dir,
                                 skip_system_fonts=True, font_dirs=[], background=background)


def ladder(svg: str, p, scale: float, max_edge: int, max_bytes: int | None,
           background: str | None, res_dir: str) -> dict:
    """PNG at the requested size; over `max_bytes`, PNG at long edge 1280, then
    1024, then WebP 85, then JPEG 85 (design 46 section 3.1); still over:
    UD-IMAGE-TOO-LARGE carrying the node's box."""
    from PIL import Image
    from agentplatform.ui_designer.errors import UD_IMAGE_TOO_LARGE, UD_RENDER_FAILED, UiDesignerError
    w, h = p.w * scale, p.h * scale
    if max(w, h) > max_edge:
        f = max_edge / max(w, h)
        w, h = w * f, h * f
    W, H = max(1, round(w)), max(1, round(h))
    if W * H > MAX_PIXELS:
        raise UiDesignerError(UD_RENDER_FAILED, f"output {W}x{H} exceeds {MAX_PIXELS // 1_000_000} megapixels")
    sizes = [(W, H)]
    for edge in TIERS:
        if max(W, H) > edge:
            f = edge / max(W, H)
            sizes.append((max(1, round(W * f)), max(1, round(H * f))))
    png = b""
    for tw, th in sizes:
        png = _raster(svg, tw, th, background, res_dir)
        if not max_bytes or len(png) <= max_bytes:
            return {"format": "png", "mime": "image/png", "width": tw, "height": th, "data": png}
    tw, th = sizes[-1]
    im = Image.open(io.BytesIO(png))
    im.load()
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=85)
    if len(buf.getvalue()) <= max_bytes:
        return {"format": "webp", "mime": "image/webp", "width": tw, "height": th, "data": buf.getvalue()}
    flat = Image.new("RGB", im.size, background if background and background.startswith("#") else "#ffffff")
    flat.paste(im, mask=im.getchannel("A") if "A" in im.getbands() else None)
    buf = io.BytesIO()
    flat.save(buf, "JPEG", quality=85)
    if len(buf.getvalue()) <= max_bytes:
        return {"format": "jpeg", "mime": "image/jpeg", "width": tw, "height": th, "data": buf.getvalue()}
    raise UiDesignerError(UD_IMAGE_TOO_LARGE, f"{len(buf.getvalue())} bytes after the ladder (limit {max_bytes})",
                          detail={"box": [round(p.x, 1), round(p.y, 1), round(p.w, 1), round(p.h, 1)],
                                  "bytes": len(buf.getvalue()), "limit": max_bytes})


def _node_assets(node: dict) -> list[str]:
    from agentplatform.ui_designer.svg import assets_used
    kids = node["children"] if node.get("type") == "page" else [node]
    return assets_used({"pages": [{"children": kids}]})


def job_render(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer import svg
    from agentplatform.ui_designer.layout import layout_document
    Path(cfg.asset_dir).mkdir(parents=True, exist_ok=True)
    resolved = layout_document(req["doc"], req["mode"])
    nodes = req["nodes"] or [None]
    found = [svg.find_node(resolved, n)[0] for n in nodes]
    need: dict[str, None] = {}
    for n in found:
        for s in _node_assets(n):
            need.setdefault(s)
    missing = [s for s in need if not _asset_path(cfg, s).is_file()]
    if missing:
        return {"ok": False, "code": "MISSING-ASSETS", "missing": missing}
    infos = {s: _asset_info(cfg, s) for s in need}
    images = []
    for nid, node in zip(nodes, found):
        p = svg.paint(resolved, nid, annotate=req["annotate"], assets=infos)
        out = ladder(p.svg, p, req["scale"], req["max_edge"], req["max_bytes"], req["background"],
                     cfg.asset_dir)
        out.update(node=nid if nid is not None else p.node.get("id"), bytes=len(out["data"]),
                   boxes=svg.boxes(p.node))
        images.append(out)
    return {"ok": True, "renderer_version": svg.renderer_version(), "images": images,
            "warnings": resolved.get("warnings", [])}


def job_svg(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer import svg
    from agentplatform.ui_designer.layout import layout_document
    resolved = layout_document(req["doc"], req["mode"])
    prefix = svg.safe_prefix(req["asset_prefix"])
    dims = req["assets"]
    infos = {s: svg.AssetInfo(prefix + s, d.get("w"), d.get("h")) for s, d in dims.items()}
    p = svg.paint(resolved, req["node"], annotate=req["annotate"], assets=infos,
                  href=lambda s: prefix + s)
    return {"ok": True, "renderer_version": svg.renderer_version(), "svg": p.svg,
            "box": [p.x, p.y, p.w, p.h], "warnings": resolved.get("warnings", [])}


def _slim(n: dict) -> dict:
    out = {k: v for k, v in n.items() if k not in ("children", "text")}
    if isinstance(n.get("text"), dict):
        out["text"] = {"align": n["text"].get("align"), "truncated": n["text"].get("truncated"),
                       "lines": [{k: v for k, v in ln.items() if k != "runs"} for ln in n["text"].get("lines", [])]}
    if n.get("children"):
        out["children"] = [_slim(c) for c in n["children"]]
    return out


def job_resolved(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer.layout import layout_document
    r = layout_document(req["doc"], req["mode"])
    return {"ok": True, "tree": {**{k: v for k, v in r.items() if k not in ("glyphs", "pages")},
                                 "pages": [{**pg, "children": [_slim(c) for c in pg.get("children", [])]}
                                           for pg in r["pages"]]}}


JOBS = {"asset": job_asset, "render": job_render, "svg": job_svg, "resolved": job_resolved}


def _run_job(cfg: Config, kind: str, req: dict) -> dict:
    from agentplatform.ui_designer.errors import UD_RENDER_FAILED, UiDesignerError
    try:
        return JOBS[kind](cfg, req)
    except UiDesignerError as e:
        return {**e.as_dict(), "ok": False}
    except Exception as e:
        log.exception("job %s failed", kind)
        return _err(UD_RENDER_FAILED, f"{type(e).__name__}: {str(e)[:200]}")


def _worker_main(conn, cfg: Config) -> None:
    try:
        import resource
        lim = cfg.job_memory_mb << 20
        resource.setrlimit(resource.RLIMIT_DATA, (lim, lim))
    except (ImportError, ValueError, OSError) as e:  # not enforceable here (macOS dev); the cgroup remains
        log.warning("RLIMIT_DATA not set: %s", e)
    from agentplatform.ui_designer import svg  # import fonts, harfbuzz and resvg once per worker
    svg.renderer_version()
    conn.send(("ready", None))
    while True:
        try:
            kind, req = conn.recv()
        except EOFError:
            return
        conn.send(("done", _run_job(cfg, kind, req)))


# --- the pool --------------------------------------------------------------

class JobFailed(Exception):
    pass


class _Worker:
    def __init__(self, cfg: Config):
        ctx = mp.get_context("spawn")
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker_main, args=(child, cfg), daemon=True)
        self.proc.start()
        child.close()
        self.ready = False

    def call(self, kind: str, req: dict, timeout: float, start_timeout: float) -> dict:
        try:
            if not self.ready:
                if not self.conn.poll(start_timeout):
                    raise JobFailed("worker did not start")
                self.conn.recv()
                self.ready = True
            self.conn.send((kind, req))
            if not self.conn.poll(timeout):
                raise JobFailed(f"timed out after {timeout:g}s")
            return self.conn.recv()[1]
        except (EOFError, OSError, BrokenPipeError):
            raise JobFailed("worker exited (over its memory limit or crashed)") from None

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.join(2)
        except Exception:
            pass
        self.conn.close()


class Pool:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self.all: list[_Worker] = []

    def start(self) -> None:
        for _ in range(self.cfg.workers):
            w = _Worker(self.cfg)
            self.all.append(w)
            self.idle.put_nowait(w)

    def close(self) -> None:
        for w in self.all:
            w.kill()

    async def run(self, kind: str, req: dict) -> dict:
        w = await self.idle.get()
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, w.call, kind, req, self.cfg.job_timeout_s, self.cfg.start_timeout_s)
        except JobFailed:
            w.kill()
            self.all.remove(w)
            w = _Worker(self.cfg)
            self.all.append(w)
            raise
        finally:
            self.idle.put_nowait(w)


# --- HTTP ------------------------------------------------------------------

def _status(res: dict) -> int:
    if res.get("ok"):
        return 200
    return {"MISSING-ASSETS": 409, "UD-RENDER-FAILED": 500, "UD-INVALID": 422}.get(res.get("code"), 422)


async def _job(request: web.Request, kind: str, req: dict) -> web.Response:
    t0 = time.monotonic()
    try:
        res = await request.app[POOL].run(kind, req)
    except JobFailed as e:
        log.warning("%s job failed: %s", kind, e)
        return web.json_response(_err("UD-RENDER-FAILED", str(e)), status=504)
    log.info("%s %s %.0fms", kind, "ok" if res.get("ok") else res.get("code"), (time.monotonic() - t0) * 1000)
    if kind == "render" and res.get("ok"):
        for im in res["images"]:
            im["data"] = base64.b64encode(im["data"]).decode()
    return web.json_response(res, status=_status(res))


POOL = web.AppKey("pool", Pool)


class BadRequest(Exception):
    pass


def _common(body: Any) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("doc"), dict):
        raise BadRequest("body must be an object with a `doc` object")
    mode = body.get("mode")
    if mode is not None and not isinstance(mode, str):
        raise BadRequest("`mode` must be a string")
    return {"doc": body["doc"], "mode": mode, "annotate": bool(body.get("annotate", False))}


def _render_req(body: Any) -> dict:
    req = _common(body)
    nodes = body.get("nodes") or []
    if (not isinstance(nodes, list) or len(nodes) > 4
            or not all(n is None or isinstance(n, str) for n in nodes)):
        raise BadRequest("`nodes` is a list of at most 4 ids")
    scale = body.get("scale", 1)
    if scale not in SCALES:
        raise BadRequest(f"`scale` is one of {list(SCALES)}")
    max_edge = body.get("max_edge", 1568)
    if not isinstance(max_edge, int) or isinstance(max_edge, bool) or not 64 <= max_edge <= 4096:
        raise BadRequest("`max_edge` is an integer in 64..4096")
    max_bytes = body.get("max_bytes")
    if max_bytes is not None and (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0):
        raise BadRequest("`max_bytes` is a non-negative integer")
    bg = body.get("background")
    if bg is not None and not (isinstance(bg, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", bg)):
        raise BadRequest("`background` is #rrggbb")
    req.update(nodes=nodes, scale=scale, max_edge=max_edge, max_bytes=max_bytes or None, background=bg)
    return req


def _svg_req(body: Any) -> dict:
    req = _common(body)
    node = body.get("node")
    if node is not None and not isinstance(node, str):
        raise BadRequest("`node` must be a string")
    assets = body.get("assets") or {}
    if not isinstance(assets, dict) or not all(
            SHA_RE.fullmatch(k) and isinstance(v, dict) and all(
                isinstance(v.get(d), int) and v[d] > 0 for d in ("w", "h") if d in v)
            for k, v in assets.items()):
        raise BadRequest("`assets` maps sha256 -> {w, h}")
    prefix = body.get("asset_prefix", "/api/ui-designer/assets/")
    from agentplatform.ui_designer.errors import UiDesignerError
    from agentplatform.ui_designer.svg import safe_prefix
    try:
        safe_prefix(prefix)
    except UiDesignerError:
        raise BadRequest("`asset_prefix` must be a plain URL path prefix") from None
    req.update(node=node, asset_prefix=prefix, assets=assets)
    return req


async def _post(request: web.Request, kind: str, parse) -> web.Response:
    try:
        body = await request.json()
        req = parse(body)
    except (BadRequest, ValueError) as e:
        return web.json_response(_err("UD-INVALID", str(e) or "bad JSON"), status=400)
    return await _job(request, kind, req)


async def put_asset(request: web.Request) -> web.Response:
    sha = request.match_info["sha"]
    if not SHA_RE.fullmatch(sha):
        return web.json_response(_err("UD-INVALID", "bad asset id"), status=400)
    data = await request.read()
    if not data or len(data) > ASSET_MAX_BYTES:
        return web.json_response(_err("UD-INVALID", f"asset must be 1..{ASSET_MAX_BYTES} bytes"), status=400)
    return await _job(request, "asset", {"sha": sha, "data": data})


def create_app(cfg: Config | None = None) -> web.Application:
    cfg = cfg or Config.from_env()
    app = web.Application(client_max_size=BODY_MAX)
    
    async def health(_: web.Request) -> web.Response:
        from agentplatform.ui_designer.svg import renderer_version
        return web.json_response({"ok": True, "renderer_version": renderer_version()})

    async def on_start(a: web.Application) -> None:
        a[POOL] = Pool(cfg)
        a[POOL].start()

    async def on_stop(a: web.Application) -> None:
        a[POOL].close()

    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    app.router.add_get("/healthz", health)
    app.router.add_put("/assets/{sha}", put_asset)
    async def render(r: web.Request) -> web.Response:
        return await _post(r, "render", _render_req)

    async def svg_(r: web.Request) -> web.Response:
        return await _post(r, "svg", _svg_req)

    async def resolved(r: web.Request) -> web.Response:
        return await _post(r, "resolved", _common)

    app.router.add_post("/render", render)
    app.router.add_post("/svg", svg_)
    app.router.add_post("/resolved", resolved)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    web.run_app(create_app(), host="0.0.0.0", port=8000, access_log=None)
