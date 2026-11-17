"""The bundled font allowlist and glyph access (design 46 §5).

Fonts are SIL OFL static TrueType files kept next to this module in
`fonts/` (Inter, JetBrains Mono, Noto Sans, Nunito, Fredoka; Regular/Medium/SemiBold/Bold
each). A requested weight snaps to the nearest bundled one. Adding a family
is a reviewed change: a font file, one line in `_FILES`, and a
`renderer_version` bump.

`Face` wraps one font file for the two libraries that agree on it:
uharfbuzz shapes (advances, kerning, ligatures) and fontTools draws the
outlines. Both use the font's own units (`upem` per em), so a measurement
made at any size is a multiplication.
"""

from __future__ import annotations

import threading
import io
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from .schema import FONT_FAMILIES

FONT_DIR = Path(__file__).parent / "fonts"
WEIGHTS = (400, 500, 600, 700)
DEFAULT_FAMILY = "Inter"
DEFAULT_WEIGHT = 400

_FILES = {
    "Inter": "Inter-{w}.ttf",
    "JetBrains Mono": "JetBrainsMono-{w}.ttf",
    "Noto Sans": "NotoSans-{w}.ttf",
    "Nunito": "Nunito-{w}.ttf",
    "Fredoka": "Fredoka-{w}.ttf",
}
_WEIGHT_NAMES = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}

assert set(_FILES) == set(FONT_FAMILIES), "fonts.py and schema.FONT_FAMILIES disagree"


def snap_weight(weight: float) -> int:
    """The nearest bundled weight (ties go lighter)."""
    return min(WEIGHTS, key=lambda w: (abs(w - weight), w))


class Face:
    """One bundled font file: shaping font, outline source and vertical metrics."""

    def __init__(
        self,
        family: str,
        weight: int,
        data: bytes | None = None,
        sha: str | None = None,
    ):
        self.family = family
        self.weight = weight
        self.key = f"{family}:{weight}" + ("@" + sha if sha else "")
        self.path = (
            FONT_DIR / _FILES[family].format(w=_WEIGHT_NAMES[weight])
            if data is None
            else None
        )
        blob = (
            hb.Blob(data)
            if data is not None
            else hb.Blob.from_file_path(str(self.path))
        )
        self.hb_font = hb.Font(hb.Face(blob))
        self.tt = TTFont(
            io.BytesIO(data) if data is not None else str(self.path), lazy=True
        )
        self.upem: int = self.tt["head"].unitsPerEm
        hhea = self.tt["hhea"]
        self.ascent: int = hhea.ascent
        self.descent: int = -hhea.descent  # positive, below the baseline
        self.glyph_set = self.tt.getGlyphSet()
        self.glyph_order = self.tt.getGlyphOrder()
        self._paths: dict[
            int, tuple[str, tuple[float, float, float, float] | None]
        ] = {}
        self._lock = threading.Lock()

    def glyph_outline(
        self, gid: int
    ) -> tuple[str, tuple[float, float, float, float] | None]:
        """(`d`, ink bounds) of a glyph in font units with **y pointing down**
        (already flipped), so a painter only needs `translate(x, baseline)
        scale(size / upem)`. A blank glyph is ("", None). Cached per face."""
        hit = self._paths.get(gid)
        if hit is not None:
            return hit
        with self._lock:
            name = self.glyph_order[gid] if gid < len(self.glyph_order) else ".notdef"
            gs = self.glyph_set[name]
            sp = SVGPathPen(
                self.glyph_set, ntos=lambda v: ("%.2f" % v).rstrip("0").rstrip(".")
            )
            gs.draw(TransformPen(sp, (1, 0, 0, -1, 0, 0)))
            bp = BoundsPen(self.glyph_set)
            gs.draw(bp)
            b = bp.bounds
            bounds = (
                None if b is None else (b[0], -b[3], b[2], -b[1])
            )  # x0, y0, x1, y1 (y down)
            out = (sp.getCommands(), bounds)
            self._paths[gid] = out
            return out


_FACES: dict[tuple[str, int], Face] = {}
_FACES_LOCK = threading.Lock()


def get_face(family: str | None, weight: float | None) -> Face:
    """The `Face` for a family and weight; an unknown family raises KeyError
    (the validator already refuses one, so this only trips on unvalidated input)."""
    fam = family or DEFAULT_FAMILY
    if fam not in _FILES:
        registry = _DOCUMENT_FACES.get() or {}
        choices = [face for (name, w), face in registry.items() if name == fam]
        if not choices:
            raise KeyError(fam)
        return min(
            choices,
            key=lambda face: (
                abs(face.weight - (weight if weight is not None else DEFAULT_WEIGHT)),
                face.weight,
            ),
        )
    w = snap_weight(weight if weight is not None else DEFAULT_WEIGHT)
    key = (fam, w)
    face = _FACES.get(key)
    if face is None:
        with _FACES_LOCK:
            face = _FACES.get(key)
            if face is None:
                face = _FACES[key] = Face(fam, w)
    return face


@lru_cache(maxsize=None)
def _bundled_face_by_key(key: str) -> Face:
    family, _, weight = key.rpartition(":")
    return get_face(family, int(weight))


_DOCUMENT_FACES = ContextVar("ui_document_faces", default=None)


def face_by_key(key: str) -> Face:
    if "@" not in key:
        if key.rpartition(":")[0] not in _FILES:
            raise KeyError(key)
        return _bundled_face_by_key(key)
    for face in (_DOCUMENT_FACES.get() or {}).values():
        if face.key == key:
            return face
    raise KeyError("custom face is not in this document context")


@contextmanager
def document_fonts(doc: dict, loader):
    """One worker job's immutable registry; never widen another document."""
    registry = {}
    try:
        for entry in doc.get("fonts", []):
            raw = loader(entry["sha256"])
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("font cache hash mismatch")
            face = Face(entry["family"], entry["weight"], raw, entry["sha256"])
            registry[(face.family, face.weight)] = face
        token = _DOCUMENT_FACES.set(registry)
        try:
            yield
        finally:
            _DOCUMENT_FACES.reset(token)
    finally:
        for face in registry.values():
            face.tt.close()
