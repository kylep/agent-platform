"""Prototype links are inert metadata with whole-document target validation."""

import copy
import pytest
from agentplatform.ui_designer import schema as S
from agentplatform.ui_designer.layout import layout_document
from agentplatform.ui_designer.errors import UiDesignerError


def prototype_document():
    return {
        "schema": 1,
        "components": {
            "card": {
                "id": "card",
                "type": "frame",
                "w": 50,
                "h": 40,
                "link_to": {"target": "end"},
                "children": [
                    {
                        "id": "card-text",
                        "type": "text",
                        "text": "Next",
                        "link_to": {
                            "target": "home",
                            "trigger": "hover",
                            "transition": "dissolve",
                        },
                        "style": {"family": "Inter", "size": 14},
                    }
                ],
            }
        },
        "pages": [
            {
                "id": "page",
                "children": [
                    {
                        "id": "home",
                        "type": "frame",
                        "w": 200,
                        "h": 100,
                        "children": [
                            {"id": "button", "type": "instance", "of": "card"}
                        ],
                    },
                    {
                        "id": "end",
                        "type": "frame",
                        "x": 300,
                        "w": 200,
                        "h": 100,
                        "children": [],
                    },
                ],
            }
        ],
    }


def test_inherited_shared_and_legacy_links_are_resolved_in_precedence_order():
    doc = prototype_document()
    S.validate_document(doc)
    tree = layout_document(doc)
    button = tree["pages"][0]["children"][0]["children"][0]
    assert button["link_to"] == {"target": "end"}
    assert button["children"][0]["link_to"]["trigger"] == "hover"
    instance = doc["pages"][0]["children"][0]["children"][0]
    instance.update(
        link_to={"target": "home"},
        overrides={
            "link_to": {"target": "end", "trigger": "after-delay"},
            "card-text.link_to": {
                "target": "end",
                "transition": "slide",
                "duration_ms": 350,
            },
        },
    )
    S.validate_document(doc)
    result = layout_document(doc)["pages"][0]["children"][0]["children"][0]
    assert result["link_to"] == {"target": "end", "trigger": "after-delay"}
    assert result["children"][0]["link_to"]["duration_ms"] == 350
    assert not list(S.iter_token_refs(doc))
    assert S.resolve_tokens(doc) == doc


@pytest.mark.parametrize(
    "link",
    [
        None,
        True,
        [],
        "https://example.test",
        {},
        {"target": "https://example.test"},
        {"target": "end", "url": "https://example.test"},
        {"target": "end", "trigger": "click"},
        {"target": "end", "trigger": []},
        {"target": "end", "transition": "script"},
        {"target": "end", "duration_ms": True},
        {"target": "end", "duration_ms": 1.5},
        {"target": "end", "duration_ms": -1},
        {"target": "end", "duration_ms": 2001},
        {"target": "end", "delay_ms": 1000},
        {"target": "end", "trigger": "after-delay", "delay_ms": 99},
        {"target": "end", "trigger": "after-delay", "delay_ms": 60001},
        {"target": "end", "trigger": "after-delay", "delay_ms": False},
        {"target": "$color.nope"},
        {"target": "card"},
        {"target": "button/card-text"},
        {"target": "button"},
        {"target": "card-text"},
        {"target": []},
    ],
)
def test_malformed_links_and_non_authored_frame_targets_rejected(link):
    doc = prototype_document()
    doc["components"]["card"]["link_to"] = link
    assert S.collect_issues(doc)
    with pytest.raises(UiDesignerError) as caught:
        S.validate_document(doc)
    assert caught.value.code == "UD-INVALID"


@pytest.mark.parametrize(
    "link",
    [
        {"target": "end"},
        {"target": "end", "trigger": "after-delay"},
        {
            "target": "end",
            "trigger": "after-delay",
            "delay_ms": 60000,
            "duration_ms": 0,
        },
        {"target": "end", "transition": "slide", "duration_ms": 2000},
    ],
)
def test_optional_defaults_are_not_written_into_authored_or_resolved_link(link):
    doc = prototype_document()
    doc["components"]["card"]["link_to"] = link
    before = copy.deepcopy(doc)
    S.validate_document(doc)
    tree = layout_document(doc)
    assert tree["pages"][0]["children"][0]["children"][0]["link_to"] == link
    assert before == doc


def test_only_actual_authored_links_count_toward_link_budget(monkeypatch):
    doc = prototype_document()
    # Two actual component links + one actual instance override. Repeated
    # validation of that override is not another authored interaction.
    instance = doc["pages"][0]["children"][0]["children"][0]
    instance["overrides"] = {"link_to": {"target": "home"}}
    monkeypatch.setattr(S, "MAX_LINKS", 3)
    S.validate_document(doc)
    instance["link_to"] = {"target": "end"}
    assert any(i.code == "UD-LIMIT" for i in S.collect_issues(doc))


def test_removing_target_invalidates_default_component_links_and_overrides():
    doc = prototype_document()
    doc["pages"][0]["children"].pop()
    assert any(i.path.endswith(".target") for i in S.collect_issues(doc))
    doc["components"]["card"].pop("link_to")
    S.validate_document(doc)
