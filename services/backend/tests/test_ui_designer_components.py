"""Native component sets: strict schemas and bounded, shared resolution."""

import copy
import pytest
from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.components import describe_instance, resolve_instance
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.errors import UiDesignerError


def component_document():
    return {
        "schema": 1,
        "components": {
            "arrow": {
                "id": "arrow",
                "type": "frame",
                "w": 10,
                "h": 10,
                "children": [{"id": "arrow-path", "type": "rect", "w": 10, "h": 10}],
            },
            "check": {
                "id": "check",
                "type": "frame",
                "w": 20,
                "h": 20,
                "children": [{"id": "check-path", "type": "rect", "w": 20, "h": 20}],
            },
            "normal": {
                "id": "normal",
                "type": "frame",
                "w": 100,
                "h": 30,
                "children": [
                    {
                        "id": "normal-label",
                        "type": "text",
                        "text": "Base",
                        "style": {"family": "Inter", "size": 14},
                    },
                    {"id": "normal-icon", "type": "instance", "of": "arrow"},
                ],
            },
            "hover": {
                "id": "hover",
                "type": "frame",
                "w": 110,
                "h": 35,
                "children": [
                    {
                        "id": "hover-label",
                        "type": "text",
                        "text": "Hover",
                        "style": {"family": "Inter", "size": 14},
                    },
                    {"id": "hover-icon", "type": "instance", "of": "arrow"},
                ],
            },
        },
        "component_sets": {
            "button": {
                "axes": {"state": ["normal", "hover"]},
                "properties": {
                    "label": {"type": "text", "default": "Play"},
                    "shown": {"type": "boolean", "default": True},
                    "icon": {
                        "type": "instance-swap",
                        "default": "arrow",
                        "allowed": ["arrow", "check"],
                    },
                },
                "variants": [
                    {
                        "when": {"state": state},
                        "of": state,
                        "bindings": {
                            "label": [state + "-label"],
                            "shown": [state + "-icon"],
                            "icon": [state + "-icon"],
                        },
                    }
                    for state in ["normal", "hover"]
                ],
            }
        },
        "pages": [
            {
                "id": "p",
                "children": [{"id": "button-one", "type": "instance", "of": "button"}],
            }
        ],
    }


def test_defaults_selection_binding_precedence_and_literal_text():
    doc = component_document()
    S.validate_document(doc)
    instance = doc["pages"][0]["children"][0]
    of, root = resolve_instance(doc, instance)
    assert of == "normal" and root["children"][0]["text"] == "Play"
    instance.update(
        variant={"state": "hover"},
        props={"label": "$color.missing", "shown": False, "icon": "check"},
        w=120,
        overrides={"hover-label.text": "Override"},
    )
    S.validate_document(doc)
    assert not list(S.iter_token_refs(doc))
    assert (
        S.resolve_tokens(doc)["pages"][0]["children"][0]["props"]["label"]
        == "$color.missing"
    )
    of, root = resolve_instance(doc, instance)
    assert of == "hover" and root["children"][0]["text"] == "$color.missing"
    assert (
        root["children"][1]["of"] == "check" and root["children"][1]["visible"] is False
    )
    assert doc["components"]["hover"]["children"][0]["text"] == "Hover"
    description = describe_instance(doc, instance)
    assert description["axes"] == {"state": ["normal", "hover"]}
    assert description["variant"] == {"state": "hover"}
    resolved = layout_document(doc)
    button = resolved["pages"][0]["children"][0]
    assert button["w"] == 120
    assert button["children"][0]["text"]["lines"][0]["text"] == "Override"
    assert button["children"][1]["visible"] is False


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["component_sets"]["button"].update(unknown=True),
        lambda d: d["component_sets"]["button"]["variants"].pop(),
        lambda d: d["component_sets"]["button"]["variants"].append(
            copy.deepcopy(d["component_sets"]["button"]["variants"][0])
        ),
        lambda d: d["component_sets"]["button"]["variants"][0]["bindings"].pop("label"),
        lambda d: d["component_sets"]["button"]["variants"][0]["bindings"].update(
            label=["normal"]
        ),
        lambda d: d["component_sets"]["button"]["variants"][0]["bindings"].update(
            label=["normal-icon"]
        ),
        lambda d: d["component_sets"]["button"]["properties"]["shown"].update(
            default="yes"
        ),
        lambda d: d["component_sets"]["button"]["properties"]["icon"].update(
            allowed=["button"]
        ),
        lambda d: d["pages"][0]["children"][0].update(variant={"state": "pressed"}),
        lambda d: d["pages"][0]["children"][0].update(props={"shown": "no"}),
        lambda d: d["pages"][0]["children"][0].update(props={"missing": "no"}),
        lambda d: d["pages"][0]["children"][0].update(props={"label": "x" * 4097}),
        lambda d: d["components"]["normal"]["children"][0].update(id=[]),
        lambda d: d["component_sets"]["button"]["properties"]["label"].update(type=[]),
    ],
)
def test_invalid_sets_are_closed_and_bounded(change):
    doc = component_document()
    change(doc)
    assert S.collect_issues(doc)


def test_properties_only_set_and_expansion_swaps_and_hidden_work():
    doc = component_document()
    definition = doc["component_sets"]["button"]
    definition["axes"] = {}
    definition["variants"] = definition["variants"][:1]
    definition["variants"][0]["when"] = {}
    S.validate_document(doc)
    doc["components"]["check"]["children"] = [
        {"id": f"large-{i}", "type": "rect", "w": 1, "h": 1} for i in range(40)
    ]
    doc["pages"][0]["children"][0]["props"] = {"shown": False}
    # Swap max is counted even when default arrow is small and the target is hidden.
    assert S.check_expansion(doc) == 43
    for n in doc["components"]["check"]["children"]:
        n["shadow"] = {"x": 1, "y": 1, "blur": 1, "color": "#000"}
    with pytest.raises(UiDesignerError, match="budget"):
        S.check_expansion(doc)


def test_cycles_and_legacy_overrides_on_all_swap_alternatives():
    doc = component_document()
    doc["components"]["check"]["children"] = [
        {"id": "cycle", "type": "instance", "of": "button"}
    ]
    with pytest.raises(UiDesignerError, match="cycle"):
        S.check_expansion(doc)
    doc = component_document()
    doc["components"]["normal"]["children"][1]["overrides"] = {
        "arrow-path.fill": "#f00"
    }
    assert any(i.code == "UD-OVERRIDE" for i in S.collect_issues(doc))


def test_global_set_ids_and_generated_ids_do_not_collide():
    doc = component_document()
    doc["pages"][0].pop("id")
    doc["pages"][0]["title"] = "button"
    normalized = S.normalize_document(doc)
    assert normalized["pages"][0]["id"] != "button"
    S.validate_document(normalized)


@pytest.mark.parametrize("bad", [None, True, 7, "wrong", []])
@pytest.mark.parametrize("field", ["axes", "variant"])
def test_malformed_selection_containers_are_validation_issues(field, bad):
    doc = component_document()
    if field == "axes":
        doc["component_sets"]["button"]["axes"] = bad
    else:
        doc["pages"][0]["children"][0]["variant"] = bad
    assert S.collect_issues(doc)
    with pytest.raises(UiDesignerError) as caught:
        S.validate_document(doc)
    assert caught.value.code == "UD-INVALID"
