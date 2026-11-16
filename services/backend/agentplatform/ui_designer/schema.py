"""The UI Designer document (docs/design/46 §2.2): closed schema, ids, tokens,
the expansion budget and lint warnings. Pure functions over plain dicts; no
database, no HTTP, nothing here renders.

Validation reports every issue at once (`UD-INVALID`), then checks the
expansion budget (`UD-EXPANSION`), then image placements (`UD-ASSET-UNKNOWN`).
"""
from __future__ import annotations

import copy
import difflib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from agentplatform.ui_designer.errors import (UD_ASSET_UNKNOWN, UD_EXPANSION, Issue,
                                              UiDesignerError, invalid, issue, join_path)

SCHEMA_VERSION = 1

# Limits (§2.2).
MAX_DOC_BYTES = 1 << 20
MAX_NODES = 5000
MAX_PAGES = 50
MAX_COMPONENTS = 200
MAX_INSTANCE_DEPTH = 6
MAX_EXPANDED_NODES = 20_000
MAX_TEXT_BYTES = 4096
MAX_PATH_D_BYTES = 32 * 1024
MAX_PATH_TOTAL_BYTES = 1 << 20
MAX_SHADOWS = 32
MAX_BLUR = 64
MAX_STOPS = 8
MAX_PAINTS = 8        # paints stacked in one `fill`
MAX_BLURS = 32        # nodes with a layer `blur`
MAX_BACKDROPS = 8     # nodes with `backdrop_blur` (each re-paints what is behind it)
MAX_DASH = 8
MAX_STEPPED = 64      # angular/diamond paints in the expanded tree (each is ~50-100 shapes)
GRADIENT_KINDS = ("linear", "radial", "angular", "diamond")
# Supported CSS mix-blend-mode names. Omit `blend` for normal compositing;
# modes without a supported CSS equivalent are not offered.
BLEND_MODES = ("normal", "darken", "multiply", "color-burn", "lighten", "screen", "color-dodge",
               "plus-lighter", "overlay", "soft-light", "hard-light", "difference", "exclusion",
               "hue", "saturation", "color", "luminosity")
MAX_META_BYTES = 2048
MAX_NUMBER = 100_000
MAX_FRAME_DEPTH = 64  # guards the recursive walkers; not a design limit a document should near

# The bundled allowlist (§5). T2's fonts.py owns the files; the names live here
# so validation needs no font code.
FONT_FAMILIES = ("Inter", "JetBrains Mono", "Noto Sans", "Nunito", "Fredoka")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MODE_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
TOKEN_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}"
REF_RE = re.compile(rf"^\$(color|space|radius|type)\.({TOKEN_NAME})$")
PATH_D_RE = re.compile(r"^[MmLlHhVvCcSsQqTtAaZz0-9eE+\-., \t\r\n]*$")

TOKEN_GROUPS = ("color", "space", "radius", "type")
NODE_TYPES = ("frame", "text", "rect", "ellipse", "line", "path", "image", "instance")

_SHARED = {"id", "type", "title", "w", "h", "x", "y", "absolute", "rotate", "fill", "stroke",
           "radius", "opacity", "visible", "clip", "shadow", "blur", "backdrop_blur", "blend",
           "meta"}
TYPE_KEYS: dict[str, set[str]] = {
    "frame": _SHARED | {"layout", "children", "mask"},
    "text": _SHARED | {"text", "style", "color", "align", "max_lines"},
    "rect": set(_SHARED),
    "ellipse": set(_SHARED),
    "line": set(_SHARED),
    "path": _SHARED | {"d"},
    "image": _SHARED | {"asset", "fit"},
    "instance": _SHARED | {"of", "overrides"},
}
# Never overridable on an instance's target.
# asset is not overridable: image_assets() walks node assets only, so an
# override would place an image the asset check never saw.
_NO_OVERRIDE = {"id", "type", "children", "layout", "mask", "of", "overrides", "asset"}

# Value positions that are text, not token references (resolution skips them).
_LITERAL_KEYS = {"text", "meta", "title", "id", "d", "asset", "of", "image"}


# --- ids -----------------------------------------------------------------

def slugify(text: str, fallback: str = "node") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:48].strip("-")
    return s if s and ID_RE.fullmatch(s) else fallback


def generate_id(taken: set[str], title: str | None = None, kind: str = "node") -> str:
    """A fresh id from the title (or the node type), suffixed -2, -3 ... until
    unused. Adds the id to `taken`."""
    base = slugify(title, kind) if title else slugify(kind)
    cand, n = base, 1
    while cand in taken:
        n += 1
        suffix = f"-{n}"
        cand = base[:48 - len(suffix)].rstrip("-") + suffix
    taken.add(cand)
    return cand


def assign_ids(node: Any, taken: set[str]) -> None:
    """Give every node in the subtree without an id a generated one (in place).
    Tolerates junk; validation reports it."""
    if not isinstance(node, dict):
        return
    if "id" not in node:
        node["id"] = generate_id(taken, node.get("title") if isinstance(node.get("title"), str)
                                 else None, node["type"] if isinstance(node.get("type"), str)
                                 else "node")
    elif isinstance(node["id"], str):
        taken.add(node["id"])
    children = node.get("children")
    if isinstance(children, list):
        for c in children:
            assign_ids(c, taken)


def normalize_document(doc: dict) -> dict:
    """A copy with server-generated ids filled in: pages, nodes, and the id of
    each component's root (which is its key)."""
    out = copy.deepcopy(doc)
    taken: set[str] = set()
    _collect_ids(out, taken)
    pages = out.get("pages")
    for page in pages if isinstance(pages, list) else []:
        if isinstance(page, dict):
            if "id" not in page:
                page["id"] = generate_id(taken, page.get("title") if isinstance(
                    page.get("title"), str) else None, "page")
            for c in page.get("children") if isinstance(page.get("children"), list) else []:
                assign_ids(c, taken)
    comps = out.get("components")
    for key, root in (comps.items() if isinstance(comps, dict) else []):
        if isinstance(root, dict):
            root.setdefault("id", key)
            assign_ids(root, taken)
    return out


def _collect_ids(doc: dict, taken: set[str]) -> None:
    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if isinstance(n.get("id"), str):
                taken.add(n["id"])
            for c in n.get("children") if isinstance(n.get("children"), list) else []:
                walk(c)
    for page in doc.get("pages") if isinstance(doc.get("pages"), list) else []:
        walk(page)
    comps = doc.get("components")
    for key, root in (comps.items() if isinstance(comps, dict) else []):
        taken.add(key)
        walk(root)


# --- tokens --------------------------------------------------------------

def resolve_token(ref: str, tokens: dict, mode: str | None = None,
                  modes: list[str] | None = None) -> Any:
    """The literal a `$group.name` reference names. A mode-keyed colour picks
    the render's mode, falling back to the first mode (in `modes` order, then
    key order) that it defines; the lint reports the gap."""
    m = REF_RE.fullmatch(ref)
    if not m:
        raise KeyError(ref)
    group, name = m.groups()
    value = tokens[group][name]
    if group == "color" and isinstance(value, dict):
        if mode in value:
            return value[mode]
        for k in (modes or []):
            if k in value:
                return value[k]
        return next(iter(value.values()))
    return copy.deepcopy(value)


def resolve_tokens(doc: dict, mode: str | None = None) -> dict:
    """A copy of the document with every token reference replaced by its
    literal for `mode`. Text, titles, ids, path data and `meta` are never
    treated as references."""
    tokens = doc.get("tokens") or {}
    modes = doc.get("modes")

    def sub(v: Any, key: str | None = None) -> Any:
        if isinstance(v, str):
            return resolve_token(v, tokens, mode, modes) if REF_RE.fullmatch(v) else v
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if k in _LITERAL_KEYS or (key == "overrides" and (k == "text" or k.endswith(".text"))):
                    out[k] = copy.deepcopy(x)
                else:
                    out[k] = sub(x, k)
            return out
        return v

    out = {}
    for k, v in doc.items():
        out[k] = copy.deepcopy(v) if k in ("tokens", "schema", "title", "modes") else sub(v, k)
    return out


def iter_token_refs(doc: dict) -> Iterator[tuple[str, str]]:
    """Yield (json path, reference) for every `$group.name` used outside `tokens`."""
    def walk(v: Any, path: str, key: str | None = None) -> Iterator[tuple[str, str]]:
        if isinstance(v, str):
            if REF_RE.fullmatch(v):
                yield path, v
        elif isinstance(v, list):
            for i, x in enumerate(v):
                yield from walk(x, join_path(path, i))
        elif isinstance(v, dict):
            for k, x in v.items():
                if k in _LITERAL_KEYS or (key == "overrides" and (k == "text" or k.endswith(".text"))):
                    continue
                yield from walk(x, join_path(path, k), k)
    for k, v in doc.items():
        if k not in ("tokens", "schema", "title", "modes"):
            yield from walk(v, join_path("$", k), k)


# --- the validator -------------------------------------------------------

@dataclass
class _Ctx:
    tokens: dict
    modes: list[str] | None
    components: dict
    issues: list[Issue] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    nodes: int = 0
    shadows: int = 0
    blurs: int = 0
    backdrops: int = 0
    path_bytes: int = 0
    instances: list[tuple[str, dict, str]] = field(default_factory=list)  # path, node, scope
    comp_nodes: dict[str, dict[str, dict]] = field(default_factory=dict)

    def add(self, code: str, path: str, message: str, value: Any = None,
            fix: str | None = None) -> None:
        self.issues.append(issue(code, path, message, value, fix))


def _isnum(v: Any) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            and abs(v) <= MAX_NUMBER)


def _isref(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("$")


def _tokref(c: _Ctx, v: str, path: str, groups: tuple[str, ...]) -> None:
    m = REF_RE.fullmatch(v)
    if not m:
        c.add("UD-FORMAT", path, "malformed token reference", v,
              fix="Write `$<group>.<name>`, e.g. `$color.accent`.")
        return
    group, name = m.groups()
    if group not in groups:
        c.add("UD-TOKEN-KIND", path, f"`$%s` tokens are not accepted here" % group, v,
              fix=f"Use a token from: {', '.join('$' + g for g in groups)}.")
    elif name not in (c.tokens.get(group) or {}):
        c.add("UD-TOKEN-UNKNOWN", path, f"no token {v}", v)


# Spellings agents reach for first; difflib alone would not find these.
_KEY_ALIASES = {"width": "w", "height": "h", "padding": "pad", "direction": "dir",
                "font_size": "size", "fontsize": "size", "font": "family",
                "background": "fill", "bg": "fill", "border": "stroke",
                "corner_radius": "radius", "border_radius": "radius", "content": "text",
                "kids": "children", "items": "children", "spacing": "gap"}


def _closest_key(key: str, allowed: set[str]) -> str | None:
    alias = _KEY_ALIASES.get(key.lower())
    if alias in allowed:
        return alias
    near = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
    return near[0] if near else None


def _obj(c: _Ctx, v: Any, path: str, allowed: set[str], required: tuple[str, ...] = ()) -> bool:
    if not isinstance(v, dict):
        c.add("UD-TYPE", path, "expected an object", v)
        return False
    for k in v:
        if k not in allowed:
            guess = _closest_key(k, allowed)
            hint = f"Did you mean {guess!r}? " if guess else ""
            c.add("UD-UNKNOWN-KEY", join_path(path, k), f"unknown key {k!r}",
                  fix=f"{hint}Allowed keys: {', '.join(sorted(allowed))}.")
    for k in required:
        if k not in v:
            c.add("UD-MISSING", join_path(path, k), f"missing required key {k!r}")
    return True


def _num(c: _Ctx, v: Any, path: str, lo: float = -MAX_NUMBER, hi: float = MAX_NUMBER,
         groups: tuple[str, ...] = ("space", "radius")) -> None:
    if _isref(v):
        _tokref(c, v, path, groups)
    elif not _isnum(v):
        c.add("UD-TYPE", path, f"expected a finite number within ±{MAX_NUMBER}", v)
    elif not lo <= v <= hi:
        c.add("UD-BOUNDS", path, f"must be between {lo} and {hi}", v)


def _enum(c: _Ctx, v: Any, path: str, values: tuple[str, ...]) -> None:
    if v not in values or not isinstance(v, str):
        c.add("UD-VALUE", path, f"must be one of {', '.join(values)}", v)


def _bool(c: _Ctx, v: Any, path: str) -> None:
    if not isinstance(v, bool):
        c.add("UD-TYPE", path, "expected true or false", v)


def _color(c: _Ctx, v: Any, path: str) -> None:
    if _isref(v):
        _tokref(c, v, path, ("color",))
    elif not (isinstance(v, str) and COLOR_RE.fullmatch(v)):
        c.add("UD-FORMAT", path, "expected #rgb, #rrggbb or #rrggbbaa", v)


def _size(c: _Ctx, v: Any, path: str) -> None:
    if v in ("fill", "hug") and isinstance(v, str):
        return
    _num(c, v, path, 0, MAX_NUMBER)


def _fill(c: _Ctx, v: Any, path: str) -> None:
    """One paint, or a list of paints stacked bottom to top."""
    if isinstance(v, list):
        if not 1 <= len(v) <= MAX_PAINTS:
            c.add("UD-BOUNDS", path, f"a fill list holds 1 to {MAX_PAINTS} paints", v)
            return
        for i, p in enumerate(v):
            _paint(c, p, join_path(path, i))
    else:
        _paint(c, v, path)


def _paint(c: _Ctx, v: Any, path: str) -> None:
    if not isinstance(v, dict):
        return _color(c, v, path)
    kind = next((k for k in ("color", "image") if k in v), "gradient")
    if sum(k in v for k in ("color", "gradient", "image")) > 1:
        c.add("UD-VALUE", path, "a paint is one of `color`, `gradient` or `image`")
    allowed = {"color": {"color", "opacity"}, "image": {"image", "fit", "opacity"},
               "gradient": {"gradient", "stops", "angle", "opacity"}}[kind]
    required = {"color": ("color",), "image": ("image",), "gradient": ("gradient", "stops")}[kind]
    if not _obj(c, v, path, allowed, required):
        return
    if "opacity" in v:
        _num(c, v["opacity"], join_path(path, "opacity"), 0, 1, groups=())
    if kind == "color":
        _color(c, v["color"], join_path(path, "color"))
        return
    if kind == "image":
        if "image" in v:
            _asset(c, v["image"], join_path(path, "image"))
        if "fit" in v:
            _enum(c, v["fit"], join_path(path, "fit"), ("cover", "contain", "fill"))
        return
    if "gradient" in v:
        _enum(c, v["gradient"], join_path(path, "gradient"), GRADIENT_KINDS)
    if "angle" in v:
        _num(c, v["angle"], join_path(path, "angle"), groups=())
    stops = v.get("stops")
    if "stops" in v:
        sp = join_path(path, "stops")
        if not isinstance(stops, list) or not 2 <= len(stops) <= MAX_STOPS:
            c.add("UD-BOUNDS", sp, f"a gradient has 2 to {MAX_STOPS} stops", stops)
            return
        for i, s in enumerate(stops):
            p = join_path(sp, i)
            if not (isinstance(s, list) and len(s) == 2):
                c.add("UD-TYPE", p, "a stop is [offset, color]", s)
                continue
            if not (_isnum(s[0]) and 0 <= s[0] <= 1):
                c.add("UD-BOUNDS", join_path(p, 0), "stop offset is a number from 0 to 1", s[0])
            _color(c, s[1], join_path(p, 1))


def paints_of(fill: Any) -> list:
    """The paints of a `fill` value (a single paint or a list), bottom to top."""
    return list(fill) if isinstance(fill, list) else ([] if fill is None else [fill])


def _stroke(c: _Ctx, v: Any, path: str) -> None:
    if _obj(c, v, path, {"color", "width", "align", "dash", "cap", "join", "sides"}, ("color",)):
        if "color" in v:
            _color(c, v["color"], join_path(path, "color"))
        if "width" in v:
            _num(c, v["width"], join_path(path, "width"), 0, MAX_NUMBER)
        if "align" in v:
            _enum(c, v["align"], join_path(path, "align"), ("inside", "center", "outside"))
        if "cap" in v:
            _enum(c, v["cap"], join_path(path, "cap"), ("butt", "round", "square"))
        if "join" in v:
            _enum(c, v["join"], join_path(path, "join"), ("miter", "round", "bevel"))
        if "dash" in v:
            d = v["dash"]
            dp = join_path(path, "dash")
            if not isinstance(d, list) or not 1 <= len(d) <= MAX_DASH:
                c.add("UD-BOUNDS", dp, f"dash is a list of 1 to {MAX_DASH} lengths", d)
            else:
                for i, x in enumerate(d):
                    _num(c, x, join_path(dp, i), 0, MAX_NUMBER, groups=())
                if all(_isnum(x) and x == 0 for x in d):
                    c.add("UD-BOUNDS", dp, "dash needs at least one length above 0", d)
        if "sides" in v:
            sd = v["sides"]
            sp = join_path(path, "sides")
            if isinstance(sd, list):
                if len(sd) != 4:
                    c.add("UD-BOUNDS", sp, "sides is a width or [top, right, bottom, left]", sd)
                else:
                    for i, x in enumerate(sd):
                        _num(c, x, join_path(sp, i), 0, MAX_NUMBER)
            else:
                _num(c, sd, sp, 0, MAX_NUMBER)


def _radius(c: _Ctx, v: Any, path: str) -> None:
    if isinstance(v, list):
        if len(v) != 4:
            c.add("UD-BOUNDS", path, "a radius list has 4 corners", v)
            return
        for i, x in enumerate(v):
            _num(c, x, join_path(path, i), 0, MAX_NUMBER)
    else:
        _num(c, v, path, 0, MAX_NUMBER)


def _shadow(c: _Ctx, v: Any, path: str) -> None:
    """One shadow or a list of them (drop and `inset`), painted first to last."""
    if isinstance(v, list):
        if not 1 <= len(v) <= MAX_SHADOWS:
            c.add("UD-BOUNDS", path, f"a shadow list holds 1 to {MAX_SHADOWS} shadows", v)
            return
        for i, sh in enumerate(v):
            _shadow1(c, sh, join_path(path, i))
    else:
        _shadow1(c, v, path)


def _shadow1(c: _Ctx, v: Any, path: str) -> None:
    c.shadows += 1
    if _obj(c, v, path, {"x", "y", "blur", "spread", "color", "inset"}, ("color",)):
        for k in ("x", "y"):
            if k in v:
                _num(c, v[k], join_path(path, k))
        if "blur" in v:
            _num(c, v["blur"], join_path(path, "blur"), 0, MAX_BLUR, groups=())
        if "spread" in v:
            _num(c, v["spread"], join_path(path, "spread"), -MAX_BLUR, MAX_BLUR, groups=())
        if "inset" in v:
            _bool(c, v["inset"], join_path(path, "inset"))
        if "color" in v:
            _color(c, v["color"], join_path(path, "color"))


def _blur(c: _Ctx, v: Any, path: str) -> None:
    c.blurs += 1
    _num(c, v, path, 0, MAX_BLUR, groups=())


def _backdrop(c: _Ctx, v: Any, path: str) -> None:
    c.backdrops += 1
    _num(c, v, path, 0, MAX_BLUR, groups=())


def _layout(c: _Ctx, v: Any, path: str) -> None:
    if not _obj(c, v, path, {"dir", "gap", "pad", "align", "justify", "wrap"}):
        return
    if "dir" in v:
        _enum(c, v["dir"], join_path(path, "dir"), ("row", "column"))
    if "gap" in v:
        _num(c, v["gap"], join_path(path, "gap"), 0, MAX_NUMBER)
    if "pad" in v:
        p = v["pad"]
        if isinstance(p, list):
            if len(p) not in (2, 4):
                c.add("UD-BOUNDS", join_path(path, "pad"), "pad is a number, [v, h] or [t, r, b, l]", p)
            else:
                for i, x in enumerate(p):
                    _num(c, x, join_path(path, "pad", i), 0, MAX_NUMBER)
        else:
            _num(c, p, join_path(path, "pad"), 0, MAX_NUMBER)
    if "align" in v:
        _enum(c, v["align"], join_path(path, "align"), ("start", "center", "end", "stretch"))
    if "justify" in v:
        _enum(c, v["justify"], join_path(path, "justify"),
              ("start", "center", "end", "space-between"))
    if "wrap" in v:
        _bool(c, v["wrap"], join_path(path, "wrap"))


def _typespec(c: _Ctx, v: Any, path: str, required: tuple[str, ...] = ()) -> None:
    if not _obj(c, v, path, {"family", "size", "weight", "line", "letter"}, required):
        return
    if "family" in v:
        if not isinstance(v["family"], str):
            c.add("UD-TYPE", join_path(path, "family"), "expected a font family name", v["family"])
        elif v["family"] not in FONT_FAMILIES:
            c.add("UD-FONT", join_path(path, "family"), f"font {v['family']!r} is not bundled",
                  v["family"])
    if "size" in v:
        _num(c, v["size"], join_path(path, "size"), 1, 1000, groups=())
    if "weight" in v:
        w = v["weight"]
        if isinstance(w, bool) or not isinstance(w, int) or not 1 <= w <= 1000:
            c.add("UD-BOUNDS", join_path(path, "weight"), "weight is an integer from 1 to 1000", w)
    if "line" in v:
        _num(c, v["line"], join_path(path, "line"), 0.1, 10, groups=())
    if "letter" in v:
        _num(c, v["letter"], join_path(path, "letter"), -100, 100, groups=())


def _style(c: _Ctx, v: Any, path: str) -> None:
    if _isref(v):
        _tokref(c, v, path, ("type",))
    else:
        _typespec(c, v, path)


def _text(c: _Ctx, v: Any, path: str) -> None:
    if isinstance(v, str):
        if len(v.encode("utf-8")) > MAX_TEXT_BYTES:
            c.add("UD-LIMIT", path, f"text is limited to {MAX_TEXT_BYTES} bytes")
    elif isinstance(v, list):
        total = 0
        for i, run in enumerate(v):
            p = join_path(path, i)
            if not _obj(c, run, p, {"text", "style", "color"}, ("text",)):
                continue
            if isinstance(run.get("text"), str):
                total += len(run["text"].encode("utf-8"))
            elif "text" in run:
                c.add("UD-TYPE", join_path(p, "text"), "expected a string", run["text"])
            if "style" in run:
                _style(c, run["style"], join_path(p, "style"))
            if "color" in run:
                _color(c, run["color"], join_path(p, "color"))
        if total > MAX_TEXT_BYTES:
            c.add("UD-LIMIT", path, f"text runs are limited to {MAX_TEXT_BYTES} bytes in all")
    else:
        c.add("UD-TYPE", path, "expected a string or a list of runs", v)


def _title(c: _Ctx, v: Any, path: str) -> None:
    if not isinstance(v, str) or len(v) > 200:
        c.add("UD-TYPE", path, "expected a string of at most 200 characters", v)


def _meta(c: _Ctx, v: Any, path: str) -> None:
    if not isinstance(v, dict):
        c.add("UD-TYPE", path, "expected an object", v)
    elif len(json.dumps(v).encode("utf-8")) > MAX_META_BYTES:
        c.add("UD-LIMIT", path, f"meta is limited to {MAX_META_BYTES} bytes")


def _pathd(c: _Ctx, v: Any, path: str) -> None:
    if not isinstance(v, str):
        c.add("UD-TYPE", path, "expected an SVG path data string", v)
        return
    n = len(v.encode("utf-8"))
    c.path_bytes += n
    if n > MAX_PATH_D_BYTES:
        c.add("UD-LIMIT", path, f"path data is limited to {MAX_PATH_D_BYTES} bytes per node")
    if not PATH_D_RE.fullmatch(v):
        c.add("UD-FORMAT", path, "path data holds only SVG path commands and numbers")


def _asset(c: _Ctx, v: Any, path: str) -> None:
    if not (isinstance(v, str) and SHA_RE.fullmatch(v)):
        c.add("UD-FORMAT", path, "an asset is a lowercase sha256 hex digest", v)


PROP_CHECKS = {
    "title": _title,
    "w": _size, "h": _size,
    "x": lambda c, v, p: _num(c, v, p), "y": lambda c, v, p: _num(c, v, p),
    "absolute": _bool, "clip": _bool, "visible": _bool,
    "rotate": lambda c, v, p: _num(c, v, p, groups=()),
    "fill": _fill, "stroke": _stroke, "radius": _radius, "shadow": _shadow, "meta": _meta,
    "blur": _blur, "backdrop_blur": _backdrop,
    "blend": lambda c, v, p: _enum(c, v, p, BLEND_MODES),
    "opacity": lambda c, v, p: _num(c, v, p, 0, 1, groups=()),
    "mask": lambda c, v, p: _enum(c, v, p, ("alpha", "vector", "luminance")),
    "layout": _layout,
    "text": _text, "style": _style, "color": _color,
    "align": lambda c, v, p: _enum(c, v, p, ("left", "center", "right")),
    "max_lines": lambda c, v, p: (_num(c, v, p, 1, 1000, groups=())
                                  if not isinstance(v, float) or v == int(v) else
                                  c.add("UD-TYPE", p, "expected an integer", v)),
    "d": _pathd, "asset": _asset,
    "fit": lambda c, v, p: _enum(c, v, p, ("cover", "contain", "fill")),
}


def _register(c: _Ctx, id_: Any, path: str) -> None:
    if not (isinstance(id_, str) and ID_RE.fullmatch(id_)):
        c.add("UD-ID", path, "invalid id", id_)
    elif id_ in c.ids:
        c.add("UD-DUPLICATE-ID", path, f"id {id_!r} is already used at {c.ids[id_]}", id_)
    else:
        c.ids[id_] = path


def _node(c: _Ctx, n: Any, path: str, depth: int, scope: str, root_key: str | None = None) -> None:
    if not isinstance(n, dict):
        c.add("UD-TYPE", path, "expected a node object", n)
        return
    c.nodes += 1
    t = n.get("type")
    if t not in NODE_TYPES or not isinstance(t, str):
        c.add("UD-VALUE", join_path(path, "type"), f"type must be one of {', '.join(NODE_TYPES)}", t)
        return
    allowed = TYPE_KEYS[t]
    _obj(c, n, path, allowed, ("type",) if root_key else ("id", "type"))
    if root_key is None:
        if "id" in n:
            _register(c, n["id"], join_path(path, "id"))
    elif "id" in n and n["id"] != root_key:
        c.add("UD-ID", join_path(path, "id"),
              f"a component's root id is its key {root_key!r}", n["id"])
    nid = n.get("id") if isinstance(n.get("id"), str) else None
    if scope != "page" and nid:
        c.comp_nodes.setdefault(scope, {})[nid] = n
    for k, v in n.items():
        if k in PROP_CHECKS and k in allowed:
            PROP_CHECKS[k](c, v, join_path(path, k))
    if t not in ("frame", "rect", "ellipse") and any(
            isinstance(x, dict) and "image" in x for x in paints_of(n.get("fill"))):
        c.add("UD-VALUE", join_path(path, "fill"),
              f"image fills go on frame, rect or ellipse nodes, not {t}",
              fix="Use an `image` node for a picture inside a path or text.")
    if t == "instance":
        if "of" not in n:
            c.add("UD-MISSING", join_path(path, "of"), "an instance needs `of`")
        c.instances.append((path, n, scope))
    if t == "frame" and "children" in n:
        kids = n["children"]
        if not isinstance(kids, list):
            c.add("UD-TYPE", join_path(path, "children"), "expected a list", kids)
        elif depth >= MAX_FRAME_DEPTH:
            c.add("UD-LIMIT", join_path(path, "children"),
                  f"frames nest at most {MAX_FRAME_DEPTH} deep")
        else:
            for i, k in enumerate(kids):
                _node(c, k, join_path(path, "children", i), depth + 1, scope)


def _check_tokens(c: _Ctx, tokens: Any, modes: list[str] | None) -> None:
    path = "$.tokens"
    if not _obj(c, tokens, path, set(TOKEN_GROUPS)):
        return
    for group, entries in tokens.items():
        if group not in TOKEN_GROUPS:
            continue
        gp = join_path(path, group)
        if not isinstance(entries, dict):
            c.add("UD-TYPE", gp, "expected an object of name -> value", entries)
            continue
        for name, v in entries.items():
            p = join_path(gp, name)
            if not re.fullmatch(TOKEN_NAME, name):
                c.add("UD-FORMAT", p, "token names are letters, digits, `_` and `-`", name)
            if group == "color":
                if isinstance(v, dict):
                    if modes is None:
                        c.add("UD-MODE", p, "a mode-keyed colour needs a top-level `modes` list")
                    for mk, mv in v.items():
                        if modes is not None and mk not in modes:
                            c.add("UD-MODE", join_path(p, mk), f"mode {mk!r} is not in `modes`")
                        if _isref(mv) or not (isinstance(mv, str) and COLOR_RE.fullmatch(mv)):
                            c.add("UD-FORMAT", join_path(p, mk), "expected #rgb, #rrggbb or #rrggbbaa", mv)
                    if not v:
                        c.add("UD-MISSING", p, "a mode-keyed colour defines at least one mode")
                elif _isref(v) or not (isinstance(v, str) and COLOR_RE.fullmatch(v)):
                    c.add("UD-FORMAT", p, "expected #rgb, #rrggbb or #rrggbbaa", v)
            elif group in ("space", "radius"):
                if not _isnum(v) or isinstance(v, bool) or v < 0:
                    c.add("UD-TYPE", p, "expected a non-negative number", v)
            else:
                _typespec(c, v, p, ("family", "size"))


def collect_issues(doc: Any) -> list[Issue]:
    """Every structural issue in the document, in one pass."""
    if not isinstance(doc, dict):
        return [issue("UD-TYPE", "$", "the document is an object", doc)]
    try:
        size = len(json.dumps(doc, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        return [issue("UD-TYPE", "$", "the document must be plain JSON (string keys, finite numbers)")]
    if size > MAX_DOC_BYTES:
        return [issue("UD-LIMIT", "$", f"document is {size} bytes; the limit is {MAX_DOC_BYTES}")]
    comps = doc.get("components") if isinstance(doc.get("components"), dict) else {}
    tokens = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
    tokens = {g: v for g, v in tokens.items() if isinstance(v, dict)}
    modes = doc.get("modes") if isinstance(doc.get("modes"), list) else None
    c = _Ctx(tokens=tokens, modes=[m for m in modes if isinstance(m, str)] if modes else None,
             components=comps)
    _obj(c, doc, "$", {"schema", "title", "tokens", "modes", "components", "pages"},
         ("schema", "pages"))
    if "schema" in doc and doc["schema"] != SCHEMA_VERSION:
        c.add("UD-VALUE", "$.schema", f"schema must be {SCHEMA_VERSION}", doc["schema"])
    if "title" in doc:
        _title(c, doc["title"], "$.title")
    if "modes" in doc:
        m = doc["modes"]
        if not (isinstance(m, list) and 1 <= len(m) <= 8 and len(set(map(str, m))) == len(m)
                and all(isinstance(x, str) and MODE_RE.fullmatch(x) for x in m)):
            c.add("UD-BOUNDS", "$.modes", "1 to 8 distinct lowercase mode names", m)
    if "tokens" in doc:
        _check_tokens(c, doc["tokens"], c.modes)
    pages = doc.get("pages")
    if "pages" in doc:
        if not isinstance(pages, list):
            c.add("UD-TYPE", "$.pages", "expected a list", pages)
        else:
            if len(pages) > MAX_PAGES:
                c.add("UD-LIMIT", "$.pages", f"at most {MAX_PAGES} pages")
            for i, page in enumerate(pages):
                p = join_path("$.pages", i)
                if not _obj(c, page, p, {"id", "title", "children"}, ("id",)):
                    continue
                if "id" in page:
                    _register(c, page["id"], join_path(p, "id"))
                if "title" in page:
                    _title(c, page["title"], join_path(p, "title"))
                kids = page.get("children", [])
                if not isinstance(kids, list):
                    c.add("UD-TYPE", join_path(p, "children"), "expected a list", kids)
                    continue
                for j, k in enumerate(kids):
                    _node(c, k, join_path(p, "children", j), 1, "page")
    if "components" in doc:
        if not isinstance(doc["components"], dict):
            c.add("UD-TYPE", "$.components", "expected an object of id -> node", doc["components"])
        else:
            if len(comps) > MAX_COMPONENTS:
                c.add("UD-LIMIT", "$.components", f"at most {MAX_COMPONENTS} components")
            for key, root in comps.items():
                p = join_path("$.components", key)
                _register(c, key, p)
                _node(c, root, p, 1, key, root_key=key)
    for path, n, scope in c.instances:
        _check_instance(c, path, n, scope)
    if c.nodes > MAX_NODES:
        c.add("UD-LIMIT", "$", f"{c.nodes} authored nodes; the limit is {MAX_NODES}")
    if c.shadows > MAX_SHADOWS:
        c.add("UD-LIMIT", "$", f"{c.shadows} shadows; the limit is {MAX_SHADOWS}")
    if c.blurs > MAX_BLURS:
        c.add("UD-LIMIT", "$", f"{c.blurs} blurred layers; the limit is {MAX_BLURS}")
    if c.backdrops > MAX_BACKDROPS:
        c.add("UD-LIMIT", "$", f"{c.backdrops} backdrop blurs; the limit is {MAX_BACKDROPS}")
    if c.path_bytes > MAX_PATH_TOTAL_BYTES:
        c.add("UD-LIMIT", "$", f"path data totals {c.path_bytes} bytes; the limit is {MAX_PATH_TOTAL_BYTES}")
    return c.issues


def _check_instance(c: _Ctx, path: str, n: dict, scope: str) -> None:
    of = n.get("of")
    if "of" in n and not (isinstance(of, str) and of in c.components):
        c.add("UD-COMPONENT-UNKNOWN", join_path(path, "of"), f"no component {of!r}", of)
        return
    if "overrides" not in n:
        return
    op = join_path(path, "overrides")
    ov = n["overrides"]
    if not isinstance(ov, dict):
        c.add("UD-TYPE", op, "expected an object of \"<child-id>.<prop>\" -> value", ov)
        return
    if not isinstance(of, str):
        return
    root = c.components[of]
    for key, val in ov.items():
        p = join_path(op, key)
        child, _, prop = key.rpartition(".")
        if child:
            target = c.comp_nodes.get(of, {}).get(child)
            if target is None or child == of:
                c.add("UD-OVERRIDE", p, f"component {of!r} has no child {child!r}", key)
                continue
        else:
            target = root
        ttype = target.get("type") if isinstance(target, dict) else None
        if ttype not in TYPE_KEYS or prop in _NO_OVERRIDE or prop not in TYPE_KEYS[ttype]:
            c.add("UD-OVERRIDE", p, f"{ttype!r} nodes have no overridable property {prop!r}", key)
            continue
        if prop == "fill" and any(isinstance(x, dict) and "image" in x for x in paints_of(val)):
            c.add("UD-OVERRIDE", p, "an image fill cannot be an override; put it on the node", key)
            continue
        PROP_CHECKS[prop](c, val, p)


# --- expansion budget ----------------------------------------------------

def effect_counts(n: dict) -> tuple[int, int, int, int]:
    """(shadows, blurs, backdrop blurs, stepped gradients) one node paints."""
    sh = n.get("shadow")
    stepped = sum(1 for x in paints_of(n.get("fill"))
                  if isinstance(x, dict) and x.get("gradient") in ("angular", "diamond"))
    return ((len(sh) if isinstance(sh, list) else 1 if sh else 0),
            1 if n.get("blur") else 0, 1 if n.get("backdrop_blur") else 0, stepped)


def check_expansion(doc: dict) -> int:
    """Expand instances with a counter; raise UD-EXPANSION past depth 6 or
    20,000 expanded nodes (a component containing itself hits the depth cap).
    Run on a structurally valid document. Returns the expanded node count of
    the pages."""
    comps = doc.get("components") or {}

    def expand(n: dict, depth: int, state: list[int], at: str) -> None:
        t = n.get("type")
        if t == "instance":
            root = comps.get(n.get("of"))
            if root is None:
                state[0] += 1
            else:
                if depth + 1 > MAX_INSTANCE_DEPTH:
                    raise UiDesignerError("UD-EXPANSION", f"instance nesting deeper than "
                                          f"{MAX_INSTANCE_DEPTH} at {n.get('id')!r}",
                                          detail={"reason": "depth", "limit": MAX_INSTANCE_DEPTH,
                                                  "at": n.get("id")})
                expand(root, depth + 1, state, at)
            return
        state[0] += 1
        for i, k in enumerate(effect_counts(n)):
            eff[i] += k
        if eff[0] > MAX_SHADOWS or eff[1] > MAX_BLURS or eff[2] > MAX_BACKDROPS or eff[3] > MAX_STEPPED:
            raise UiDesignerError("UD-EXPANSION", f"effects over the limit after instance expansion "
                                  f"(shadows {eff[0]}/{MAX_SHADOWS}, blurs {eff[1]}/{MAX_BLURS}, "
                                  f"backdrop blurs {eff[2]}/{MAX_BACKDROPS}, angular/diamond fills "
                                  f"{eff[3]}/{MAX_STEPPED})", detail={"reason": "effects", "at": at})
        if state[0] > MAX_EXPANDED_NODES:
            raise UiDesignerError("UD-EXPANSION", f"more than {MAX_EXPANDED_NODES} expanded nodes "
                                  f"under {at!r}", detail={"reason": "nodes",
                                                           "limit": MAX_EXPANDED_NODES, "at": at})
        for k in n.get("children") or []:
            expand(k, depth, state, at)

    state = [0]
    eff = [0, 0, 0, 0]
    for page in doc.get("pages") or []:
        for n in page.get("children") or []:
            expand(n, 0, state, page.get("id"))
    for key, root in comps.items():  # a component nobody instances must still be bounded
        saved = list(eff)
        expand(root, 0, [0], key)
        eff[:] = saved
    return state[0]


def image_assets(doc: dict) -> list[tuple[str, str]]:
    """(json path, sha256) of every image node, in pages and components."""
    found: list[tuple[str, str]] = []

    def walk(n: Any, path: str) -> None:
        if not isinstance(n, dict):
            return
        if n.get("type") == "image" and isinstance(n.get("asset"), str):
            found.append((join_path(path, "asset"), n["asset"]))
        for i, pnt in enumerate(paints_of(n.get("fill"))):
            if isinstance(pnt, dict) and isinstance(pnt.get("image"), str):
                found.append((join_path(path, "fill", i, "image") if isinstance(n["fill"], list)
                              else join_path(path, "fill", "image"), pnt["image"]))
        for i, k in enumerate(n.get("children") or []):
            walk(k, join_path(path, "children", i))
    for i, page in enumerate(doc.get("pages") or []):
        for j, n in enumerate(page.get("children") or []):
            walk(n, join_path("$.pages", i, "children", j))
    for key, root in (doc.get("components") or {}).items():
        walk(root, join_path("$.components", key))
    return found


def validate_document(doc: Any, *, known_assets: set[str] | None = None) -> None:
    """Raise `UiDesignerError` unless the document is valid. `known_assets`
    is the set of sha256s placed in this document (§9); None skips that check
    (pure callers with no store)."""
    issues = collect_issues(doc)
    if issues:
        raise invalid(issues)
    check_expansion(doc)
    if known_assets is not None:
        missing = [{"path": p, "asset": a} for p, a in image_assets(doc) if a not in known_assets]
        if missing:
            raise UiDesignerError(UD_ASSET_UNKNOWN, f"{len(missing)} image(s) were never placed "
                                  "in this document", detail={"assets": missing})


def new_document(title: str = "") -> dict:
    return {"schema": SCHEMA_VERSION, "title": title, "tokens": {}, "components": {}, "pages": []}


# --- tree index ----------------------------------------------------------

@dataclass
class NodeRef:
    kind: str                    # "page" | "component" | "node"
    node: dict
    siblings: list | None        # the list holding it (None for a component root)
    parent: str | None           # parent id (None for pages / component roots)
    in_component: str | None     # owning component key, for nodes inside one


def index_nodes(doc: dict) -> dict[str, NodeRef]:
    """id -> NodeRef over pages, nodes and component roots. Pages are
    addressable so a node can be inserted at a page's top level."""
    out: dict[str, NodeRef] = {}

    def walk(n: dict, siblings: list | None, parent: str | None, comp: str | None) -> None:
        if isinstance(n.get("id"), str):
            out[n["id"]] = NodeRef("node", n, siblings, parent, comp)
        for k in n.get("children") or []:
            if isinstance(k, dict):
                walk(k, n["children"], n.get("id"), comp)

    for page in doc.get("pages") or []:
        if isinstance(page.get("id"), str):
            out[page["id"]] = NodeRef("page", page, None, None, None)
        for k in page.get("children") or []:
            if isinstance(k, dict):
                walk(k, page["children"], page.get("id"), None)
    for key, root in (doc.get("components") or {}).items():
        out[key] = NodeRef("component", root, None, None, key)
        for k in root.get("children") or []:
            if isinstance(k, dict):
                walk(k, root["children"], key, key)
    return out


def subtree_ids(node: dict) -> set[str]:
    ids = {node["id"]} if isinstance(node.get("id"), str) else set()
    for k in node.get("children") or []:
        if isinstance(k, dict):
            ids |= subtree_ids(k)
    return ids


# --- warnings (lint, not errors; §3.3) -----------------------------------

def lint(doc: dict) -> list[dict]:
    """The warnings that need no layout: stray x/y under auto layout, `fill`
    inside `hug`, a token used in one mode but missing in another, a component
    instanced nowhere. Text overflow and contrast need measured text (T2)."""
    out: list[dict] = []

    def warn(code: str, path: str, message: str) -> None:
        out.append({"code": code, "path": path, "message": message})

    used_components: set[str] = set()

    def walk(n: Any, path: str, parent: dict | None) -> None:
        if not isinstance(n, dict):
            return
        if n.get("type") == "instance" and isinstance(n.get("of"), str):
            used_components.add(n["of"])
        if parent is not None:
            if parent.get("layout") is not None and not n.get("absolute") and \
                    ("x" in n or "y" in n):
                warn("UD-WARN-XY-IN-LAYOUT", path, f"{n.get('id')!r} has x/y inside an auto-layout "
                     "frame; they are ignored unless `absolute: true`")
            for axis in ("w", "h"):
                if n.get(axis) == "fill" and parent.get(axis) == "hug":
                    warn("UD-WARN-FILL-IN-HUG", path, f"{n.get('id')!r} is `fill` {axis} in a `hug` "
                         "parent; resolved as `hug`")
        for i, k in enumerate(n.get("children") or []):
            walk(k, join_path(path, "children", i), n if n.get("type") == "frame" else None)

    for i, page in enumerate(doc.get("pages") or []):
        for j, n in enumerate(page.get("children") or []):
            walk(n, join_path("$.pages", i, "children", j), None)
    for key, root in (doc.get("components") or {}).items():
        walk(root, join_path("$.components", key), None)
    for key in (doc.get("components") or {}):
        if key not in used_components:
            warn("UD-WARN-COMPONENT-UNUSED", join_path("$.components", key),
                 f"component {key!r} is instanced nowhere")
    modes = doc.get("modes") or []
    color = (doc.get("tokens") or {}).get("color") or {}
    seen: set[str] = set()
    for path, ref in iter_token_refs(doc):
        m = REF_RE.fullmatch(ref)
        if not m or m.group(1) != "color" or ref in seen:
            continue
        seen.add(ref)
        value = color.get(m.group(2))
        if isinstance(value, dict):
            gone = [x for x in modes if x not in value]
            if gone:
                warn("UD-WARN-TOKEN-MODE", path, f"{ref} has no value for mode(s) "
                     f"{', '.join(gone)}; the first defined mode is used")
    return out
