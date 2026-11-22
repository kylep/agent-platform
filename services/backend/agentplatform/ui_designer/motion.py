"""Canonical, inert motion manifests and bounded native frame sampling.

The player and export consume these exact layers; matching never uses titles.
"""

from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import math
import re
from collections import OrderedDict, Counter
from PIL import Image
from . import svg
from .errors import UiDesignerError, invalid, issue
from .layout import layout_document
from .visual_diff import viewport

VERSION = "motion-1"
EASINGS = ("linear", "ease-in", "ease-out", "ease-in-out")
MAX_MANIFEST = 16 << 20
MAX_RAW_BYTES = 6 << 20
MAX_RASTER_PIXELS = 32_000_000


def fail(message):
    return invalid([issue("UD-VALUE", "$.motion", message)])


def options(duration_ms=800, easing="ease-in-out", fps=24, max_edge=720):
    if type(duration_ms) is not int or not 200 <= duration_ms <= 5000:
        raise fail("duration_ms must be200..5000 integer milliseconds")
    if easing not in EASINGS:
        raise fail("Unknown motion easing")
    if type(fps) is not int or fps not in (12, 24):
        raise fail("fps must be12 or24")
    if type(max_edge) is not int or not 64 <= max_edge <= 720:
        raise fail("max_edge must be64..720")
    return max(2, round(duration_ms * fps / 1000))


def ease(t, easing):
    t = max(0, min(1, t))
    if easing == "ease-in":
        return t * t
    if easing == "ease-out":
        return 1 - (1 - t) ** 2
    if easing == "ease-in-out":
        return 2 * t * t if t < 0.5 else 1 - 2 * (1 - t) ** 2
    return t


def _uri(raw):
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def prepare(
    doc,
    from_node,
    to_node,
    *,
    mode=None,
    duration_ms=800,
    easing="ease-in-out",
    fps=24,
    max_edge=720,
    assets=None,
    asset_bytes=None,
    raster,
):
    frames = options(duration_ms, easing, fps, max_edge)
    resolved = layout_document(doc, mode)
    roots = [svg.find_node(resolved, n)[0] for n in (from_node, to_node)]
    if any(n.get("type") not in ("frame", "page") for n in roots):
        raise fail("Motion selects authored frames or expanded frame instances")
    for root in roots:
        pending = [root]
        count = 0
        while pending:
            n = pending.pop()
            if n.get("visible") is False:
                continue
            count += 1
            if count > 64:
                raise UiDesignerError(
                    "UD-IMAGE-TOO-LARGE",
                    "Motion is at most64 visible layers per endpoint",
                )
            pending.extend(n.get("children", []))
    boxes = [svg._box(n) for n in roots]
    nw, nh = max(1, *[b[2] for b in boxes]), max(1, *[b[3] for b in boxes])
    scale = min(1, max_edge / max(nw, nh))
    width, height = max(2, int(nw * scale) // 2 * 2), max(2, int(nh * scale) // 2 * 2)
    if frames * width * height > 64_000_000:
        raise UiDesignerError("UD-IMAGE-TOO-LARGE", "Motion exceeds64MP of frames")
    sx, sy = width / nw, height / nh
    infos = {
        sha: svg.AssetInfo(_uri(raw), assets[sha].w, assets[sha].h)
        for sha, raw in (asset_bytes or {}).items()
        if sha in (assets or {})
    }
    from .raster_effects import new_budget

    effect_budget = new_budget()
    warnings = []
    blobs = {}
    raster_work = 0
    raw_bytes = 0

    def image(source, w, h):
        nonlocal raster_work, raw_bytes
        if raster_work + w * h > MAX_RASTER_PIXELS:
            raise UiDesignerError(
                "UD-IMAGE-TOO-LARGE", "Motion raster work exceeds32MP"
            )
        raster_work += w * h
        raw = raster(source, w, h)
        key = hashlib.sha256(raw).hexdigest()
        if key not in blobs:
            if raw_bytes + len(raw) > MAX_RAW_BYTES:
                raise UiDesignerError(
                    "UD-IMAGE-TOO-LARGE",
                    "Motion PNG inputs exceed6MiB before base64 expansion",
                )
            raw_bytes += len(raw)
            blobs[key] = raw
        return key

    def svg_canvas(source, box, w, h):
        return viewport(source, box, w, h).replace(
            "<svg ", '<svg preserveAspectRatio="none" ', 1
        )

    endpoints = []
    for nid, box in zip((from_node, to_node), boxes):
        p = svg.paint(
            resolved,
            nid,
            assets=infos,
            asset_bytes=asset_bytes,
            effect_scale=scale,
            effect_budget=effect_budget,
        )
        endpoints.append(
            image(
                svg_canvas(p.svg, [box[0], box[1], nw, nh], width, height),
                width,
                height,
            )
        )

    def flatten(root):
        out = []

        def walk(n, parent=None):
            if n.get("visible") is False:
                return
            out.append((n, parent))
            for child in n.get("children", []):
                walk(child, n)

        walk(root)
        if len(out) > 64:
            raise UiDesignerError(
                "UD-IMAGE-TOO-LARGE", "Motion is at most64 visible layers per endpoint"
            )
        return out

    trees = [flatten(n) for n in roots]

    def matching(tree):
        explicit = [n.get("motion_key") for n, _ in tree if "motion_key" in n]
        if any(
            not isinstance(k, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", k)
            for k in explicit
        ) or len(explicit) != len(set(explicit)):
            raise fail(
                "meta.motion_key must be unique within each selected frame, using1..64 letters/digits/dash/underscore"
            )
        rels = Counter(n["id"].split("/", 1)[1] for n, _ in tree if "/" in n["id"])
        comps = Counter(n.get("of") for n, _ in tree if n.get("of"))
        keys = {}
        for i, (n, _) in enumerate(tree):
            if i == 0:
                key = "root"
            elif n.get("motion_key"):
                key = "key:" + n["motion_key"]
            elif "/" in n["id"] and rels[n["id"].split("/", 1)[1]] == 1:
                key = "relative:" + n["id"].split("/", 1)[1]
            elif n.get("of") and comps[n["of"]] == 1:
                key = "component:" + n["of"]
            else:
                key = "id:" + n["id"]
            keys[n["id"]] = key
        return keys

    maps = [matching(t) for t in trees]
    # Raster islands cannot safely separate group effects or clipping hierarchy.
    fallback = False
    for tree, root, box in zip(trees, roots, boxes):
        for n, parent in tree:
            complex_ = (
                any(n.get(k) for k in ("mask", "shadow", "blur", "backdrop_blur"))
                or n.get("blend", "normal") != "normal"
            )
            complex_ |= bool(
                n.get("children") and (n.get("rotate") or n.get("opacity", 1) != 1)
            )
            complex_ |= bool(n.get("bounds") and not n.get("rotate"))
            if parent and (
                n.get("x", 0) < box[0]
                or n.get("y", 0) < box[1]
                or n.get("x", 0) + n.get("w", 0) > box[0] + box[2] + 0.01
                or n.get("y", 0) + n.get("h", 0) > box[1] + box[3] + 0.01
            ):
                complex_ = True
            if parent and parent.get("clip") and parent is not root:
                complex_ = True
            fallback |= complex_
    sequences = [[m[n["id"]] for n, _ in t] for m, t in zip(maps, trees)]
    common = set(sequences[0]) & set(sequences[1])
    if [k for k in sequences[0] if k in common] != [
        k for k in sequences[1] if k in common
    ]:
        fallback = True
    parents = []
    for tree, m in zip(trees, maps):
        parents.append({m[n["id"]]: m[p["id"]] if p else None for n, p in tree})
    if any(parents[0][k] != parents[1][k] for k in common):
        fallback = True
    layers = []
    if fallback:
        warnings.append(
            "Complex effects, clipping, rotation ancestry or changed hierarchy use a whole-frame dissolve; glyphs and paths are not morphed."
        )
        layers = [
            {
                "id": "frame-dissolve",
                "from": {
                    "x": 0,
                    "y": 0,
                    "w": width,
                    "h": height,
                    "rotate": 0,
                    "opacity": 1,
                    "asset": endpoints[0],
                },
                "to": {
                    "x": 0,
                    "y": 0,
                    "w": width,
                    "h": height,
                    "rotate": 0,
                    "opacity": 1,
                    "asset": endpoints[1],
                },
            }
        ]
    else:
        states = []
        for tree, keys, box in zip(trees, maps, boxes):
            state = {}
            for n, _ in tree:
                local = copy.deepcopy(n)
                local.pop("children", None)
                local.pop("rotate", None)
                local.pop("bounds", None)
                local.pop("opacity", None)
                w, h = max(0.01, n.get("w", 0)), max(0.01, n.get("h", 0))
                iw, ih = max(1, round(w * sx)), max(1, round(h * sy))
                if max(iw, ih) > 720:
                    raise UiDesignerError(
                        "UD-IMAGE-TOO-LARGE", "Motion layer exceeds720px"
                    )
                isolated = {
                    **resolved,
                    "pages": [{"id": "isolated-page", "children": [local]}],
                }
                p = svg.paint(
                    isolated,
                    n["id"],
                    assets=infos,
                    asset_bytes=asset_bytes,
                    effect_scale=scale,
                    effect_budget=effect_budget,
                )
                key = image(
                    svg_canvas(p.svg, [n.get("x", 0), n.get("y", 0), w, h], iw, ih),
                    iw,
                    ih,
                )
                state[keys[n["id"]]] = {
                    "x": (n.get("x", 0) - box[0]) * sx,
                    "y": (n.get("y", 0) - box[1]) * sy,
                    "w": w * sx,
                    "h": h * sy,
                    "rotate": n.get("rotate", 0),
                    "opacity": n.get("opacity", 1),
                    "asset": key,
                }
            states.append(state)
        ordered = list(sequences[0])
        for i, k in enumerate(sequences[1]):
            if k not in ordered:
                prev = sequences[1][i - 1] if i else None
                ordered.insert(ordered.index(prev) + 1 if prev in ordered else 0, k)
        if len(ordered) > 64:
            raise UiDesignerError(
                "UD-IMAGE-TOO-LARGE", "Motion is at most64 combined layers"
            )
        layers = [
            {"id": k, "from": states[0].get(k), "to": states[1].get(k)} for k in ordered
        ]
        warnings.append(
            "Matched layers interpolate geometry and opacity; changed content crossfades as raster images rather than morphing glyphs or paths."
        )
    used = {endpoints[0], endpoints[1]} | {
        s["asset"] for layer in layers for s in (layer["from"], layer["to"]) if s
    }
    asset_map = {key: base64.b64encode(blobs[key]).decode() for key in sorted(used)}
    result = {
        "motion_version": VERSION,
        "renderer_version": svg.renderer_version(),
        "width": width,
        "height": height,
        "box": [0, 0, width, height],
        "duration_ms": duration_ms,
        "fps": fps,
        "frames": frames,
        "easing": easing,
        "mode": resolved.get("mode"),
        "from_image": asset_map[endpoints[0]],
        "to_image": asset_map[endpoints[1]],
        "assets": asset_map,
        "layers": layers,
        "warnings": warnings,
    }
    if (
        len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode())
        > MAX_MANIFEST
    ):
        raise UiDesignerError("UD-IMAGE-TOO-LARGE", "Motion manifest exceeds16MiB")
    validate_manifest(result)
    return result


def render_frame(manifest, index):
    """Sample canonical layers using premultiplied content crossfades."""
    import numpy as np

    width, height = manifest["width"], manifest["height"]

    def load(key):
        raw = base64.b64decode(manifest["assets"][key])
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im.convert("RGBA")

    if index == 0:
        return Image.open(io.BytesIO(base64.b64decode(manifest["from_image"]))).convert(
            "RGBA"
        )
    if index >= manifest["frames"] - 1:
        return Image.open(io.BytesIO(base64.b64decode(manifest["to_image"]))).convert(
            "RGBA"
        )
    t = ease(index / (manifest["frames"] - 1), manifest["easing"])
    canvas = Image.new("RGBA", (width, height))
    for layer in manifest["layers"]:
        a, b = layer["from"], layer["to"]
        if a is None:
            a = {**b, "opacity": 0}
        if b is None:
            b = {**a, "opacity": 0}
        values = {
            k: a[k] + (b[k] - a[k]) * t
            for k in ("x", "y", "w", "h", "rotate", "opacity")
        }
        w, h = max(1, round(values["w"])), max(1, round(values["h"]))
        if values["opacity"] <= 0:
            continue
        image = load(a["asset"]).resize((w, h), Image.Resampling.BICUBIC)
        if a["asset"] != b["asset"]:
            after = load(b["asset"]).resize((w, h), Image.Resampling.BICUBIC)
            x = np.asarray(image, dtype=np.float32) / 255
            y = np.asarray(after, dtype=np.float32) / 255
            alpha = x[:, :, 3:4] * (1 - t) + y[:, :, 3:4] * t
            rgb = x[:, :, :3] * x[:, :, 3:4] * (1 - t) + y[:, :, :3] * y[:, :, 3:4] * t
            rgb = np.divide(rgb, alpha, out=np.zeros_like(rgb), where=alpha > 0)
            image = Image.fromarray(
                np.clip(
                    np.concatenate((rgb, alpha), axis=2) * 255 + 0.5, 0, 255
                ).astype("uint8"),
                "RGBA",
            )
        image.putalpha(
            image.getchannel("A").point(lambda alpha: round(alpha * values["opacity"]))
        )
        if values["rotate"]:
            image = image.rotate(
                -values["rotate"], resample=Image.Resampling.BICUBIC, expand=True
            )
        cx, cy = values["x"] + values["w"] / 2, values["y"] + values["h"] / 2
        canvas.alpha_composite(
            image, (round(cx - image.width / 2), round(cy - image.height / 2))
        )
    return canvas


def validate_manifest(body):
    """Validate the worker boundary before exposing PNGs to a model/browser."""

    def bad():
        raise UiDesignerError(
            "UD-RENDER-FAILED", "Invalid or over-budget motion manifest"
        )

    required = {
        "motion_version",
        "renderer_version",
        "width",
        "height",
        "box",
        "duration_ms",
        "fps",
        "frames",
        "easing",
        "mode",
        "from_image",
        "to_image",
        "assets",
        "layers",
        "warnings",
    }
    if (
        not isinstance(body, dict)
        or not required <= body.keys()
        or body.keys() - required - {"ok"}
    ):
        bad()
    if (
        body["motion_version"] != VERSION
        or body["renderer_version"] != svg.renderer_version()
    ):
        bad()
    try:
        frames = options(
            body["duration_ms"],
            body["easing"],
            body["fps"],
            max(body["width"], body["height"], 64),
        )
    except (UiDesignerError, TypeError):
        bad()
    if (
        any(
            type(body[k]) is not int or not 2 <= body[k] <= 720
            for k in ("width", "height")
        )
        or body["frames"] != frames
        or type(body["frames"]) is not int
    ):
        bad()
    if (
        body["box"] != [0, 0, body["width"], body["height"]]
        or body["width"] * body["height"] * frames > 64_000_000
    ):
        bad()
    if (
        len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
        > MAX_MANIFEST
    ):
        bad()
    if not isinstance(body["assets"], dict) or len(body["assets"]) > 128:
        bad()
    if (
        not isinstance(body["warnings"], list)
        or len(body["warnings"]) > 32
        or any(
            not isinstance(n, str) or len(n.encode()) > 500 for n in body["warnings"]
        )
    ):
        bad()
    unique = {}
    pixels = 0

    def png(encoded, endpoint=False):
        nonlocal pixels
        if not isinstance(encoded, str):
            bad()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            bad()
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            bad()
        w, h = int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
        if (
            not 1 <= w <= 720
            or not 1 <= h <= 720
            or (endpoint and (w, h) != (body["width"], body["height"]))
        ):
            bad()
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in unique:
            pixels += w * h
            unique[sha] = True
        if pixels > 32_000_000:
            bad()
        return sha

    png(body["from_image"], True)
    png(body["to_image"], True)
    for sha, encoded in body["assets"].items():
        if png(encoded) != sha:
            bad()
    layers = body["layers"]
    if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
        bad()
    ids = set()
    for layer in layers:
        if (
            not isinstance(layer, dict)
            or set(layer) != {"id", "from", "to"}
            or not isinstance(layer["id"], str)
            or len(layer["id"]) > 1024
            or layer["id"] in ids
        ):
            bad()
        ids.add(layer["id"])
        if layer["from"] is None and layer["to"] is None:
            bad()
        for pose in (layer["from"], layer["to"]):
            if pose is None:
                continue
            if (
                not isinstance(pose, dict)
                or set(pose) != {"x", "y", "w", "h", "rotate", "opacity", "asset"}
                or not isinstance(pose["asset"], str)
                or pose["asset"] not in body["assets"]
            ):
                bad()
            if any(
                isinstance(pose[k], bool)
                or not isinstance(pose[k], (int, float))
                or not math.isfinite(pose[k])
                or abs(pose[k]) > 1_000_000
                for k in ("x", "y", "w", "h", "rotate", "opacity")
            ):
                bad()
            if (
                not 0 < pose["w"] <= 720
                or not 0 < pose["h"] <= 720
                or not 0 <= pose["opacity"] <= 1
            ):
                bad()
    return body
