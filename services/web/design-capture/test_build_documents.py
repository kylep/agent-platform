from collections import Counter
from hashlib import sha256
import json

from build_documents import build, document_for, identify, normalize_color, partition, convert_screen
from agentplatform.ui_designer import schema
from agentplatform.ui_designer.components import nodes
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.platform_catalog import platform_tokens


def fixture(slug):
    rect = {"x": 12, "y": 12, "width": 80, "height": 24}
    route = {"slug": slug, "title": slug.title(), "path": "/" + slug, "group": "Test", "source": "console"}
    scene = {
        "width": 320, "height": 200, "unsupported": {},
        "groups": [
            {"id": 1, "parentId": None, "tag": "header", "label": "Toolbar", "role": "", "classes": [], **rect},
            {"id": 2, "parentId": 1, "tag": "button", "label": "", "role": "", "classes": [], **rect},
            {"id": 3, "parentId": None, "tag": "a", "label": "", "role": "", "classes": [], **rect,
             "href": "/first"},
        ],
        "primitives": [
            {"kind": "rect", "groupId": 2, **rect, "fill": "#222a38", "radii": [4, 4, 4, 4], "borders": []},
            {"kind": "text", "groupId": 2, **rect, "text": "Edit", "color": "#e6e9ef", "fontFamily": "Inter",
             "fontSize": 14, "fontWeight": 400, "lineHeight": 20},
            {"kind": "svg", "groupId": 2, "x": 12, "y": 12, "width": 16, "height": 16, "viewBox": "0 0 8 8",
             "shapes": [{"tag": "path", "attributes": {"d": "M1 2L3 4", "stroke": "rgb(230, 233, 239)",
                                                        "stroke-width": "1", "fill": "none"}}]},
            {"kind": "text", "groupId": 3, **rect, "y": 55, "text": "First", "color": "#36e2c4",
             "fontFamily": "Inter", "fontSize": 13},
        ],
    }
    return route, scene


def test_round_trip_build_and_svg(tmp_path):
    captures, outputs, svg = (tmp_path / part for part in ["captures", "outputs", "svg"])
    captures.mkdir()
    rows = []
    for slug in ["first", "second"]:
        route, scene = fixture(slug)
        if slug == "first":
            route["path"] = "/first/#/detail"
        scene["groups"][2]["href"] = "/first/#/detail"
        (captures / f"{slug}.json").write_text(json.dumps({"scene": scene}))
        rows.append(route)
    inventory = tmp_path / "routes.json"
    inventory.write_text(json.dumps({"version": 1, "routes": rows}))
    manifest = build(captures, inventory, outputs, svg_dir=svg)
    assert manifest["screenCount"] == 2
    assert manifest["captureMode"] == "dark"
    group, = manifest["documents"]
    assert group["components"] == 1
    content = (outputs / group["file"]).read_bytes()
    assert group["sha256"] == sha256(content).hexdigest()
    doc = json.loads(content)
    schema.validate_document(doc, known_assets=set())
    assert group["expandedNodes"] == schema.check_expansion(doc)
    first, second = doc["pages"]
    first_root = first["children"][0]
    assert first_root["meta"]["path"] == "/first/#/detail"
    assert first_root["children"][0]["type"] == "instance"
    first_link, = [n for n in nodes(first) if n.get("meta", {}).get("href")]
    second_link, = [n for n in nodes(second) if n.get("meta", {}).get("href")]
    assert "link_to" not in first_link
    assert second_link["link_to"]["target"] == first_root["id"]
    renders = sorted(svg.glob("*.svg"))
    assert len(renders) == 4
    assert any("M0.0 0.0 4.0 4.0" in path.read_text() for path in renders)
    resolved = layout_document(doc)
    all_layers = [node for page in resolved["pages"] for node in nodes(page) if "type" in node]
    assert Counter(node["type"] for node in all_layers)["path"] == 2


def test_split_at_page_limit(monkeypatch):
    monkeypatch.setattr(schema, "MAX_PAGES", 1)
    tokens = platform_tokens()
    screens = [convert_screen(*fixture(slug), tokens, "dark", {}) for slug in ("first", "second")]
    batches = list(partition("Test", screens, tokens, "dark"))
    assert [slug for slug, _, _ in batches] == ["agent-platform-site-test-1", "agent-platform-site-test-2"]
    for _, doc, entries in batches:
        schema.validate_document(doc)
        assert len(entries) == len(doc["pages"]) == 1


def test_ambiguous_colors_stay_literal():
    tokens = platform_tokens()
    screen = convert_screen(*fixture("second"), tokens, "dark", {})
    doc = document_for("Test", [screen], tokens, "dark")
    text_layers = {node["text"]: node for node in nodes(doc["pages"][0]) if node.get("type") == "text"}
    assert text_layers["First"]["color"] == "#36e2c4"
    assert text_layers["Edit"]["color"] == "$color.ap-ui-v1-text"
    assert text_layers["Edit"]["style"] == "$type.ap-ui-v1-body"
    assert normalize_color("#abc") == "#aabbcc"
    assert identify("a very long id" * 5, "key") == identify("a very long id" * 5, "key")


def test_image_svg_is_native_asset(tmp_path):
    from base64 import b64encode

    route, scene = fixture("first")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="8">'
           '<rect width="16" height="8" fill="red"/></svg>')
    primitive = {"kind": "image", "x": 10, "y": 50, "width": 32, "height": 16, "alt": "Fixture asset"}
    scene["primitives"].append({
        **primitive, "dataUrl": "data:image/svg+xml;base64," + b64encode(svg.encode()).decode(),
    })
    captures, output = tmp_path / "captures", tmp_path / "out"
    captures.mkdir()
    (captures / "first.json").write_text(json.dumps(scene))
    inventory = tmp_path / "routes.json"
    inventory.write_text(json.dumps({"version": 1, "routes": [route]}))
    manifest = build(captures, inventory, output)
    digest, = manifest["assets"]
    asset = manifest["assets"][digest]
    assert asset["mime"] == "image/png"
    assert asset["width"] == 32 and asset["height"] == 16
    assert sha256((output / asset["file"]).read_bytes()).hexdigest() == digest
    group, = manifest["documents"]
    document = json.loads((output / group["file"]).read_text())
    image, = (layer for layer in nodes(document["pages"][0]) if layer.get("type") == "image")
    assert image["asset"] == digest
