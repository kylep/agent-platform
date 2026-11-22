"""Convert locally captured console fixtures into editable UI Designer documents."""

from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from math import atan2, degrees, hypot, radians, sqrt
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from agentplatform.ui_designer import schema
from agentplatform.ui_designer.components import nodes
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.platform_catalog import PREFIX, platform_tokens
from agentplatform.ui_designer.svg import AssetInfo, paint
from agentplatform.ui_designer.text import layout_text, make_style
from fontTools.misc.transform import Transform
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from PIL import Image, UnidentifiedImageError


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def identify(prefix, key):
    digest = hashlib.sha256(key.encode()).hexdigest()[:10]
    return f"{schema.slugify(prefix)[:36].rstrip('-')}-{digest}"


def rounded(value):
    return round(float(value), 4)


def numeric(value, default=0):
    try:
        return float(str(value).removesuffix("px"))
    except (TypeError, ValueError):
        return default


def numbers(value):
    return [float(item) for item in re.findall(r"-?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", str(value))]


def box(item, origin=(0, 0)):
    return {"x": rounded(item["x"] - origin[0]), "y": rounded(item["y"] - origin[1]),
            "w": rounded(item["width"]), "h": rounded(item["height"])}


def normalize_color(value):
    if value in (None, "", "none", "transparent"):
        return None
    value = value.lower()
    if schema.COLOR_RE.fullmatch(value):
        if len(value) == 4:
            value = "#" + "".join(char * 2 for char in value[1:])
        return value.removesuffix("ff") if len(value) == 9 and value.endswith("ff") else value
    if value.startswith(("rgb(", "rgba(")):
        channels = numbers(value)
        if len(channels) in (3, 4):
            if len(channels) == 4:
                channels[3] *= 255
            return "#" + "".join(f"{round(max(0, min(255, channel))):02x}" for channel in channels)
    return None


def opacity(node, value):
    value = rounded(value)
    if value < 1:
        node["opacity"] = value


def rect_paints(scene, item):
    width, height = item["width"], item["height"]
    radii = [rounded(min(radius, min(width, height) / 2)) for radius in item.get("radii", [])]
    fill = scene.color(item.get("fill"), item.get("fillToken"))
    main = {}
    if fill:
        main["fill"] = fill
    if any(radii):
        main["radius"] = scene.radius(radii)
    strokes = defaultdict(lambda: [0, 0, 0, 0])
    for side, border in enumerate(item.get("borders", [])):
        ink = scene.color(border.get("color"), border.get("token"))
        if ink and border.get("style") not in ("none", "hidden") and numeric(border["width"]) > 0:
            strokes[(ink, border.get("style", "solid"))][side] = rounded(border["width"])
    layers = []
    for (ink, style), sides in strokes.items():
        stroke = {"color": ink, "align": "inside"}
        if len(set(sides)) == 1 and style in ("solid", "dashed", "dotted"):
            stroke["width"] = sides[0]
            if style == "dashed":
                stroke["dash"] = [sides[0] * 3, sides[0] * 2]
            elif style == "dotted":
                stroke["dash"] = [sides[0], sides[0]]
        else:
            stroke["sides"] = sides
            if style != "solid":
                scene.losses[f"border style {style}"] += 1
        if "stroke" not in main:
            main["stroke"] = stroke
        else:
            layers.append({"stroke": stroke, **({"radius": main["radius"]} if "radius" in main else {})})
    return [main, *layers] if fill or strokes else []


def svg_shape_data(tag, attrs):
    x, y = numeric(attrs.get("x")), numeric(attrs.get("y"))
    w, h = numeric(attrs.get("width")), numeric(attrs.get("height"))
    if tag == "path":
        return attrs.get("d", "")
    if tag == "line":
        return f'M{numeric(attrs.get("x1"))} {numeric(attrs.get("y1"))}L{numeric(attrs.get("x2"))} {numeric(attrs.get("y2"))}'
    if tag in ("polygon", "polyline"):
        values = numbers(attrs.get("points", ""))
        pairs = list(zip(values[::2], values[1::2], strict=True))
        return ("M" + "L".join(f"{a} {b}" for a, b in pairs) + ("Z" if tag == "polygon" else "")) if pairs else ""
    if tag in ("circle", "ellipse"):
        cx, cy = numeric(attrs.get("cx")), numeric(attrs.get("cy"))
        rx, ry = (numeric(attrs.get(axis, attrs.get("r"))) for axis in ("rx", "ry"))
        if rx > 0 and ry > 0:
            return (f"M{cx-rx} {cy}a{rx} {ry} 0 1 0 {2*rx} 0"
                    f"a{rx} {ry} 0 1 0 {-2*rx} 0Z")
        return ""
    if tag == "rect":
        rx = min(w / 2, numeric(attrs.get("rx", attrs.get("ry"))))
        ry = min(h / 2, numeric(attrs.get("ry", attrs.get("rx"))))
        if w <= 0 or h <= 0:
            return ""
        if rx <= 0 or ry <= 0:
            return f"M{x} {y}h{w}v{h}h{-w}Z"
        return (f"M{x+rx} {y}H{x+w-rx}A{rx} {ry} 0 0 1 {x+w} {y+ry}"
                f"V{y+h-ry}A{rx} {ry} 0 0 1 {x+w-rx} {y+h}"
                f"H{x+rx}A{rx} {ry} 0 0 1 {x} {y+h-ry}"
                f"V{y+ry}A{rx} {ry} 0 0 1 {x+rx} {y}Z")
    return ""


def viewbox_transform(item):
    width, height = item["width"], item["height"]
    coords = numbers(item.get("viewBox", ""))
    if len(coords) != 4 or min(coords[2:]) <= 0:
        return Transform()
    x, y, w, h = coords
    sx, sy = width / w, height / h
    aspect = item.get("preserveAspectRatio", "xMidYMid meet").split()
    if aspect and aspect[0] == "none":
        return Transform().scale(sx, sy).translate(-x, -y)
    scale = max(sx, sy) if "slice" in aspect else min(sx, sy)
    anchor = aspect[0] if aspect and aspect[0] != "defer" else "xMidYMid"
    offsets = [
        remainder * (0 if minimum in anchor else 1 if maximum in anchor else 0.5)
        for remainder, minimum, maximum in ((width - w * scale, "xMin", "xMax"),
                                             (height - h * scale, "YMin", "YMax"))
    ]
    return Transform().translate(*offsets).scale(scale).translate(-x, -y)


def svg_transform(value):
    if isinstance(value, list) and len(value) == 6:
        return Transform(*value)
    transform = Transform()
    for operation, args in re.findall(r"(\w+)\s*\(([^)]*)\)", value or ""):
        params = numbers(args)
        if operation == "matrix" and len(params) == 6:
            transform = transform.transform(params)
        elif operation == "translate" and len(params) in (1, 2):
            transform = transform.translate(*params)
        elif operation == "scale" and len(params) in (1, 2):
            transform = transform.scale(*params)
        elif operation == "rotate" and len(params) in (1, 3):
            x, y = params[1:] or (0, 0)
            transform = transform.translate(x, y).rotate(radians(params[0])).translate(-x, -y)
    return transform


@dataclass
class CapturedAsset:
    data: bytes
    mime: str
    extension: str
    width: int
    height: int

    def render_info(self):
        href = f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}"
        return AssetInfo(href, self.width, self.height)



def raster_svg_asset(data, item):
    """Rasterize image-tag SVGs with every external reference disabled."""
    import resvg_py

    if re.search(rb"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet|@import", data, re.IGNORECASE):
        return None
    root = ET.fromstring(data)
    if root.tag.split("}")[-1] != "svg":
        return None
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.split("}")[-1] == "href" and not value.startswith("#"):
                return None
            if any(not url.strip(" \"'").startswith("#") for url in re.findall(r"url\((.*?)\)", value, re.I)):
                return None
        if element.tag.split("}")[-1] == "style":
            return None
    width = max(1, min(2048, round(numeric(root.get("width"), item["width"]) * 2)))
    fonts = Path(schema.__file__).with_name("fonts")
    font_files = [str(fonts / f"Inter-{weight}.ttf") for weight in ("Regular", "Bold")]
    return bytes(resvg_py.svg_to_bytes(svg_string=data.decode(), width=width,
                                      skip_system_fonts=True, font_files=font_files,
                                      sans_serif_family="Inter"))


def image_asset(item):
    source = item.get("dataUrl") or item.get("src") or ""
    match = re.fullmatch(r"data:image/(png|jpeg|webp|gif|svg\+xml);base64,(.*)", source, re.DOTALL)
    if not match or len(match[2]) > 11_184_816:
        return None
    try:
        data = base64.b64decode(match[2], validate=True)
        if match[1] == "svg+xml":
            data = raster_svg_asset(data, item)
            if not data:
                return None
        if len(data) > 8 << 20:
            return None
        with Image.open(BytesIO(data)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
            if mime is None or image.width * image.height > 16_000_000:
                return None
            image.verify()
            return CapturedAsset(data, mime, mime.split("/")[1], image.width, image.height)
    except (binascii.Error, ET.ParseError, UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        return None


class Converter:
    def __init__(self, tokens, mode, assets):
        self.tokens = tokens
        self.mode = mode
        self.assets = assets
        self.losses = Counter()
        self.colors = defaultdict(list)
        for name, value in self.tokens["color"].items():
            color = normalize_color(value.get(self.mode) if isinstance(value, dict) else value)
            self.colors[color].append(name)

    def color(self, value, token=None):
        color = normalize_color(value)
        if not color:
            if value not in (None, "", "none", "transparent"):
                self.losses["unsupported paint"] += 1
            return None
        if color.endswith("00") and len(color) == 9:
            return None
        names = self.colors[color]
        if token:
            preferred = PREFIX + token.removeprefix("--ds-").removeprefix("--")
            if preferred in names:
                return f"$color.{preferred}"
        # Several different tokens are visually identical in dark mode. Keep
        # the observed literal when the extractor cannot disambiguate them.
        if len(names) == 1:
            return f"$color.{names[0]}"
        return color

    def radius(self, radii):
        if len(set(radii)) > 1:
            return radii
        value = radii[0]
        return next((f"$radius.{name}" for name, size in self.tokens["radius"].items()
                     if size == value), value)

    def text(self, item, position):
        value = item["text"]
        if not value.strip():
            return None
        if len(value.encode()) > schema.MAX_TEXT_BYTES:
            raise ValueError("captured text exceeds the per-layer limit")
        fonts = [name.strip(" \"'") for name in item["fontFamily"].split(",")]
        family = next((name for name in fonts if name in schema.FONT_FAMILIES), None)
        if not family:
            self.losses["font substitution"] += 1
            family = "JetBrains Mono" if any("mono" in font.lower() for font in fonts) else "Inter"
        if item.get("fontStyle", "normal") != "normal":
            self.losses["italic font"] += 1
        size = max(1, numeric(item["fontSize"], 14))
        line = numeric(item.get("lineHeight"), item["height"]) / size
        style = {"family": family, "size": size, "weight": round(numeric(item.get("fontWeight"), 400)),
                 "line": max(0.1, min(10, line)), "letter": numeric(item.get("letterSpacing"))}
        native_style = make_style(style)
        metrics = layout_text([(value, native_style)])
        # The captured run is already line-broken by Chromium. A slightly
        # wider frame prevents width rounding or a fallback face breaking it again.
        position["w"] = max(position["w"], rounded(metrics.w + 0.1))
        style_ref = style
        for name, token in self.tokens["type"].items():
            if token == {key: value for key, value in style.items() if key != "letter"} and not style["letter"]:
                style_ref = f"$type.{name}"
                break
        node = {**position, "type": "text", "title": value.strip()[:90], "text": value,
                "style": style_ref, "vertical_align": "center",
                "color": self.color(item.get("color"), item.get("colorToken")) or "#00000000"}
        decorations = item.get("textDecoration", "")
        if "underline" in decorations:
            node["decoration"] = "underline"
        elif "line-through" in decorations:
            node["decoration"] = "line-through"
        return node

    def gradient(self, definition, width, height, alpha):
        attrs = definition.attrib
        if attrs.get("gradientUnits", "objectBoundingBox") != "objectBoundingBox" or attrs.get("gradientTransform"):
            return None

        def factor(value):
            return numeric(value[:-1]) / 100 if value.endswith("%") else numeric(value)

        def coordinates(defaults):
            return [factor(attrs.get(key, value)) for key, value in defaults.items()]

        kind = definition.tag.rsplit("}", 1)[-1].lower()
        paint = {}
        if kind == "lineargradient":
            x1, y1, x2, y2 = coordinates({"x1": "0%", "y1": "0%", "x2": "100%", "y2": "0%"})
            # Designer's linear endpoints always span the bounding box.
            if abs(x1 + x2 - 1) > 1e-6 or abs(y1 + y2 - 1) > 1e-6:
                if not ((x1 == x2 == 0 or y1 == y2 == 0) and abs(x2 - x1 + y2 - y1) == 1):
                    return None
            paint = {"gradient": "linear", "angle": rounded(degrees(atan2((x2 - x1) * width, -(y2 - y1) * height)))}
        elif kind == "radialgradient":
            defaults = {"cx": "50%", "cy": "50%", "r": "50%", "fx": "50%", "fy": "50%"}
            if any(value != 0.5 for value in coordinates(defaults)) or numeric(attrs.get("fr")):
                return None
            paint = {"gradient": "radial"}
        else:
            return None
        stops = []
        for element in definition:
            stop = element.attrib
            if "offset" not in stop:
                continue
            color = normalize_color(stop.get("stop-color", "#000"))
            if not color:
                return None
            stop_alpha = numeric(stop.get("stop-opacity"), 1) * alpha
            if stop_alpha < 1:
                old_alpha = int(color[7:], 16) if len(color) == 9 else 255
                color = color[:7] + f"{round(old_alpha * stop_alpha):02x}"
            offset = max(stops[-1][0] if stops else 0, min(1, factor(stop["offset"])))
            stops.append([offset, self.color(color) or "#00000000"])
        if not 2 <= len(stops) <= schema.MAX_STOPS:
            return None
        return {**paint, "stops": stops}


    def vector(self, item, position, key):
        children = []
        viewport = viewbox_transform(item)
        definitions = {}
        if "url(" in item.get("svg", ""):
            try:
                root = ET.fromstring(item["svg"])
                definitions = {element.attrib["id"]: element for element in root.iter()
                               if "id" in element.attrib}
            except ET.ParseError:
                self.losses["invalid SVG definitions"] += 1
        for index, shape in enumerate(item.get("shapes", [])):
            tag, attrs = shape["tag"], shape["attributes"]
            try:
                data = svg_shape_data(tag, attrs)
                if not data:
                    self.losses[f"SVG {tag}"] += 1
                    continue
                # DOMMatrix fields supplied by the extractor can account for a
                # transform on an ancestor <g> as well as one on the shape.
                matrix = shape.get("matrix")
                transform = viewport.transform(svg_transform(matrix or attrs.get("transform")))
                paths = RecordingPen()
                parse_path(data, TransformPen(paths, transform))
                bounds = BoundsPen(None)
                paths.replay(bounds)
                if bounds.bounds is None:
                    continue
                x, y, right, bottom = bounds.bounds
                pen = SVGPathPen(None, ntos=lambda value: str(rounded(value)))
                paths.replay(TransformPen(pen, Transform().translate(-x, -y)))
                result = pen.getCommands()
                if len(result.encode()) > schema.MAX_PATH_D_BYTES:
                    self.losses["oversized SVG path"] += 1
                    continue
            except (ValueError, IndexError, TypeError, ZeroDivisionError):
                self.losses[f"invalid SVG {tag}"] += 1
                continue
            child = {
                "id": identify("vector", f"{key}:{index}"), "type": "path", "title": tag.title(),
                "x": rounded(x), "y": rounded(y), "w": rounded(right - x), "h": rounded(bottom - y), "d": result,
            }
            fill = attrs.get("fill", "#000")
            stroke = attrs.get("stroke")
            if fill == "currentColor":
                fill = attrs.get("color", "#000")
            if stroke == "currentColor":
                stroke = attrs.get("color", "#000")
            factor = sqrt(hypot(transform.xx, transform.xy) * hypot(transform.yx, transform.yy))
            for paint_type, value in (("fill", fill), ("stroke", stroke)):
                alpha = numeric(attrs.get(f"{paint_type}-opacity"), 1)
                gradient = re.fullmatch(r"url\(#([^)]*)\)", value or "")
                if gradient and paint_type == "fill" and gradient[1] in definitions:
                    fill_paint = self.gradient(definitions[gradient[1]], child["w"], child["h"], alpha)
                    if fill_paint is not None:
                        child["fill"] = fill_paint
                        continue
                if alpha < 1 and (ink := normalize_color(value)):
                    original_alpha = int(ink[7:], 16) if len(ink) == 9 else 255
                    value = ink[:7] + f"{round(original_alpha * alpha):02x}"
                ink = self.color(value)
                if not ink:
                    continue
                if paint_type == "fill":
                    child["fill"] = ink
                    continue
                sw = numeric(attrs.get("stroke-width"), 1)
                if attrs.get("vector-effect") != "non-scaling-stroke":
                    sw *= factor
                child["stroke"] = {"color": ink, "width": rounded(sw)}
                for key_name, attr_name, allowed in (("cap", "stroke-linecap", ("butt", "round", "square")),
                                                     ("join", "stroke-linejoin", ("miter", "round", "bevel"))):
                    if attrs.get(attr_name) in allowed:
                        child["stroke"][key_name] = attrs[attr_name]
                dash = numbers(attrs.get("stroke-dasharray", ""))
                if 0 < len(dash) <= schema.MAX_DASH and any(length > 0 for length in dash):
                    child["stroke"]["dash"] = [rounded(length * factor) for length in dash]
            if "fill" not in child and "stroke" not in child:
                continue
            for effect in ("clip-path", "mask"):
                if attrs.get(effect, "none") != "none":
                    self.losses[f"SVG {effect}"] += 1
            if attrs.get("fill-rule") == "evenodd":
                self.losses["SVG even-odd fill"] += 1
            opacity(child, numeric(attrs.get("opacity"), 1))
            children.append(child)
        if children:
            return {**position, "type": "frame", "title": item.get("label") or "Vector graphic",
                    "children": children}
        return None

    def primitive(self, item, origin, key, label):
        kind = item["kind"]
        position = box(item, origin)
        if kind == "text":
            node = self.text(item, position)
        elif kind == "rect":
            parts = rect_paints(self, item)
            if len(parts) == 1:
                node = {**position, "type": "rect", "title": label, **parts[0]}
            elif parts:
                children = [
                    {**position, **part, "x": 0, "y": 0, "type": "rect", "title": "Border" if i else "Surface",
                     "id": identify("surface", f"{key}:{i}")}
                    for i, part in enumerate(parts)
                ]
                node = {**position, "type": "frame", "title": label, "children": children}
            else:
                node = None
        elif kind == "svg":
            node = self.vector(item, position, key)
        elif kind == "image":
            asset = image_asset(item)
            if asset is None:
                self.losses["image bytes unavailable or unsupported"] += 1
                return None
            digest = hashlib.sha256(asset.data).hexdigest()
            self.assets[digest] = asset
            fit = item.get("objectFit")
            if fit not in ("cover", "contain", "fill"):
                fit = "contain" if fit in ("none", "scale-down") else "fill"
            if item.get("objectPosition") not in (None, "50% 50%", "center"):
                self.losses["non-centered image position"] += 1
            canvas = item.get("sourceKind") == "canvas"
            node = {**position, "type": "image", "title": item.get("alt") or "Image", "asset": digest, "fit": fit}
            if canvas:
                node["meta"] = {"source": "canvas"}
        else:
            self.losses[f"primitive {kind}"] += 1
            return None
        if node:
            node["id"] = identify(kind, key)
            node["title"] = node.get("title", label)[:200]
            opacity(node, item.get("opacity", 1))
        return node


SEMANTIC_TAGS = {
    "article": "Article", "aside": "Sidebar", "button": "Button", "dialog": "Dialog",
    "fieldset": "Fieldset", "footer": "Footer", "form": "Form", "header": "Header",
    "input": "Input", "label": "Field label", "li": "List item", "main": "Main content",
    "nav": "Navigation", "section": "Section", "select": "Select", "table": "Table",
    "tbody": "Table body", "td": "Table cell", "textarea": "Text area",
    "th": "Table header", "thead": "Table headings", "tr": "Table row",
}
SEMANTIC_CLASSES = re.compile(
    r"(?:^|-)(?:sidebar|sidenav|panel|card|modal|header|footer|toolbar|tabs|list|chart|shell|row|field|dialog)(?:$|-)"
)
SHELL_CLASSES = re.compile(r"(?:^|-)(?:sidebar|sidenav|nav|shell|toolbar|header|footer)(?:$|-)")


def class_title(value):
    return re.sub(r"[_-]+", " ", value).strip().capitalize()


def group_kind(group):
    tag, role = group["tag"], group["role"]
    classes = [name for name in group["classes"] if SEMANTIC_CLASSES.search(name)]
    shell = (tag in ("aside", "header", "nav", "footer") or role == "navigation"
             or any(SHELL_CLASSES.search(name) for name in group["classes"]))
    if group.get("label"):
        return group["label"], shell
    if tag in SEMANTIC_TAGS:
        return SEMANTIC_TAGS[tag], shell
    if tag == "a":
        return "Link", shell
    if role and role not in ("none", "presentation"):
        return class_title(role), shell
    if classes:
        return class_title(classes[0]), shell
    return None, shell


def clip_node(node, item, origin, key):
    clip = item.get("clip")
    if not clip:
        return node
    if (item["x"] >= clip["x"] - 0.02 and item["y"] >= clip["y"] - 0.02
            and item["x"] + item["width"] <= clip["x"] + clip["width"] + 0.02
            and item["y"] + item["height"] <= clip["y"] + clip["height"] + 0.02):
        return node
    # Use the actual intersection so the editable clip frame remains close
    # to its child when the browser clips by a distant ancestor.
    x, y = max(item["x"], clip["x"]), max(item["y"], clip["y"])
    right = min(item["x"] + item["width"], clip["x"] + clip["width"])
    bottom = min(item["y"] + item["height"], clip["y"] + clip["height"])
    if x >= right or y >= bottom:
        return None
    node["x"], node["y"] = rounded(node["x"] + origin[0] - x), rounded(node["y"] + origin[1] - y)
    return {
        "id": identify("clip", key), "type": "frame", "title": node["title"],
        "x": rounded(x - origin[0]), "y": rounded(y - origin[1]),
        "w": rounded(right - x), "h": rounded(bottom - y), "clip": True, "children": [node],
    }


@dataclass
class Screen:
    route: dict
    page: dict
    shells: set
    hrefs: dict
    losses: dict


def convert_screen(route, capture, tokens, mode, assets):
    scene = capture.get("scene", capture)
    if capture.get("mode", mode) != mode:
        raise ValueError(f"{route['slug']}: capture mode differs from the requested document mode")
    captured_route = capture.get("route") or {}
    if captured_route.get("slug", route["slug"]) != route["slug"]:
        raise ValueError(f"{route['slug']}: fixture records a different route")
    converter = Converter(tokens, mode, assets)
    groups = {group["id"]: group for group in scene["groups"]}
    semantic = {key: group_kind(group) for key, group in groups.items()}
    title = route["title"][:200]
    route_key = route["slug"]
    root = {
        "id": identify("screen-" + route_key, route_key), "type": "frame", "title": title,
        "x": 0, "y": 0, "w": scene["width"], "h": scene["height"], "clip": True, "children": [],
        "meta": {
            "path": route["path"], "pattern": route.get("pattern", route["path"]),
            "source": route.get("source", ""), "capture_mode": mode,
            "scenario": route.get("scenario", "fixture"),
        },
    }
    # Preserve Chromium paint order even if a later primitive revisits a
    # semantic ancestor after leaving it. Only group contiguous paint runs.
    stack = [(None, root, (0, 0))]
    shells, hrefs = set(), {}
    for i, item in enumerate(scene["primitives"]):
        group_id = item.get("groupId")
        if (i < 2 and item["kind"] == "rect" and groups.get(group_id, {}).get("tag") in ("html", "body")
                and item["x"] == item["y"] == 0 and item["width"] >= root["w"]):
            background = converter.color(item.get("fill"), "canvas")
            if background and item.get("opacity", 1) == 1:
                root["fill"] = background
                if not any(edge["width"] for edge in item.get("borders", [])) and not any(item.get("radii", [])):
                    continue
        chain = []
        while group_id in groups:
            if semantic[group_id][0]:
                chain.append(group_id)
            group_id = groups[group_id].get("parentId")
        chain.reverse()
        common = 0
        while common < len(chain) and common + 1 < len(stack) and chain[common] == stack[common + 1][0]:
            common += 1
        stack = stack[:common + 1]
        for group_id in chain[common:]:
            group = groups[group_id]
            name, shell = semantic[group_id]
            frame_id = identify(name, f"{route_key}:group:{group_id}:{i}")
            frame = {**box(group, stack[-1][2]), "id": frame_id, "type": "frame",
                     "title": name[:200], "children": []}
            stack[-1][1]["children"].append(frame)
            stack.append((group_id, frame, (group["x"], group["y"])))
            if shell:
                shells.add(frame_id)
            if group.get("href"):
                hrefs[frame_id] = group["href"]
        _, parent, origin = stack[-1]
        key = f"{route_key}:primitive:{i}"
        label = "Surface" if item["kind"] == "rect" else item["kind"].title()
        node = converter.primitive(item, origin, key, label)
        if node and (node := clip_node(node, item, origin, key)):
            parent["children"].append(node)

    def tidy(frame):
        frame["children"] = [
            node for node in frame["children"]
            if node["type"] != "frame" or tidy(node)
        ]
        if frame["title"] in {"Link", "Button", "Input", "Field label", "List item", "Table cell"}:
            label = next((node["text"].strip() for node in nodes(frame) if node["type"] == "text"), "")
            if label:
                frame["title"] = f"{frame['title']} · {label[:80]}"
        return frame["children"]

    tidy(root)
    losses = Counter(scene.get("unsupported", {})) + converter.losses
    return Screen(route, {"id": identify("page-" + route_key, route_key), "title": title, "children": [root]},
                  shells, hrefs, dict(sorted(losses.items())))


def shape_key(node, root=True):
    shape = {k: v for k, v in node.items() if k not in {"id", "children"}}
    if root:
        shape.pop("x", None)
        shape.pop("y", None)
    if "children" in node:
        shape["children"] = [shape_key(child, root=False) for child in node["children"]]
    return shape


def reusable_shells(doc, candidates):
    by_shape = defaultdict(list)
    for page in doc["pages"]:
        for node in nodes(page):
            if node["id"] in candidates:
                by_shape[encoded(shape_key(node))].append(node)

    reusable = {}
    for key, matches in by_shape.items():
        # A trivial wrapper alone cannot offset storing a component definition.
        if len(matches) < 2 or sum(1 for _ in nodes(matches[0])) < 3:
            continue
        component_id = identify("shared-" + matches[0]["title"], key.decode())
        for node in matches:
            reusable[node["id"]] = component_id

    def replace(node):
        component_id = reusable.get(node["id"])
        if component_id and len(doc["components"]) < schema.MAX_COMPONENTS:
            if component_id not in doc["components"]:
                component = deepcopy(node)
                for i, child in enumerate(nodes(component)):
                    child["id"] = identify("component-layer", f"{component_id}:{i}") if i else component_id
                component.pop("x", None)
                component.pop("y", None)
                doc["components"][component_id] = component
            instance = {key: node[key] for key in ("id", "title", "x", "y")}
            return {**instance, "type": "instance", "of": component_id}
        if "children" in node:
            node["children"] = [replace(child) for child in node["children"]]
        return node

    for page in doc["pages"]:
        page["children"] = [replace(frame) for frame in page["children"]]


def document_for(title, screens, tokens, mode):
    doc = schema.new_document(title)
    doc.update(tokens=tokens, modes=[mode, "light" if mode == "dark" else "dark"],
               pages=[deepcopy(screen.page) for screen in screens])
    hrefs = {key: href for screen in screens for key, href in screen.hrefs.items()}
    first_screen = {}
    for screen in screens:
        first_screen.setdefault(screen.route["path"], screen.page["children"][0]["id"])
    links = 0
    # URL-backed navigation remains recognizable on the layer even when its
    # destination lives in another Designer document.
    for page in doc["pages"]:
        for node in nodes(page):
            if href := hrefs.get(node["id"]):
                node["meta"] = {"href": href[:500]}
                target = first_screen.get(href) or first_screen.get(href.split("#", 1)[0])
                if target and target != page["children"][0]["id"] and links < schema.MAX_LINKS:
                    node["link_to"] = {"target": target}
                    links += 1
    # Only turn a subtree into a shared component if links and behavior stay
    # unchanged. Cross-document navigation is retained as route provenance.
    candidates = set().union(*(screen.shells for screen in screens))
    reusable_shells(doc, candidates)
    return doc


def partition(group, screens, tokens, mode):
    partitions = []
    current = []
    title = f"Agent Platform site — {group}"
    for screen in screens:
        candidate = [*current, screen]
        doc = document_for(title, candidate, tokens, mode)
        try:
            schema.validate_document(doc)
        except UiDesignerError as exc:
            limit_error = exc.code == "UD-EXPANSION" or (exc.issues and all(issue.code == "UD-LIMIT" for issue in exc.issues))
            if not current or not limit_error:
                raise ValueError(f"{screen.route['slug']}: {exc}") from exc
            partitions.append(current)
            current = [screen]
        else:
            current = candidate
    if current:
        partitions.append(current)
    for i, entries in enumerate(partitions, 1):
        suffix = f" ({i})" if len(partitions) > 1 else ""
        slug_suffix = f"-{i}" if len(partitions) > 1 else ""
        yield f"agent-platform-site-{schema.slugify(group)}{slug_suffix}", document_for(title + suffix, entries, tokens, mode), entries


def index_document(entries, aliases, tokens, mode):
    doc = schema.new_document("Agent Platform site — Index")
    doc.update(tokens=tokens, modes=[mode, "light" if mode == "dark" else "dark"])
    namespace = "site-index"

    def label(text, x, y, w, h, *, muted=False, size=16, weight=400, mono=False):
        return {
            "id": identify("index-label", f"{namespace}:{x}:{y}:{text}"),
            "type": "text", "title": text[:120], "text": text, "x": x, "y": y, "w": w, "h": h,
            "vertical_align": "center",
            "style": {"family": "JetBrains Mono" if mono else "Inter", "size": size, "weight": weight},
            "color": f"$color.{PREFIX}text{'-muted' if muted else ''}",
        }

    def page(title, rows, subtitle, references=None):
        nonlocal namespace
        namespace = title
        children = [label(title, 28, 18, 1124, 40, size=30, weight=700),
                    label(subtitle, 28, 66, 1124, 32, muted=True, size=14)]
        for i, (name, route) in enumerate(rows):
            y = 122 + i * 30
            if references:
                children.append({
                    "id": identify("index-entry", name), "type": "frame", "title": name,
                    "x": 28, "y": y, "w": 1124, "h": 28,
                    "link_to": {"target": identify("index-frame", name)},
                    "meta": {"document_slug": references[name]},
                    "children": [label(name, 0, 0, 560, 28),
                                 label(route, 584, 0, 540, 28, muted=True, size=13, mono=True)],
                })
            else:
                children.extend((label(name, 28, y, 560, 28),
                                 label(route, 612, y, 540, 28, muted=True, size=13, mono=True)))
        return {
            "id": identify("index-page", title), "title": title, "children": [{
                "id": identify("index-frame", title), "type": "frame", "title": title,
                "w": 1180, "h": 146 + 30 * len(rows), "fill": f"$color.{PREFIX}canvas", "children": children,
            }],
        }

    references = {item["title"].removeprefix("Agent Platform site — "): item["slug"] for item in entries}
    doc["pages"] = [page("Agent Platform site", [
        (item["title"].removeprefix("Agent Platform site — "), f"{item['slug']}  ·  {len(item['routes'])} screens")
        for item in entries
    ], f"{sum(len(item['routes']) for item in entries)} fixture screens · {len(entries)} documents · {mode} capture",
       references)]
    for entry in entries:
        doc["pages"].append(page(entry["title"].removeprefix("Agent Platform site — "),
                                 [(route["title"], route["path"]) for route in entry["routes"]],
                                 entry["slug"]))
    if aliases:
        paths = {route["slug"]: route["path"] for item in entries for route in item["routes"]}
        for start in range(0, len(aliases), 36):
            suffix = f" ({start // 36 + 1})" if len(aliases) > 36 else ""
            doc["pages"].append(page(f"Route aliases{suffix}", [
                (alias["path"], paths.get(alias["canonical"], alias["canonical"]))
                for alias in aliases[start:start + 36]
            ], "Redirects and alternate paths with a canonical destination"))
    return doc


def write_document(output, slug, doc, screens, mode, assets, svg_dir):
    used = {sha for _, sha in schema.image_assets(doc)}
    schema.validate_document(doc, known_assets=used.intersection(assets))
    resolved = layout_document(doc, mode)
    render_assets = {sha: assets[sha].render_info() for sha in used}
    asset_bytes = {sha: assets[sha].data for sha in used}
    for page in doc["pages"]:
        svg = paint(resolved, page["id"], assets=render_assets, asset_bytes=asset_bytes)
        if svg_dir:
            (svg_dir / f"{slug}--{page['id']}.svg").write_text(svg.svg)
    data = encoded(doc)
    file = f"{slug}.json"
    (output / file).write_bytes(data)
    return {
        "slug": slug, "file": file, "title": doc["title"], "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": len(doc["pages"]), "nodes": len(schema.index_nodes(doc)) - len(doc["pages"]),
        "expandedNodes": resolved["stats"]["nodes"], "components": len(doc["components"]),
        "assets": sorted(used),
        "routes": [{
            **screen.route, "pageId": screen.page["id"], "frameId": screen.page["children"][0]["id"],
            "unsupported": screen.losses,
        } for screen in screens],
    }


def build(captures, routes, output, mode="dark", svg_dir=None):
    inventory = json.loads(routes.read_text())
    tokens = platform_tokens()
    assets = {}
    grouped = defaultdict(list)
    output.mkdir(parents=True, exist_ok=True)
    if svg_dir:
        svg_dir.mkdir(parents=True, exist_ok=True)
    for route in inventory["routes"]:
        capture = json.loads((captures / f"{route['slug']}.json").read_text())
        grouped[route["group"]].append(convert_screen(route, capture, tokens, mode, assets))
    documents = [
        write_document(output, slug, doc, screens, mode, assets, svg_dir)
        for group, screens in grouped.items()
        for slug, doc, screens in partition(group, screens, tokens, mode)
    ]
    index = write_document(output, "agent-platform-site-index",
                           index_document(documents, inventory.get("aliases", []), tokens, mode),
                           [], mode, assets, svg_dir)

    placed = {}
    for digest in sorted({sha for item in documents for sha in item["assets"]}):
        asset = assets[digest]
        file = f"assets/{digest}.{asset.extension}"
        (output / "assets").mkdir(exist_ok=True)
        (output / file).write_bytes(asset.data)
        placed[digest] = {"file": file, "mime": asset.mime, "bytes": len(asset.data),
                          "width": asset.width, "height": asset.height}

    manifest = {
        "version": 1, "captureMode": mode, "index": index, "documents": documents,
        "assets": placed, "aliases": inventory.get("aliases", []),
        "screenCount": sum(len(doc["routes"]) for doc in documents),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, required=True, help="Per-route JSON files named after the route slug")
    parser.add_argument("--routes", type=Path, required=True, help="Route inventory JSON")
    parser.add_argument("--output", type=Path, required=True, help="Native documents, assets and manifest")
    parser.add_argument("--mode", choices=("dark", "light"), default="dark", help="Theme used by the browser fixture")
    parser.add_argument("--svg-dir", type=Path, help="Optional render of every Designer page")
    args = parser.parse_args()
    manifest = build(args.captures, args.routes, args.output, args.mode, args.svg_dir)
    print(json.dumps({"screens": manifest["screenCount"], "documents": len(manifest["documents"]) + 1,
                      "assets": len(manifest["assets"]), "manifest": str(args.output / "manifest.json")}))


if __name__ == "__main__":
    main()
