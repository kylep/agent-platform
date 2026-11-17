import copy
import hashlib
import json

import pytest
from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path.parser import parse_path
from agentplatform.ui_designer import icons
from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.schema import (
    normalize_document,
    validate_document,
    index_nodes,
)
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.svg import paint


def document():
    return normalize_document(
        {
            "schema": 1,
            "tokens": {},
            "components": {},
            "pages": [
                {
                    "id": "page",
                    "children": [
                        {
                            "id": "canvas",
                            "type": "frame",
                            "w": 200,
                            "h": 200,
                            "children": [],
                        }
                    ],
                }
            ],
        }
    )


def operation(**extra):
    return {
        "op": "icon",
        "icon": "arrow-right",
        "revision": 1,
        "parent": "canvas",
        "id": "inserted",
        **extra,
    }


def apply(doc, ops):
    return apply_ops(doc, ops, version=1, expected_version=1).doc


def test_catalog_integrity_bounds_copy_search_and_cursor():
    raw = (icons._DATA / "catalog.json").read_bytes()
    manifest = json.loads((icons._DATA / "manifest.json").read_text())
    assert (
        len(raw) < 4 << 20
        and hashlib.sha256(raw).hexdigest() == manifest["catalog_sha256"]
    )
    first = icons.search_icons(limit=100)
    assert first["total"] == 2053
    ids = []
    page = first
    while True:
        ids.extend(i["id"] for i in page["icons"])
        if not page["next_cursor"]:
            break
        page = icons.search_icons(limit=100, cursor=page["next_cursor"])
    assert ids == sorted(set(ids)) and len(ids) == 2053
    assert not {"figma", "airplay", "blender"} & set(ids)
    assert "alarm-clock" in [i["id"] for i in icons.search_icons("morning")["icons"]]
    first["source"]["name"] = "bad"
    assert icons.search_icons()["source"]["name"] == "Lucide"
    with pytest.raises(UiDesignerError):
        icons.search_icons("other", cursor=first["next_cursor"])
    assert "Cole Bemis" in (icons._DATA / "LICENSE-Lucide.txt").read_text()


def test_every_catalog_path_is_valid_bounded_and_open_paths_stay_open():
    from agentplatform.ui_designer.vector_geometry import validate_path

    data, _ = icons._catalog()
    for icon in data["icons"]:
        assert len(icon["paths"]) <= 32
        for path in icon["paths"]:
            validate_path(path["d"], max_segments=256)
    arrow = icons.get_icon("arrow-right")
    assert not any("Z" in p["d"] or "z" in p["d"] for p in arrow["paths"])
    assert any(p["fill"] for p in icons.get_icon("chart-scatter")["paths"])
    arrow["paths"].clear()
    assert icons.get_icon("arrow-right")["paths"]


@pytest.mark.parametrize("size", [16, 24, 128])
def test_native_generation_scales_actual_coordinates_and_stroke_and_preserves_paints(
    size,
):
    doc = document()
    original = copy.deepcopy(doc)
    plan = icons.plan_icon(doc, operation(size=size, color="#aBc", x=3, y=4))
    out = apply(doc, plan["ops"])
    assert doc == original
    validate_document(out)
    component = next(iter(out["components"].values()))
    assert component["w"] == component["h"] == size
    for source, child in zip(
        icons.get_icon("arrow-right")["paths"], component["children"]
    ):
        a = BoundsPen(None)
        b = BoundsPen(None)
        parse_path(source["d"], a)
        parse_path(child["d"], b)
        assert b.bounds == pytest.approx(tuple(v * size / 24 for v in a.bounds))
        assert child["stroke"]["width"] == pytest.approx(2 * size / 24)
        assert child["stroke"]["color"] == "#aabbcc" and "fill" not in child
    assert len(index_nodes(out)) == len(set(index_nodes(out)))
    assert apply(out, plan["undo_ops"]) == doc
    filled = apply(doc, icons.plan_icon(doc, operation(icon="chart-scatter"))["ops"])
    assert any(
        child.get("fill") == "#000000"
        for child in next(iter(filled["components"].values()))["children"]
    )


def test_canonical_reuse_local_edit_collision_and_global_ids():
    doc = document()
    first = icons.plan_icon(doc, operation())
    out = apply(doc, first["ops"])
    second = icons.plan_icon(out, operation(id="another"))
    assert len(second["ops"]) == 1 and second["ops"][0]["node"]["type"] == "instance"
    assert apply(apply(out, second["ops"]), second["undo_ops"]) == out
    cid = next(iter(out["components"]))
    out["components"][cid]["children"][0]["stroke"]["color"] = "#ff0000"
    with pytest.raises(UiDesignerError, match="local edits"):
        icons.plan_icon(out, operation(id="another"))
    collision = document()
    collision["pages"][0]["children"].append(
        {"id": cid, "type": "rect", "w": 2, "h": 2}
    )
    with pytest.raises(UiDesignerError, match="collide"):
        icons.plan_icon(collision, operation())
    assert len(cid) <= 48 and all(
        len(n["id"]) <= 48 for n in first["ops"][0]["node"]["children"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"revision": True},
        {"revision": 2},
        {"size": True},
        {"size": 15},
        {"size": 129},
        {"stroke_width": 0},
        {"stroke_width": float("nan")},
        {"color": "url(x)"},
        {"color": "$color.x"},
        {"id": "canvas"},
        {"parent": "nope"},
        {"index": True},
        {"x": float("inf")},
        {"unexpected": 1},
    ],
)
def test_invalid_options(extra):
    with pytest.raises(UiDesignerError):
        icons.plan_icon(document(), operation(**extra))


def test_native_preview_renders_stroked_open_contours_without_closing_triangle():
    import io
    import resvg_py
    from PIL import Image

    doc = apply(
        document(),
        icons.plan_icon(document(), operation(size=128, color="#ff0000"))["ops"],
    )
    rendered = paint(layout_document(doc), "inserted")
    # Paint API is deliberately exercised so source-style stroke defaults are real.
    svg = rendered.svg if hasattr(rendered, "svg") else rendered
    image = Image.open(io.BytesIO(resvg_py.svg_to_bytes(svg_string=svg)))
    assert image.width >= 128 and image.height >= 128
    assert image.convert("RGBA").getbbox()


def test_converter_primitives_open_contours_and_unsupported_paints():
    import runpy
    import xml.etree.ElementTree as ET
    from pathlib import Path

    convert = runpy.run_path(
        str(Path(__file__).parents[3] / "scripts/ui_designer/generate_icons.py")
    )["convert"]
    for xml, expected in [
        ('<rect x="2" y="3" width="10" height="8" rx="2"/>', (2, 3, 12, 11)),
        ('<circle cx="12" cy="12" r="8"/>', (4, 4, 20, 20)),
        ('<ellipse cx="12" cy="12" rx="8" ry="4"/>', (4, 8, 20, 16)),
        ('<polygon points="1,2 3,4 5,6"/>', (1, 2, 5, 6)),
    ]:
        path = convert(ET.fromstring(xml))
        pen = BoundsPen(None)
        parse_path(path["d"], pen)
        assert pen.bounds == pytest.approx(expected, abs=1e-6) and path["d"].endswith(
            "Z"
        )
    assert "Z" not in convert(ET.fromstring('<polyline points="1,2 3,4"/>'))["d"]
    assert "Z" not in convert(ET.fromstring('<line x1="1" y1="2" x2="3" y2="4"/>'))["d"]
    for xml in [
        '<path d="M0 0L1 1" opacity=".5"/>',
        '<path d="M0 0L1 1" stroke="none"/>',
        '<circle r="2" fill="url(x)"/>',
        '<use href="https://example.com"/>',
    ]:
        with pytest.raises(ValueError):
            convert(ET.fromstring(xml))


@pytest.mark.parametrize(
    "q,limit,cursor",
    [
        (None, 40, None),
        ("\ud800", 40, None),
        ("x" * 129, 40, None),
        ("", True, None),
        ("", 101, None),
        ("", 40, "%%%"),
        ("", 40, "x" * 257),
    ],
)
def test_bad_catalog_queries_are_structured(q, limit, cursor):
    with pytest.raises(UiDesignerError):
        icons.search_icons(q, limit, cursor)


def test_catalog_search_prefers_exact_and_prefix_names():
    matches = icons.search_icons("camera")["icons"]
    assert matches[0]["id"] == "camera"
    prefix = [i["id"] for i in matches if i["id"].startswith("camera")]
    assert [i["id"] for i in matches[: len(prefix)]] == prefix
