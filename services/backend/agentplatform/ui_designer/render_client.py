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


def _raise(status: int, body: Any) -> None:
    if not isinstance(body, dict):
        raise UiDesignerError(UD_RENDER_FAILED, f"renderer answered HTTP {status}")
    code = body.get("code")
    detail = {k: v for k, v in body.items() if k not in ("ok", "code", "message", "fix", "errors")}
    if code not in CODES:
        code = UD_RENDER_FAILED
    raise UiDesignerError(code, str(body.get("message") or ""), detail=detail)


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
        variant = f"{int(annotate)}:{max_edge}:{max_bytes or 0}:{background or ''}"
        keys = [store.render_key(version, doc_sha256, n or "", mode or "", scale, variant) for n in nodes]
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

    async def svg(self, *, doc: dict, mode: str | None, node: str | None = None, annotate: bool = False,
                  asset_prefix: str = "/api/ui-designer/assets/", assets: dict | None = None) -> dict:
        """The server's own SVG for the web: {svg, box, warnings, renderer_version}. `assets`
        maps sha256 -> {w, h} so images are placed without a second request."""
        status, body = await self._call("POST", "/svg", json={
            "doc": doc, "mode": mode, "node": node, "annotate": annotate,
            "asset_prefix": asset_prefix, "assets": assets or {}})
        if status != 200 or not isinstance(body, dict):
            _raise(status, body)
        return body

    async def resolved(self, *, doc: dict, mode: str | None) -> dict:
        """The resolved tree (glyph data cut) for the web overlay and text answers."""
        status, body = await self._call("POST", "/resolved", json={"doc": doc, "mode": mode})
        if status != 200 or not isinstance(body, dict):
            _raise(status, body)
        return body["tree"]
