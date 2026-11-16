"""Text shaping and line breaking (design 46 §4, §5).

`layout_text(runs, max_w, max_lines)` shapes with uharfbuzz against the
bundled fonts, breaks lines greedily at spaces (a word wider than the width
is broken between glyph clusters), applies `max_lines` with an ellipsis and
returns the ink size plus the broken lines. It is a pure function of its
arguments, so results are cached, and measuring "at the width it is given"
(the parent's constraint) is just calling it with that width.

Geometry is in px at the run's size. Glyph ids index the run's `Face`
(`fonts.face_by_key(frag.face)`); outlines come from `Face.glyph_outline`.
Colour is not part of the geometry: a `Frag` carries the index of the run it
came from and the caller maps that to a colour.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import uharfbuzz as hb

from .fonts import Face, get_face

DEFAULT_SIZE = 14.0
DEFAULT_LINE = 1.2
ELLIPSIS = "…"
_EPS = 1e-6
_CACHE_MAX = 40_000
_TOKEN_RE = re.compile(r"[ \t\r\f\v]+|[^ \t\r\f\v]+")


@dataclass(frozen=True)
class Style:
    face: Face
    size: float = DEFAULT_SIZE
    letter: float = 0.0
    line: float = DEFAULT_LINE

    @property
    def key(self) -> tuple:
        return (self.face.key, self.size, self.letter, self.line)


def make_style(spec: dict | None, base: Style | None = None) -> Style:
    """A `Style` from a resolved `{family, size, weight, line, letter}` object,
    each missing key falling back to `base` (or the defaults: Inter 14 / 400 /
    line 1.2 / letter 0)."""
    spec = spec or {}
    if base is None:
        return Style(get_face(spec.get("family"), spec.get("weight")), float(spec.get("size", DEFAULT_SIZE)),
                     float(spec.get("letter", 0)), float(spec.get("line", DEFAULT_LINE)))
    face = base.face
    if "family" in spec or "weight" in spec:
        face = get_face(spec.get("family", face.family), spec.get("weight", face.weight))
    return Style(face, float(spec.get("size", base.size)), float(spec.get("letter", base.letter)),
                 float(spec.get("line", base.line)))


@dataclass
class Frag:
    """A same-run stretch of glyphs on one line. `glyphs` is `[gid, x]` (or
    `[gid, x, dy]` when shaping moved it off the baseline); `x` is the pen
    position in px from the line's left edge."""
    run: int
    face: str
    size: float
    glyphs: list
    text: str


@dataclass
class Line:
    top: float          # px from the text box's top
    baseline: float     # px from the text box's top
    w: float            # advance width without trailing spaces
    h: float
    text: str
    frags: list[Frag] = field(default_factory=list)


@dataclass
class TextLayout:
    lines: list[Line]
    w: float            # widest line
    h: float
    truncated: bool     # max_lines cut the text
    hard_broken: bool   # a word was wider than the width and was split


# --- shaping -------------------------------------------------------------

_SHAPED: dict[tuple, tuple] = {}


def _shape(face: Face, text: str) -> tuple:
    """((gid, cluster, advance, dx, dy), ...) in font units; kerning and
    ligatures on. Clusters are codepoint indices into `text`."""
    k = (face.key, text)
    hit = _SHAPED.get(k)
    if hit is not None:
        return hit
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(face.hb_font, buf)
    out = tuple((i.codepoint, i.cluster, p.x_advance, p.x_offset, p.y_offset)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions))
    if len(_SHAPED) > _CACHE_MAX:
        _SHAPED.clear()
    _SHAPED[k] = out
    return out


class _Atom:
    """A word or a run of spaces, positioned in px from its own origin."""
    __slots__ = ("run", "style", "text", "space", "glyphs", "w")

    def __init__(self, run: int, style: Style, text: str, space: bool, glyphs: tuple, w: float):
        self.run, self.style, self.text, self.space, self.glyphs, self.w = run, style, text, space, glyphs, w
        # glyphs: ((gid, pen, dy, cluster), ...); pen includes the glyph's x offset


_ATOMS: dict[tuple, tuple] = {}


def _atom(run: int, style: Style, text: str, space: bool) -> _Atom:
    k = (style.key, text)
    hit = _ATOMS.get(k)
    if hit is None:
        scale = style.size / style.face.upem
        pen = 0.0
        gl = []
        for gid, cl, adv, dx, dy in _shape(style.face, text):
            gl.append((gid, pen + dx * scale, dy * scale, cl))
            pen += adv * scale + style.letter
        hit = (tuple(gl), pen)
        if len(_ATOMS) > _CACHE_MAX:
            _ATOMS.clear()
        _ATOMS[k] = hit
    return _Atom(run, style, text, space, hit[0], hit[1])


def _cut(atom: _Atom, n_groups: int) -> tuple[_Atom, _Atom | None]:
    """Split a word after its first `n_groups` cluster groups."""
    gl = atom.glyphs
    starts = [i for i in range(len(gl)) if i == 0 or gl[i][3] != gl[i - 1][3]]
    if n_groups >= len(starts):
        return atom, None
    j = starts[n_groups]
    c = gl[j][3]
    # the shift is the pen where glyph j began (offsets excluded): recover it
    scale = atom.style.size / atom.style.face.upem
    pen_j = _pen_start(atom, j, scale)
    head = _Atom(atom.run, atom.style, atom.text[:c], False, gl[:j], pen_j)
    tail_gl = tuple((g, p - pen_j, dy, cl - c) for g, p, dy, cl in gl[j:])
    tail = _Atom(atom.run, atom.style, atom.text[c:], False, tail_gl, atom.w - pen_j)
    return head, tail


def _pen_start(atom: _Atom, j: int, scale: float) -> float:
    # pen position of glyph j = sum of advances before it; reshaping is
    # avoided by using the stored (pen + dx) and the shaped dx
    sh = _shape(atom.style.face, atom.text)
    return atom.glyphs[j][1] - sh[j][3] * scale


# --- line building -------------------------------------------------------

def _runs_to_atoms(runs: list[tuple[str, Style]]) -> list[list[_Atom]]:
    """One atom list per paragraph (`\\n` is a hard break)."""
    paras: list[list[_Atom]] = [[]]
    for ri, (text, style) in enumerate(runs):
        parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for pi, part in enumerate(parts):
            if pi:
                paras.append([])
            for m in _TOKEN_RE.finditer(part):
                tok = m.group()
                paras[-1].append(_atom(ri, style, tok, tok[0] in " \t\r\f\v"))
    return paras


def _trim(items: list[_Atom]) -> None:
    while items and items[-1].space:
        items.pop()


def _width(items: Iterable[_Atom]) -> float:
    return sum(a.w for a in items)


def _lines_of(paras: list[list[_Atom]], max_w: float | None, limit: int | None
              ) -> tuple[list[list[_Atom]], bool, bool]:
    """Greedy breaking. Returns (lines as atom lists, hard_broken, more) where
    `more` is True when the text continues past `limit` lines."""
    lines: list[list[_Atom]] = []
    hard = False
    for atoms in paras:
        cur: list[_Atom] = []
        cur_w = 0.0
        first = True
        queue = list(reversed(atoms))
        while queue:
            a = queue.pop()
            if a.space:
                if cur or first:
                    cur.append(a)
                    cur_w += a.w
                continue
            if max_w is not None and cur and cur_w + a.w > max_w + _EPS:
                _trim(cur)
                lines.append(cur)
                if limit is not None and len(lines) > limit:
                    return lines, hard, True
                cur, cur_w, first = [], 0.0, False
            if max_w is not None and a.w > max_w + _EPS:
                # alone on the line and still too wide: break between clusters
                groups = len({g[3] for g in a.glyphs})
                best = 1
                for n in range(2, groups):
                    if _cut(a, n)[0].w > max_w + _EPS:
                        break
                    best = n
                head, tail = _cut(a, best)
                if tail is not None:
                    hard = True
                    cur.append(head)
                    lines.append(cur)
                    if limit is not None and len(lines) > limit:
                        return lines, hard, True
                    cur, cur_w, first = [], 0.0, False
                    queue.append(tail)
                    continue
            cur.append(a)
            cur_w += a.w
        _trim(cur)
        lines.append(cur)
        if limit is not None and len(lines) > limit:
            return lines, hard, True
    return lines, hard, False


def _metrics(items: list[_Atom], base: Style) -> tuple[float, float]:
    """(height, baseline offset) of a line box: each item's content area
    (ascent + descent) is centred in size*line, and the line takes the largest
    above-baseline and below-baseline extents."""
    if not items:
        items_styles = [base]
    else:
        items_styles = {id(a.style): a.style for a in items}.values()
    above = below = 0.0
    for st in items_styles:
        f = st.face
        asc = f.ascent * st.size / f.upem
        desc = f.descent * st.size / f.upem
        lh = st.size * st.line
        half = (lh - (asc + desc)) / 2
        above = max(above, half + asc)
        below = max(below, lh - half - asc)
    return above + below, above


def _ellipsize(items: list[_Atom], max_w: float | None, base: Style) -> list[_Atom]:
    _trim(items)
    last = items[-1] if items else None
    style = last.style if last else base
    run = last.run if last else 0
    ell = _atom(run, style, ELLIPSIS, False)
    if ell.glyphs and ell.glyphs[0][0] == 0:   # no glyph for it in this face
        ell = _atom(run, style, "...", False)
    if max_w is not None:
        while items and _width(items) + ell.w > max_w + _EPS:
            a = items[-1]
            if a.space:
                items.pop()
                continue
            starts = {g[3] for g in a.glyphs}
            if len(starts) <= 1:
                items.pop()
            else:
                head, _ = _cut(a, len(starts) - 1)
                items[-1] = head
    items.append(ell)
    return items


def _frags(items: list[_Atom]) -> list[Frag]:
    frags: list[Frag] = []
    x = 0.0
    for a in items:
        if not frags or frags[-1].run != a.run or frags[-1].face != a.style.face.key \
                or frags[-1].size != a.style.size:
            frags.append(Frag(a.run, a.style.face.key, a.style.size, [], ""))
        f = frags[-1]
        for gid, pen, dy, _c in a.glyphs:
            f.glyphs.append([gid, round(x + pen, 3), round(dy, 3)] if dy else [gid, round(x + pen, 3)])
        f.text += a.text
        x += a.w
    return frags


_LAYOUTS: dict[tuple, TextLayout] = {}


def _build(runs: list[tuple[str, Style]], max_w: float | None, max_lines: int | None) -> TextLayout:
    base = runs[0][1]
    paras = _runs_to_atoms(runs)
    lines, hard, more = _lines_of(paras, max_w, max_lines)
    truncated = False
    if max_lines is not None and (more or len(lines) > max_lines):
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], max_w, base)
        truncated = True
    out: list[Line] = []
    top = 0.0
    for items in lines:
        h, baseline = _metrics(items, base)
        out.append(Line(top, top + baseline, _width(items), h, "".join(a.text for a in items),
                        _frags(items)))
        top += h
    if not out:
        h, baseline = _metrics([], base)
        out.append(Line(0.0, baseline, 0.0, h, ""))
        top = h
    return TextLayout(out, max(l.w for l in out), top, truncated, hard)


def layout_text(runs: list[tuple[str, Style]], max_w: float | None = None,
                max_lines: int | None = None) -> TextLayout:
    """Shape and break `runs` (a list of `(text, Style)`) at `max_w` px
    (None: only explicit newlines break). The result must be treated as
    read-only; it is shared through a cache."""
    if not runs:
        runs = [("", make_style(None))]
    if max_lines is not None and max_lines < 1:
        max_lines = None
    key = tuple((t, s.key) for t, s in runs)
    natural = _LAYOUTS.get((key, None, max_lines))
    if natural is None:
        natural = _build(runs, None, max_lines)
        if len(_LAYOUTS) > _CACHE_MAX:
            _LAYOUTS.clear()
        _LAYOUTS[(key, None, max_lines)] = natural
    if max_w is None or natural.w <= max_w + _EPS:
        return natural
    k = (key, round(max_w, 3), max_lines)
    hit = _LAYOUTS.get(k)
    if hit is None:
        hit = _build(runs, max_w, max_lines)
        if len(_LAYOUTS) > _CACHE_MAX:
            _LAYOUTS.clear()
        _LAYOUTS[k] = hit
    return hit


def plain_text(spec: Any) -> str:
    """The text of a `text` value (a string or a list of runs) as one string."""
    if isinstance(spec, str):
        return spec
    if isinstance(spec, list):
        return "".join(r.get("text", "") for r in spec if isinstance(r, dict))
    return ""
