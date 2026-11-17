"""The API-side client of the renderer service (design 46 section 5).

The API stays I/O only: it validates, stores and forwards a document to
`services/ui-renderer`, which expands, lays out, paints and rasterizes. This
module owns the HTTP calls and the render cache (`ui_designer_renders`, keyed
by `renderer_version` so a font or painter change invalidates every row).

* The renderer holds no credentials, so image bytes travel with the call: a
  render that needs an asset the renderer lacks gets a 409 listing the hashes,
  the client `PUT`s them from the assets table and retries once.
* Only PNG results are cached; the key also folds in `max_edge`, `max_bytes`
  and `background`, which change the pixels. A cache hit has no `boxes`; ask
  `resolved()` when the resolved boxes are needed.
* A response whose `renderer_version` differs from this process's (a rolling
  deploy with a new painter on one side) is returned but never cached.
"""
from __future__ import annotations

import base64
import math
import json as json_module
import re
import struct
from dataclasses import dataclass
from typing import Any

import httpx

from . import store
from .errors import CODES, UD_ASSET_UNKNOWN, UD_RENDER_FAILED, UiDesignerError
from .svg import renderer_version


@dataclass
class Rendered:
    node: str | None
    png: bytes            # the encoded image; `format` says which encoding
    format: str           # png | webp | jpeg
    mime: str
    width: int
    height: int
    boxes: dict | None    # the resolved box of the node and its children (None on a cache hit)
    cached: bool = False


EXPORT_MIMES = {"svg": "image/svg+xml", "pdf": "application/pdf", "html": "text/html"}


@dataclass
class Exported:
    data: bytes
    format: str
    mime: str
    width: int
    height: int
    box: list
    warnings: list[str]


def cache_key(doc_sha256: str, node: str | None, mode: str | None, scale: float, annotate: bool = False,
              max_edge: int = 1568, max_bytes: int | None = None, background: str | None = None) -> str:
    """The render-cache key (also the web ETag) of one image."""
    variant = f"{int(annotate)}:{max_edge}:{max_bytes or 0}:{background or ''}"
    return store.render_key(renderer_version(), doc_sha256, node or "", mode or "", scale, variant)


def _raise(status: int, body: Any) -> None:
    if not isinstance(body, dict):
        raise UiDesignerError(UD_RENDER_FAILED, f"renderer answered HTTP {status}")
    code = body.get("code")
    detail = {k: v for k, v in body.items() if k not in ("ok", "code", "message", "fix", "errors")}
    if code not in CODES:
        code = UD_RENDER_FAILED
    raise UiDesignerError(code, str(body.get("message") or ""), detail=detail)


def _validated_visual_diff(body: dict, max_bytes: int) -> dict:
    def fail():
        raise UiDesignerError(UD_RENDER_FAILED, "renderer returned an invalid or over-budget visual diff")
    if body.get("renderer_version") != renderer_version() or body.get("mime") != "image/png":
        fail()
    width, height = body.get("width"), body.get("height")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (width, height)):
        fail()
    if (width * height > 10_000_000 or not isinstance(body.get("total_pixels"), int)
            or isinstance(body.get("total_pixels"), bool) or body["total_pixels"] != width * height):
        fail()
    changed = body.get("changed_pixels")
    if not isinstance(changed, int) or isinstance(changed, bool) or not 0 <= changed <= width * height:
        fail()
    box = body.get("box")
    finite = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 100_000_000
    if not (isinstance(box, list) and len(box) == 4 and all(finite(v) for v in box) and box[2] > 0 and box[3] > 0):
        fail()
    used = 0
    for field in ("image", "base_image"):
        encoded = body.get(field)
        if not isinstance(encoded, str) or len(encoded) > ((max_bytes + 2) // 3) * 4:
            fail()
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            fail()
        used += len(data)
        if (len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                or struct.unpack(">II", data[16:24]) != (width, height) or used > max_bytes):
            fail()
    id_ok = lambda value: isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9/-]{0,511}", value) is not None
    ids = body.get("node_ids")
    regions = body.get("regions")
    warnings = body.get("warnings", [])
    if not (isinstance(ids, list) and len(ids) <= 200 and all(id_ok(v) for v in ids)
            and isinstance(regions, list) and len(regions) <= 200
            and isinstance(body.get("truncated"), bool) and isinstance(warnings, list)
            and len(warnings) <= 32 and all(isinstance(v, str) and len(v.encode()) <= 500 for v in warnings)):
        fail()
    for region in regions:
        if not isinstance(region, dict) or set(region) != {"x", "y", "w", "h", "node_ids"}:
            fail()
        if not (all(finite(region[k]) for k in ("x", "y", "w", "h"))
                and region["w"] > 0 and region["h"] > 0
                and isinstance(region["node_ids"], list) and len(region["node_ids"]) <= 200
                and all(id_ok(v) for v in region["node_ids"])):
            fail()
        if (region["x"] < box[0] - .01 or region["y"] < box[1] - .01
                or region["x"] + region["w"] > box[0] + box[2] + .01
                or region["y"] + region["h"] > box[1] + box[3] + .01):
            fail()
    metadata = {key: body[key] for key in ("mime", "width", "height", "box", "changed_pixels",
                "total_pixels", "regions", "node_ids", "truncated")}
    metadata["warnings"] = warnings
    if len(json_module.dumps(metadata, separators=(",", ":")).encode()) > 64 << 10:
        fail()
    return {**metadata, "image": body["image"], "base_image": body["base_image"]}


class RenderClient:
    def __init__(self, base_url: str, *, timeout_s: float = 15.0,
                 client: httpx.AsyncClient | None = None,
                 cache_max_bytes: int = 256 << 20):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.cache_max_bytes = cache_max_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, *, json: Any = None, content: bytes | None = None,
                    timeout_s: float | None = None) -> tuple[int, Any]:
        try:
            r = await self._client.request(method, self.base_url + path, json=json, content=content,
                                           timeout=timeout_s or self.timeout_s)
        except httpx.HTTPError as e:
            raise UiDesignerError(UD_RENDER_FAILED, f"renderer unreachable: {type(e).__name__}") from e
        try:
            body = r.json()
        except ValueError:
            body = None
        return r.status_code, body

    async def _send_assets(self, s, shas: list[str]) -> None:
        for sha in shas:
            row = await store.get_asset(s, sha)
            if row is None:
                raise UiDesignerError(UD_ASSET_UNKNOWN, f"asset {sha[:12]} is not stored",
                                      detail={"assets": [{"asset": sha}]})
            status, body = await self._call("PUT", f"/assets/{sha}", content=row.data)
            if status != 200:
                _raise(status, body)

    async def _fetch(self, s, req: dict, timeout_s: float | None) -> dict:
        for attempt in (0, 1):
            status, body = await self._call("POST", "/render", json=req, timeout_s=timeout_s)
            if status == 409 and attempt == 0 and isinstance(body, dict) and body.get("missing"):
                await self._send_assets(s, list(body["missing"]))
                continue
            if status != 200 or not isinstance(body, dict):
                _raise(status, body)
            return body
        raise UiDesignerError(UD_RENDER_FAILED, "renderer still lacks assets after upload")

    async def render(self, s, *, document_id: str, doc: dict, doc_sha256: str, mode: str | None,
                     nodes: list[str | None] | None = None, scale: float = 1, annotate: bool = False,
                     max_edge: int = 1568, max_bytes: int | None = None, background: str | None = None,
                     timeout_s: float | None = None) -> list[Rendered]:
        """One image per requested node (None: the first page), in order.
        Cached PNGs come from the table; the rest are rendered in one call."""
        nodes = list(nodes or [None])
        version = renderer_version()
        keys = [cache_key(doc_sha256, n, mode, scale, annotate, max_edge, max_bytes, background)
                for n in nodes]
        out: list[Rendered | None] = [None] * len(nodes)
        for i, (n, key) in enumerate(zip(nodes, keys)):
            row = await store.render_get(s, key)
            if row is not None:
                out[i] = Rendered(n, row.png, "png", "image/png", row.width, row.height, None, True)
        todo = [i for i, r in enumerate(out) if r is None]
        if todo:
            req = {"doc": doc, "mode": mode, "nodes": [nodes[i] for i in todo], "scale": scale,
                   "annotate": annotate, "max_edge": max_edge, "max_bytes": max_bytes,
                   "background": background}
            body = await self._fetch(s, req, timeout_s)
            fresh = body.get("renderer_version") == version
            for i, im in zip(todo, body["images"]):
                data = base64.b64decode(im["data"])
                out[i] = Rendered(nodes[i], data, im["format"], im["mime"], im["width"], im["height"],
                                  im.get("boxes"))
                if fresh and im["format"] == "png":
                    await store.render_put(s, keys[i], document_id=document_id, png=data,
                                           width=im["width"], height=im["height"],
                                           max_bytes=self.cache_max_bytes)
        return [r for r in out if r is not None]

    async def lint(self, *, doc: dict, node: str | None, mode: str | None) -> dict:
        """Read normalized findings only; the renderer owns all layout/rule work."""
        from .lint import RULES_VERSION
        status, body = await self._call("POST", "/lint", json={"doc": doc, "node": node, "mode": mode})
        if status != 200 or not isinstance(body, dict):
            _raise(status, body)
        def fail():
            raise UiDesignerError(UD_RENDER_FAILED, "renderer returned invalid or over-budget lint findings")
        if (body.get("renderer_version") != renderer_version() or body.get("rules_version") != RULES_VERSION
                or len(json_module.dumps(body, separators=(",", ":")).encode()) > 2 << 20):
            fail()
        findings, total = body.get("findings"), body.get("total")
        counts, truncated = body.get("by_severity"), body.get("truncated")
        if not (isinstance(findings, list) and len(findings) <= 2000 and isinstance(truncated, bool)
                and isinstance(total, int) and not isinstance(total, bool) and total >= len(findings)
                and isinstance(counts, dict) and set(counts) <= {"error", "warning", "info"}
                and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values())
                and sum(counts.values()) == total):
            fail()
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != {"code", "severity", "node_id", "mode", "message", "suggestion", "approximate"}:
                fail()
            if not (isinstance(finding["code"], str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", finding["code"])
                    and finding["severity"] in ("error", "warning", "info")
                    and isinstance(finding["approximate"], bool)
                    and all(isinstance(finding[k], str) and len(finding[k].encode()) <= 500 for k in ("message", "suggestion"))
                    and (finding["node_id"] is None or isinstance(finding["node_id"], str)
                         and re.fullmatch(r"[a-z0-9][a-z0-9/-]{0,511}", finding["node_id"]))
                    and (finding["mode"] is None or isinstance(finding["mode"], str) and len(finding["mode"]) <= 16)):
                fail()
        actual = {severity: sum(f["severity"] == severity for f in findings) for severity in ("error", "warning", "info")}
        if not truncated and (total != len(findings) or any(actual[k] != counts.get(k, 0) for k in actual)):
            fail()
        return {"rules_version": RULES_VERSION, "findings": findings, "total": total,
                "by_severity": counts, "truncated": truncated}

    async def visual_diff(self, s, *, before_doc: dict, after_doc: dict,
                          node: str | None, mode: str | None, scale: float = 1,
                          tolerance: int = 0, max_bytes: int = 8 << 20) -> dict:
        """Two aligned PNGs and bounded pixel-change metadata, without storage."""
        from .schema import image_assets
        req = {"before_doc": before_doc, "after_doc": after_doc, "node": node,
               "mode": mode, "scale": scale, "tolerance": tolerance, "max_bytes": max_bytes}
        for attempt in (0, 1):
            status, body = await self._call("POST", "/diff", json=req)
            if status == 409 and attempt == 0 and isinstance(body, dict) and body.get("missing"):
                missing = body["missing"]
                known = {sha for _, sha in image_assets(before_doc) + image_assets(after_doc)}
                if not (isinstance(missing, list) and len(missing) <= 10000 and
                        all(isinstance(sha, str) and sha in known for sha in missing)):
                    raise UiDesignerError(UD_RENDER_FAILED, "renderer requested an unrelated diff asset")
                await self._send_assets(s, list(dict.fromkeys(missing)))
                continue
            if status != 200 or not isinstance(body, dict):
                _raise(status, body)
            return _validated_visual_diff(body, max_bytes)
        raise UiDesignerError(UD_RENDER_FAILED, "renderer still lacks diff assets after upload")

    async def export_file(self, s, *, doc: dict, mode: str | None, node: str | None,
                          format: str, scale: float = 1, max_bytes: int = 8 << 20) -> Exported:
        """One bounded, self-contained download. No export bytes enter the PNG cache."""
        from .schema import image_assets
        if format not in EXPORT_MIMES or not 1 <= max_bytes <= 8 << 20:
            raise UiDesignerError(UD_RENDER_FAILED, "invalid export request")
        req = {"doc": doc, "mode": mode, "node": node, "format": format,
               "scale": scale, "max_bytes": max_bytes}
        for attempt in (0, 1):
            status, body = await self._call("POST", "/export", json=req)
            if status == 409 and attempt == 0 and isinstance(body, dict) and body.get("missing"):
                missing = body["missing"]
                known = {sha for _, sha in image_assets(doc)}
                if not (isinstance(missing, list) and len(missing) <= 5000 and
                        all(isinstance(sha, str) and sha in known for sha in missing)):
                    raise UiDesignerError(UD_RENDER_FAILED, "renderer requested an unrelated asset")
                await self._send_assets(s, list(dict.fromkeys(missing)))
                continue
            if status != 200 or not isinstance(body, dict):
                _raise(status, body)
            if (body.get("renderer_version") != renderer_version() or body.get("format") != format
                    or body.get("mime") != EXPORT_MIMES[format]):
                raise UiDesignerError(UD_RENDER_FAILED, "renderer export capability mismatch")
            box = body.get("box")
            if not (isinstance(box, list) and len(box) == 4 and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                    and abs(v) <= 100_000_000 for v in box)
                    and box[2] > 0 and box[3] > 0 and box[2] * box[3] <= 40_000_000):
                raise UiDesignerError(UD_RENDER_FAILED, "renderer export has invalid geometry")
            encoded = body.get("data")
            width, height = body.get("width"), body.get("height")
            if not (isinstance(encoded, str) and len(encoded) <= ((max_bytes + 2) // 3) * 4
                    and isinstance(width, int) and not isinstance(width, bool) and width > 0
                    and isinstance(height, int) and not isinstance(height, bool) and height > 0
                    and width * height <= 10_000_000):
                raise UiDesignerError(UD_RENDER_FAILED, "renderer export exceeds its bounds")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise UiDesignerError(UD_RENDER_FAILED, "renderer returned invalid export bytes") from exc
            if not data or len(data) > max_bytes:
                raise UiDesignerError(UD_RENDER_FAILED, "renderer export exceeds its byte budget")
            warnings = body.get("warnings", [])
            if not (isinstance(warnings, list) and len(warnings) <= 32 and
                    all(isinstance(w, str) and len(w.encode()) <= 500 for w in warnings)):
                raise UiDesignerError(UD_RENDER_FAILED, "renderer export warnings exceed their bounds")
            return Exported(data, format, EXPORT_MIMES[format], width, height, box, warnings)
        raise UiDesignerError(UD_RENDER_FAILED, "renderer still lacks export assets after upload")

    async def boolean_geometry(self, kind: str, operands: list[dict]) -> dict:
        """Native Skia runs only in the renderer's resource-bounded worker."""
        from .vector_geometry import validate_operands, validate_path
        validate_operands(operands,kind)
        status,body=await self._call('POST','/boolean',json={'kind':kind,'operands':operands})
        if status!=200 or not isinstance(body,dict): _raise(status,body)
        geometry=body.get('geometry')
        if body.get('ok') is not True or body.get('renderer_version')!=renderer_version() or not isinstance(geometry,dict) or set(geometry)!={'x','y','w','h','d','empty'}:
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean capability mismatch')
        if not all(isinstance(geometry.get(key),(int,float)) and not isinstance(geometry[key],bool) and
                   math.isfinite(geometry[key]) and abs(geometry[key])<=100_000 for key in ('x','y','w','h')):
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean returned invalid bounds')
        empty=geometry.get('empty');d=geometry.get('d')
        try: encoded=d.encode() if isinstance(d,str) else b''
        except UnicodeError as error:
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean returned invalid UTF-8') from error
        if not isinstance(empty,bool) or not isinstance(d,str) or len(encoded)>32<<10 or (
                empty and (d!='' or geometry['w']!=0 or geometry['h']!=0)) or (
                not empty and (not d or geometry['w']<=0 or geometry['h']<=0)):
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean returned invalid geometry')
        try: validate_path(d,max_segments=4096)
        except UiDesignerError as error:
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean returned malformed path') from error
        warnings=body.get('warnings',[])
        if not isinstance(warnings,list) or len(warnings)>32 or any(not isinstance(w,str) or len(w.encode())>500 for w in warnings):
            raise UiDesignerError(UD_RENDER_FAILED,'renderer boolean warnings exceed bounds')
        return body

    async def svg(self, s=None, *, doc: dict, mode: str | None, node: str | None = None, annotate: bool = False,
                  asset_prefix: str = "/api/ui-designer/assets/", assets: dict | None = None) -> dict:
        """SVG stays vector except native-effect patches. Assets are uploaded by
        verified document SHA only, after a bounded missing-assets response."""
        from .schema import image_assets
        req = {"doc": doc, "mode": mode, "node": node, "annotate": annotate,
               "asset_prefix": asset_prefix, "assets": assets or {}}
        for attempt in (0, 1):
            status, body = await self._call("POST", "/svg", json=req)
            if status == 409 and attempt == 0 and isinstance(body, dict) and body.get("missing"):
                missing = body["missing"]
                known = {sha for _, sha in image_assets(doc)}
                if s is None or not (isinstance(missing, list) and len(missing) <= 5000 and
                        all(isinstance(sha, str) and sha in known for sha in missing)):
                    raise UiDesignerError(UD_RENDER_FAILED, "renderer requested unavailable or unrelated SVG assets")
                await self._send_assets(s, list(dict.fromkeys(missing)))
                continue
            if status != 200 or not isinstance(body, dict):
                _raise(status, body)
            if not isinstance(body.get("svg"), str) or len(body["svg"].encode()) > 8 << 20:
                raise UiDesignerError(UD_RENDER_FAILED, "renderer SVG exceeds 8MiB")
            return body
        raise UiDesignerError(UD_RENDER_FAILED, "renderer still lacks SVG assets after upload")

    async def resolved(self, *, doc: dict, mode: str | None) -> dict:
        """The resolved tree (glyph data cut) for the web overlay and text answers."""
        status, body = await self._call("POST", "/resolved", json={"doc": doc, "mode": mode})
        if status != 200 or not isinstance(body, dict):
            _raise(status, body)
        return body["tree"]
