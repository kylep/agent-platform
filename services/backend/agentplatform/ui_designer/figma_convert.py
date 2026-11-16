"""Figma node dump -> UI Designer document (design 46 section 8, converter part).

Pure: `convert(dump)` takes the REST-like JSON tree of one Figma file (the
shape documented in `tests/fixtures/figma/README` terms: `document.children`
are the page's top-level nodes; each node has type, size, relativeTransform,
fills/strokes/effects, auto-layout props, text `segments`, `componentId` on
instances, and `fillGeometry` for vectors) and returns `(document, report)`.

The report counts, per Figma node type, `converted`, `approximated` (with the
approximation named) and `dropped`, plus an `unmapped` tally of Figma features
with no equivalent in the schema (feeds the wishlist).

Schema features added by later units (list fills, shadow lists, blur, blend,
dash, per-side strokes) are probed once against the real validator and used
only when the schema accepts them; otherwise the old single-paint fallback is
used and the loss is reported.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from . import schema as S

FALLBACK_FONT = "Inter"
_ALLOWED_FONTS = {f.lower(): f for f in S.FONT_FAMILIES}


def _accepts(node: dict) -> bool:
    doc = {"schema": 1, "pages": [{"id": "p", "children": [dict(node, id="n", type="rect")]}]}
    return not S.collect_issues(doc)


_EXT = {
    "fill_list": _accepts({"fill": ["#000000", "#ffffff"]}),
    "shadow_list": _accepts({"shadow": [{"x": 0, "y": 0, "blur": 1, "color": "#000000"}]}),
    "inset": _accepts({"shadow": [{"x": 0, "y": 0, "blur": 1, "color": "#000000", "inset": True}]}),
    "blur": _accepts({"blur": 2}),
    "backdrop_blur": _accepts({"backdrop_blur": 2}),
    "blend": _accepts({"blend": "multiply"}),
    "dash": _accepts({"stroke": {"color": "#000000", "width": 1, "dash": [2, 2]}}),
    "sides": _accepts({"stroke": {"color": "#000000", "width": 1, "sides": [1, 0, 1, 0]}}),
    "capjoin": _accepts({"stroke": {"color": "#000000", "width": 1, "cap": "round", "join": "round"}}),
    "angular": _accepts({"fill": {"gradient": "angular", "stops": [[0, "#000000"], [1, "#ffffff"]]}}),
    "diamond": _accepts({"fill": {"gradient": "diamond", "stops": [[0, "#000000"], [1, "#ffffff"]]}}),
}

_BLEND = {"MULTIPLY": "multiply", "SCREEN": "screen", "OVERLAY": "overlay", "DARKEN": "darken",
          "LIGHTEN": "lighten", "COLOR_DODGE": "color-dodge", "COLOR_BURN": "color-burn",
          "HARD_LIGHT": "hard-light", "SOFT_LIGHT": "soft-light", "DIFFERENCE": "difference",
          "EXCLUSION": "exclusion", "HUE": "hue", "SATURATION": "saturation", "COLOR": "color",
          "LUMINOSITY": "luminosity"}

# 2x3 affine matrices as (a, b, c, d, e, f): x' = a x + c y + e, y' = b x + d y + f
_I = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat(t: list | None) -> tuple:
    if not t:
        return _I
    return (t[0][0], t[1][0], t[0][1], t[1][1], t[0][2], t[1][2])


def _mul(m: tuple, n: tuple) -> tuple:
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def _inv(m: tuple) -> tuple:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return _I
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def _num(v: float, nd: int = 2) -> float | int:
    r = round(float(v), nd)
    return int(r) if r == int(r) else r


def _hex(c: dict, opacity: float = 1.0) -> str:
    a = max(0.0, min(1.0, (c.get("a", 1.0) if c.get("a") is not None else 1.0) * opacity))
    h = "#%02x%02x%02x" % tuple(round(max(0.0, min(1.0, c[k])) * 255) for k in "rgb")
    return h if a >= 0.999 else h + "%02x" % round(a * 255)


class ConvertError(ValueError):
    """The converted document breaks the schema in a way no approximation covers."""


class _Conv:
    def __init__(self, dump: dict, ext: dict | None = None):
        self.dump = dump
        self.ext = _EXT if ext is None else ext
        self.taken: set[str] = set()
        self.rep: dict[str, Any] = {
            "converted": Counter(), "approximated": defaultdict(Counter), "dropped": Counter(),
            "unmapped": Counter(), "items": [],
        }
        self.comp_ids: dict[str, str] = {}      # figma component id -> doc component id
        self.comp_nodes: dict[str, dict] = {}   # figma component id -> figma node
        self.rest_groups = bool(dump.get("restGroupTransforms"))
        self.tokens: dict[str, str] = {}
        self.n_shadows = 0
        self.n_blurs = 0

    # --- report ---------------------------------------------------------
    def ok(self, n: dict) -> None:
        self.rep["converted"][n["type"]] += 1

    def approx(self, n: dict, what: str) -> None:
        self.rep["approximated"][n["type"]][what] += 1
        self.rep["items"].append({"kind": "approximated", "what": what, "figma_id": n.get("id"),
                                  "name": n.get("name")})

    def drop(self, n: dict, what: str) -> None:
        self.rep["dropped"][n["type"]] += 1
        self.rep["items"].append({"kind": "dropped", "what": what, "figma_id": n.get("id"),
                                  "name": n.get("name")})

    def unmapped(self, n: dict, what: str) -> None:
        self.rep["unmapped"][what] += 1
        self.approx(n, what)

    def newid(self, title: str, kind: str) -> str:
        return S.generate_id(self.taken, title, kind)

    # --- paints ---------------------------------------------------------
    def _grad_angle(self, p: dict, w: float, h: float) -> float:
        m = _inv(_mat(p.get("gradientTransform")))
        x0 = m[0] * 0 + m[2] * 0.5 + m[4]
        y0 = m[1] * 0 + m[3] * 0.5 + m[5]
        x1 = m[0] * 1 + m[2] * 0.5 + m[4]
        y1 = m[1] * 1 + m[3] * 0.5 + m[5]
        dx, dy = (x1 - x0) * w, (y1 - y0) * h
        return round(math.degrees(math.atan2(dx, -dy)) % 360, 1)

    def _solid(self, p: dict, op: float) -> str:
        """A solid paint; one bound to a Figma variable becomes a colour token."""
        name = p.get("variable")
        if name and op >= 0.999:
            tok = S.slugify(name, "color").replace("-", "-")
            self.tokens[tok] = _hex(p["color"])
            return f"$color.{tok}"
        return _hex(p["color"], op)

    def _paint(self, n: dict, p: dict, w: float, h: float) -> Any | None:
        t = p["type"]
        op = p.get("opacity", 1.0)
        if t == "SOLID":
            return self._solid(p, op)
        if t.startswith("GRADIENT_"):
            stops = p.get("gradientStops", [])
            kind = {"GRADIENT_LINEAR": "linear", "GRADIENT_RADIAL": "radial",
                    "GRADIENT_ANGULAR": "angular", "GRADIENT_DIAMOND": "diamond"}[t]
            if kind in ("angular", "diamond") and not self.ext[kind]:
                self.unmapped(n, f"{kind} gradient reduced to a 2-stop linear")
                kind = "linear"
                stops = [stops[0], stops[-1]] if len(stops) > 2 else stops
            if len(stops) > S.MAX_STOPS:
                self.approx(n, "gradient stops truncated to 8")
                stops = stops[:S.MAX_STOPS]
            g: dict[str, Any] = {"gradient": kind,
                                 "stops": [[_num(s["position"], 3), _hex(s["color"], op)] for s in stops]}
            if kind == "linear":
                g["angle"] = self._grad_angle(p, w, h)
            return g
        if t == "IMAGE":
            self.unmapped(n, "image fill (no asset fetched)")
            return None
        self.unmapped(n, f"paint {t}")
        return None

    def _paints(self, n: dict, key: str, w: float, h: float) -> list:
        vis = [p for p in n.get(key) or [] if p.get("visible", True)]
        out = [x for x in (self._paint(n, p, w, h) for p in vis) if x is not None]
        return out

    def _fill(self, n: dict, w: float, h: float) -> Any | None:
        paints = self._paints(n, "fills", w, h)
        if not paints:
            return None
        if len(paints) == 1:
            return paints[0]
        if self.ext["fill_list"]:
            if len(paints) > S.MAX_PAINTS:
                self.approx(n, f"fill stack trimmed to the top {S.MAX_PAINTS} paints")
                paints = paints[-S.MAX_PAINTS:]
            return paints  # Figma lists paints bottom to top, as the schema does
        self.approx(n, "stacked fills collapsed to the topmost paint")
        return paints[-1]

    def _stroke(self, n: dict) -> dict | None:
        w, h = n.get("width", 0), n.get("height", 0)
        paints = self._paints(n, "strokes", w, h)
        if not paints:
            return None
        c = paints[-1]
        if len(paints) > 1:
            self.approx(n, "stacked strokes collapsed to the topmost paint")
        if not isinstance(c, str):
            self.approx(n, "gradient stroke reduced to its first stop")
            c = c["stops"][0][1]
        st: dict[str, Any] = {"color": c, "width": _num(n.get("strokeWeight", 1))}
        al = (n.get("strokeAlign") or "INSIDE").lower()
        st["align"] = {"inside": "inside", "outside": "outside", "center": "center"}[al]
        sides = [n.get(k) for k in ("strokeTopWeight", "strokeRightWeight",
                                    "strokeBottomWeight", "strokeLeftWeight")]
        if any(s is not None for s in sides) and len({s for s in sides}) > 1:
            if self.ext["sides"]:
                st["sides"] = [_num(s or 0) for s in sides]
            else:
                self.unmapped(n, "per-side stroke weights")
        if n.get("dashPattern"):
            if self.ext["dash"]:
                st["dash"] = [_num(x) for x in n["dashPattern"]]
            else:
                self.unmapped(n, "dashed stroke")
        for k, ours in (("strokeCap", "cap"), ("strokeJoin", "join")):
            v = n.get(k)
            if v and v not in ("NONE", "MITER"):
                if self.ext["capjoin"]:
                    st[ours] = v.lower()
                else:
                    self.unmapped(n, f"stroke {ours}")
        return st

    def _clamp(self, n: dict, v: float, lo: float, hi: float, what: str) -> float | int:
        c = max(lo, min(hi, v))
        if c != v:
            self.approx(n, f"{what} clamped to {_num(c)}")
        return _num(c)

    def _effects(self, n: dict, out: dict) -> None:
        shadows = []
        for e in n.get("effects") or []:
            if not e.get("visible", True):
                continue
            t = e["type"]
            if t in ("DROP_SHADOW", "INNER_SHADOW"):
                if self.n_shadows >= S.MAX_SHADOWS:
                    self.unmapped(n, f"shadow beyond the {S.MAX_SHADOWS} per document")
                    continue
                self.n_shadows += 1
                sh = {"x": _num(e.get("offset", {}).get("x", 0)), "y": _num(e.get("offset", {}).get("y", 0)),
                      "blur": self._clamp(n, e.get("radius", 0), 0, S.MAX_BLUR, "shadow blur"),
                      "color": _hex(e["color"])}
                if e.get("spread"):
                    sh["spread"] = self._clamp(n, e["spread"], -S.MAX_BLUR, S.MAX_BLUR, "shadow spread")
                if t == "INNER_SHADOW":
                    if not self.ext["inset"]:
                        self.unmapped(n, "inner shadow")
                        continue
                    sh["inset"] = True
                shadows.append(sh)
            elif t == "LAYER_BLUR":
                if self.ext["blur"] and self.n_blurs < S.MAX_BLURS:
                    self.n_blurs += 1
                    out["blur"] = self._clamp(n, e.get("radius", 0), 0, S.MAX_BLUR, "layer blur")
                elif self.ext["blur"]:
                    self.unmapped(n, f"layer blur beyond the {S.MAX_BLURS} per document")
                else:
                    self.unmapped(n, "layer blur")
            elif t == "BACKGROUND_BLUR":
                if self.ext["backdrop_blur"]:
                    out["backdrop_blur"] = self._clamp(n, e.get("radius", 0), 0, S.MAX_BLUR, "background blur")
                else:
                    self.unmapped(n, "background blur")
            else:
                self.unmapped(n, f"effect {t}")
        if shadows:
            if self.ext["shadow_list"]:
                out["shadow"] = [dict(s) for s in shadows]
            else:
                if len(shadows) > 1 or "spread" in shadows[0]:
                    self.unmapped(n, "multiple shadows / shadow spread")
                out["shadow"] = {k: v for k, v in shadows[0].items() if k in ("x", "y", "blur", "color")}
        bm = n.get("blendMode")
        if bm and bm not in ("PASS_THROUGH", "NORMAL"):
            if self.ext["blend"] and bm in _BLEND:
                out["blend"] = _BLEND[bm]
            else:
                self.unmapped(n, f"blend mode {bm}")

    # --- geometry -------------------------------------------------------
    def _place(self, n: dict, out: dict, parent_layout: bool, rebase: tuple) -> None:
        """size, position and rotation. `rebase` is the matrix that converts this
        node's relativeTransform into the coordinates of its emitted parent."""
        w, h = n.get("width", 0), n.get("height", 0)
        m = _mul(rebase, _mat(n.get("relativeTransform")))
        rot = math.degrees(math.atan2(m[1], m[0]))
        det = m[0] * m[3] - m[1] * m[2]
        sx = math.hypot(m[0], m[1])
        sy = abs(det) / sx if sx else 1.0
        flipped = det < 0 or abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3
        if flipped:
            self.approx(n, "scale/flip/skew in transform ignored")
        if abs(rot) > 0.01:
            out["rotate"] = _num(rot, 2)
        # top-left of the unrotated box so rotation about our centre matches Figma
        cx, cy = w / 2, h / 2
        rcx = m[0] * cx + m[2] * cy
        rcy = m[1] * cx + m[3] * cy
        x, y = m[4] + rcx - cx, m[5] + rcy - cy
        lp = n.get("layoutPositioning") == "ABSOLUTE"
        if not parent_layout or lp:
            out["x"], out["y"] = _num(x), _num(y)
            if lp:
                out["absolute"] = True
        sh = n.get("layoutSizingHorizontal")
        sv = n.get("layoutSizingVertical")
        out["w"] = "fill" if sh == "FILL" else "hug" if sh == "HUG" else _num(w)
        out["h"] = "fill" if sv == "FILL" else "hug" if sv == "HUG" else _num(h)

    def _common(self, n: dict, out: dict) -> None:
        if n.get("visible") is False:
            out["visible"] = False
        if n.get("opacity") not in (None, 1):
            out["opacity"] = _num(n["opacity"], 3)
        self._effects(n, out)
        out["meta"] = {"figma": {"id": n["id"]}}

    def _radius(self, n: dict, out: dict) -> None:
        r = [n.get(k) for k in ("topLeftRadius", "topRightRadius", "bottomRightRadius", "bottomLeftRadius")]
        if any(v is not None for v in r) and len({v for v in r}) > 1:
            out["radius"] = [_num(v or 0) for v in r]
        elif n.get("cornerRadius"):
            out["radius"] = _num(n["cornerRadius"])

    # --- nodes ----------------------------------------------------------
    def node(self, n: dict, parent_layout: bool, rebase: tuple = _I, in_comp: bool = False,
             root_id: str | None = None) -> list[dict]:
        """Convert one figma node; returns a list (groups can flatten to nothing)."""
        t = n["type"]
        if n.get("visible") is False and t not in ("INSTANCE",):
            pass  # kept as visible:false
        if t in ("FRAME", "GROUP", "SECTION", "COMPONENT", "COMPONENT_SET", "BOOLEAN_OPERATION") \
                and t != "BOOLEAN_OPERATION":
            return [self.frame(n, parent_layout, rebase, root_id)]
        if t == "BOOLEAN_OPERATION" and not n.get("fillGeometry"):
            return [self.frame(n, parent_layout, rebase, root_id)]
        if t in ("RECTANGLE", "ELLIPSE", "LINE"):
            return [self.shape(n, parent_layout, rebase)]
        if t in ("VECTOR", "BOOLEAN_OPERATION", "STAR", "POLYGON", "REGULAR_POLYGON"):
            return [self.path(n, parent_layout, rebase)]
        if t == "TEXT":
            return [self.text(n, parent_layout, rebase)]
        if t == "INSTANCE":
            return [self.instance(n, parent_layout, rebase)]
        self.drop(n, f"unsupported node type {t}")
        return []

    def _children(self, n: dict, out_layout: bool, rebase: tuple) -> list[dict]:
        kids: list[dict] = []
        src = n.get("children") or []
        i = 0
        while i < len(src):
            c = src[i]
            if c.get("isMask"):
                mt = {"ALPHA": "alpha", "VECTOR": "vector", "LUMINANCE": "luminance"}.get(
                    c.get("maskType", "ALPHA"), "alpha")
                grp = {"id": self.newid(c.get("name", "mask") + " group", "mask-group"), "type": "frame",
                       "x": 0, "y": 0, "w": _num(n.get("width", 0)), "h": _num(n.get("height", 0)),
                       "mask": mt, "children": []}
                self.approx(c, "mask regrouped under a masked frame")
                for sib in src[i:]:
                    if sib is not c and sib.get("isMask"):
                        break
                    grp["children"].extend(self.node(sib, False, rebase))
                    i += 1
                kids.append(grp)
                continue
            kids.extend(self.node(c, out_layout, rebase))
            i += 1
        return kids

    def frame(self, n: dict, parent_layout: bool, rebase: tuple, root_id: str | None = None) -> dict:
        t = n["type"]
        out: dict[str, Any] = {"type": "frame"}
        out["id"] = root_id or self.newid(n.get("name", "frame"), "frame")
        if n.get("name") and not root_id:
            out["title"] = n["name"][:200]
        is_group = t in ("GROUP", "BOOLEAN_OPERATION")
        if root_id:
            self._common(n, out)
            out["w"], out["h"] = _num(n.get("width", 0)), _num(n.get("height", 0))
        else:
            self._place(n, out, parent_layout, rebase)
            self._common(n, out)
        w, h = n.get("width", 0), n.get("height", 0)
        fill = self._fill(n, w, h)
        if fill is not None:
            out["fill"] = fill
        st = self._stroke(n)
        if st:
            out["stroke"] = st
        self._radius(n, out)
        if n.get("clipsContent"):
            out["clip"] = True
        lm = n.get("layoutMode")
        has_layout = lm in ("HORIZONTAL", "VERTICAL", "GRID")
        if has_layout:
            lay: dict[str, Any] = {"dir": "row" if lm == "HORIZONTAL" else "column"}
            if lm == "GRID":
                self.approx(n, "grid layout flattened to a column")
            if n.get("itemSpacing"):
                lay["gap"] = _num(n["itemSpacing"])
            pads = [n.get(k, 0) or 0 for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft")]
            if any(pads):
                lay["pad"] = _num(pads[0]) if len(set(pads)) == 1 else [_num(p) for p in pads]
            ju = {"MIN": "start", "CENTER": "center", "MAX": "end", "SPACE_BETWEEN": "space-between"}
            al = {"MIN": "start", "CENTER": "center", "MAX": "end", "BASELINE": "start"}
            if n.get("primaryAxisAlignItems") in ju and ju[n["primaryAxisAlignItems"]] != "start":
                lay["justify"] = ju[n["primaryAxisAlignItems"]]
            if n.get("counterAxisAlignItems") in al and al[n["counterAxisAlignItems"]] != "start":
                lay["align"] = al[n["counterAxisAlignItems"]]
            if n.get("layoutWrap") == "WRAP":
                lay["wrap"] = True
            out["layout"] = lay
        # children of a group are relative to the nearest real container: rebase
        child_rebase = _I
        if is_group and self.rest_groups:
            child_rebase = _inv(_mul(rebase, _mat(n.get("relativeTransform"))))
            child_rebase = _inv(_mat(n.get("relativeTransform")))
        out["children"] = self._children(n, has_layout, child_rebase)
        if is_group:
            self.approx(n, f"{t.lower().replace('_', ' ')} converted to a plain frame") \
                if t == "BOOLEAN_OPERATION" else None
        self.ok(n)
        return out

    def shape(self, n: dict, parent_layout: bool, rebase: tuple) -> dict:
        kind = {"RECTANGLE": "rect", "ELLIPSE": "ellipse", "LINE": "line"}[n["type"]]
        out: dict[str, Any] = {"type": kind, "id": self.newid(n.get("name", kind), kind)}
        self._place(n, out, parent_layout, rebase)
        self._common(n, out)
        fill = self._fill(n, n.get("width", 0), n.get("height", 0))
        if fill is not None and kind != "line":
            out["fill"] = fill
        st = self._stroke(n)
        if st:
            out["stroke"] = st
        if kind == "rect":
            self._radius(n, out)
        if kind == "ellipse" and n.get("arcData") and (
                abs(n["arcData"].get("endingAngle", 6.283185) - n["arcData"].get("startingAngle", 0) - 6.283185) > 1e-3
                or n["arcData"].get("innerRadius")):
            self.unmapped(n, "ellipse arc / ring")
        if kind == "line":
            out["h"] = 0 if out["h"] in (0, "hug") else out["h"]
        self.ok(n)
        return out

    def path(self, n: dict, parent_layout: bool, rebase: tuple) -> dict:
        out: dict[str, Any] = {"type": "path", "id": self.newid(n.get("name", "path"), "path")}
        self._place(n, out, parent_layout, rebase)
        self._common(n, out)
        geoms = n.get("vectorPaths") or n.get("fillGeometry") or []
        sgeoms = n.get("strokeGeometry") or []
        d = " ".join(g["data"] for g in geoms)
        fill = self._fill(n, n.get("width", 0), n.get("height", 0))
        st = self._stroke(n)
        if not d and sgeoms:
            # stroke-only vector: paint the stroke outline as a fill
            d = " ".join(g["data"] for g in sgeoms)
            if st and fill is None and not n.get("vectorPaths"):
                fill = st["color"]
                st = None
                self.approx(n, "stroke-only vector painted from stroke outline")
        if not d:
            self.drop(n, "vector without geometry")
            return {"type": "frame", "id": out["id"], "w": out.get("w", 0), "h": out.get("h", 0),
                    "meta": out["meta"], "visible": False}
        out["d"] = d
        if fill is not None:
            out["fill"] = fill
        if st:
            out["stroke"] = st
        if n["type"] in ("STAR", "POLYGON", "REGULAR_POLYGON", "BOOLEAN_OPERATION"):
            self.approx(n, f"{n['type'].lower()} flattened to path")
        self.ok(n)
        return out

    def text(self, n: dict, parent_layout: bool, rebase: tuple) -> dict:
        out: dict[str, Any] = {"type": "text", "id": self.newid(n.get("name", "text")[:40], "text")}
        self._place(n, out, parent_layout, rebase)
        self._common(n, out)
        auto = n.get("textAutoResize", "NONE")
        if auto == "WIDTH_AND_HEIGHT":
            out["w"] = "hug"
            out["h"] = "hug"
        elif auto == "HEIGHT":
            out["h"] = "hug"
        segs = n.get("segments") or []
        chars = n.get("characters", "")
        runs = []
        for s in segs:
            runs.append((chars[s["start"]:s["end"]], self._tstyle(n, s), self._tcolor(n, s)))
        if not runs:
            runs = [(chars, {"family": FALLBACK_FONT, "size": 14}, None)]
        base = max(runs, key=lambda r: len(r[0]))
        out["style"] = base[1]
        if base[2]:
            out["color"] = base[2]
        if len(runs) == 1:
            out["text"] = runs[0][0]
        else:
            rr = []
            for txt, style, col in runs:
                r: dict[str, Any] = {"text": txt}
                if style != base[1]:
                    r["style"] = style
                if col and col != base[2]:
                    r["color"] = col
                rr.append(r)
            out["text"] = rr
        ah = n.get("textAlignHorizontal", "LEFT")
        if ah in ("CENTER", "RIGHT"):
            out["align"] = ah.lower()
        elif ah == "JUSTIFIED":
            self.approx(n, "justified text left-aligned")
        if n.get("textAlignVertical") in ("CENTER", "BOTTOM") and auto != "WIDTH_AND_HEIGHT":
            self.approx(n, "vertical text alignment ignored")
        self.ok(n)
        return out

    def _tstyle(self, n: dict, s: dict) -> dict:
        fam = s.get("fontFamily", FALLBACK_FONT)
        if fam.lower() in _ALLOWED_FONTS:
            fam = _ALLOWED_FONTS[fam.lower()]
        else:
            self.approx(n, f"font {fam} substituted with {FALLBACK_FONT}")
            fam = FALLBACK_FONT
        size = s.get("fontSize", 14)
        st: dict[str, Any] = {"family": fam, "size": _num(size)}
        wt = s.get("fontWeight")
        if wt:
            st["weight"] = int(wt)
        lh = s.get("lineHeight") or {}
        if lh.get("unit") == "PIXELS" and size:
            st["line"] = _num(lh["value"] / size, 3)
        elif lh.get("unit") == "PERCENT":
            st["line"] = _num(lh["value"] / 100, 3)
        ls = s.get("letterSpacing") or {}
        if ls.get("value"):
            st["letter"] = _num(ls["value"] if ls.get("unit") == "PIXELS" else ls["value"] / 100 * size, 3)
        return st

    def _tcolor(self, n: dict, s: dict) -> str | None:
        fills = [p for p in s.get("fills") or [] if p.get("visible", True)]
        if not fills:
            return None
        p = fills[-1]
        if p["type"] == "SOLID":
            return self._solid(p, p.get("opacity", 1.0))
        self.approx(n, "non-solid text fill")
        return None

    def instance(self, n: dict, parent_layout: bool, rebase: tuple) -> dict:
        cid = n.get("componentId")
        if cid not in self.comp_ids:
            f = self.frame(dict(n, type="FRAME"), parent_layout, rebase)
            f["meta"]["figma"]["instance_of"] = n.get("componentName") or cid
            self.approx(n, "instance of an external component flattened")
            return f
        out: dict[str, Any] = {"type": "instance", "of": self.comp_ids[cid],
                               "id": self.newid(n.get("name", "instance"), "instance")}
        self._place(n, out, parent_layout, rebase)
        self._common(n, out)
        main = self.comp_nodes[cid]
        if (out.get("w") == _num(main.get("width", 0)) and "w" in out) or out.get("w") in ("fill", "hug"):
            pass
        ov = self._overrides(n, main, self.comp_children_ids.get(cid, {}), self.comp_ids[cid])
        if ov:
            out["overrides"] = ov
        self.ok(n)
        return out

    def _overrides(self, inst: dict, main: dict, ids: dict, root: str) -> dict:
        ov: dict[str, Any] = {}

        def walk(a: dict, b: dict, is_root: bool = False) -> None:
            tid = ids.get(b["id"])
            if tid:
                self._diff(a, b, tid, ov, root=is_root)
            for ca, cb in zip(a.get("children") or [], b.get("children") or []):
                walk(ca, cb)
        walk(inst, main, True)
        # the component root is addressed by the bare property name
        return {(k[len(root) + 1:] if k.startswith(root + ".") else k): v for k, v in ov.items()
                if not k.startswith(root + ".") or k[len(root) + 1:] not in ("x", "y")}

    def _diff(self, a: dict, b: dict, tid: str, ov: dict, root: bool = False) -> None:
        if root:
            # an INSTANCE root against its COMPONENT: only the paint can differ here
            # (size, opacity, visibility are the instance's own props)
            fa, fb = a.get("fills"), b.get("fills")
            if fa != fb and fa is not None:
                vis = self._paints(a, "fills", a.get("width", 0), a.get("height", 0))
                if vis:
                    ov[f"{tid}.fill"] = vis if len(vis) > 1 and self.ext["fill_list"] else vis[-1]
            return
        if a.get("type") != b.get("type"):
            return
        if a.get("characters") != b.get("characters") and a.get("characters") is not None:
            ov[f"{tid}.text"] = a["characters"]
        fa, fb = a.get("fills"), b.get("fills")
        if fa != fb and fa is not None and a["type"] != "TEXT":
            vis = self._paints(a, "fills", a.get("width", 0), a.get("height", 0))
            if vis:
                ov[f"{tid}.fill"] = vis[-1]
        if a.get("visible", True) != b.get("visible", True):
            ov[f"{tid}.visible"] = a.get("visible", True)
        if a.get("opacity", 1) != b.get("opacity", 1):
            ov[f"{tid}.opacity"] = _num(a.get("opacity", 1), 3)
        if a["type"] in ("RECTANGLE", "ELLIPSE", "VECTOR", "FRAME") and (
                a.get("width") != b.get("width") or a.get("height") != b.get("height")) and False:
            pass
        ta, tb = a.get("relativeTransform"), b.get("relativeTransform")
        if ta and tb and tid and a["type"] != "TEXT":
            if abs(ta[0][2] - tb[0][2]) > 0.01 or abs(ta[1][2] - tb[1][2]) > 0.01:
                ov[f"{tid}.x"] = _num(ta[0][2])
                ov[f"{tid}.y"] = _num(ta[1][2])


def convert(dump: dict, *, ext: dict | None = None) -> tuple[dict, dict]:
    """Figma dump -> (document dict, report dict). Raises nothing for odd nodes;
    they are reported as dropped. `ext` overrides the schema-feature probe (e.g. all
    False to target a server that predates the extended effects)."""
    c = _Conv(dump, ext)
    page = dump["document"]
    tops = page.get("children") or []
    comps = [t for t in tops if t["type"] in ("COMPONENT", "COMPONENT_SET")]
    for t in comps:
        c.comp_ids[t["id"]] = c.newid(t.get("name", "component"), "component")
        c.comp_nodes[t["id"]] = t
    c.comp_children_ids = {}
    doc: dict[str, Any] = {"schema": S.SCHEMA_VERSION, "title": dump.get("name") or page.get("name") or "Figma import",
                           "components": {}, "pages": []}
    for t in comps:
        key = c.comp_ids[t["id"]]
        # descendants first so every child gets a stable id; root keeps the component key
        root = c.frame(t, False, _I, root_id=key)
        c.comp_children_ids[t["id"]] = _id_map(t, root)
        doc["components"][key] = _strip_root_pos(root)
    children: list[dict] = []
    for t in tops:
        if t in comps:
            continue
        children.extend(c.node(t, False))
    if c.tokens:
        doc["tokens"] = {"color": dict(sorted(c.tokens.items()))}
    doc["pages"].append({"id": c.newid(page.get("name", "page"), "page"),
                         "title": page.get("name", "Page")[:200], "children": children})
    # instances were converted after components so overrides are diffed against ids
    issues = S.collect_issues(doc)
    if issues:
        raise ConvertError("converted document is invalid: " + "; ".join(
            f"{i.code} at {i.path}: {i.message}" for i in issues[:10]))
    rep = {
        "converted": dict(c.rep["converted"]),
        "approximated": {k: dict(v) for k, v in c.rep["approximated"].items()},
        "dropped": dict(c.rep["dropped"]),
        "unmapped": dict(c.rep["unmapped"]),
        "items": c.rep["items"],
    }
    return doc, rep


def _strip_root_pos(root: dict) -> dict:
    for k in ("x", "y", "absolute"):
        root.pop(k, None)
    return root


def _id_map(fig: dict, doc: dict) -> dict[str, str]:
    """figma id -> doc id for a component, by walking both trees in step."""
    m: dict[str, str] = {}

    def walk(f: dict, d: dict) -> None:
        if "id" in d:
            m[f["id"]] = d["id"]
        fk, dk = f.get("children") or [], d.get("children") or []
        if len(fk) != len(dk):
            return  # a masked regroup changed the shape; overrides skip this subtree
        for a, b in zip(fk, dk):
            walk(a, b)
    walk(fig, doc)
    m[fig["id"]] = doc["id"]
    return m
