"""The built-in catalog covers the public design-system contract."""

import json
from pathlib import Path

import pytest

from agentplatform.ui_designer import platform_catalog as catalog
from agentplatform.ui_designer.components import resolve_instance
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.schema import lint, validate_document
from agentplatform.ui_designer.svg import paint

REPO = Path(__file__).resolve().parents[3]


def test_public_components_and_stories_are_represented():
    coverage_path = REPO / "packages/ui/story-coverage.json"
    if not coverage_path.exists():
        pytest.skip("Design-system source is only available in a repository checkout.")
    coverage = {entry["name"]: entry for entry in json.loads(coverage_path.read_text())["components"]}
    components = {entry["name"]: entry for entry in catalog.catalog_manifest()["components"]}
    assert coverage.keys() == components.keys()
    for name, entry in components.items():
        assert entry["import_path"] == coverage[name]["import"]
        assert entry["story_id"] in {state["story"] for state in coverage[name]["states"]}


@pytest.mark.parametrize("component", catalog.catalog_manifest()["components"], ids=lambda item: item["name"])
def test_every_variant_is_a_native_scene_and_matches_its_manifest(component):
    for variant in component["variants"]:
        doc = catalog.component_document(component["name"], variant["id"])
        assert lint(doc) == []
        instance = doc["pages"][0]["children"][0]
        mapping = catalog.implementation_mapping(instance)
        assert mapping["props"] == variant["props"]
        assert mapping["fixture"] == variant["fixture"]
        assert mapping["variant"] == variant["id"]
        assert instance["variant"] == variant["selection"]
        for theme in ("dark", "light"):
            layout = layout_document(doc, theme)
            assert layout["warnings"] == []
            sample = paint(layout, "sample")
            assert sample.w == pytest.approx(variant["width"], abs=.001)
            assert sample.h == pytest.approx(variant["height"], abs=.001)
            assert 'data:image/' not in sample.svg


def test_all_families_can_coexist_in_one_document():
    doc = catalog.component_document("Button")
    for component in catalog.catalog_manifest()["components"]:
        name = component["name"]
        if name == "Button":
            continue
        _, roots, sets = catalog.get_definition(name)
        doc["components"].update(roots)
        doc["component_sets"].update(sets)
        doc["pages"][0]["children"].append(catalog.make_instance(name, id=name.lower()))
    validate_document(doc)


def test_text_override_changes_native_width_and_react_props():
    doc = catalog.component_document("Button", "secondary-sm")
    before = paint(layout_document(doc), "sample")
    instance = catalog.make_instance(
        "Button", "secondary-sm", id="sample", props={"children": "Review deployment"}, x=20, y=10
    )
    doc["pages"][0]["children"][0] = instance
    _, definition = resolve_instance(doc, instance)
    assert definition["children"][0]["text"] == "Review deployment"
    after = paint(layout_document(doc), "sample")
    assert after.w > before.w
    assert (after.x, after.y) == (20, 10)
    assert catalog.implementation_mapping(instance)["props"] == {
        "variant": "secondary", "size": "sm", "disabled": False, "children": "Review deployment"
    }


def test_native_edits_keep_canonical_catalog_content_isolated():
    doc = catalog.component_document("Banner")
    component, *_ = doc["components"].values()
    component["children"][0]["text"] = "Changed"
    doc["tokens"]["color"][catalog.PREFIX + "surface"]["dark"] = "#000"
    original = catalog.component_document("Banner")
    assert original["components"][component["id"]]["children"][0]["text"] != "Changed"
    assert original["tokens"]["color"][catalog.PREFIX + "surface"]["dark"] != "#000"


@pytest.mark.parametrize("props", [{"children": False}, {"disabled": True}])
def test_native_props_are_closed(props):
    with pytest.raises(ValueError):
        catalog.make_instance("Button", id="sample", props=props)
