"""Constrained layout for UI Designer documents (design 46 §4).

`layout_document(doc, mode)` expands instances, resolves tokens for `mode`,
lays every page out and returns the **resolved tree**. It follows the
Flutter/Yoga discipline: constraints go down, sizes come up, and text is
measured at the width it is given (`text.layout_text`).

Sizing, per axis, for a node with spec `w`/`h`:
  * a number is tight (and wins over the constraint);
  * `fill` takes the remaining main-axis space of a laid-out parent (shared
    equally between fill siblings), or the whole cross axis; under a free
    frame it fills the frame's box;
  * `hug` is unbounded on the parent's main axis and bounded by the parent's
    cross constraint (so text wraps at the width it is given);
  * absent behaves as `hug`, except that `align: "stretch"` stretches an
    absent or `fill` cross size, and a leaf shape (rect, ellipse, line, path,
    image) with no size is 0 wide. (Hug text and hug frames measure their
    content.)
A `fill` child inside a `hug` parent on the same axis resolves as `hug` on
the main axis; on the cross axis the parent measures its other children
first and gives the result to the fill child (two passes, never more; if
there are no other children the fill child just hugs). `UD-WARN-FILL-IN-HUG`
(schema.lint) reports both.

Other rules: `wrap` (row or column) breaks lines at the available main size
and treats `fill` as `hug`; children with `absolute: true` (and the first
child of a laid-out frame that has `mask`) are out of flow and placed at
their `x`/`y` from the frame's top-left corner, measured against the final
frame box; an invisible node (`visible: false`) is not laid out (it is
emitted as 0 x 0 at its parent's content origin, without children);
free-position frames (no `layout`) measure children against their own box
and place them at `x`/`y` (default 0); a hug free frame is the bounding box
of its children. Rotation is applied after layout and never feeds it.

Resolved tree (JSON-able, the painter's input)::

    {"schema": 1, "mode": "dark"|None, "title": str,
     "pages": [{"id", "title", "children": [Node, ...]}],
     "glyphs": {"<face>": {"upem": int, "paths": {"<gid>": "<svg d>"}}},
     "warnings": [{"code", "path", "message", "id", "mode"}],
     "stats": {"nodes": int}}

    Node = {"id": str,          # instance children are "<instance-id>/<child-id>"
            "type": "frame"|"text"|"rect"|"ellipse"|"line"|"path"|"image",
            "x", "y", "w", "h": float,   # document coordinates, before rotation
            "of"?: str,         # component id on an expanded instance root
            "title"?: str, "visible"?: false,
            "rotate"?: degrees about the node's centre,
            "bounds"?: [x, y, w, h],  # axis-aligned box of the node after its
                                      # own and its ancestors' rotation; present
                                      # only inside a rotated subtree
            "fill"?, "stroke"?, "radius"?, "opacity"?, "shadow"?, "blur"?, "backdrop_blur"?,
            "blend"?, "clip"?,
            "mask"?, "d"?, "asset"?, "fit"?,   # resolved literals (tokens gone)
            "children"?: [Node, ...],          # source order = paint order
            "text"?: {"align", "truncated", "lines": [Line, ...]}}  # text nodes

    Line = {"x", "y": float,     # left edge and BASELINE, document coordinates
            "w", "h": float, "text": str,
            "runs": [{"face": "Inter:400", "size": px, "color": "#rrggbb[aa]",
                      "glyphs": [[gid, dx] | [gid, dx, dy], ...]}]}

Glyph `dx` is the pen position in px from `line.x` (alignment is already in
`line.x`); `dy` (rare) is the shaper's vertical offset in px, downwards. To
paint a glyph: look `gid` up in `glyphs[face]["paths"]` (outline in font
units, y pointing down, origin at the pen on the baseline) and draw it with
`translate(line.x + dx, line.y + dy) scale(size / glyphs[face]["upem"])`.
Identical text shares its `runs` lists between nodes: treat them as
read-only. Text never leaves the document as `<text>`.
"""
from __future__ import annotations

import math
from typing import Any

from .errors import UD_EXPANSION, UiDesignerError, join_path
from .fonts import face_by_key
from .schema import (MAX_EXPANDED_NODES, MAX_INSTANCE_DEPTH, REF_RE, SCHEMA_VERSION, lint,
                     resolve_token)
from .text import Style, layout_text, make_style, plain_text

INF = math.inf
CONTRAST_MIN = 3.0
_STYLE_KEYS = ("fill", "stroke", "radius", "opacity", "shadow", "blur", "backdrop_blur", "blend", "clip",
               "mask", "d", "asset", "fit")
_INSTANCE_KEYS = ("w", "h", "x", "y", "absolute", "rotate", "fill", "stroke", "radius", "opacity",
                  "visible", "clip", "shadow", "blur", "backdrop_blur", "blend")
_LEAF = ("rect", "ellipse", "line", "path", "image")


# --- items (expanded, token-resolved source nodes) -------------------------

_NOSTYLE: dict = {}
_STYLE_SET = frozenset(_STYLE_KEYS)
_RESOLVABLE = (str, dict, list)


class Item:
    __slots__ = ("id", "type", "w", "h", "x", "y", "absolute", "rotate", "visible", "title", "of",
                 "kids", "layout", "style", "text", "parent", "idx", "base", "cache")
    # class-level defaults would clash with __slots__, so they are set in __init__
    def __init__(self):
        self.w = self.h = self.x = self.y = self.title = self.of = self.layout = self.text = None
        self.absolute = False
        self.rotate = 0
        self.visible = True
        self.kids = ()
        self.style = _NOSTYLE
        self.parent = None
        self.idx = 0
        self.base = None            # JSON path of an authored top-level node / a component root
        self.cache = None


class _Plan:
    """A measured node: its size, children (item, dx, dy, plan) in source
    order, and for text the broken lines."""
    __slots__ = ("w", "h", "kids", "tl")

    def __init__(self, w: float, h: float, kids=(), tl=None):
        self.w, self.h, self.kids, self.tl = w, h, kids, tl


class _Builder:
    def __init__(self, doc: dict, mode: str | None):
        self.comps = doc.get("components") or {}
        self.tokens = doc.get("tokens") or {}
        self.modes = doc.get("modes")
        self.mode = mode
        self.refs: dict[str, Any] = {}
        self.styles: dict[str, Style] = {}
        self.count = 0
        self.text_cache: dict[int, tuple] = {}

    # tokens
    def ref(self, v: str) -> Any:
        hit = self.refs.get(v, self)
        if hit is not self:
            return hit
        if REF_RE.match(v):
            try:
                out = resolve_token(v, self.tokens, self.mode, self.modes)
            except KeyError:
                out = v
        else:
            out = v
        self.refs[v] = out
        return out

    def rv(self, v: Any) -> Any:
        c = v.__class__
        if c is str:
            return self.ref(v) if v[:1] == "$" else v
        if c is dict:
            return {k: self.rv(x) for k, x in v.items()}
        if c is list:
            return [self.rv(x) for x in v]
        return v

    # nodes
    def build(self, node: dict, prefix: str, parent: Item | None, idx: int, ov: dict | None,
              depth: int, base: str | None) -> Item:
        self.count += 1
        if self.count > MAX_EXPANDED_NODES:
            raise UiDesignerError(UD_EXPANSION, f"more than {MAX_EXPANDED_NODES} expanded nodes",
                                  detail={"reason": "nodes", "limit": MAX_EXPANDED_NODES})
        local = node.get("id", "")
        shared = True       # the node dict is the document's own (safe to key caches on)
        if ov and local in ov:
            node = {**node, **ov[local]}
            shared = False
        t = node.get("type")
        of = None
        child_prefix = prefix
        if t == "instance":
            of = node.get("of")
            comp = self.comps.get(of)
            if depth + 1 > MAX_INSTANCE_DEPTH:
                raise UiDesignerError(UD_EXPANSION, f"instance nesting deeper than {MAX_INSTANCE_DEPTH} "
                                      f"at {node.get('id')!r}",
                                      detail={"reason": "depth", "limit": MAX_INSTANCE_DEPTH,
                                              "at": node.get("id")})
            if comp is None:
                comp = {"type": "frame"}
            merged = dict(comp)
            for k in _INSTANCE_KEYS:
                if k in node:
                    merged[k] = node[k]
            child_ov = {}
            for key, val in (node.get("overrides") or {}).items():
                child, _, prop = key.rpartition(".")
                if child:
                    child_ov.setdefault(child, {})[prop] = val
                else:
                    merged[prop] = val
            merged["id"] = local
            if "title" in node:
                merged["title"] = node["title"]
            child_prefix = prefix + local + "/"
            base = join_path("$.components", of) if of in self.comps else base
            node = merged
            shared = False
            t = node.get("type")
            depth += 1
            ov = child_ov
            if t == "instance":     # a component whose root is itself an instance
                it = self.build(node, prefix, parent, idx, ov, depth, base)
                it.of = it.of or of
                return it
        it = Item()
        it.id = prefix + local
        it.type = t
        it.of = of
        it.parent = parent
        it.idx = idx
        it.base = base if parent is None or of is not None else None
        rv = self.rv
        lay = kids = None
        style = _NOSTYLE
        for k, v in node.items():
            if k in _STYLE_SET:
                if style is _NOSTYLE:
                    style = {}
                style[k] = rv(v) if v.__class__ in _RESOLVABLE else v
            elif k == "w":
                it.w = _spec(rv(v))
            elif k == "h":
                it.h = _spec(rv(v))
            elif k == "x":
                it.x = rv(v)
            elif k == "y":
                it.y = rv(v)
            elif k == "absolute":
                it.absolute = bool(v)
            elif k == "rotate":
                it.rotate = rv(v) or 0
            elif k == "visible":
                it.visible = v is not False
            elif k == "title":
                it.title = v
            elif k == "layout":
                lay = v
            elif k == "children":
                kids = v
        it.style = style
        if not it.visible:
            return it
        if t == "frame":
            it.cache = {}
            if lay is not None:
                lay = rv(lay)
                pad = lay.get("pad", 0)
                if isinstance(pad, list):
                    pad = (pad[0], pad[1], pad[0], pad[1]) if len(pad) == 2 else tuple(pad)
                else:
                    pad = (pad, pad, pad, pad)
                it.layout = (lay.get("dir", "row") == "row", float(lay.get("gap", 0)),
                             tuple(float(p) for p in pad), lay.get("align", "start"),
                             lay.get("justify", "start"), bool(lay.get("wrap")))
            if kids:
                out = it.kids = []
                for i, k in enumerate(kids):
                    out.append(self.build(k, child_prefix, it, i, ov, depth, None))
        elif t == "text":
            if shared:
                hit = self.text_cache.get(id(node))
                if hit is None:
                    hit = self.text_cache[id(node)] = (node, self._text(node))
                it.text = hit[1]
            else:
                it.text = self._text(node)
        return it

    def _style(self, spec: Any, base: Style | None) -> Style:
        if spec.__class__ is str and spec[:1] == "$" and base is None:
            hit = self.styles.get(spec)
            if hit is None:
                v = self.ref(spec)
                hit = self.styles[spec] = make_style(v if isinstance(v, dict) else None)
            return hit
        v = self.rv(spec) if spec is not None else None
        return make_style(v if isinstance(v, dict) else None, base)

    def _text(self, node: dict) -> tuple:
        base = self._style(node.get("style"), None)
        color = self.rv(node.get("color")) or "#000000"
        raw = node.get("text", "")
        runs: list[tuple[str, Style]] = []
        colors: list[str] = []
        if isinstance(raw, list):
            for r in raw:
                st = self._style(r["style"], base) if r.get("style") is not None else base
                runs.append((r.get("text", ""), st))
                colors.append(self.rv(r["color"]) if r.get("color") is not None else color)
        else:
            runs.append((raw if isinstance(raw, str) else "", base))
            colors.append(color)
        if not runs:
            runs.append(("", base))
            colors.append(color)
        return runs, colors, node.get("align", "left"), node.get("max_lines"), color


def _spec(v: Any) -> Any:
    if v is None or v == "fill" or v == "hug":
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


# --- measure ---------------------------------------------------------------

def _known(spec: Any, mn: float, mx: float) -> float | None:
    """The axis size when it is definite, else None (content decides)."""
    if spec.__class__ is float:
        return spec
    if mn == mx:
        return mn
    if spec == "fill" and mx != INF:
        return mx
    return None


_EMPTY = _Plan(0.0, 0.0)


def _measure(it: Item, cons: tuple) -> _Plan:
    cache = it.cache
    if cache is not None:
        hit = cache.get(cons)
        if hit is not None:
            return hit
    if not it.visible:
        plan = _EMPTY
    elif it.type == "text":
        plan = _measure_text(it, cons)
    elif it.type == "frame":
        if it.layout is not None:
            plan = _measure_flow(it, cons)
        else:
            plan = _measure_free(it, cons)
    else:
        minw, maxw, minh, maxh = cons
        w = _known(it.w, minw, maxw)
        h = _known(it.h, minh, maxh)
        plan = _Plan(minw if w is None else w, minh if h is None else h)
    if cache is not None:
        cache[cons] = plan
    return plan


def _measure_text(it: Item, cons: tuple) -> _Plan:
    minw, maxw, minh, maxh = cons
    runs, _c, _a, max_lines, _col = it.text
    w = _known(it.w, minw, maxw)
    wrap = w if w is not None else maxw
    tl = layout_text(runs, None if wrap == INF else wrap, max_lines)
    if w is None:
        w = max(tl.w, minw)
    h = _known(it.h, minh, maxh)
    if h is None:
        h = max(tl.h, minh)
    return _Plan(w, h, (), tl)


def _measure_free(it: Item, cons: tuple) -> _Plan:
    minw, maxw, minh, maxh = cons
    w = _known(it.w, minw, maxw)
    h = _known(it.h, minh, maxh)
    ccw = w if w is not None else maxw
    cch = h if h is not None else maxh
    kids = []
    right = bottom = 0.0
    for c in it.kids:
        if not c.visible:
            kids.append((c, 0.0, 0.0, _EMPTY))
            continue
        plan = _measure(c, (ccw if c.w == "fill" and w is not None else 0.0, ccw,
                            cch if c.h == "fill" and h is not None else 0.0, cch))
        x = c.x or 0.0
        y = c.y or 0.0
        kids.append((c, x, y, plan))
        right = max(right, x + plan.w)
        bottom = max(bottom, y + plan.h)
    return _Plan(max(right, minw) if w is None else w, max(bottom, minh) if h is None else h, kids)


def _measure_flow(it: Item, cons: tuple) -> _Plan:
    minw, maxw, minh, maxh = cons
    row, gap, (pt, pr, pb, pl), align, justify, wrap = it.layout
    W = _known(it.w, minw, maxw)
    H = _known(it.h, minh, maxh)
    padw, padh = pl + pr, pt + pb
    if row:
        main_known, cross_known = W, H
        main_max, cross_max = maxw, maxh
        main_pad, cross_pad = padw, padh
        main_min, cross_min = minw, minh
    else:
        main_known, cross_known = H, W
        main_max, cross_max = maxh, maxw
        main_pad, cross_pad = padh, padw
        main_min, cross_min = minh, minw
    inner_main = max(0.0, main_known - main_pad) if main_known is not None else (
        max(0.0, main_max - main_pad) if main_max != INF else INF)
    inner_cross = max(0.0, cross_known - cross_pad) if cross_known is not None else (
        max(0.0, cross_max - cross_pad) if cross_max != INF else INF)
    cross_definite = cross_known is not None
    stretch = align == "stretch"
    mask_first = it.style.get("mask") is not None

    def mk(mn_m, mx_m, mn_c, mx_c):
        return (mn_m, mx_m, mn_c, mx_c) if row else (mn_c, mx_c, mn_m, mx_m)

    flow: list[int] = []
    absolute: list[int] = []
    for i, c in enumerate(it.kids):
        if not c.visible:
            continue
        if c.absolute or (mask_first and i == 0):
            absolute.append(i)
        else:
            flow.append(i)
    kids_main = kids_cross = None
    sizes: dict[int, _Plan] = {}

    def cross_cons(c: Item) -> tuple[float, float]:
        cspec = c.h if row else c.w
        if cross_definite and (cspec == "fill" or (stretch and cspec is None)):
            return inner_cross, inner_cross
        return 0.0, inner_cross

    main_definite = main_known is not None and not wrap
    fills: list[int] = []
    fixed_main = 0.0
    deferred_cross: list[int] = []
    for i in flow:
        c = it.kids[i]
        mspec = c.w if row else c.h
        cspec = c.h if row else c.w
        if mspec == "fill" and main_definite:
            fills.append(i)
            continue
        if cspec == "fill" and not cross_definite and not wrap:
            deferred_cross.append(i)
            continue
        cmin, cmax = cross_cons(c)
        sizes[i] = _measure(c, mk(0.0, INF, cmin, cmax))
    # cross-axis fill inside a hug parent: measure the others first
    if deferred_cross:
        others = [(sizes[i].h if row else sizes[i].w) for i in sizes]
        cross_now = max(others) if others else None
        if cross_now is not None:
            cross_now = max(cross_now, cross_min - cross_pad)
    for i in deferred_cross:
        c = it.kids[i]
        if cross_now:
            sizes[i] = _measure(c, mk(0.0, INF, cross_now, cross_now))
        else:
            sizes[i] = _measure(c, mk(0.0, INF, 0.0, inner_cross))
    for i in sizes:
        fixed_main += sizes[i].w if row else sizes[i].h
    n_flow = len(flow)
    gaps = gap * max(0, n_flow - 1)
    if fills:
        share = max(0.0, (inner_main - fixed_main - gaps) / len(fills)) if inner_main != INF else 0.0
        for i in fills:
            c = it.kids[i]
            cmin, cmax = cross_cons(c)
            sizes[i] = _measure(c, mk(share, share, cmin, cmax))

    # lines
    lines: list[list[int]] = []
    if wrap and n_flow:
        cur: list[int] = []
        cur_main = 0.0
        for i in flow:
            m = sizes[i].w if row else sizes[i].h
            if cur and inner_main != INF and cur_main + gap + m > inner_main + 1e-6:
                lines.append(cur)
                cur, cur_main = [], 0.0
            cur_main += (gap if cur else 0.0) + m
            cur.append(i)
        lines.append(cur)
    elif n_flow:
        lines.append(flow)

    def msize(i):
        return sizes[i].w if row else sizes[i].h

    def csize(i):
        return sizes[i].h if row else sizes[i].w

    line_main = [sum(msize(i) for i in ln) + gap * (len(ln) - 1) for ln in lines]
    line_cross = [max(csize(i) for i in ln) for ln in lines]
    content_main = max(line_main) if lines else 0.0
    content_cross = sum(line_cross) + gap * max(0, len(lines) - 1)

    outer_main = main_known if main_known is not None else max(content_main + main_pad, main_min)
    outer_cross = cross_known if cross_known is not None else max(content_cross + cross_pad, cross_min)
    in_main = max(0.0, outer_main - main_pad)
    in_cross = max(0.0, outer_cross - cross_pad)
    multi = len(lines) > 1

    placed: dict[int, tuple[float, float, _Plan]] = {}
    cpos = 0.0
    for li, ln in enumerate(lines):
        lc = line_cross[li] if multi else in_cross
        free = in_main - line_main[li]
        extra = 0.0
        if justify == "center":
            pos = max(0.0, free / 2)
        elif justify == "end":
            pos = max(0.0, free)
        elif justify == "space-between" and len(ln) > 1 and free > 0:
            pos, extra = 0.0, free / (len(ln) - 1)
        else:
            pos = 0.0
        for i in ln:
            c = it.kids[i]
            p = sizes[i]
            cs = p.h if row else p.w
            cspec = c.h if row else c.w
            if stretch and (cspec is None or cspec == "fill") and abs(cs - lc) > 1e-9:
                mspec = c.w if row else c.h
                msz = p.w if row else p.h
                p = _measure(c, mk(0.0, INF, lc, lc) if mspec in (None, "hug")
                             else mk(msz, msz, lc, lc))
                cs = lc
            if align == "center":
                off = (lc - cs) / 2
            elif align == "end":
                off = lc - cs
            else:
                off = 0.0
            m = p.w if row else p.h
            if row:
                placed[i] = (pl + pos, pt + cpos + off, p)
            else:
                placed[i] = (pl + cpos + off, pt + pos, p)
            pos += m + gap + extra
        cpos += lc + gap

    # absolute / out-of-flow children against the final box
    ow, oh = (outer_main, outer_cross) if row else (outer_cross, outer_main)
    for i in absolute:
        c = it.kids[i]
        p = _measure(c, (ow if c.w == "fill" else 0.0, ow, oh if c.h == "fill" else 0.0, oh))
        placed[i] = (c.x or 0.0, c.y or 0.0, p)

    kids = []
    for i, c in enumerate(it.kids):
        hit = placed.get(i)
        kids.append((c, hit[0], hit[1], hit[2]) if hit else (c, 0.0, 0.0, _EMPTY))
    return _Plan(ow, oh, kids)


# --- emit ------------------------------------------------------------------

def _r(v: float) -> float:
    v = round(v, 3)
    return 0.0 if v == 0 else v


def _rotate(m, cx: float, cy: float, deg: float):
    """m (a, b, c, d, e, f) composed with a rotation of `deg` about (cx, cy)."""
    rad = math.radians(deg)
    cs, sn = math.cos(rad), math.sin(rad)
    a, b, c, d = cs, sn, -sn, cs
    e = cx - cs * cx + sn * cy
    f = cy - sn * cx - cs * cy
    if m is None:
        return (a, b, c, d, e, f)
    ma, mb, mc, md, me, mf = m
    return (ma * a + mc * b, mb * a + md * b, ma * c + mc * d, mb * c + md * d,
            ma * e + mc * f + me, mb * e + md * f + mf)


def _bounds(m, x: float, y: float, w: float, h: float) -> list[float]:
    a, b, c, d, e, f = m
    xs, ys = [], []
    for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
        xs.append(a * px + c * py + e)
        ys.append(b * px + d * py + f)
    return [_r(min(xs)), _r(min(ys)), _r(max(xs) - min(xs)), _r(max(ys) - min(ys))]


_RGBA: dict[str, Any] = {}


def _rgba(c: Any):
    hit = _RGBA.get(c, _RGBA)
    if hit is _RGBA:
        hit = _rgba_parse(c)
        if len(_RGBA) < 4096 and isinstance(c, str):
            _RGBA[c] = hit
    return hit


def _rgba_parse(c: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(c, str) or not c.startswith("#"):
        return None
    h = c[1:]
    try:
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            h += "ff"
        v = [int(h[i:i + 2], 16) for i in (0, 2, 4, 6)]
    except ValueError:
        return None
    return v[0], v[1], v[2], v[3] / 255


def _lum(rgb) -> float:
    def ch(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def contrast_ratio(fg: tuple, bg: tuple) -> float:
    """WCAG 2 contrast ratio of two opaque (r, g, b) colours."""
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _over(c: tuple, bg: tuple | None) -> tuple | None:
    r, g, b, a = c
    if a >= 0.999:
        return (r, g, b)
    if bg is None:
        return None
    return (r * a + bg[0] * (1 - a), g * a + bg[1] * (1 - a), b * a + bg[2] * (1 - a))


def _fill_color(fill: Any, bg: tuple | None) -> tuple | None:
    """The effective opaque colour a fill leaves behind (a gradient averages
    its stops, a list of paints composites bottom to top); None when it is
    unknown, an image, or the fill is absent."""
    if isinstance(fill, list):
        got = False
        for p in fill:
            nb = _fill_color(p, bg)
            if nb is not None:
                bg, got = nb, True
        return bg if got else None
    op = 1.0
    if isinstance(fill, dict):
        o = fill.get("opacity")
        op = float(o) if isinstance(o, (int, float)) and not isinstance(o, bool) else 1.0
        if "color" in fill:
            fill = fill["color"]
        else:
            cols = [x for x in (_rgba(s[1]) for s in fill.get("stops", [])) if x]
            if not cols:
                return None
            n = len(cols)
            return _over((sum(c[0] for c in cols) / n, sum(c[1] for c in cols) / n,
                          sum(c[2] for c in cols) / n, sum(c[3] for c in cols) / n * op), bg)
    c = _rgba(fill)
    return _over((c[0], c[1], c[2], c[3] * op), bg) if c else None


def _path(it: Item) -> str:
    segs: list[int] = []
    cur = it
    while cur.base is None and cur.parent is not None:
        segs.append(cur.idx)
        cur = cur.parent
    p = cur.base or "$"
    for i in reversed(segs):
        p = join_path(p, "children", i)
    return p


class _Emit:
    def __init__(self, mode: str | None):
        self.mode = mode
        self.warnings: list[dict] = []
        self.used: dict[str, set[int]] = {}
        self.run_cache: dict = {}
        self.keep: list = []
        self.nodes = 0

    def warn(self, code: str, it: Item, message: str) -> None:
        w = {"code": code, "path": _path(it), "message": message, "id": it.id}
        if self.mode is not None:
            w["mode"] = self.mode
        self.warnings.append(w)

    def node(self, it: Item, plan: _Plan, x: float, y: float, m, bg) -> dict:
        self.nodes += 1
        w, h = plan.w, plan.h
        d: dict[str, Any] = {"id": it.id, "type": it.type, "x": round(x, 3) + 0.0, "y": round(y, 3) + 0.0,
                             "w": round(w, 3) + 0.0, "h": round(h, 3) + 0.0}
        if it.of is not None:
            d["of"] = it.of
        if it.title:
            d["title"] = it.title
        if not it.visible:
            d["visible"] = False
            return d
        rot = it.rotate
        if rot:
            d["rotate"] = rot
            m = _rotate(m, x + w / 2, y + h / 2, rot)
        if m is not None:
            d["bounds"] = _bounds(m, x, y, w, h)
        d.update(it.style)
        fill = it.style.get("fill")
        if fill is not None:
            nb = _fill_color(fill, bg)
            if nb is not None:
                bg = nb
        if it.type == "text":
            self._text(it, plan, d, x, y, bg)
        elif plan.kids:
            kids = []
            for c, dx, dy, p in plan.kids:
                kids.append(self.node(c, p, x + dx, y + dy, m, bg))
            d["children"] = kids
        return d

    def _text(self, it: Item, plan: _Plan, d: dict, x: float, y: float, bg) -> None:
        runs, colors, align, _ml, _c = it.text
        tl = plan.tl
        key = (id(tl), tuple(colors))
        runs_out = self.run_cache.get(key)
        if runs_out is None:
            runs_out = []
            for ln in tl.lines:
                fr = []
                for f in ln.frags:
                    self.used.setdefault(f.face, set()).update(g[0] for g in f.glyphs)
                    fr.append({"face": f.face, "size": f.size, "color": colors[f.run], "glyphs": f.glyphs})
                runs_out.append(fr)
            self.run_cache[key] = runs_out
            self.keep.append(tl)    # keeps id(tl) from being reused while the cache lives
        lines = []
        for ln, fr in zip(tl.lines, runs_out):
            off = 0.0
            if align == "center":
                off = (plan.w - ln.w) / 2
            elif align == "right":
                off = plan.w - ln.w
            lines.append({"x": round(x + off, 3) + 0.0, "y": round(y + ln.baseline, 3) + 0.0,
                          "w": round(ln.w, 3), "h": round(ln.h, 3), "text": ln.text, "runs": fr})
        d["text"] = {"align": align, "truncated": tl.truncated, "lines": lines}
        if tl.h > plan.h + 0.5 or tl.w > plan.w + 0.5:
            self.warn("UD-WARN-TEXT-OVERFLOW", it,
                      f"text {it.id!r} needs {_r(tl.w)} x {_r(tl.h)} but its box is "
                      f"{_r(plan.w)} x {_r(plan.h)}")
        if bg is not None:
            worst = None
            for ri, (txt, _st) in enumerate(runs):
                if not txt.strip():
                    continue
                c = _rgba(colors[ri])
                fg = _over(c, bg) if c else None
                if fg is None:
                    continue
                ratio = contrast_ratio(fg, bg)
                if worst is None or ratio < worst:
                    worst = ratio
            if worst is not None and worst < CONTRAST_MIN:
                self.warn("UD-WARN-CONTRAST", it,
                          f"text {it.id!r} has contrast {worst:.2f}:1 against its background; "
                          f"WCAG large-text minimum is {CONTRAST_MIN:g}:1")


# --- public API ------------------------------------------------------------

def layout_document(doc: dict, mode: str | None = None) -> dict:
    """Expand, resolve for `mode` (default: the document's first mode), lay
    out and return the resolved tree (see the module docstring). Raises
    `UiDesignerError(UD-EXPANSION)` past the expansion budget; assumes a
    document that passed `schema.validate_document`."""
    if mode is None:
        modes = doc.get("modes") or []
        mode = modes[0] if modes else None
    b = _Builder(doc, mode)
    em = _Emit(mode)
    pages = []
    try:
        for pi, page in enumerate(doc.get("pages") or []):
            nodes = []
            for ci, n in enumerate(page.get("children") or []):
                it = b.build(n, "", None, ci, None, 0, join_path("$.pages", pi, "children", ci))
                plan = _measure(it, (0.0, INF, 0.0, INF))
                nodes.append(em.node(it, plan, it.x or 0.0, it.y or 0.0, None, None))
            pages.append({"id": page.get("id"), "title": page.get("title", ""), "children": nodes})
    except RecursionError:
        raise UiDesignerError(UD_EXPANSION, "document nests too deeply to lay out",
                              detail={"reason": "depth"}) from None
    glyphs: dict[str, dict] = {}
    for key, ids in em.used.items():
        face = face_by_key(key)
        paths = {}
        for gid in sorted(ids):
            dpath = face.glyph_outline(gid)[0]
            if dpath:
                paths[str(gid)] = dpath
        glyphs[key] = {"upem": face.upem, "paths": paths}
    return {"schema": SCHEMA_VERSION, "mode": mode, "title": doc.get("title", ""), "pages": pages,
            "glyphs": glyphs, "warnings": em.warnings, "stats": {"nodes": em.nodes}}


def iter_nodes(resolved: dict):
    """Every node of a resolved tree, depth first, in paint order."""
    def walk(n: dict):
        yield n
        for c in n.get("children") or ():
            yield from walk(c)
    for page in resolved.get("pages") or ():
        for n in page.get("children") or ():
            yield from walk(n)


def document_warnings(doc: dict) -> list[dict]:
    """Every §3.3 warning: `schema.lint` plus text overflow and contrast,
    the latter checked in each of the document's modes (one pass when it has
    none). Layout warnings carry the `mode` they were found in; an overflow
    seen in several modes is reported once."""
    out = lint(doc)
    modes = doc.get("modes") or [None]
    seen: set[tuple] = set()
    for mode in modes:
        for w in layout_document(doc, mode)["warnings"]:
            key = (w["code"], w["path"], w["id"]) if w["code"] == "UD-WARN-TEXT-OVERFLOW" \
                else (w["code"], w["path"], w["id"], w.get("mode"))
            if key not in seen:
                seen.add(key)
                out.append(w)
    return out


__all__ = ["layout_document", "document_warnings", "iter_nodes", "contrast_ratio", "plain_text"]
