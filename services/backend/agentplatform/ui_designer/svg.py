"""The painter: a resolved tree to SVG (design 46 §5).

One painter feeds both the PNG (resvg) and the web (the server's own SVG), so
the two agree. Text is never `<text>`: every glyph is an outline `<path>` in
`<defs>` (one per face and glyph id) that `<use>` places, so no font is ever
needed by the rasterizer or the browser. Nothing the document says is
interpreted as markup: colours, path data and hashes are re-validated here, and
every dynamic attribute is XML-escaped.

`paint(resolved, node_id=None, ...)` returns a `Painted` (the SVG text and the
viewBox it covers). Conventions fixed here because the schema leaves them open:

* gradient `angle` is CSS-like: 0 points up, 90 points right, 180 (the default)
  points down; the gradient line is stretched so the corners get the end stops;
* a 4-tuple `radius` is (top-left, top-right, bottom-right, bottom-left), and a
  radius is clamped to half the shorter side;
* `stroke.align` inside/center/outside moves the stroke's centre line by half
  its width; it applies to frames, rects and ellipses (other shapes stroke
  centred);
* a `path` node's `d` is in the node's own coordinates, drawn at `(x, y)`;
* a `line` runs from `(x, y)` to `(x + w, y + h)` and uses its stroke (or, with
  none, its fill as a 1 px stroke);
* `mask`: a frame's first child is the mask for its other children (`alpha`
  uses the child's alpha; `luminance` its luminance; `vector` its shape,
  painted opaque white whatever its paint);
* an `image` with known pixel size is one `<image>` in `<defs>` and a `<use>`
  per node (cover/contain by transform); without it, an inline `<image>` with
  `preserveAspectRatio`.

Rendering a node that has rotated ancestors keeps their rotation (as empty
groups), so the node sits where the whole page paints it.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Mapping

from .errors import UD_OP_TARGET, UD_RENDER_FAILED, UiDesignerError
from .fonts import FONT_DIR, face_by_key, get_face
from .schema import (COLOR_RE, MAX_BLUR, MAX_PATH_TOTAL_BYTES, MAX_SHADOWS, MAX_STOPS, PATH_D_RE,
                     SHA_RE)
from .text import layout_text, make_style

# Bump with any change to what the painter emits; part of `renderer_version`.
PAINTER_VERSION = 1

ANNOTATE_COLOR = "#ff2bd6"
ANNOTATE_HALO = "#000000"
_PREFIX_RE = re.compile(r"^(?:/[A-Za-z0-9_.\-]+)*/?$|^[A-Za-z0-9_.\-]+/$")
_SHAPES = ("frame", "rect", "ellipse")


@lru_cache(maxsize=1)
def renderer_version() -> str:
    """The painter's code version plus the sha256 of the bundled font set, so a
    font or painter change invalidates every cached render."""
    h = hashlib.sha256()
    for p in sorted(FONT_DIR.glob("*.ttf")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return f"p{PAINTER_VERSION}-{h.hexdigest()[:16]}"


@dataclass(frozen=True)
class AssetInfo:
    """Where an image's pixels live (`href`) and, when known, their size."""
    href: str
    w: int | None = None
    h: int | None = None


@dataclass
class Painted:
    svg: str
    x: float
    y: float
    w: float
    h: float
    node: dict

    @property
    def size(self) -> tuple[float, float]:
        return self.w, self.h


def _n(v: float) -> str:
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _color(c: Any, white: bool = False) -> tuple[str, float] | None:
    """(`#rrggbb`, opacity) of a colour literal, or None for anything else."""
    if white:
        return "#ffffff", 1.0
    if not isinstance(c, str) or not COLOR_RE.fullmatch(c):
        return None
    h = c[1:].lower()
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h)
    a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
    return "#" + h[:6], a


def _radii(r: Any, w: float, h: float, inset: float = 0.0) -> list[float]:
    cap = max(0.0, min(w, h) / 2)
    if isinstance(r, (list, tuple)) and len(r) == 4:
        vals = [float(v) for v in r]
    elif isinstance(r, (int, float)) and not isinstance(r, bool):
        vals = [float(r)] * 4
    else:
        vals = [0.0] * 4
    return [max(0.0, min(v, cap) - inset) for v in vals]


def _rrect(x: float, y: float, w: float, h: float, r: list[float]) -> str:
    """A rounded rectangle as path data (per-corner radii)."""
    tl, tr, br, bl = r
    return (f"M{_n(x + tl)} {_n(y)}H{_n(x + w - tr)}"
            + (f"A{_n(tr)} {_n(tr)} 0 0 1 {_n(x + w)} {_n(y + tr)}" if tr else "")
            + f"V{_n(y + h - br)}"
            + (f"A{_n(br)} {_n(br)} 0 0 1 {_n(x + w - br)} {_n(y + h)}" if br else "")
            + f"H{_n(x + bl)}"
            + (f"A{_n(bl)} {_n(bl)} 0 0 1 {_n(x)} {_n(y + h - bl)}" if bl else "")
            + f"V{_n(y + tl)}"
            + (f"A{_n(tl)} {_n(tl)} 0 0 1 {_n(x + tl)} {_n(y)}" if tl else "") + "Z")


class _Painter:
    def __init__(self, assets: Mapping[str, AssetInfo] | None,
                 href: Callable[[str], str | None] | None):
        self.assets = assets or {}
        self.href = href
        self.defs: list[str] = []
        self.n = 0
        self.glyph_ids: dict[tuple[str, int], str] = {}
        self.grads: dict[str, str] = {}
        self.image_defs: dict[str, str] = {}
        self.shadows = 0
        self.path_bytes = 0

    def uid(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    # --- paints -------------------------------------------------------------

    def paint_attr(self, kind: str, v: Any, box: tuple[float, float, float, float],
                   white: bool) -> str:
        """` fill="…" fill-opacity="…"` (or stroke) for a colour or gradient."""
        if isinstance(v, dict) and "gradient" in v:
            if white:
                return f' {kind}="#ffffff"'
            return f' {kind}="url(#{self.gradient(v, box)})"'
        c = _color(v, white)
        if c is None:
            return f' {kind}="none"'
        out = f' {kind}="{c[0]}"'
        return out + (f' {kind}-opacity="{_n(c[1])}"' if c[1] < 1 else "")

    def gradient(self, g: dict, box: tuple[float, float, float, float]) -> str:
        stops = g.get("stops") or []
        if len(stops) > MAX_STOPS:
            raise UiDesignerError(UD_RENDER_FAILED, f"a gradient has {len(stops)} stops (max {MAX_STOPS})")
        x, y, w, h = box
        if g.get("gradient") == "radial":
            head = (f'<radialGradient id="@" gradientUnits="userSpaceOnUse" cx="{_n(x + w / 2)}" '
                    f'cy="{_n(y + h / 2)}" r="{_n(max(w, h) / 2 or 1)}"'
                    + (f' gradientTransform="translate({_n(x + w / 2)} {_n(y + h / 2)}) '
                       f'scale({_n(w / max(w, h))} {_n(h / max(w, h))}) '
                       f'translate({_n(-(x + w / 2))} {_n(-(y + h / 2))})"' if w and h and w != h else "")
                    + ">")
            tail = "</radialGradient>"
        else:
            ang = math.radians(float(g.get("angle", 180)))
            dx, dy = math.sin(ang), -math.cos(ang)
            half = (abs(w * dx) + abs(h * dy)) / 2
            cx, cy = x + w / 2, y + h / 2
            head = (f'<linearGradient id="@" gradientUnits="userSpaceOnUse" x1="{_n(cx - dx * half)}" '
                    f'y1="{_n(cy - dy * half)}" x2="{_n(cx + dx * half)}" y2="{_n(cy + dy * half)}">')
            tail = "</linearGradient>"
        body = ""
        for st in stops:
            off, col = st[0], _color(st[1])
            if col is None:
                continue
            body += (f'<stop offset="{_n(min(1.0, max(0.0, float(off))))}" stop-color="{col[0]}"'
                     + (f' stop-opacity="{_n(col[1])}"' if col[1] < 1 else "") + "/>")
        key = head + body + tail
        gid = self.grads.get(key)
        if gid is None:
            gid = self.grads[key] = self.uid("gr")
            self.defs.append(key.replace('id="@"', f'id="{gid}"', 1))
        return gid

    # --- nodes --------------------------------------------------------------

    def node(self, n: dict, o: list[str], white: bool = False) -> None:
        if n.get("visible") is False:
            return
        x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
        closers = 0
        rot = n.get("rotate")
        if rot:
            o.append(f'<g transform="rotate({_n(rot)} {_n(x + w / 2)} {_n(y + h / 2)})">')
            closers += 1
        op = n.get("opacity")
        if isinstance(op, (int, float)) and not isinstance(op, bool) and op < 1:
            o.append(f'<g opacity="{_n(max(0.0, op))}">')
            closers += 1
        t = n.get("type")
        if n.get("shadow") and t in _SHAPES + ("path", "image") and not white:
            self.shadow(n, o)
        else:
            self.body(n, o, white)
        kids = n.get("children") or []
        if kids:
            self.children(n, kids, o, white)
        o.append("</g>" * closers)

    def shadow(self, n: dict, o: list[str]) -> None:
        sh = n["shadow"]
        self.shadows += 1
        if self.shadows > MAX_SHADOWS:
            raise UiDesignerError(UD_RENDER_FAILED, f"more than {MAX_SHADOWS} shadows")
        blur = float(sh.get("blur", 0))
        if blur > MAX_BLUR:
            raise UiDesignerError(UD_RENDER_FAILED, f"shadow blur above {MAX_BLUR}px")
        col = _color(sh.get("color", "#000000")) or ("#000000", 1.0)
        dx, dy = float(sh.get("x", 0)), float(sh.get("y", 0))
        x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
        pad = blur * 3 + 2
        fid = self.uid("sh")
        self.defs.append(
            f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="{_n(x + min(dx, 0) - pad)}" '
            f'y="{_n(y + min(dy, 0) - pad)}" width="{_n(w + abs(dx) + 2 * pad)}" '
            f'height="{_n(h + abs(dy) + 2 * pad)}"><feDropShadow dx="{_n(dx)}" dy="{_n(dy)}" '
            f'stdDeviation="{_n(blur / 2)}" flood-color="{col[0]}" flood-opacity="{_n(col[1])}"/></filter>')
        o.append(f'<g filter="url(#{fid})">')
        self.body(n, o, False)
        o.append("</g>")

    def body(self, n: dict, o: list[str], white: bool) -> None:
        t = n.get("type")
        x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
        box = (x, y, w, h)
        fill, stroke = n.get("fill"), n.get("stroke")
        if t in ("frame", "rect"):
            r = _radii(n.get("radius"), w, h)
            if fill is not None and w > 0 and h > 0:
                o.append(self.shape("rect", x, y, w, h, r, self.paint_attr("fill", fill, box, white)
                                    + ' stroke="none"'))
            self.stroke_shape("rect", n, o, white)
        elif t == "ellipse":
            if fill is not None and w > 0 and h > 0:
                o.append(f'<ellipse cx="{_n(x + w / 2)}" cy="{_n(y + h / 2)}" rx="{_n(w / 2)}" '
                         f'ry="{_n(h / 2)}"{self.paint_attr("fill", fill, box, white)}/>')
            self.stroke_shape("ellipse", n, o, white)
        elif t == "line":
            src = stroke if isinstance(stroke, dict) else None
            color = (src or {}).get("color", fill if isinstance(fill, str) else None)
            sw = float((src or {}).get("width", 1))
            c = _color(color, white)
            if c is not None:
                o.append(f'<line x1="{_n(x)}" y1="{_n(y)}" x2="{_n(x + w)}" y2="{_n(y + h)}" '
                         f'stroke="{c[0]}" stroke-width="{_n(sw)}"'
                         + (f' stroke-opacity="{_n(c[1])}"' if c[1] < 1 else "") + "/>")
        elif t == "path":
            d = n.get("d")
            if isinstance(d, str) and d and PATH_D_RE.fullmatch(d):
                self.path_bytes += len(d)
                if self.path_bytes > MAX_PATH_TOTAL_BYTES:
                    raise UiDesignerError(UD_RENDER_FAILED, "path data above 1 MiB")
                sattr = ""
                if isinstance(stroke, dict):
                    sattr = (self.paint_attr("stroke", stroke.get("color"), box, white)
                             + f' stroke-width="{_n(float(stroke.get("width", 1)))}"')
                o.append(f'<path transform="translate({_n(x)} {_n(y)})" d="{_esc(d)}"'
                         f'{self.paint_attr("fill", fill, box, white) if fill is not None else " fill=\"none\""}'
                         f'{sattr}/>')
        elif t == "image":
            self.image(n, o, white)
        elif t == "text":
            self.text(n, o, white)

    def shape(self, kind: str, x: float, y: float, w: float, h: float, r: list[float],
              attrs: str) -> str:
        if kind == "rect" and not any(r):
            return f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"{attrs}/>'
        if kind == "rect" and len(set(r)) == 1:
            return (f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
                    f'rx="{_n(r[0])}"{attrs}/>')
        return f'<path d="{_rrect(x, y, w, h, r)}"{attrs}/>'

    def stroke_shape(self, kind: str, n: dict, o: list[str], white: bool) -> None:
        st = n.get("stroke")
        if not isinstance(st, dict):
            return
        x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
        sw = float(st.get("width", 1))
        if sw <= 0:
            return
        shift = {"inside": sw / 2, "outside": -sw / 2}.get(st.get("align", "center"), 0.0)
        sx, sy, sw_, sh_ = x + shift, y + shift, w - 2 * shift, h - 2 * shift
        if sw_ <= 0 or sh_ <= 0:
            return
        attrs = (' fill="none"' + self.paint_attr("stroke", st.get("color"), (x, y, w, h), white)
                 + f' stroke-width="{_n(sw)}"')
        if kind == "ellipse":
            o.append(f'<ellipse cx="{_n(x + w / 2)}" cy="{_n(y + h / 2)}" rx="{_n(sw_ / 2)}" '
                     f'ry="{_n(sh_ / 2)}"{attrs}/>')
        else:
            o.append(self.shape("rect", sx, sy, sw_, sh_, _radii(n.get("radius"), w, h, shift), attrs))

    def glyph(self, face: str, gid: int) -> str | None:
        key = (face, gid)
        gid_s = self.glyph_ids.get(key)
        if gid_s is None:
            d, _ = face_by_key(face).glyph_outline(gid)
            if not d:
                return None
            gid_s = self.glyph_ids[key] = self.uid("g")
            self.defs.append(f'<path id="{gid_s}" d="{d}"/>')
        return gid_s

    def text(self, n: dict, o: list[str], white: bool) -> None:
        tx = n.get("text")
        if not isinstance(tx, dict):
            return
        for line in tx.get("lines") or []:
            for run in line.get("runs") or []:
                face = run["face"]
                c = _color(run.get("color"), white)
                if c is None:
                    continue
                scale = run["size"] / face_by_key(face).upem
                parts = []
                for g in run.get("glyphs") or []:
                    gref = self.glyph(face, g[0])
                    if gref is None:
                        continue
                    gx = line["x"] + g[1]
                    gy = line["y"] + (g[2] if len(g) > 2 else 0)
                    parts.append(f'<use href="#{gref}" transform="translate({_n(gx)} {_n(gy)}) '
                                 f'scale({scale:.6g})"/>')
                if parts:
                    o.append(f'<g fill="{c[0]}"' + (f' fill-opacity="{_n(c[1])}"' if c[1] < 1 else "")
                             + ">" + "".join(parts) + "</g>")

    def image(self, n: dict, o: list[str], white: bool) -> None:
        x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
        sha = n.get("asset")
        info = self.assets.get(sha) if isinstance(sha, str) and SHA_RE.fullmatch(sha) else None
        if info is None and isinstance(sha, str) and SHA_RE.fullmatch(sha) and self.href:
            link = self.href(sha)
            info = AssetInfo(link) if link else None
        if w <= 0 or h <= 0:
            return
        if info is None or white:
            o.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
                     f'fill="{"#ffffff" if white else "#9aa0a6"}"' + ("" if white else ' fill-opacity="0.35"') + "/>")
            return
        fit = n.get("fit") or "cover"
        r = _radii(n.get("radius"), w, h)
        clip = ""
        if fit == "cover" or any(r):
            cid = self.uid("ic")
            self.defs.append(f'<clipPath id="{cid}"><path d="{_rrect(x, y, w, h, r)}"/></clipPath>')
            clip = f' clip-path="url(#{cid})"'
        href = _esc(info.href)
        if info.w and info.h:
            did = self.image_defs.get(info.href)
            if did is None:
                did = self.image_defs[info.href] = self.uid("im")
                self.defs.append(f'<image id="{did}" href="{href}" width="{info.w}" height="{info.h}"/>')
            if fit == "fill":
                sx, sy, tx, ty = w / info.w, h / info.h, x, y
            else:
                s = (max if fit == "cover" else min)(w / info.w, h / info.h)
                sx = sy = s
                tx, ty = x + (w - info.w * s) / 2, y + (h - info.h * s) / 2
            o.append(f'<g{clip}><use href="#{did}" transform="translate({_n(tx)} {_n(ty)}) '
                     f'scale({sx:.6g} {sy:.6g})"/></g>')
        else:
            par = {"fill": "none", "contain": "xMidYMid meet"}.get(fit, "xMidYMid slice")
            o.append(f'<g{clip}><image href="{href}" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" '
                     f'height="{_n(h)}" preserveAspectRatio="{par}"/></g>')

    def children(self, n: dict, kids: list[dict], o: list[str], white: bool) -> None:
        closers = 0
        if n.get("clip"):
            cid = self.uid("cl")
            x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
            self.defs.append(f'<clipPath id="{cid}"><path d="{_rrect(x, y, w, h, _radii(n.get("radius"), w, h))}"/></clipPath>')
            o.append(f'<g clip-path="url(#{cid})">')
            closers += 1
        mode = n.get("mask") if n.get("type") == "frame" else None
        if mode in ("alpha", "vector", "luminance") and kids:
            mid = self.uid("mk")
            x, y, w, h = n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)
            m: list[str] = []
            self.node(kids[0], m, white=(mode == "vector"))
            kind = "alpha" if mode == "alpha" else "luminance"
            self.defs.append(f'<mask id="{mid}" maskUnits="userSpaceOnUse" x="{_n(x)}" y="{_n(y)}" '
                             f'width="{_n(w)}" height="{_n(h)}" mask-type="{kind}">{"".join(m)}</mask>')
            o.append(f'<g mask="url(#{mid})">')
            closers += 1
            kids = kids[1:]
        for k in kids:
            self.node(k, o, white)
        o.append("</g>" * closers)

    def annotate(self, kids: list[dict], o: list[str]) -> None:
        style = make_style({"family": "Inter", "size": 11, "weight": 600})
        face = get_face("Inter", 600)
        for k in kids:
            if k.get("visible") is False:
                continue
            bx, by, bw, bh = k.get("bounds") or (k.get("x", 0), k.get("y", 0), k.get("w", 0), k.get("h", 0))
            o.append(f'<rect x="{_n(bx)}" y="{_n(by)}" width="{_n(bw)}" height="{_n(bh)}" fill="none" '
                     f'stroke="{ANNOTATE_HALO}" stroke-width="3.5"/>')
            o.append(f'<rect x="{_n(bx)}" y="{_n(by)}" width="{_n(bw)}" height="{_n(bh)}" fill="none" '
                     f'stroke="{ANNOTATE_COLOR}" stroke-width="1.5"/>')
            label = str(k.get("id", ""))[:48]
            lay = layout_text([(label, style)])
            pw, ph = lay.w + 6, lay.h + 2
            o.append(f'<rect x="{_n(bx)}" y="{_n(by)}" width="{_n(pw)}" height="{_n(ph)}" '
                     f'fill="{ANNOTATE_COLOR}"/>')
            for line in lay.lines:
                for fr in line.frags:
                    for g in fr.glyphs:
                        gref = self.glyph(fr.face, g[0])
                        if gref:
                            o.append(f'<use href="#{gref}" fill="#000000" transform="translate('
                                     f'{_n(bx + 3 + g[1])} {_n(by + 1 + line.baseline)}) '
                                     f'scale({style.size / face.upem:.6g})"/>')


# --- tree helpers ----------------------------------------------------------

def find_node(resolved: dict, node_id: str | None) -> tuple[dict, list[dict]]:
    """(node, ancestors, outermost first) for a node id or a page id. None names
    the first page. A page is returned as a synthetic frame over its children."""
    pages = resolved.get("pages") or []
    if node_id is None:
        if not pages:
            return _page_node({"id": "", "children": []}), []
        return _page_node(pages[0]), []
    for page in pages:
        if page.get("id") == node_id:
            return _page_node(page), []

    def walk(n: dict, stack: list[dict]):
        if n.get("id") == node_id:
            return n, stack
        for c in n.get("children") or []:
            hit = walk(c, stack + [n])
            if hit:
                return hit
        return None
    for page in pages:
        for c in page.get("children") or []:
            hit = walk(c, [])
            if hit:
                return hit
    raise UiDesignerError(UD_OP_TARGET, f"no node or page {node_id!r}", detail={"node": node_id})


def _box(n: dict) -> tuple[float, float, float, float]:
    return tuple(n.get("bounds") or (n.get("x", 0), n.get("y", 0), n.get("w", 0), n.get("h", 0)))  # type: ignore[return-value]


def _page_node(page: dict) -> dict:
    kids = [c for c in page.get("children") or [] if c.get("visible") is not False]
    if kids:
        x0 = min(_box(c)[0] for c in kids)
        y0 = min(_box(c)[1] for c in kids)
        x1 = max(_box(c)[0] + _box(c)[2] for c in kids)
        y1 = max(_box(c)[1] + _box(c)[3] for c in kids)
    else:
        x0 = y0 = x1 = y1 = 0
    return {"id": page.get("id", ""), "type": "page", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
            "children": page.get("children") or []}


def iter_tree(resolved: dict):
    def walk(n: dict):
        yield n
        for c in n.get("children") or []:
            yield from walk(c)
    for page in resolved.get("pages") or []:
        for c in page.get("children") or []:
            yield from walk(c)


def assets_used(resolved: dict) -> list[str]:
    """Distinct image sha256s the resolved tree paints, in first-use order."""
    seen: dict[str, None] = {}
    for n in iter_tree(resolved):
        a = n.get("asset")
        if n.get("type") == "image" and isinstance(a, str) and SHA_RE.fullmatch(a):
            seen.setdefault(a)
    return list(seen)


def boxes(node: dict) -> dict:
    """The node's resolved box and its direct children's, for text answers."""
    def b(n: dict) -> dict:
        out = {"id": n.get("id"), "type": n.get("type"), "x": n.get("x", 0), "y": n.get("y", 0),
               "w": n.get("w", 0), "h": n.get("h", 0)}
        if n.get("bounds"):
            out["bounds"] = n["bounds"]
        return out
    out = b(node)
    out["children"] = [b(c) for c in node.get("children") or []]
    return out


# --- entry point -----------------------------------------------------------

def paint(resolved: dict, node_id: str | None = None, *, annotate: bool = False,
          assets: Mapping[str, AssetInfo] | None = None,
          href: Callable[[str], str | None] | None = None) -> Painted:
    """SVG for one node (or a page) of a resolved tree. `assets` maps an image
    sha256 to its file and size; `href` (sha -> URL or None) covers the rest."""
    node, ancestors = find_node(resolved, node_id)
    p = _Painter(assets, href)
    body: list[str] = []
    closers = 0
    for a in ancestors:  # keep ancestors' rotation so the node sits where the page paints it
        if a.get("rotate"):
            ax, ay, aw, ah = a.get("x", 0), a.get("y", 0), a.get("w", 0), a.get("h", 0)
            body.append(f'<g transform="rotate({_n(a["rotate"])} {_n(ax + aw / 2)} {_n(ay + ah / 2)})">')
            closers += 1
    if node.get("type") == "page":
        for c in node["children"]:
            p.node(c, body)
    else:
        p.node(node, body)
    body.append("</g>" * closers)
    if annotate:
        p.annotate(node.get("children") or [], body)
    x, y, w, h = _box(node) if node.get("type") != "page" else (node["x"], node["y"], node["w"], node["h"])
    w, h = max(w, 1.0), max(h, 1.0)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(w)}" height="{_n(h)}" '
           f'viewBox="{_n(x)} {_n(y)} {_n(w)} {_n(h)}">'
           + (f'<defs>{"".join(p.defs)}</defs>' if p.defs else "") + "".join(body) + "</svg>")
    return Painted(svg, x, y, w, h, node)


def safe_prefix(prefix: str) -> str:
    """Validate a URL prefix for asset links (path characters only)."""
    if not isinstance(prefix, str) or not _PREFIX_RE.fullmatch(prefix) or ".." in prefix:
        raise UiDesignerError(UD_RENDER_FAILED, "bad asset prefix")
    return prefix if prefix.endswith("/") else prefix + "/"
