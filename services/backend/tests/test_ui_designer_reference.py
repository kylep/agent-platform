"""GET /api/ui-designer/reference is generated from live constants and its examples are real."""
import json

from agentplatform.config import Settings
from agentplatform.ui_designer import ops, schema
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.reference import build_reference, example_document, example_ops

from .test_ui_designer_api import P

SETTINGS = Settings()


def _apply(doc, op):
    result = apply_ops(doc, [op], version=1, expected_version=1)
    schema.validate_document(result.doc)
    return result.doc


def test_reference_is_small_and_names_every_live_constant():
    text = build_reference(SETTINGS)
    assert len(text.encode()) < 6 * 1024
    for op in ops.OP_FIELDS:
        assert f"\n  {op}:" in text
    for words in (schema.NODE_TYPES, schema.TOKEN_GROUPS, schema.FONT_FAMILIES, schema.LAYOUT_KEYS,
                  schema.LAYOUT_DIRS, schema.SIZE_KEYWORDS):
        assert all(w in text for w in words)
    for keys in schema.TYPE_KEYS.values():
        assert all(k in text for k in keys)
    assert "no `name` key" in text


def test_every_example_applies_through_the_real_op_engine():
    examples = example_ops()
    with_node = _apply(example_document(), examples["insert"])
    assert _apply(with_node, examples["update"])
    assert _apply(with_node, examples["move"])
    assert _apply(with_node, examples["tokens"])
    _apply(example_document(), examples["platform_component"])
    text = build_reference(SETTINGS)
    for op in examples.values():
        assert json.dumps(op, separators=(",", ":")) in text


def test_platform_component_example_installs_tokens_and_modes():
    out = _apply(example_document(), example_ops()["platform_component"])
    assert set(out["modes"]) >= {"dark", "light"}
    assert any(n.startswith("ap-ui-v") for n in out["tokens"]["color"])


async def test_route_returns_text(admin_client):
    response = await admin_client.get(f"{P}/reference")
    assert response.status_code == 200, response.text
    assert response.json()["text"] == build_reference(SETTINGS)


async def test_catalog_index_and_full_modes(admin_client):
    # No key (the browser, an older web or broker build): the full manifest.
    default = (await admin_client.get(f"{P}/component-library")).json()
    assert "limitations" in default["components"][0]
    index = (await admin_client.get(f"{P}/component-library", params={"detail": "index"})).json()
    assert index["components"][0].keys() == {"name", "category", "description", "import_path", "variants",
                                              "properties", "default_width", "default_height"}
    assert all(isinstance(v, str) for v in index["components"][0]["variants"])
    print("INDEX_BYTES", len(json.dumps(index, separators=(",", ":"))))
    one = (await admin_client.get(f"{P}/component-library", params={"name": "button"})).json()
    assert "fixture" in one["components"][0]["variants"][0]
    full = (await admin_client.get(f"{P}/component-library", params={"detail": "full"})).json()
    assert "limitations" in full["components"][0]
    forced = (await admin_client.get(f"{P}/component-library", params={"name": "button", "detail": "index"})).json()
    assert "fixture" not in forced["components"][0]
