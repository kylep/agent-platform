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
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable

import uharfbuzz as hb

from .fonts import Face, get_face

DEFAULT_SIZE = 14.0
DEFAULT_LINE = 1.2
ELLIPSIS = "…"
_EPS = 1e-6
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
    start: float = 0.0
    w: float = 0.0


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


# --- bounded caches ------------------------------------------------------

class _LRU:
    """An LRU bounded by total cost (stored glyphs), not by entry count, so a
    few huge texts cannot pin unbounded memory."""

    def __init__(self, max_cost: int):
        self.max_cost = max_cost
        self.cost = 0
        self.d: OrderedDict = OrderedDict()

    def get(self, key):
        hit = self.d.get(key)
        if hit is not None:
            self.d.move_to_end(key)
            return hit[0]
        return None

    def put(self, key, value, cost: int) -> None:
        cost += 1
        old = self.d.pop(key, None)
        if old is not None:
            self.cost -= old[1]
        if cost > self.max_cost:
            return                      # too big to be worth keeping
        self.d[key] = (value, cost)
        self.cost += cost
        while self.cost > self.max_cost:
            _k, (_v, c) = self.d.popitem(last=False)
            self.cost -= c


CACHE_MAX_GLYPHS = 200_000          # per cache; roughly 20-40 MB each at worst
_SHAPED = _LRU(CACHE_MAX_GLYPHS)
_ATOMS = _LRU(CACHE_MAX_GLYPHS)
_LAYOUTS = _LRU(CACHE_MAX_GLYPHS)


# --- shaping -------------------------------------------------------------

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
    _SHAPED.put(k, out, len(out))
    return out


class _Atom:
    """A word or a run of spaces, positioned in px from its own origin.
    `glyphs` is ((gid, x, dy, cluster), ...) with x the pen plus the shaper's
    offset; `starts` is ((glyph index, pen before it, cluster), ...) with one
    entry per cluster group, computed once so breaking never reshapes."""
    __slots__ = ("run", "style", "text", "space", "glyphs", "w", "starts")

    def __init__(self, run: int, style: Style, text: str, space: bool, glyphs: tuple, w: float,
                 starts: tuple):
        self.run, self.style, self.text, self.space = run, style, text, space
        self.glyphs, self.w, self.starts = glyphs, w, starts


def _atom(run: int, style: Style, text: str, space: bool) -> _Atom:
    k = (style.key, text)
    hit = _ATOMS.get(k)
    if hit is None:
        scale = style.size / style.face.upem
        pen = 0.0
        gl = []
        starts = []
        last = None
        for j, (gid, cl, adv, dx, dy) in enumerate(_shape(style.face, text)):
            if cl != last:
                starts.append((j, pen, cl))
                last = cl
            gl.append((gid, pen + dx * scale, dy * scale, cl))
            pen += adv * scale + style.letter
        hit = (tuple(gl), pen, tuple(starts))
        _ATOMS.put(k, hit, len(gl) + len(starts))
    return _Atom(run, style, text, space, hit[0], hit[1], hit[2])


def _piece(atom: _Atom, g0: int, g1: int) -> _Atom:
    """The cluster groups [g0, g1) of a word as an atom of its own, in O(piece)."""
    st = atom.starts
    j0, p0, c0 = st[g0]
    if g1 < len(st):
        j1, p1, c1 = st[g1]
    else:
        j1, p1, c1 = len(atom.glyphs), atom.w, len(atom.text)
    gl = tuple((g, x - p0, dy, cl - c0) for g, x, dy, cl in atom.glyphs[j0:j1])
    starts = tuple((j - j0, p - p0, c - c0) for j, p, c in st[g0:g1])
    return _Atom(atom.run, atom.style, atom.text[c0:c1], False, gl, p1 - p0, starts)


def _split_word(atom: _Atom, max_w: float) -> list[_Atom]:
    """Break a word wider than `max_w` between cluster groups, in one linear
    pass: every piece but the last fills the width (at least one group); the
    last piece is returned too so following atoms can join it."""
    st = atom.starts
    out: list[_Atom] = []
    g0 = 0
    p0 = 0.0
    g = 1
    n = len(st)
    while g < n:
        # the piece [g0, g) fits while the pen at group g stays within width
        if st[g][1] - p0 > max_w + _EPS:
            cut = g - 1 if g - 1 > g0 else g0 + 1     # at least one group per piece
            out.append(_piece(atom, g0, cut))
            g0, p0 = cut, st[cut][1]
            g = cut + 1
            continue
        g += 1
    if atom.w - p0 > max_w + _EPS and n - g0 > 1:   # the last group alone is what overflows
        out.append(_piece(atom, g0, n - 1))
        g0 = n - 1
    out.append(_piece(atom, g0, n))
    return out


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


def _lines_of(paras: list[list[_Atom]], max_w: float | None, limit: int | None,
              paragraph_ends: set[int] | None = None
              ) -> tuple[list[list[_Atom]], bool, bool]:
    """Greedy breaking. Returns (lines as atom lists, hard_broken, more) where
    `more` is True when the text continues past `limit` lines; breaking stops
    as soon as one line more than `limit` exists."""
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
            if max_w is not None and a.w > max_w + _EPS and len(a.starts) > 1:
                pieces = _split_word(a, max_w)
                hard = True
                for piece in pieces[:-1]:
                    lines.append([piece])
                    if limit is not None and len(lines) > limit:
                        return lines, hard, True
                a = pieces[-1]
            cur.append(a)
            cur_w += a.w
        _trim(cur)
        lines.append(cur)
        if paragraph_ends is not None:
            paragraph_ends.add(len(lines)-1)
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
        total = _width(items)
        while items and total + ell.w > max_w + _EPS:
            a = items[-1]
            total -= a.w
            if not a.space:
                head = _prefix(a, max_w - ell.w - total)
                if head is not None:
                    items[-1] = head
                    break
            items.pop()
    items.append(ell)
    return items


def _prefix(atom: _Atom, budget: float) -> _Atom | None:
    """The longest run of leading cluster groups of a word that fits in
    `budget` px (binary search over the stored pen positions), or None."""
    st = atom.starts
    lo, hi = 0, len(st)         # largest n in [0, len(st)) with pen(group n) <= budget
    if atom.w <= budget + _EPS:
        return atom
    lo = 0
    hi = len(st) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if st[mid][1] <= budget + _EPS:
            lo = mid
        else:
            hi = mid - 1
    return _piece(atom, 0, lo) if lo > 0 else None


def _frags(items: list[_Atom], gap_extra: float = 0, gaps: set[int] | None = None) -> list[Frag]:
    frags: list[Frag] = []
    x = 0.0
    for index, a in enumerate(items):
        if not frags or frags[-1].run != a.run or frags[-1].face != a.style.face.key \
                or frags[-1].size != a.style.size:
            frags.append(Frag(a.run, a.style.face.key, a.style.size, [], "", x))
        f = frags[-1]
        for gid, pen, dy, _c in a.glyphs:
            f.glyphs.append([gid, round(x + pen, 3), round(dy, 3)] if dy else [gid, round(x + pen, 3)])
        f.text += a.text
        x += a.w
        if gaps and index in gaps:
            x += gap_extra
        f.w = x-f.start
    return frags


def _build(runs: list[tuple[str, Style]], max_w: float | None, max_lines: int | None, justify: bool = False) -> TextLayout:
    base = runs[0][1]
    paras = _runs_to_atoms(runs)
    paragraph_ends = set() if justify else None
    lines, hard, more = _lines_of(paras, max_w, max_lines, paragraph_ends)
    truncated = False
    if max_lines is not None and (more or len(lines) > max_lines):
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], max_w, base)
        truncated = True
    out: list[Line] = []
    top = 0.0
    for index, items in enumerate(lines):
        h, baseline = _metrics(items, base)
        width = _width(items)
        gaps = set()
        if justify and max_w is not None and index not in paragraph_ends and not (truncated and index == len(lines)-1):
            i=0
            while i<len(items):
                if not items[i].space:
                    i+=1; continue
                start=i
                while i<len(items) and items[i].space: i+=1
                if start>0 and i<len(items): gaps.add(i-1)
        extra=max(0,(max_w-width)/len(gaps)) if gaps else 0
        out.append(Line(top, top + baseline, max_w if extra else width, h, "".join(a.text for a in items),
                        _frags(items,extra,gaps)))
        top += h
    if not out:
        h, baseline = _metrics([], base)
        out.append(Line(0.0, baseline, 0.0, h, ""))
        top = h
    return TextLayout(out, max(l.w for l in out), top, truncated, hard)


def layout_text(runs: list[tuple[str, Style]], max_w: float | None = None,
                max_lines: int | None = None, *, justify: bool = False) -> TextLayout:
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
        _LAYOUTS.put((key, None, max_lines), natural, _cost(natural))
    if max_w is None or natural.w <= max_w + _EPS:
        return natural
    k = (key, round(max_w, 3), max_lines, "justify") if justify else (key, round(max_w, 3), max_lines)
    hit = _LAYOUTS.get(k)
    if hit is None:
        hit = _build(runs, max_w, max_lines, justify)
        _LAYOUTS.put(k, hit, _cost(hit))
    return hit


def _cost(tl: TextLayout) -> int:
    return sum(len(f.glyphs) for ln in tl.lines for f in ln.frags) + len(tl.lines)


def plain_text(spec: Any) -> str:
    """The text of a `text` value (a string or a list of runs) as one string."""
    if isinstance(spec, str):
        return spec
    if isinstance(spec, list):
        return "".join(r.get("text", "") for r in spec if isinstance(r, dict))
    return ""


def transform_runs(runs: list[tuple[str, Style]], text_case: str) -> list[tuple[str, Style]]:
    """Unicode case over the logical string, preserving rich-run ownership.

    Default Unicode upper/lower expansions have per-character lengths even
    where the character itself is contextual (Greek final sigma). Locale-
    specific casing is deliberately not inferred from a user's environment.
    """
    if text_case == 'none':
        return runs
    original=''.join(text for text,_ in runs)
    if text_case in ('uppercase','lowercase'):
        operation=str.upper if text_case=='uppercase' else str.lower
        transformed=operation(original)
        lengths=[len(operation(char)) for char in original]
    else:
        pieces=[]; lengths=[]; word=False
        for char in original:
            piece=char.upper() if char.isalpha() and not word else char
            if char.isalpha(): word=True
            elif char.isdigit() or unicodedata.category(char).startswith('M') or (char in ("'",'’') and word): pass
            else: word=False
            pieces.append(piece);lengths.append(len(piece))
        transformed=''.join(pieces)
    if len(transformed)!=sum(lengths):
        raise ValueError('Unicode case mapping changed contextual length unexpectedly')
    output=[]; source=target=0
    for text,style in runs:
        count=sum(lengths[source:source+len(text)])
        output.append((transformed[target:target+count],style))
        source+=len(text);target+=count
    return output
