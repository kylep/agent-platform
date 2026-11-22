"""Pinned offline icon catalog and native, reversible component generation."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path.parser import parse_path
from .errors import UiDesignerError, invalid, issue
from .schema import ID_RE, index_nodes

REVISION = 1
_DATA = Path(__file__).with_name("icon_data")


def _fail(message, field="$"):
    return invalid([issue("UD-VALUE", field, message)])


@lru_cache(maxsize=1)
def _catalog():
    data = json.loads((_DATA / "catalog.json").read_text())
    return data, {icon["id"]: icon for icon in data["icons"]}


def _revision(revision):
    if type(revision) is not int or revision != REVISION:
        raise UiDesignerError("UD-NOT-FOUND", "Icon catalog revision not found.")


def _summary(icon):
    return {
        key: copy.deepcopy(icon[key]) for key in ("id", "name", "tags", "categories")
    }


def search_icons(q="", limit=40, cursor=None):
    try:
        valid_query = isinstance(q, str) and len(q.encode("utf-8")) <= 128
    except UnicodeError:
        valid_query = False
    if not valid_query:
        raise _fail("Search query is limited to 128 UTF-8 bytes.", "$.q")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise _fail("Search limit must be 1..100.", "$.limit")
    query = " ".join(q.casefold().split())
    scope = hashlib.sha256(query.encode()).hexdigest()[:16]
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 256:
                raise ValueError()
            revision, digest, offset = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            )
            if (
                type(revision) is not int
                or revision != REVISION
                or digest != scope
                or type(offset) is not int
                or offset < 0
            ):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError):
            raise _fail(
                "Cursor does not match this catalog/query.", "$.cursor"
            ) from None
    data, _ = _catalog()
    terms = query.split()
    matches = [
        icon
        for icon in data["icons"]
        if all(
            term
            in " ".join(
                [icon["id"], icon["name"], *icon["tags"], *icon["categories"]]
            ).casefold()
            for term in terms
        )
    ]
    if query:
        matches.sort(
            key=lambda icon: (
                0
                if query in (icon["id"], icon["name"].casefold())
                else 1
                if icon["id"].startswith(query.replace(" ", "-"))
                else 2,
                icon["id"],
            )
        )
    if offset > len(matches):
        raise _fail("Cursor is outside this result.", "$.cursor")
    end = offset + limit
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps([REVISION, scope, end], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
        if end < len(matches)
        else None
    )
    return {
        "revision": REVISION,
        "source": copy.deepcopy(data["source"]),
        "icons": [_summary(i) for i in matches[offset:end]],
        "total": len(matches),
        "next_cursor": next_cursor,
    }


def get_icon(icon_id, revision=REVISION):
    _revision(revision)
    data, icons = _catalog()
    if not isinstance(icon_id, str) or icon_id not in icons:
        raise UiDesignerError("UD-NOT-FOUND", "Icon not found in the pinned catalog.")
    icon = icons[icon_id]
    return {
        **_summary(icon),
        "revision": REVISION,
        "source": copy.deepcopy(data["source"]),
        "view_box": [0, 0, 24, 24],
        "paths": copy.deepcopy(icon["paths"]),
        "d": " ".join(p["d"] for p in icon["paths"]),
    }


def _number(value, low, high, field):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise _fail(f"{field} must be a finite number in{low}..{high}.", f"$.{field}")
    return float(value)


def _scaled_path(d, scale):
    pen = SVGPathPen(None, ntos=lambda n: format(float(n), ".8g"))
    parse_path(d, TransformPen(pen, (scale, 0, 0, scale, 0, 0)))
    return pen.getCommands()


def plan_icon(doc, op):
    """Return ordinary literal ops and a compact inverse; never mutate the input."""
    required = {"op", "icon", "revision", "parent", "id"}
    allowed = required | {"index", "size", "color", "stroke_width", "x", "y"}
    if (
        not isinstance(op, dict)
        or not required <= op.keys()
        or op.keys() - allowed
        or op.get("op") != "icon"
    ):
        raise _fail("Expected a closed icon op with icon, revision, parent and id.")
    icon = get_icon(op["icon"], op["revision"])
    refs = index_nodes(doc)
    taken = set(refs) | set(doc.get("component_sets", {}))
    identifier = op["id"]
    parent = refs.get(op["parent"]) if isinstance(op["parent"], str) else None
    if (
        not isinstance(identifier, str)
        or not ID_RE.fullmatch(identifier)
        or identifier in taken
    ):
        raise _fail("Icon instance id must be a free native identifier.", "$.id")
    if (
        parent is None
        or parent.in_component
        or (parent.kind != "page" and parent.node.get("type") != "frame")
    ):
        raise _fail(
            "Insert icons into an authored page or frame outside component definitions.",
            "$.parent",
        )
    index = op.get("index", len(parent.node.get("children", [])))
    if type(index) is not int or not 0 <= index <= len(parent.node.get("children", [])):
        raise _fail("Icon insertion index is outside the parent children.", "$.index")
    size = _number(op.get("size", 24), 16, 128, "size")
    stroke = _number(op.get("stroke_width", 2), 0.5, 4, "stroke_width")
    color = op.get("color", "#000000")
    if not isinstance(color, str) or not re.fullmatch(
        r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)?", color
    ):
        raise _fail(
            "Icon color must be a literal #rgb, #rrggbb or #rrggbbaa.", "$.color"
        )
    color = color.lower()
    if len(color) == 4:
        color = "#" + "".join(c * 2 for c in color[1:])
    semantics = {
        "revision": REVISION,
        "icon": icon["id"],
        "size": size,
        "color": color,
        "stroke_width": stroke,
    }
    digest = hashlib.sha256(
        json.dumps(semantics, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    cid = "icon-" + icon["id"][:16].rstrip("-") + "-" + digest
    children = []
    for i, path in enumerate(icon["paths"]):
        child = {
            "id": f"{cid}-p{i}",
            "type": "path",
            "w": size,
            "h": size,
            "d": _scaled_path(path["d"], size / 24),
            "stroke": {
                "color": color,
                "width": stroke * size / 24,
                "cap": "round",
                "join": "round",
            },
        }
        if path["fill"]:
            child["fill"] = color
        children.append(child)
    canonical = {
        "id": cid,
        "type": "frame",
        "title": icon["name"],
        "w": size,
        "h": size,
        "children": children,
        "meta": {"icon": {**semantics, "source": icon["source"]}},
    }
    existing = doc.get("components", {}).get(cid)
    if existing is not None and existing != canonical:
        raise _fail(
            "Generated component has local edits; restore or rename it before reuse.",
            "$.icon",
        )
    if existing is None and ({cid, *[child["id"] for child in children]} & taken):
        raise _fail(
            "Generated icon identifiers collide with existing authored nodes.", "$.icon"
        )
    placement = {
        key: _number(op[key], -100000, 100000, key) for key in ("x", "y") if key in op
    }
    if existing is None:
        node = {**copy.deepcopy(canonical), "id": identifier, **placement}
        ops = [
            {"op": "insert", "parent": op["parent"], "index": index, "node": node},
            {"op": "define_component", "from": identifier, "id": cid},
        ]
        undo = [{"op": "remove", "ids": [identifier, cid]}]
    else:
        node = {
            "id": identifier,
            "type": "instance",
            "of": cid,
            "title": icon["name"],
            "w": size,
            "h": size,
            **placement,
        }
        ops = [{"op": "insert", "parent": op["parent"], "index": index, "node": node}]
        undo = [{"op": "remove", "ids": [identifier]}]
    if len(json.dumps(undo, separators=(",", ":")).encode()) > 8192:
        raise _fail("Icon inverse exceeds 8KiB.")
    # Existing validators enforce global IDs, component/node/expansion/document budgets.
    from .ops import apply_ops

    apply_ops(doc, ops, version=0, expected_version=0)
    return {"ops": ops, "undo_ops": undo, "warnings": []}
