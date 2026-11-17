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
import copy
import json
from collections import OrderedDict
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
    # Admission: requests waiting for a worker beyond `queue_max` are refused at
    # once, and one that waits longer than `queue_deadline_s` gives up (503).
    queue_max: int = 8
    queue_deadline_s: float = 10.0
    process_group: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(workers=int(e("UI_RENDERER_WORKERS", "2")),
                   job_timeout_s=float(e("UI_RENDERER_JOB_TIMEOUT_S", "10")),
                   job_memory_mb=int(e("UI_RENDERER_JOB_MEMORY_MB", "512")),
                   asset_dir=e("UI_RENDERER_ASSET_DIR", "/var/cache/ui-renderer/assets"),
                   queue_max=int(e("UI_RENDERER_QUEUE_MAX", "8")),
                   queue_deadline_s=float(e("UI_RENDERER_QUEUE_DEADLINE_S", "10")))


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


def _paint_assets(resolved: dict, node_id: str | None) -> list[str]:
    """Effects read preceding page context; ordinary crops keep narrow assets."""
    from agentplatform.ui_designer import svg, raster_effects
    from agentplatform.ui_designer.errors import UiDesignerError
    node = svg.find_node(resolved, node_id)[0]
    if not raster_effects.required(resolved, node_id):
        return _node_assets(node)
    for page in resolved.get("pages", []):
        try:
            svg.find_node({"pages": [page]}, node_id)
        except UiDesignerError as error:
            if error.code == "UD-OP-TARGET":
                continue
            raise
        return svg.assets_used({"pages": [page]})
    return []


def _effect_bytes(cfg: Config, resolved: dict, node_id: str | None, need: list[str]) -> dict:
    from agentplatform.ui_designer import raster_effects
    from agentplatform.ui_designer.errors import UiDesignerError
    if not raster_effects.required(resolved, node_id):
        return {}
    # Cache files are created only by job_asset after source-hash verification.
    # No renderer inputs are interpreted as paths or network locations.
    total = 0
    result = {}
    for sha in need:
        path = _asset_path(cfg, sha)
        total += path.stat().st_size
        if total > 128 << 20:
            raise UiDesignerError("UD-IMAGE-TOO-LARGE", "effects asset inputs exceed 128MiB")
        result[sha] = path.read_bytes()
    return result


def job_render(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer import svg
    from agentplatform.ui_designer.layout import layout_document
    Path(cfg.asset_dir).mkdir(parents=True, exist_ok=True)
    resolved = layout_document(req["doc"], req["mode"])
    nodes = req["nodes"] or [None]
    found = [svg.find_node(resolved, n)[0] for n in nodes]
    need: dict[str, None] = {}
    for nid in nodes:
        for s in _paint_assets(resolved, nid):
            need.setdefault(s)
    missing = [s for s in need if not _asset_path(cfg, s).is_file()]
    if missing:
        return {"ok": False, "code": "MISSING-ASSETS", "missing": missing}
    infos = {s: _asset_info(cfg, s) for s in need}
    images = []
    from agentplatform.ui_designer.raster_effects import new_budget
    effect_budget = new_budget()
    for nid, node in zip(nodes, found):
        # Match the actual ladder viewport, including small thumbnails.
        box = svg._box(node)
        effect_scale = min(req["scale"], req["max_edge"] / max(1, box[2], box[3]))
        p = svg.paint(resolved, nid, annotate=req["annotate"], assets=infos,
                      asset_bytes=_effect_bytes(cfg, resolved, nid, list(need)), effect_scale=effect_scale,
                      effect_budget=effect_budget)
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
    need = _paint_assets(resolved, req["node"])
    from agentplatform.ui_designer import raster_effects
    if raster_effects.required(resolved, req["node"]):
        missing = [sha for sha in need if not _asset_path(cfg, sha).is_file()]
        if missing:
            return {"ok": False, "code": "MISSING-ASSETS", "missing": missing}
        # Effects sample actual verified cache pixels, including preceding
        # siblings absent from the selected subtree and caller size hints.
        infos = {sha: _asset_info(cfg, sha) for sha in need}
    p = svg.paint(resolved, req["node"], annotate=req["annotate"], assets=infos,
                  href=lambda s: prefix + s,
                  asset_bytes=_effect_bytes(cfg, resolved, req["node"], need))
    return {"ok": True, "renderer_version": svg.renderer_version(), "svg": p.svg,
            "box": [p.x, p.y, p.w, p.h], "warnings": resolved.get("warnings", [])}


def job_export(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer import svg
    from agentplatform.ui_designer.export_formats import MIMES, bounded, html_document, raster_pdf, standalone_svg
    from agentplatform.ui_designer.layout import layout_document
    resolved = layout_document(req["doc"], req["mode"])
    node = svg.find_node(resolved, req["node"])[0]
    need = _paint_assets(resolved, req["node"])
    missing = [sha for sha in need if not _asset_path(cfg, sha).is_file()]
    if missing:
        return {"ok": False, "code": "MISSING-ASSETS", "missing": missing}
    infos = {}
    # Bound encoded inputs before constructing a potentially huge SVG string.
    total = 0
    for sha in need:
        raw = _asset_path(cfg, sha).read_bytes()
        total += len(raw) * 4 // 3 + 4
        if total > req["max_bytes"]:
            from agentplatform.ui_designer.errors import UiDesignerError
            raise UiDesignerError("UD-IMAGE-TOO-LARGE", "embedded assets exceed the export byte cap")
        info = _asset_info(cfg, sha)
        infos[sha] = svg.AssetInfo("data:image/png;base64," + base64.b64encode(raw).decode(), info.w, info.h)
    warnings_ = []
    if req["format"] == "html":
        data, painted, notes = html_document(req["doc"], resolved, req["node"], infos, req["max_bytes"],
            asset_bytes=_effect_bytes(cfg, resolved, req["node"], need), effect_scale=req["scale"])
        warnings_.extend(notes)
    else:
        data, painted = standalone_svg(resolved, req["node"], infos, req["max_bytes"],
            asset_bytes=_effect_bytes(cfg, resolved, req["node"], need), effect_scale=req["scale"])
        if req["format"] == "pdf":
            width, height = max(1, round(painted.w * req["scale"])), max(1, round(painted.h * req["scale"]))
            if width * height > MAX_PIXELS or max(width, height) > 4096:
                from agentplatform.ui_designer.errors import UiDesignerError
                raise UiDesignerError("UD-IMAGE-TOO-LARGE", "PDF raster is at most 4096 pixels per edge and 10 megapixels")
            png = _raster(data.decode(), width, height, None, cfg.asset_dir)
            data = raster_pdf(png, max(1, painted.w), max(1, painted.h), req["max_bytes"])
            warnings_.append("PDF contains a raster image at the requested scale; text is not selectable and transparency is flattened onto white.")
    # Warnings are inert human-readable strings, bounded across the transport.
    notes = [str(w.get("message", w)) if isinstance(w, dict) else str(w) for w in warnings_ + list(resolved.get("warnings", []))]
    return {"ok": True, "renderer_version": svg.renderer_version(), "format": req["format"],
            "mime": MIMES[req["format"]], "data": data, "width": width if req["format"] == "pdf" else max(1, round(painted.w)),
            "height": height if req["format"] == "pdf" else max(1, round(painted.h)), "box": [painted.x, painted.y, painted.w, painted.h],
            "warnings": [n.encode()[:500].decode(errors="ignore") for n in notes[:32]]}


def _job_diff_uncached(cfg: Config, req: dict) -> dict:
    from PIL import Image
    from agentplatform.ui_designer import svg, visual_diff
    from agentplatform.ui_designer.errors import UiDesignerError
    from agentplatform.ui_designer.layout import layout_document
    Path(cfg.asset_dir).mkdir(parents=True, exist_ok=True)
    resolved = [layout_document(req[key], req['mode']) for key in ('before_doc', 'after_doc')]
    selected = []
    for tree in resolved:
        try:
            selected.append(svg.find_node(tree, req['node'])[0])
        except UiDesignerError as exc:
            if exc.code != 'UD-OP-TARGET':
                raise
            selected.append(None)
    if not any(selected):
        raise UiDesignerError('UD-OP-TARGET', 'selected node is missing from both versions')
    need = list(dict.fromkeys(sha for tree,node in zip(resolved,selected) if node for sha in _paint_assets(tree, req['node'])))
    missing = [sha for sha in need if not _asset_path(cfg, sha).is_file()]
    if missing:
        return {'ok': False, 'code': 'MISSING-ASSETS', 'missing': missing}
    infos = {sha:_asset_info(cfg, sha) for sha in need}
    from agentplatform.ui_designer.raster_effects import new_budget
    effect_budget = new_budget()
    paints = [svg.paint(tree, req['node'], assets=infos, asset_bytes=_effect_bytes(cfg, tree, req['node'], need), effect_scale=req['scale'], effect_budget=effect_budget) if node else None for tree,node in zip(resolved,selected)]
    box, width, height = visual_diff.union_box([[p.x,p.y,p.w,p.h] for p in paints if p], req['scale'])
    images = []
    for painted in paints:
        if painted is None:
            images.append(Image.new('RGBA', (width,height)))
        else:
            source = visual_diff.viewport(painted.svg, box, width, height)
            image = Image.open(io.BytesIO(_raster(source, width, height, None, cfg.asset_dir)))
            image.load()
            images.append(image.convert('RGBA'))
    nodes = [item for node in selected for item in visual_diff.node_boxes(node)]
    result = visual_diff.compare(*images, box, req['scale'], nodes, req['tolerance'], req['max_bytes'])
    return {'ok': True, 'renderer_version':svg.renderer_version(), **result}


# Each worker has its own cache. Byte strings are immutable; deep-copy only the
# response containers so HTTP base64 serialization never poisons cached bytes.
_DIFF_CACHE: OrderedDict[str, tuple[dict, int]] = OrderedDict()
_DIFF_CACHE_BYTES = 32 << 20
_DIFF_CACHE_ENTRIES = 4


def job_diff(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer.svg import renderer_version
    encoded = json.dumps(req, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    key = hashlib.sha256((renderer_version() + cfg.asset_dir).encode() + encoded).hexdigest()
    cached = _DIFF_CACHE.get(key)
    if cached is not None:
        _DIFF_CACHE.move_to_end(key)
        return copy.deepcopy(cached[0])
    result = _job_diff_uncached(cfg, req)
    if result.get('ok'):
        size = sum(len(result.get(field, b'')) for field in ('image', 'base_image'))
        size += len(json.dumps({k:v for k,v in result.items() if k not in ('image','base_image')}).encode())
        if size <= _DIFF_CACHE_BYTES:
            while _DIFF_CACHE and (len(_DIFF_CACHE) >= _DIFF_CACHE_ENTRIES or
                                  sum(item[1] for item in _DIFF_CACHE.values()) + size > _DIFF_CACHE_BYTES):
                _DIFF_CACHE.popitem(last=False)
            _DIFF_CACHE[key] = (copy.deepcopy(result), size)
    return result


_LINT_CACHE: OrderedDict[str, tuple[dict, int]] = OrderedDict()


def job_lint(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer.lint import RULES_VERSION, findings
    from agentplatform.ui_designer.svg import renderer_version
    key = hashlib.sha256((RULES_VERSION + renderer_version()).encode() +
        json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    cached = _LINT_CACHE.get(key)
    if cached is not None:
        _LINT_CACHE.move_to_end(key)
        return copy.deepcopy(cached[0])
    result = {'renderer_version':renderer_version(), **findings(req['doc'],req['mode'],req['node'])}
    size = len(json.dumps(result,ensure_ascii=False).encode())
    if result.get('ok') and size <= 32 << 20:
        while _LINT_CACHE and (len(_LINT_CACHE) >= 8 or sum(item[1] for item in _LINT_CACHE.values()) + size > 32 << 20):
            _LINT_CACHE.popitem(last=False)
        _LINT_CACHE[key] = (copy.deepcopy(result),size)
    return result


def job_boolean(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer.vector_geometry import boolean_geometry
    from agentplatform.ui_designer.svg import renderer_version
    return {'ok':True, 'renderer_version':renderer_version(), **boolean_geometry(req['operands'],req['kind'])}


def _slim(n: dict) -> dict:
    out = {k: v for k, v in n.items() if k not in ("children", "text")}
    if isinstance(n.get("text"), dict):
        out["text"] = {"align": n["text"].get("align"), "truncated": n["text"].get("truncated"),
                       "lines": [{k: v for k, v in ln.items() if k != "runs"} for ln in n["text"].get("lines", [])]}
        for field in ("vertical_align", "text_case", "decoration"):
            if field in n["text"]:
                out["text"][field] = n["text"][field]
    if n.get("children"):
        out["children"] = [_slim(c) for c in n["children"]]
    return out


def job_resolved(cfg: Config, req: dict) -> dict:
    from agentplatform.ui_designer.layout import layout_document
    r = layout_document(req["doc"], req["mode"])
    return {"ok": True, "tree": {**{k: v for k, v in r.items() if k not in ("glyphs", "pages")},
                                 "pages": [{**pg, "children": [_slim(c) for c in pg.get("children", [])]}
                                           for pg in r["pages"]]}}


_MOTION_CACHE: OrderedDict[str, dict] = OrderedDict()


def job_motion_preview(cfg: Config, req: dict) -> dict:
    Path(cfg.asset_dir).mkdir(parents=True,exist_ok=True)
    from agentplatform.ui_designer import motion, svg
    from agentplatform.ui_designer.layout import layout_document
    key = hashlib.sha256((svg.renderer_version()+cfg.asset_dir).encode()+json.dumps(req,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if key in _MOTION_CACHE:
        _MOTION_CACHE.move_to_end(key)
        return copy.deepcopy(_MOTION_CACHE[key])
    resolved = layout_document(req["doc"],req.get("mode"))
    need = list(dict.fromkeys(sha for target in (req["from_node"],req["to_node"]) for sha in _paint_assets(resolved,target)))
    missing = [sha for sha in need if not _asset_path(cfg,sha).is_file()]
    if missing:return {"ok":False,"code":"MISSING-ASSETS","missing":missing}
    infos={sha:_asset_info(cfg,sha) for sha in need}
    raw={};total=0
    for sha in need:
        path=_asset_path(cfg,sha);total+=path.stat().st_size
        if total>16<<20:return _err("UD-IMAGE-TOO-LARGE","motion source images exceed16MiB")
        raw[sha]=path.read_bytes()
    result={"ok":True,**motion.prepare(**req,assets=infos,asset_bytes=raw,raster=lambda source,w,h:_raster(source,w,h,None,cfg.asset_dir))}
    # One immutable manifest fits both preview and its immediate export preflight.
    _MOTION_CACHE.clear();_MOTION_CACHE[key]=copy.deepcopy(result)
    return result


def job_motion_export(cfg: Config, req: dict) -> dict:
    import subprocess
    import tempfile
    from agentplatform.ui_designer import motion
    format_=req["format"]
    preview={k:v for k,v in req.items() if k not in ("format","max_bytes")}
    if format_=="gif":preview["max_edge"]=min(480,preview["max_edge"])
    manifest=job_motion_preview(cfg,preview)
    if not manifest.get("ok"):return manifest
    if manifest["width"]*manifest["height"]*manifest["frames"]>(28_000_000 if format_=="gif" else 64_000_000):
        return _err("UD-IMAGE-TOO-LARGE","motion frame work exceeds format limit")
    directory=os.environ.get("UI_MOTION_ENCODER_DIR",str(Path(__file__).with_name("motion_encoder")))
    bridge=Path(directory)/"encode.mjs"
    motion_root=Path(tempfile.gettempdir())/f"ui-motion-worker-{os.getpid()}"
    motion_root.mkdir(mode=0o700,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="job-",dir=motion_root) as tmp:
        total=0
        for i in range(manifest["frames"]):
            im=motion.render_frame(manifest,i)
            flat=__import__('PIL.Image',fromlist=['Image']).new('RGB',im.size,'#ffffff')
            flat.paste(im,mask=im.getchannel('A'))
            path=Path(tmp)/f"frame-{i:04d}.png";flat.save(path,'PNG')
            total+=path.stat().st_size
            if total>96<<20:return _err("UD-IMAGE-TOO-LARGE","motion temporary frames exceed96MiB")
        (Path(tmp)/"encode.json").write_text(json.dumps({k:manifest[k] for k in ('width','height','frames','fps')}|{"format":format_}))
        def encoder_limits():
            # V8 reserves a large virtual cage. Native Python keeps its soft
            # DATA limit; the Node child uses its explicit64MiB JS heap and the
            # pod cgroup, bounded frames and encoder threads instead.
            import resource
            soft,hard=resource.getrlimit(resource.RLIMIT_DATA)
            resource.setrlimit(resource.RLIMIT_DATA,(hard,hard))
            resource.setrlimit(resource.RLIMIT_FSIZE,(8<<20,8<<20))
        logpath=Path(tmp)/"encoder.log"
        with logpath.open('wb') as logstream:
            proc=subprocess.Popen(['node','--max-old-space-size=64',str(bridge),tmp],stdout=logstream,stderr=subprocess.STDOUT,env={**os.environ,'TMPDIR':tmp,'NODE_OPTIONS':''},preexec_fn=encoder_limits if os.name=='posix' else None)
            try:status=proc.wait(timeout=50)
            except subprocess.TimeoutExpired:
                if cfg.process_group:os.killpg(os.getpgrp(),__import__('signal').SIGKILL)
                proc.kill();proc.wait();return _err("UD-RENDER-FAILED","motion encoder timed out")
        if status:
            log.warning('motion encoder failed: %s',logpath.read_bytes()[-2000:].decode(errors='replace'))
            return _err("UD-RENDER-FAILED","motion encoder failed")
        output=Path(tmp)/f"output.{format_}"
        if not output.is_file() or output.stat().st_size>req["max_bytes"]:
            return _err("UD-IMAGE-TOO-LARGE","motion output exceeds8MiB")
        data=output.read_bytes()
        return {"ok":True,"renderer_version":manifest["renderer_version"],"data":data,"mime":"video/mp4" if format_=='mp4' else 'image/gif',"format":format_,
                **{k:manifest[k] for k in ('width','height','frames','duration_ms','fps')},"warnings":manifest['warnings']+["Motion export is silent and transparency is flattened onto white; GIF colors use a palette."]}


JOBS = {"asset": job_asset, "render": job_render, "svg": job_svg, "resolved": job_resolved, "export": job_export, "diff": job_diff, "lint": job_lint, "boolean": job_boolean, "motion_preview":job_motion_preview, "motion_export":job_motion_export}


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
    if cfg.process_group and hasattr(os,"setsid"):
        os.setsid()
    try:
        import resource
        lim = cfg.job_memory_mb << 20
        resource.setrlimit(resource.RLIMIT_DATA, (lim, resource.RLIM_INFINITY if cfg.process_group else lim))
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
        self.killed = False
        self.group = cfg.process_group

    def call(self, kind: str, req: dict, timeout: float, start_timeout: float) -> dict:
        def poll(timeout):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if self.killed:raise JobFailed("worker cancelled")
                if self.conn.poll(min(.1,max(0,deadline-time.monotonic()))):return True
            return False
        try:
            if not self.ready:
                if not poll(start_timeout):
                    raise JobFailed("worker did not start")
                self.conn.recv()
                self.ready = True
            self.conn.send((kind, req))
            if not poll(timeout):
                raise JobFailed(f"timed out after {timeout:g}s")
            return self.conn.recv()[1]
        except (EOFError, OSError, BrokenPipeError):
            raise JobFailed("worker exited (over its memory limit or crashed)") from None

    def kill(self) -> None:
        self.killed = True
        try:
            if self.group and hasattr(os,"killpg"):
                try:os.killpg(self.proc.pid,__import__('signal').SIGKILL)
                except ProcessLookupError:pass
            self.proc.kill()
            self.proc.join(2)
        except Exception:
            pass
        self.conn.close()
        if self.group:
            import tempfile, shutil
            shutil.rmtree(Path(tempfile.gettempdir())/f"ui-motion-worker-{self.proc.pid}",ignore_errors=True)


class Busy(Exception):
    """No worker free and no room (or no time) to wait for one."""


class Pool:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self.all: list[_Worker] = []
        self.waiting = 0
        self.closed = False

    def start(self) -> None:
        for _ in range(self.cfg.workers):
            w = _Worker(self.cfg)
            self.all.append(w)
            self.idle.put_nowait(w)

    def close(self) -> None:
        self.closed = True
        for w in list(self.all):
            w.kill()

    async def _acquire(self) -> _Worker:
        if self.idle.empty() and self.waiting >= self.cfg.queue_max:
            raise Busy("renderer queue is full")
        self.waiting += 1
        try:
            return await asyncio.wait_for(self.idle.get(), self.cfg.queue_deadline_s)
        except asyncio.TimeoutError:
            raise Busy("renderer queue wait timed out") from None
        finally:
            self.waiting -= 1

    async def run(self, kind: str, req: dict) -> dict:
        # Waiting is cancellable (a client that left frees its place); a job
        # already on a worker is shielded so the worker is never handed back
        # while still mid-call.
        w = await self._acquire()
        task=asyncio.ensure_future(self._run_on(w,kind,req))
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            if self.cfg.process_group:
                w.kill()
                try:await asyncio.shield(task)
                except (JobFailed,asyncio.CancelledError):pass
            raise

    async def _run_on(self, w: "_Worker", kind: str, req: dict) -> dict:
        try:
            return await asyncio.get_running_loop().run_in_executor(
                None, w.call, kind, req, self.cfg.job_timeout_s, self.cfg.start_timeout_s)
        except JobFailed:
            w.kill()
            if w in self.all:self.all.remove(w)
            if not self.closed:
                w = _Worker(self.cfg)
                self.all.append(w)
            raise
        finally:
            if not self.closed:self.idle.put_nowait(w)


# --- HTTP ------------------------------------------------------------------

def _status(res: dict) -> int:
    if res.get("ok"):
        return 200
    return {"MISSING-ASSETS": 409, "UD-RENDER-FAILED": 500, "UD-INVALID": 422}.get(res.get("code"), 422)


async def _job(request: web.Request, kind: str, req: dict) -> web.Response:
    t0 = time.monotonic()
    try:
        res = await request.app[MOTION_POOL if kind.startswith("motion_") else POOL].run(kind, req)
    except Busy as e:
        log.warning("%s refused: %s", kind, e)
        return web.json_response(_err("UD-RENDER-FAILED", f"{e}; retry shortly"), status=503,
                                 headers={"Retry-After": "2"})
    except JobFailed as e:
        log.warning("%s job failed: %s", kind, e)
        return web.json_response(_err("UD-RENDER-FAILED", str(e)), status=504)
    log.info("%s %s %.0fms", kind, "ok" if res.get("ok") else res.get("code"), (time.monotonic() - t0) * 1000)
    if kind == "render" and res.get("ok"):
        for im in res["images"]:
            im["data"] = base64.b64encode(im["data"]).decode()
    if kind in ("export","motion_export") and res.get("ok"):
        res["data"] = base64.b64encode(res["data"]).decode()
    if kind == "diff" and res.get("ok"):
        for key in ("image", "base_image"):
            res[key] = base64.b64encode(res[key]).decode()
    return web.json_response(res, status=_status(res))


POOL = web.AppKey("pool", Pool)
MOTION_POOL = web.AppKey("motion_pool", Pool)


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


def _boolean_req(body: Any) -> dict:
    if not isinstance(body,dict) or set(body) != {'kind','operands'}:
        raise BadRequest('boolean accepts only kind and operands')
    from agentplatform.ui_designer.vector_geometry import validate_operands
    from agentplatform.ui_designer.errors import UiDesignerError
    try:
        validate_operands(body['operands'],body['kind'])
    except UiDesignerError as error:
        raise BadRequest(str(error)) from None
    return body


def _lint_req(body: Any) -> dict:
    req = _common(body)
    if set(body) - {'doc','node','mode'}:
        raise BadRequest('unknown lint field')
    node = body.get('node')
    if node is not None and (not isinstance(node,str) or not node or len(node)>512):
        raise BadRequest('lint node must be an id')
    req.pop('annotate')
    return {**req, 'node':node}


def _diff_req(body: Any) -> dict:
    if not isinstance(body, dict) or set(body) - {'before_doc','after_doc','node','mode','scale','tolerance','max_bytes'}:
        raise BadRequest('unknown visual diff field')
    if not all(isinstance(body.get(key), dict) for key in ('before_doc','after_doc')):
        raise BadRequest('before_doc and after_doc must be documents')
    req = _common({'doc':body['before_doc'], 'mode':body.get('mode')})
    req.pop('doc'); req.pop('annotate')
    node, scale = body.get('node'), body.get('scale',1)
    tolerance, cap = body.get('tolerance',0), body.get('max_bytes',8 << 20)
    if node is not None and (not isinstance(node,str) or len(node)>96):
        raise BadRequest('node must be an id')
    if isinstance(scale,bool) or scale not in SCALES:
        raise BadRequest('invalid diff scale')
    if isinstance(tolerance,bool) or not isinstance(tolerance,int) or not 0<=tolerance<=255:
        raise BadRequest('tolerance must be an integer in 0..255')
    if isinstance(cap,bool) or not isinstance(cap,int) or not 1<=cap<=8 << 20:
        raise BadRequest('max_bytes must be an integer in 1..8388608')
    return {**req, 'before_doc':body['before_doc'], 'after_doc':body['after_doc'],
            'node':node, 'scale':scale, 'tolerance':tolerance, 'max_bytes':cap}


def _export_req(body: Any) -> dict:
    req = _common(body)
    if set(body) - {"doc", "mode", "node", "format", "scale", "max_bytes"}:
        raise BadRequest("unknown export field")
    node = body.get("node")
    if node is not None and (not isinstance(node, str) or len(node) > 96):
        raise BadRequest("node must be an id")
    format_ = body.get("format")
    if format_ not in ("svg", "pdf", "html"):
        raise BadRequest("format is svg, pdf or html")
    scale, cap = body.get("scale", 1), body.get("max_bytes", 8 << 20)
    if isinstance(scale, bool) or scale not in SCALES:
        raise BadRequest("invalid export scale")
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 8 << 20:
        raise BadRequest("max_bytes is an integer in 1..8388608")
    req.update(node=node, format=format_, scale=scale, max_bytes=cap)
    return req


def _motion_req(body: Any, export=False) -> dict:
    req=_common(body)
    allowed={'doc','mode','from_node','to_node','duration_ms','easing','fps','max_edge'}|({'format','max_bytes'} if export else set())
    if set(body)-allowed:raise BadRequest('unknown motion field')
    from agentplatform.ui_designer.motion import options
    from agentplatform.ui_designer.errors import UiDesignerError
    try:options(body.get('duration_ms',800),body.get('easing','ease-in-out'),body.get('fps',24),body.get('max_edge',720))
    except UiDesignerError as exc:raise BadRequest(str(exc)) from None
    for key in ('from_node','to_node'):
        if not isinstance(body.get(key),str) or not re.fullmatch(r'[a-z0-9][a-z0-9/-]{0,511}',body[key]):raise BadRequest('motion node must be a native ID')
    req={k:v for k,v in req.items() if k!='annotate'}
    req.update({key:body[key] for key in ('from_node','to_node')})
    req.update(duration_ms=body.get('duration_ms',800),easing=body.get('easing','ease-in-out'),fps=body.get('fps',24),max_edge=body.get('max_edge',720))
    if export:
        if body.get('format') not in ('mp4','gif'):raise BadRequest('motion format must be mp4|gif')
        cap=body.get('max_bytes',8<<20)
        if type(cap) is not int or not 1<=cap<=8<<20:raise BadRequest('motion byte cap invalid')
        req.update(format=body['format'],max_bytes=cap)
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
        a[MOTION_POOL]=Pool(Config(workers=1,job_timeout_s=60,job_memory_mb=cfg.job_memory_mb,asset_dir=cfg.asset_dir,start_timeout_s=cfg.start_timeout_s,queue_max=1,queue_deadline_s=5,process_group=True))
        a[MOTION_POOL].start()

    async def on_stop(a: web.Application) -> None:
        a[POOL].close()
        a[MOTION_POOL].close()

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

    async def motion_call(r,kind,parse):
        task=asyncio.create_task(_post(r,kind,parse))
        try:
            while not task.done():
                await asyncio.wait({task},timeout=.05)
                if not task.done() and (r.transport is None or r.transport.is_closing()):
                    raise asyncio.CancelledError()
            return await task
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            raise
    async def motion_preview(r):return await motion_call(r,"motion_preview",_motion_req)
    async def motion_export(r):return await motion_call(r,"motion_export",lambda body:_motion_req(body,True))
    app.router.add_post("/motion/preview",motion_preview)
    app.router.add_post("/motion/export",motion_export)
    app.router.add_post("/render", render)
    app.router.add_post("/svg", svg_)
    app.router.add_post("/resolved", resolved)
    async def export(r: web.Request) -> web.Response:
        return await _post(r, "export", _export_req)
    app.router.add_post("/export", export)
    async def diff(r: web.Request) -> web.Response:
        return await _post(r, "diff", _diff_req)
    app.router.add_post("/diff", diff)
    async def lint_(r: web.Request) -> web.Response:
        return await _post(r, "lint", _lint_req)
    app.router.add_post("/lint", lint_)
    async def boolean(r: web.Request) -> web.Response:
        return await _post(r, "boolean", _boolean_req)
    app.router.add_post("/boolean", boolean)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    web.run_app(create_app(), handler_cancellation=True, host="0.0.0.0", port=8000, access_log=None)
