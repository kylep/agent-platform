#!/usr/bin/env python3
"""Offline reproducible converter. Input: unpacked, pinned Lucide source tree."""

from __future__ import annotations
import argparse, hashlib, json, math, pathlib, re, xml.etree.ElementTree as ET
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path.parser import parse_path

COMMIT = "55782beff3a0133df79c417eb68f96e8f19e2404"
VERSION = "1.73.0"
TREE_SHA256 = "3b2a1f3993a38e1e0ea2d96382bcd4a838ec741dbeff682975db31e0aed19b99"
ARCHIVE_SHA256 = "036597a0be49bed61e832d74567fa01f8339645a4582beb565c38c7faeecc90f"
ROOT = {
    "width": "24",
    "height": "24",
    "viewBox": "0 0 24 24",
    "fill": "none",
    "stroke": "currentColor",
    "stroke-width": "2",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
}
ATTRS = {
    "path": {"d"},
    "line": {"x1", "x2", "y1", "y2"},
    "polyline": {"points"},
    "polygon": {"points"},
    "circle": {"cx", "cy", "r", "fill"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "rect": {"x", "y", "width", "height", "rx", "ry"},
}


def convert(element):
    tag = element.tag.removeprefix("{http://www.w3.org/2000/svg}")
    if tag not in ATTRS or set(element.attrib) - ATTRS[tag] or list(element):
        raise ValueError(f"Unsupported SVG element/attributes: {tag} {element.attrib}")
    a = element.attrib

    def n(key, default=0):
        v = float(a.get(key, default))
        if not math.isfinite(v) or abs(v) > 100:
            raise ValueError("Invalid source coordinate")
        return v

    if a.get("fill", "none") not in ("none", "currentColor"):
        raise ValueError("Unsupported element paint")
    if tag == "path":
        d = a["d"]
    elif tag == "line":
        d = f"M{n('x1')} {n('y1')}L{n('x2')} {n('y2')}"
    elif tag in ("polyline", "polygon"):
        points = re.split(r"[ ,]+", a["points"].strip())
        if len(points) < 4 or len(points) % 2:
            raise ValueError("Invalid points")
        d = (
            "M"
            + " ".join(points[:2])
            + "L"
            + " ".join(points[2:])
            + ("Z" if tag == "polygon" else "")
        )
    elif tag in ("circle", "ellipse"):
        x, y = n("cx"), n("cy")
        rx = n("r") if tag == "circle" else n("rx")
        ry = rx if tag == "circle" else n("ry")
        if rx <= 0 or ry <= 0:
            raise ValueError("Invalid ellipse")
        d = f"M{x - rx} {y}A{rx} {ry} 0 1 0 {x + rx} {y}A{rx} {ry} 0 1 0 {x - rx} {y}Z"
    else:
        x, y, w, h = n("x"), n("y"), n("width"), n("height")
        rx = min(n("rx", a.get("ry", 0)), w / 2)
        ry = min(n("ry", a.get("rx", 0)), h / 2)
        if w <= 0 or h <= 0 or rx < 0 or ry < 0:
            raise ValueError("Invalid rectangle")
        if not rx or not ry:
            d = f"M{x} {y}H{x + w}V{y + h}H{x}Z"
        else:
            d = (
                f"M{x + rx} {y}H{x + w - rx}A{rx} {ry} 0 0 1 {x + w} {y + ry}V{y + h - ry}"
                f"A{rx} {ry} 0 0 1 {x + w - rx} {y + h}H{x + rx}A{rx} {ry} 0 0 1 {x} {y + h - ry}"
                f"V{y + ry}A{rx} {ry} 0 0 1 {x + rx} {y}Z"
            )
    # FontTools converts arcs to curves and preserves endPath versus closePath.
    pen = SVGPathPen(None, ntos=lambda n: format(float(n), ".8g"))
    parse_path(d, pen)
    return {"d": pen.getCommands(), "fill": a.get("fill") == "currentColor"}


def generate(source, out):
    files = [
        source / "LICENSE",
        source / "brand-stopwords.json",
        *(source / "icons").glob("*.svg"),
        *(source / "icons").glob("*.json"),
        *(source / "groups").glob("*.json"),
    ]
    checksum = hashlib.sha256()
    for file in sorted(files, key=lambda f: str(f.relative_to(source))):
        checksum.update(
            str(file.relative_to(source)).encode() + b"\0" + file.read_bytes() + b"\0"
        )
    if checksum.hexdigest() != TREE_SHA256:
        raise ValueError("Source contents differ from pinned release")
    blocked = set(json.loads((source / "brand-stopwords.json").read_text())) | {"figma"}
    icons = []
    excluded = []
    for svg in sorted((source / "icons").glob("*.svg"), key=lambda p: p.stem):
        if svg.stem in blocked:
            excluded.append(svg.stem)
            continue
        raw = svg.read_bytes()
        root = ET.fromstring(raw)
        if root.tag != "{http://www.w3.org/2000/svg}svg" or root.attrib != ROOT:
            raise ValueError(f"Unsupported SVG root: {svg}")
        paths = [convert(e) for e in root]
        if not paths or len(paths) > 32 or sum(len(p["d"]) for p in paths) > 8192:
            raise ValueError(f"Icon too large: {svg}")
        segments = 0
        for path in paths:
            pen = RecordingPen()
            parse_path(path["d"], pen)
            segments += len(pen.value)
        if segments > 256:
            raise ValueError(f"Icon segment budget exceeded: {svg}")
        metadata = json.loads(svg.with_suffix(".json").read_text())
        tags = []
        for tag in metadata.get("tags", []):
            if tag.startswith("$group:"):
                tags.extend(
                    json.loads((source / "groups" / f"{tag[7:]}.json").read_text())[
                        "tags"
                    ]
                )
            else:
                tags.append(tag)
        icons.append(
            {
                "id": svg.stem,
                "name": svg.stem.replace("-", " ").title(),
                "tags": sorted(set(tags)),
                "categories": sorted(metadata.get("categories", [])),
                "paths": paths,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    data = {
        "revision": 1,
        "source": {
            "name": "Lucide",
            "version": VERSION,
            "commit": COMMIT,
            "license": "ISC AND MIT",
        },
        "icons": icons,
    }
    encoded = (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()
    if len(icons) > 2500 or len(encoded) > 4 << 20:
        raise ValueError("Catalog exceeds budget")
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalog.json").write_bytes(encoded)
    (out / "LICENSE-Lucide.txt").write_bytes((source / "LICENSE").read_bytes())
    manifest = {
        "revision": 1,
        "source": data["source"],
        "source_url": f"https://github.com/lucide-icons/lucide/tree/{COMMIT}",
        "source_tree_sha256": TREE_SHA256,
        "source_archive_sha256": ARCHIVE_SHA256,
        "catalog_sha256": hashlib.sha256(encoded).hexdigest(),
        "count": len(icons),
        "bytes": len(encoded),
        "excluded": excluded,
        "converter": "scripts/ui_designer/generate_icons.py",
        "fonttools": "4.68.0",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("source", type=pathlib.Path)
    p.add_argument("output", type=pathlib.Path)
    a = p.parse_args()
    generate(a.source, a.output)
