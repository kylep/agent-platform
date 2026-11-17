"""Bounded static font validation. Called only in isolated renderer workers."""

from __future__ import annotations
import hashlib
import json
from urllib.parse import urlsplit
import io
import math
import re
import struct
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from .errors import invalid, issue
from .schema import FONT_FAMILIES

MAX_BYTES = 1 << 20


def reject(message):
    return invalid([issue("UD-FONT", "$.font", message)])


def validate_font(data: bytes) -> dict:
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_BYTES:
        raise reject("Upload a static TTF/OTF font no larger than1MiB")
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO"):
        raise reject(
            "Only static TTF/OTF supported; collections, WOFF and other containers are refused"
        )
    try:
        if len(data) < 12:
            raise ValueError("truncated SFNT header")
        count = struct.unpack_from(">H", data, 4)[0]
        if not 1 <= count <= 64 or len(data) < 12 + 16 * count:
            raise ValueError("invalid or excessive table directory")
        ranges = []
        tags = set()
        for i in range(count):
            tag, checksum, offset, length = struct.unpack_from(
                ">4sIII", data, 12 + i * 16
            )
            if tag in tags or offset < 12 + 16 * count or offset + length > len(data):
                raise ValueError("invalid table offsets or duplicate tables")
            tags.add(tag)
            if length:
                ranges.append((offset, offset + length))
        ranges.sort()
        if any(a[1] > b[0] for a, b in zip(ranges, ranges[1:])):
            raise ValueError("overlapping font tables")
        banned = {
            b"fvar",
            b"gvar",
            b"avar",
            b"CFF2",
            b"COLR",
            b"CPAL",
            b"CBDT",
            b"CBLC",
            b"sbix",
            b"SVG ",
            b"EBDT",
            b"EBLC",
        }
        if tags & banned:
            raise ValueError("variable, color and bitmap fonts are unsupported")
        if not {b"head", b"hhea", b"hmtx", b"maxp", b"name", b"OS/2", b"cmap"} <= tags:
            raise ValueError("missing required metrics/name tables")
        with TTFont(io.BytesIO(data), lazy=False, checkChecksums=2) as font:
            font.ensureDecompiled()
            weight = font["OS/2"].usWeightClass
            if not 100 <= weight <= 900 or font["OS/2"].fsType != 0:
                raise ValueError(
                    "font must have weight100..900 and unrestricted OS/2 embedding (fsType0)"
                )
            names = font["name"].names
            if len(names) > 256 or sum(len(n.string) for n in names) > 65536:
                raise ValueError("font name records exceed budget")
            family = font["name"].getDebugName(16) or font["name"].getDebugName(1)
            if (
                not family
                or family.startswith("$")
                or not re.fullmatch(r"[^\x00-\x1f<>:/\\]{1,64}", family)
                or len(family.encode()) > 128
            ):
                raise ValueError("font family name is invalid or too long")
            if family.casefold() in {n.casefold() for n in FONT_FAMILIES}:
                raise ValueError("custom font families may not shadow bundled fonts")
            declaration = font["name"].getDebugName(13) or ""
            license_url = font["name"].getDebugName(14) or ""
            if license_url:
                url = urlsplit(license_url)
                if (
                    url.scheme not in ("http", "https")
                    or not url.hostname
                    or url.username is not None
                    or url.password is not None
                    or any(ord(c) < 32 for c in license_url)
                ):
                    raise ValueError("license URL must be an inert HTTP(S) URL")
            normalized = " ".join(declaration.casefold().split())
            if not (
                (
                    "sil open font license" in normalized
                    and re.search(r"(?:version\s*)?1\.1\b", normalized)
                )
                or "ofl-1.1" in normalized
            ):
                raise ValueError(
                    "font must embed an explicit SIL Open Font License1.1 declaration"
                )
            if len(declaration.encode()) > 8192 or len(license_url.encode()) > 1024:
                raise ValueError("license metadata exceeds budget")
            upem = font["head"].unitsPerEm
            order = font.getGlyphOrder()
            if (
                not 16 <= upem <= 16384
                or not 1 <= len(order) <= 4096
                or len(set(order)) != len(order)
            ):
                raise ValueError("units-per-em or glyph count exceeds supported bounds")
            names_set = set(order)
            cmap = font.getBestCmap() or {}
            if len(cmap) > 65536 or any(
                not 0 <= c <= 0x10FFFF or g not in names_set for c, g in cmap.items()
            ):
                raise ValueError("invalid character mapping")
            if "glyf" in font:
                glyf = font["glyf"]
                graph = {}
                total_points = 0
                for name in order:
                    glyph = glyf[name]
                    if glyph.isComposite():
                        if len(glyph.components) > 32:
                            raise ValueError("too many composite components")
                        graph[name] = [c.glyphName for c in glyph.components]
                    else:
                        points = len(glyph.coordinates) if glyph.numberOfContours else 0
                        if points > 4096:
                            raise ValueError("glyph point budget exceeded")
                        total_points += points
                if total_points > 200000:
                    raise ValueError("font point budget exceeded")
                depths = {}

                def visit(name, stack):
                    if name in stack or name not in names_set:
                        raise ValueError("cyclic or missing composite glyph")
                    if name in depths:
                        if len(stack) + depths[name] > 8:
                            raise ValueError("excessively nested composite glyph")
                        return depths[name]
                    if len(stack) >= 8:
                        raise ValueError("excessively nested composite glyph")
                    depth = 1 + max(
                        (
                            visit(child, stack + (name,))
                            for child in graph.get(name, [])
                        ),
                        default=0,
                    )
                    if len(stack) + depth > 8:
                        raise ValueError("excessively nested composite glyph")
                    depths[name] = depth
                    return depth

                for name in graph:
                    visit(name, ())
            elif "CFF " not in font:
                raise ValueError("font lacks supported TrueType/CFF outlines")
            glyphs = font.getGlyphSet()
            work = [0]

            class GuardPen(BasePen):
                def _points(self, *points):
                    work[0] += len(points)
                    if work[0] > 200000:
                        raise ValueError("expanded outline budget exceeded")
                    if any(
                        not math.isfinite(float(v)) or abs(v) > upem * 16
                        for p in points
                        for v in p
                    ):
                        raise ValueError("glyph coordinate bounds exceeded")

                def _moveTo(self, p):
                    self._points(p)

                def _lineTo(self, p):
                    self._points(p)

                def _curveToOne(self, *p):
                    self._points(*p)

                def _qCurveToOne(self, *p):
                    self._points(*p)

                def _closePath(self):
                    pass

                def _endPath(self):
                    pass

            for name in order:
                glyphs[name].draw(GuardPen(glyphs))
            result = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "family": family,
                "weight": weight,
                "license": "OFL-1.1",
                "license_text": declaration,
                "license_url": license_url,
                "copyright": (font["name"].getDebugName(0) or "")
                .encode()[:2048]
                .decode(errors="ignore"),
                "font_format": "otf" if data[:4] == b"OTTO" else "ttf",
                "glyphs": len(order),
                "units_per_em": upem,
            }
            if len(json.dumps(result, ensure_ascii=False).encode()) > 12 << 10:
                raise ValueError("font metadata exceeds12KiB")
            return result
    except Exception as error:
        if hasattr(error, "issues"):
            raise
        raise reject(f"Font validation failed: {str(error)[:240]}") from None
