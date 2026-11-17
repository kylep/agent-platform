import copy
import hashlib
import io
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.DefaultTable import DefaultTable
import app as renderer
from test_app import client  # noqa: F401
from agentplatform.ui_designer.font_validation import validate_font
from agentplatform.ui_designer.fonts import document_fonts, get_face, face_by_key
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.errors import UiDesignerError


def font_bytes(
    family="Test Accessible",
    advance=500,
    license="This font is licensed under the SIL Open Font License, Version 1.1.",
    fs_type=0,
):
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A", "space"])
    fb.setupCharacterMap({65: "A", 32: "space"})
    glyphs = {}
    for name in [".notdef", "A", "space"]:
        pen = TTGlyphPen(None)
        if name == "A":
            pen.moveTo((50, 0))
            pen.lineTo((advance - 50, 0))
            pen.lineTo((advance / 2, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (advance, 0) for name in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": family,
            "styleName": "Regular",
            "uniqueFontIdentifier": family,
            "fullName": family,
            "psName": family.replace(" ", ""),
            "version": "Version1.0",
            "licenseDescription": license,
            "licenseInfoURL": "https://openfontlicense.org",
        }
    )
    fb.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=400,
        fsType=fs_type,
    )
    fb.setupPost()
    fb.setupMaxp()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


def document(data):
    metadata = validate_font(data)
    return {
        "schema": 1,
        "fonts": [{k: metadata[k] for k in ("family", "weight", "sha256")}],
        "pages": [
            {
                "id": "p",
                "children": [
                    {
                        "id": "label",
                        "type": "text",
                        "text": "AAAA",
                        "style": {
                            "family": metadata["family"],
                            "size": 20,
                            "weight": 400,
                        },
                        "color": "#000000",
                    }
                ],
            }
        ],
    }


def test_static_ofl_font_validation_and_document_scoped_sha_faces():
    first, second = font_bytes(advance=500), font_bytes(advance=900)
    a, b = validate_font(first), validate_font(second)
    assert (
        a["family"] == "Test Accessible"
        and a["license"] == "OFL-1.1"
        and a["glyphs"] == 3
    )
    assert a["sha256"] != b["sha256"]
    widths = []
    keys = []
    for raw in [first, second, first]:
        doc = document(raw)
        with document_fonts(doc, lambda sha: raw):
            face = get_face("Test Accessible", 400)
            keys.append(face.key)
            assert face_by_key(face.key) is face
            tree = layout_document(doc)
            widths.append(tree["pages"][0]["children"][0]["w"])
            assert face.key in tree["glyphs"]
        with pytest.raises(KeyError):
            face_by_key(face.key)
    assert widths[0] == widths[2] < widths[1] and keys[0] == keys[2] != keys[1]


@pytest.mark.parametrize(
    "raw", [b"bad", b"ttcf" + b"x" * 30, b"wOFF" + b"x" * 30, b"x" * ((1 << 20) + 1)]
)
def test_bad_containers_and_size_are_rejected(raw):
    with pytest.raises(UiDesignerError):
        validate_font(raw)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"license": "All rights reserved"},
        {"fs_type": 2},
        {"family": "Inter"},
        {"family": "Bad/Name"},
    ],
)
def test_license_embedding_and_shadowing_rejected(kwargs):
    with pytest.raises(UiDesignerError):
        validate_font(font_bytes(**kwargs))


def test_variable_color_and_recursive_composite_rejected():
    for tag in ["fvar", "COLR"]:
        font = TTFont(io.BytesIO(font_bytes()))
        table = DefaultTable(tag)
        table.data = b"bad"
        font[tag] = table
        out = io.BytesIO()
        font.save(out)
        with pytest.raises(UiDesignerError, match="variable, color"):
            validate_font(out.getvalue())
    from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphComponent

    font = TTFont(io.BytesIO(font_bytes()), recalcBBoxes=False)
    glyph = Glyph()
    glyph.numberOfContours = -1
    component = GlyphComponent()
    component.glyphName = "A"
    component.x = component.y = 0
    component.flags = 0
    glyph.components = [component]
    glyph.xMin = 0
    glyph.yMin = 0
    glyph.xMax = 500
    glyph.yMax = 700
    font["glyf"]["A"] = glyph
    out = io.BytesIO()
    font.save(out)
    with pytest.raises(UiDesignerError, match="cyclic"):
        validate_font(out.getvalue())


async def test_http_validates_uploads_negotiates_and_renders_custom_glyphs(client):
    data = font_bytes()
    meta = validate_font(data)
    doc = document(data)
    response = await client.post("/font-validate", data=data)
    assert (
        response.status == 200 and (await response.json())["sha256"] == meta["sha256"]
    )
    response = await client.post("/render", json={"doc": doc, "nodes": ["label"]})
    assert response.status == 409 and (await response.json())["missing_fonts"] == [
        meta["sha256"]
    ]
    assert (await client.put("/fonts/" + "b" * 64, data=data)).status == 422
    assert (await client.put("/fonts/" + meta["sha256"], data=data)).status == 200
    response = await client.post("/render", json={"doc": doc, "nodes": ["label"]})
    assert response.status == 200, await response.text()
    response = await client.post("/svg", json={"doc": doc, "node": "label"})
    assert response.status == 200 and "<text" not in (await response.json())["svg"]
    response = await client.post("/resolved", json={"doc": doc})
    assert (
        response.status == 200
        and (await response.json())["tree"]["pages"][0]["children"][0]["w"] == 40
    )


def test_worker_never_opens_font_paths_from_unvalidated_ids(tmp_path):
    doc = document(font_bytes())
    doc["fonts"][0]["sha256"] = "../../etc/passwd"
    result = renderer._run_job(
        renderer.Config(asset_dir=str(tmp_path)),
        "render",
        renderer._render_req({"doc": doc}),
    )
    assert not result["ok"] and result["code"] == "UD-INVALID"


def test_diff_registry_is_separate_for_same_family_different_sha(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    one, two = font_bytes(advance=500), font_bytes(advance=900)
    for raw in (one, two):
        assert renderer.job_font_put(
            cfg, {"sha": hashlib.sha256(raw).hexdigest(), "data": raw}
        )["ok"]
    result = renderer._run_job(
        cfg,
        "diff",
        renderer._diff_req(
            {"before_doc": document(one), "after_doc": document(two), "node": "label"}
        ),
    )
    assert result["ok"] and result["changed_pixels"] > 0


def test_all_document_render_paths_use_scoped_fonts(tmp_path):
    cfg = renderer.Config(asset_dir=str(tmp_path))
    raw = font_bytes()
    doc = document(raw)
    renderer.job_font_put(cfg, {"sha": hashlib.sha256(raw).hexdigest(), "data": raw})
    for kind, req in [
        ("lint", renderer._lint_req({"doc": doc})),
        (
            "export",
            renderer._export_req({"doc": doc, "node": "label", "format": "svg"}),
        ),
    ]:
        out = renderer._run_job(cfg, kind, req)
        assert out["ok"], out
    # Motion resolves custom text before making its trusted PNG layers.
    label = doc["pages"][0]["children"][0]
    doc["pages"][0]["children"] = [
        {"id": "a", "type": "frame", "w": 100, "h": 40, "children": [label]},
        {"id": "b", "type": "frame", "w": 100, "h": 40, "children": []},
    ]
    out = renderer._run_job(
        cfg,
        "motion_preview",
        renderer._motion_req({"doc": doc, "from_node": "a", "to_node": "b"}),
    )
    assert out["ok"], out


def test_custom_font_html_uses_exact_outlined_svg_not_browser_font(tmp_path):
    import re

    cfg = renderer.Config(asset_dir=str(tmp_path))
    raw = font_bytes()
    doc = document(raw)
    renderer.job_font_put(cfg, {"sha": hashlib.sha256(raw).hexdigest(), "data": raw})
    vector = renderer._run_job(
        cfg,
        "export",
        renderer._export_req({"doc": doc, "node": "label", "format": "svg"}),
    )
    html = renderer._run_job(
        cfg,
        "export",
        renderer._export_req({"doc": doc, "node": "label", "format": "html"}),
    )
    assert vector["ok"] and html["ok"]
    embedded = re.search(rb"<svg\b.*?</svg>", html["data"], re.S).group()
    assert embedded == vector["data"] and b"<text" not in embedded
    assert any("SVG fallback" in n for n in html["warnings"])
