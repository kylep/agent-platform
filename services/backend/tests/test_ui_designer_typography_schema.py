"""Additive node typography remains closed, literal and reversible through ops."""

import copy
import pytest
from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.ops import apply_ops
from agentplatform.ui_designer.errors import UiDesignerError


def document():
    return {
        "schema": 1,
        "components": {
            "title": {
                "id": "title",
                "type": "text",
                "text": "Original ß and Σ",
                "style": {"family": "Inter", "size": 16},
            }
        },
        "pages": [
            {
                "id": "p",
                "children": [
                    {"id": "instance", "type": "instance", "of": "title"},
                    {
                        "id": "text",
                        "type": "text",
                        "w": 100,
                        "h": 50,
                        "text": [
                            {"text": "Rich "},
                            {"text": "Text", "style": {"weight": 700}},
                        ],
                    },
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "field,values",
    [
        ("align", ["left", "center", "right", "justify"]),
        ("vertical_align", ["top", "center", "bottom"]),
        ("text_case", ["none", "uppercase", "lowercase", "capitalize"]),
        ("decoration", ["none", "underline", "line-through"]),
    ],
)
def test_text_properties_accept_each_closed_value_and_legacy_overrides(field, values):
    for value in values:
        doc = document()
        doc["pages"][0]["children"][1][field] = value
        doc["pages"][0]["children"][0]["overrides"] = {field: value}
        S.validate_document(doc)


@pytest.mark.parametrize("field", ["vertical_align", "text_case", "decoration"])
@pytest.mark.parametrize("bad", [None, True, 1, [], {}, "unknown", "$type.heading"])
def test_text_property_invalid_types_and_values_are_structured(field, bad):
    doc = document()
    doc["components"]["title"][field] = bad
    with pytest.raises(UiDesignerError) as caught:
        S.validate_document(doc)
    assert caught.value.code == "UD-INVALID"


def test_typography_is_node_only_not_rich_run_or_type_token_style():
    for location in ("frame", "run", "style"):
        doc = document()
        text = doc["pages"][0]["children"][1]
        if location == "frame":
            doc["pages"][0]["children"].append(
                {"id": "frame", "type": "frame", "decoration": "underline"}
            )
        elif location == "run":
            text["text"][0]["decoration"] = "underline"
        else:
            text["style"] = {"family": "Inter", "size": 16, "text_case": "uppercase"}
        assert any(issue.code == "UD-UNKNOWN-KEY" for issue in S.collect_issues(doc))


def test_ops_set_clear_and_undo_typography_preserve_authored_rich_text():
    doc = document()
    original = copy.deepcopy(doc)
    values = {
        "align": "justify",
        "vertical_align": "center",
        "text_case": "uppercase",
        "decoration": "underline",
    }
    changed = apply_ops(
        doc,
        [{"op": "update", "id": "text", "set": values}],
        version=1,
        expected_version=1,
    ).doc
    assert (
        changed["pages"][0]["children"][1]["text"]
        == original["pages"][0]["children"][1]["text"]
    )
    restored = apply_ops(
        changed,
        [{"op": "update", "id": "text", "set": {key: None for key in values}}],
        version=2,
        expected_version=2,
    ).doc
    assert restored == original and doc == original


def test_typography_override_target_must_be_text():
    doc = document()
    doc["components"]["shape"] = {"id": "shape", "type": "rect"}
    doc["pages"][0]["children"][0].update(
        of="shape", overrides={"decoration": "underline"}
    )
    assert any(issue.code == "UD-OVERRIDE" for issue in S.collect_issues(doc))
