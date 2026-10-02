"""App definition language: collections, views and typed/v2 pages (design 39)."""
import copy
import json

import pytest

from agentplatform.appdata import definitions as d
from agentplatform.appdata.errors import CODES, DefinitionError, json_path


# --- fixtures: the design's worked examples ---------------------------------

HABITS = {
    "collection": "habits",
    "fields": {
        "habit": {"type": "enum", "values": ["run", "read", "stretch"], "required": True},
        "day": {"type": "date", "required": True},
        "done": {"type": "bool", "required": True},
        "note": {"type": "string", "max": 500, "access": {"read": ["owner"]}},
    },
    "write_mode": "editable",
    "access": {"read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"],
               "delete": ["kyle"]},
    "rules": [{"kind": "unique", "fields": ["habit", "day"]}],
    "indexed": ["habit", "day"],
}

HABIT_VIEWS = [
    {"view": "recent", "collection": "habits",
     "fields": ["habit", "day", "done", "note"],
     "sort": [{"field": "day", "dir": "desc"}], "limit": 50, "paging": True},
    {"view": "done_12w", "collection": "habits",
     "filter": [{"field": "done", "op": "eq", "value": True},
                {"field": "day", "op": "within_last", "value": "12w"}],
     "aggregates": [{"fn": "count", "as": "days_done"}]},
    {"view": "by_habit", "collection": "habits",
     "params": {"habit": {"type": "string", "required": True}},
     "filter": [{"field": "habit", "op": "eq", "value": {"param": "habit"}}],
     "sort": [{"field": "day", "dir": "desc"}], "limit": 200},
]

HABIT_PAGE = {
    "page": "home", "renderer": "typed/v2", "title": "Habit log",
    "params": {"habit": {"type": "string"}},
    "blocks": [
        {"kind": "text", "style": "heading", "text": "Last twelve weeks"},
        {"kind": "metric", "label": "Days done", "view": "done_12w"},
        {"kind": "table", "view": "recent",
         "columns": [{"field": "day", "label": "Day", "format": "date"},
                     {"field": "habit"}, {"field": "done"}],
         "actions": ["log_habit", "mark_done", "delete_entry"]},
        {"kind": "table", "view": "by_habit",
         "params": {"habit": {"page_param": "habit"}}, "columns": [{"field": "day"}]},
        {"kind": "text", "style": "paragraph", "text": "Open tickets",
         "link": {"path": "/tickets"}},
    ],
    "actions": [
        {"name": "log_habit", "kind": "create", "collection": "habits", "label": "Log",
         "editable_fields": ["habit", "day", "done", "note"]},
        {"name": "mark_done", "kind": "update", "collection": "habits",
         "presets": {"done": True}},
        {"name": "delete_entry", "kind": "delete", "collection": "habits"},
    ],
}

PREDICTIONS = {
    "collection": "predictions",
    "description": "What Kai expected Kyle to choose, before he chose.",
    "fields": {
        "scenario": {"type": "text", "max": 4000, "required": True},
        # A JSON list kept as text until list fields (Release 2).
        "alternatives": {"type": "text", "max": 4000, "required": True},
        "predicted_choice": {"type": "string", "max": 500, "required": True},
        "rationale": {"type": "text", "max": 8000},
        "confidence": {"type": "number", "min": 0, "max": 1, "required": True},
        "timing": {"type": "enum", "values": ["prospective", "retrospective"],
                   "required": True},
        "question_ref": {"type": "string", "max": 200},
    },
    "write_mode": "immutable",
    "access": {"read": ["owner", "kyle"], "create": ["owner"], "update": [],
               "delete": ["kyle"]},
    "writers": {"create": ["tool:judgment"]},
    "indexed": ["timing"],
}

FEEDBACK = {
    "collection": "feedback",
    "fields": {
        "prediction": {"type": "ref", "collection": "predictions", "on_delete": "restrict"},
        "kyle_words": {"type": "text", "max": 4000, "required": True},
        "source_ref": {"type": "string", "max": 200},
        "source_at": {"type": "datetime"},
        "outcome": {"type": "enum", "required": True, "values": [
            "supported", "contradicted", "mixed", "context_changed", "unresolved"]},
        "interpretation": {"type": "text", "max": 4000, "access": {"read": ["owner"]}},
        "confirmed": {"type": "bool"},
    },
    "access": {"read": ["owner", "kyle"], "create": ["owner", "kyle"],
               "update": ["owner", "kyle"], "delete": ["kyle"]},
    "rules": [{"kind": "writer", "field": "confirmed", "value": True, "writers": ["kyle"]},
              {"kind": "immutable_after_create", "fields": ["prediction", "source_at"]}],
    "indexed": ["prediction", "source_at"],
    "retention": {"max_records": 10000},
}

JUDGMENT_VIEWS = [
    {"view": "pending", "collection": "predictions",
     "fields": ["scenario", "predicted_choice", "confidence", "created_at"],
     "filter": [{"field": "timing", "op": "eq", "value": "prospective"}],
     "sort": [{"field": "created_at", "dir": "asc"}], "limit": 100, "paging": True},
    {"view": "prediction", "collection": "predictions",
     "params": {"id": {"type": "string", "required": True}},
     "filter": [{"field": "id", "op": "eq", "value": {"param": "id"}}], "limit": 1},
    {"view": "prediction_count", "collection": "predictions",
     "aggregates": [{"fn": "count", "as": "n"}]},
    {"view": "recent_feedback", "collection": "feedback",
     "filter": [{"field": "source_at", "op": "within_last", "value": "30d",
                 "anchor": "max(source_at)"},
                {"field": "outcome", "op": "in", "value": ["contradicted", "mixed"]},
                {"field": "kyle_words", "op": "contains", "value": "deadline"},
                {"field": "confirmed", "op": "is_null", "value": False}],
     "sort": [{"field": "source_at", "dir": "desc"}]},
]

JUDGMENT_PAGES = [
    {"page": "review", "renderer": "typed/v2", "title": "Judgment",
     "blocks": [
         {"kind": "metric", "label": "Predictions", "view": "prediction_count"},
         {"kind": "table", "view": "pending",
          "columns": [{"field": "scenario"}, {"field": "confidence", "format": "percent"}],
          "row_link": {"page": "prediction", "param": "id"}},
         {"kind": "table", "view": "recent_feedback",
          "columns": [{"field": "kyle_words"}, {"field": "outcome"}],
          "actions": ["confirm"]},
     ],
     "actions": [{"name": "confirm", "kind": "update", "collection": "feedback",
                  "label": "Confirm", "presets": {"confirmed": True},
                  "editable_fields": ["kyle_words"]}]},
    {"page": "prediction", "renderer": "typed/v2", "title": "Prediction",
     "params": {"id": {"type": "string", "required": True}},
     "blocks": [
         {"kind": "detail", "view": "prediction", "params": {"id": {"page_param": "id"}},
          "fields": ["scenario", "alternatives", "predicted_choice", "confidence",
                     "timing", "created_at", "author"],
          "actions": ["delete_prediction"]},
         {"kind": "text", "style": "paragraph", "text": "Back to the review",
          "link": {"page": "review"}},
     ],
     "actions": [{"name": "delete_prediction", "kind": "delete",
                  "collection": "predictions"}]},
]


def habit_bundle():
    return {"collections": [copy.deepcopy(HABITS)], "views": copy.deepcopy(HABIT_VIEWS),
            "pages": [copy.deepcopy(HABIT_PAGE)]}


def judgment_bundle():
    return {"collections": [copy.deepcopy(PREDICTIONS), copy.deepcopy(FEEDBACK)],
            "views": copy.deepcopy(JUDGMENT_VIEWS), "pages": copy.deepcopy(JUDGMENT_PAGES)}


def errors_of(fn, *args):
    with pytest.raises(DefinitionError) as caught:
        fn(*args)
    for item in caught.value.issues:
        assert item.code in CODES
        assert item.path.startswith("$")
        assert item.fix
    return [(item.code, item.path) for item in caught.value.issues]


def collection_errors(body):
    return errors_of(d.validate_collection, body)


def with_changes(base, **changes):
    body = copy.deepcopy(base)
    body.update(changes)
    return body


# --- valid fixtures ----------------------------------------------------------

def test_habit_log_bundle_is_valid():
    app = d.validate_app(habit_bundle())
    assert set(app.collections) == {"habits"}
    assert set(app.views) == {"recent", "done_12w", "by_habit"}
    assert set(app.pages) == {"home"}
    habits = app.collections["habits"]
    assert habits.write_mode == "editable"
    assert habits.fields["note"].access.read == ["owner"]
    assert d.index_columns(habits) == {"habit": "ix_text1", "day": "ix_time1"}


def test_judgment_predictions_bundle_is_valid():
    app = d.validate_app(judgment_bundle())
    predictions = app.collections["predictions"]
    assert predictions.write_mode == "immutable"
    assert predictions.writers.create == ["tool:judgment"]
    assert app.collections["feedback"].fields["prediction"].on_delete == "restrict"
    assert d.index_columns(app.collections["feedback"]) == {
        "prediction": "ix_text1", "source_at": "ix_time1"}
    assert app.views["recent_feedback"].filter[0].anchor == "max(source_at)"


def test_single_definitions_validate_without_a_bundle():
    assert d.validate_definition("collection", HABITS).name == "habits"
    assert d.validate_definition("view", HABIT_VIEWS[0]).name == "recent"
    assert d.validate_definition("page", HABIT_PAGE).name == "home"
    with pytest.raises(ValueError):
        d.validate_definition("trigger", {})


def test_collection_defaults_are_narrow():
    parsed = d.validate_collection({"collection": "notes",
                                    "fields": {"body": {"type": "text"}}})
    assert parsed.write_mode == "editable"
    assert parsed.access.read == ["owner", "kyle"]
    assert parsed.access.create == parsed.access.update == parsed.access.delete == ["owner"]
    assert parsed.fields["body"].max == 16000
    assert parsed.fields["body"].access is None
    assert parsed.rules == [] and parsed.indexed == [] and parsed.retention is None


def test_every_release_one_field_type_parses():
    body = {"collection": "kinds", "fields": {
        "s": {"type": "string", "min": 1, "max": 10},
        "t": {"type": "text"},
        "i": {"type": "int", "min": -5, "max": 5},
        "n": {"type": "number", "min": 0.5},
        "b": {"type": "bool"},
        "d": {"type": "date"},
        "dt": {"type": "datetime"},
        "e": {"type": "enum", "values": ["a"]},
        "r": {"type": "ref", "collection": "kinds", "on_delete": "unlink"},
        "u": {"type": "url", "link": True},
        "a": {"type": "artifact", "label": "Attachment", "description": "A file"},
    }}
    parsed = d.validate_collection(body)
    assert {name: f.type for name, f in parsed.fields.items()} == {
        "s": "string", "t": "text", "i": "int", "n": "number", "b": "bool", "d": "date",
        "dt": "datetime", "e": "enum", "r": "ref", "u": "url", "a": "artifact"}
    assert parsed.fields["r"].on_delete == "unlink"
    assert parsed.fields["u"].link is True


def test_principals_cover_agents_and_the_qa_login():
    parsed = d.validate_collection(with_changes(HABITS, access={
        "read": ["owner", "kyle", "agent:pai", "login:qa"], "create": ["agent:olu-2"]}))
    assert parsed.access.read[-1] == "login:qa"


def test_index_slots_map_by_type_and_order():
    body = {"collection": "results", "fields": {
        "run": {"type": "ref", "collection": "results"},
        "case": {"type": "string"},
        "duration": {"type": "number"},
        "at": {"type": "datetime"}},
        "indexed": ["run", "case", "at", "duration"]}
    assert d.index_columns(d.validate_collection(body)) == {
        "run": "ix_text1", "case": "ix_text2", "at": "ix_time1", "duration": "ix_num1"}


def test_retention_by_age():
    parsed = d.validate_collection(with_changes(HABITS, retention={"max_age": "90d"}))
    assert parsed.retention.max_age == "90d"
    assert d.retention_days(parsed.retention) == 90
    assert d.retention_days(d.Retention(max_age="12w")) == 84


# --- collection errors -------------------------------------------------------

@pytest.mark.parametrize("change, expected", [
    ({"surprise": 1}, ("JD-UNKNOWN-KEY", "$.surprise")),
    ({"collection": "Habits"}, ("JD-NAME", "$.collection")),
    ({"write_mode": "append"}, ("JD-VALUE", "$.write_mode")),
    ({"fields": {}}, ("JD-BOUNDS", "$.fields")),
    ({"fields": {"Bad Name": {"type": "string"}}}, ("JD-NAME", '$.fields["Bad Name"]')),
    ({"fields": {"id": {"type": "string"}}}, ("JD-FIELD-RESERVED", "$.fields.id")),
    ({"fields": {"via": {"type": "string"}}}, ("JD-FIELD-RESERVED", "$.fields.via")),
    ({"fields": {"x": {"type": "json"}}}, ("JD-FIELD-TYPE", "$.fields.x.type")),
    ({"fields": {"x": {"type": "object"}}}, ("JD-FIELD-TYPE", "$.fields.x.type")),
    ({"fields": {"x": {"required": True}}}, ("JD-MISSING", "$.fields.x.type")),
    ({"fields": {"x": "string"}}, ("JD-TYPE", "$.fields.x")),
    ({"fields": {"x": {"type": "string", "max": 1001}}}, ("JD-BOUNDS", "$.fields.x.max")),
    ({"fields": {"x": {"type": "text", "max": 16001}}}, ("JD-BOUNDS", "$.fields.x.max")),
    ({"fields": {"x": {"type": "string", "max": "10"}}}, ("JD-TYPE", "$.fields.x.max")),
    ({"fields": {"x": {"type": "string", "min": 20, "max": 10}}},
     ("JD-FIELD-BOUNDS", "$.fields.x.min")),
    ({"fields": {"x": {"type": "int", "min": 5, "max": 1}}}, ("JD-FIELD-BOUNDS", "$.fields.x.min")),
    ({"fields": {"x": {"type": "number", "min": 2.5, "max": 1}}},
     ("JD-FIELD-BOUNDS", "$.fields.x.min")),
    ({"fields": {"x": {"type": "int", "min": 1.5}}}, ("JD-TYPE", "$.fields.x.min")),
    ({"fields": {"x": {"type": "bool", "max": 1}}}, ("JD-UNKNOWN-KEY", "$.fields.x.max")),
    ({"fields": {"x": {"type": "date", "values": ["a"]}}}, ("JD-UNKNOWN-KEY", "$.fields.x.values")),
    ({"fields": {"x": {"type": "string", "link": True}}}, ("JD-UNKNOWN-KEY", "$.fields.x.link")),
    ({"fields": {"x": {"type": "string", "required": "yes"}}}, ("JD-TYPE", "$.fields.x.required")),
    ({"fields": {"x": {"type": "enum"}}}, ("JD-MISSING", "$.fields.x.values")),
    ({"fields": {"x": {"type": "enum", "values": []}}}, ("JD-BOUNDS", "$.fields.x.values")),
    ({"fields": {"x": {"type": "enum", "values": ["a", "a"]}}},
     ("JD-ENUM-VALUES", "$.fields.x.values")),
    ({"fields": {"x": {"type": "enum", "values": [""]}}}, ("JD-BOUNDS", "$.fields.x.values[0]")),
    ({"fields": {"x": {"type": "ref"}}}, ("JD-MISSING", "$.fields.x.collection")),
    ({"fields": {"x": {"type": "ref", "collection": "habits", "on_delete": "nullify"}}},
     ("JD-VALUE", "$.fields.x.on_delete")),
    ({"fields": {"x": {"type": "ref", "collection": "habits", "on_delete": "unlink",
                       "required": True}}},
     ("JD-REF-UNLINK-REQUIRED", "$.fields.x.on_delete")),
    ({"fields": {"x": {"type": "url", "max": 5000}}}, ("JD-BOUNDS", "$.fields.x.max")),
    ({"fields": {"x": {"type": "string", "label": "x" * 81}}}, ("JD-BOUNDS", "$.fields.x.label")),
])
def test_invalid_collection_shapes(change, expected):
    assert expected in collection_errors(with_changes(HABITS, **change))


def test_too_many_fields():
    fields = {f"f{i}": {"type": "bool"} for i in range(65)}
    assert ("JD-BOUNDS", "$.fields") in collection_errors({"collection": "c", "fields": fields})


@pytest.mark.parametrize("access, expected", [
    ({"read": ["everyone"]}, ("JD-PRINCIPAL", "$.access.read[0]")),
    ({"read": ["agent:"]}, ("JD-PRINCIPAL", "$.access.read[0]")),
    ({"read": ["agent:Pai"]}, ("JD-PRINCIPAL", "$.access.read[0]")),
    ({"read": ["admin"]}, ("JD-PRINCIPAL", "$.access.read[0]")),
    ({"create": ["tool:judgment"]}, ("JD-PRINCIPAL", "$.access.create[0]")),
    ({"create": ["login:qa"]}, ("JD-PRINCIPAL-VERB", "$.access.create[0]")),
    ({"delete": ["owner", "login:qa"]}, ("JD-PRINCIPAL-VERB", "$.access.delete[1]")),
    ({"read": ["owner", "owner"]}, ("JD-PRINCIPAL-DUPLICATE", "$.access.read[1]")),
    ({"read": "owner"}, ("JD-TYPE", "$.access.read")),
    ({"share": ["owner"]}, ("JD-UNKNOWN-KEY", "$.access.share")),
])
def test_invalid_collection_access(access, expected):
    assert expected in collection_errors(with_changes(HABITS, access=access))


@pytest.mark.parametrize("access, expected", [
    ({"delete": ["kyle"]}, ("JD-UNKNOWN-KEY", "$.fields.note.access.delete")),
    ({"update": ["login:qa"]}, ("JD-PRINCIPAL-VERB", "$.fields.note.access.update[0]")),
    ({"read": ["nobody"]}, ("JD-PRINCIPAL", "$.fields.note.access.read[0]")),
])
def test_invalid_field_access(access, expected):
    body = copy.deepcopy(HABITS)
    body["fields"]["note"]["access"] = access
    assert expected in collection_errors(body)


@pytest.mark.parametrize("writers, expected", [
    ({"create": ["kyle"]}, ("JD-WRITERS", "$.writers.create[0]")),
    ({"create": ["tool:Bad"]}, ("JD-WRITERS", "$.writers.create[0]")),
    ({"create": []}, ("JD-BOUNDS", "$.writers.create")),
    ({}, ("JD-WRITERS", "$.writers")),
    ({"read": ["tool:judgment"]}, ("JD-UNKNOWN-KEY", "$.writers.read")),
    ({"create": ["tool:judgment", "tool:judgment"]},
     ("JD-PRINCIPAL-DUPLICATE", "$.writers.create[1]")),
])
def test_invalid_tool_only_writers(writers, expected):
    assert expected in collection_errors(with_changes(PREDICTIONS, writers=writers))


def test_tool_only_writers_per_verb():
    parsed = d.validate_collection(with_changes(PREDICTIONS, writers={
        "create": ["tool:judgment"], "update": ["tool:judgment", "tool:tcms"],
        "delete": ["tool:judgment"]}))
    assert parsed.writers.update == ["tool:judgment", "tool:tcms"]


@pytest.mark.parametrize("rules, expected", [
    ([{"kind": "unique", "fields": ["missing"]}], ("JD-RULE-FIELD", "$.rules[0].fields[0]")),
    ([{"kind": "unique", "fields": ["id"]}], ("JD-RULE-FIELD", "$.rules[0].fields[0]")),
    ([{"kind": "unique", "fields": []}], ("JD-BOUNDS", "$.rules[0].fields")),
    ([{"kind": "unique", "fields": ["day", "day"]}], ("JD-RULE-FIELD", "$.rules[0].fields[1]")),
    ([{"kind": "unique", "fields": ["habit", "day"]},
      {"kind": "unique", "fields": ["day", "habit"]}], ("JD-RULE-DUPLICATE", "$.rules[1]")),
    ([{"kind": "unique", "fields": ["day"], "extra": 1}], ("JD-UNKNOWN-KEY", "$.rules[0].extra")),
    ([{"kind": "always", "fields": ["day"]}], ("JD-RULE-KIND", "$.rules[0].kind")),
    ([{"fields": ["day"]}], ("JD-MISSING", "$.rules[0].kind")),
    ([{"kind": "writer", "field": "nope", "writers": ["kyle"]}], ("JD-RULE-FIELD", "$.rules[0].field")),
    ([{"kind": "writer", "field": "done", "writers": []}], ("JD-BOUNDS", "$.rules[0].writers")),
    ([{"kind": "writer", "field": "done", "writers": ["login:qa"]}],
     ("JD-PRINCIPAL-VERB", "$.rules[0].writers[0]")),
    ([{"kind": "writer", "field": "done", "writers": ["ghost"]}],
     ("JD-PRINCIPAL", "$.rules[0].writers[0]")),
    ([{"kind": "writer", "field": "done", "value": "yes", "writers": ["kyle"]}],
     ("JD-RULE-VALUE", "$.rules[0].value")),
    ([{"kind": "writer", "field": "habit", "value": "swim", "writers": ["kyle"]}],
     ("JD-RULE-VALUE", "$.rules[0].value")),
    ([{"kind": "writer", "field": "day", "value": "2026-13-40", "writers": ["kyle"]}],
     ("JD-RULE-VALUE", "$.rules[0].value")),
    ([{"kind": "immutable_after_create", "fields": ["day", "ghost"]}],
     ("JD-RULE-FIELD", "$.rules[0].fields[1]")),
    ([{"kind": "required_when", "field": "note", "when": {"done": True}}],
     ("JD-REQUEST-PATH", "$.rules[0].kind")),
    ([{"kind": "lock", "lock": ["note"]}], ("JD-REQUEST-PATH", "$.rules[0].kind")),
])
def test_invalid_rules(rules, expected):
    assert expected in collection_errors(with_changes(HABITS, rules=rules))


def test_writer_rule_values_by_type():
    for field, value in (("habit", "run"), ("done", False), ("day", "2026-10-02"),
                         ("note", "fine")):
        d.validate_collection(with_changes(HABITS, rules=[
            {"kind": "writer", "field": field, "value": value, "writers": ["kyle"]}]))
    d.validate_collection(with_changes(HABITS, rules=[
        {"kind": "writer", "field": "done", "writers": ["kyle", "agent:kai", "owner"]}]))


def test_immutable_after_create_is_redundant_on_immutable_collections():
    body = with_changes(PREDICTIONS, rules=[
        {"kind": "immutable_after_create", "fields": ["scenario"]}])
    assert ("JD-RULE-REDUNDANT", "$.rules[0]") in collection_errors(body)


@pytest.mark.parametrize("indexed, expected", [
    (["ghost"], ("JD-INDEX-FIELD", "$.indexed[0]")),
    (["id"], ("JD-INDEX-FIELD", "$.indexed[0]")),
    (["done"], ("JD-INDEX-TYPE", "$.indexed[0]")),
    (["note", "habit", "day", "habit"], ("JD-INDEX-DUPLICATE", "$.indexed[3]")),
    (["habit", "note", "day", "habit", "done"], ("JD-BOUNDS", "$.indexed")),
])
def test_invalid_indexed_fields(indexed, expected):
    assert expected in collection_errors(with_changes(HABITS, indexed=indexed))


def test_index_slot_overflow_by_type():
    body = {"collection": "c", "fields": {
        "a": {"type": "string"}, "b": {"type": "enum", "values": ["x"]},
        "c": {"type": "ref", "collection": "c"}, "n": {"type": "int"},
        "m": {"type": "number"}, "t": {"type": "date"}, "u": {"type": "datetime"},
        "big": {"type": "text"}, "link": {"type": "url"}, "file": {"type": "artifact"}}}
    assert ("JD-INDEX-SLOTS", "$.indexed[2]") in collection_errors(
        with_changes(body, indexed=["a", "b", "c"]))
    assert ("JD-INDEX-SLOTS", "$.indexed[1]") in collection_errors(
        with_changes(body, indexed=["n", "m"]))
    assert ("JD-INDEX-SLOTS", "$.indexed[1]") in collection_errors(
        with_changes(body, indexed=["t", "u"]))
    for unindexable in ("big", "link", "file"):
        assert ("JD-INDEX-TYPE", "$.indexed[0]") in collection_errors(
            with_changes(body, indexed=[unindexable]))


@pytest.mark.parametrize("retention, expected", [
    ({}, ("JD-RETENTION", "$.retention")),
    ({"max_age": "90d", "max_records": 5}, ("JD-RETENTION", "$.retention")),
    ({"max_age": "forever"}, ("JD-FORMAT", "$.retention.max_age")),
    ({"max_age": "0d"}, ("JD-FORMAT", "$.retention.max_age")),
    ({"max_age": "10000d"}, ("JD-FORMAT", "$.retention.max_age")),
    ({"max_age": "5000d"}, ("JD-BOUNDS", "$.retention.max_age")),
    ({"max_age": "600w"}, ("JD-BOUNDS", "$.retention.max_age")),
    ({"max_records": 0}, ("JD-BOUNDS", "$.retention.max_records")),
    ({"max_records": 10_000_001}, ("JD-BOUNDS", "$.retention.max_records")),
    ({"keep": "all"}, ("JD-UNKNOWN-KEY", "$.retention.keep")),
])
def test_invalid_retention(retention, expected):
    assert expected in collection_errors(with_changes(HABITS, retention=retention))


@pytest.mark.parametrize("change, expected", [
    ({"write_mode": "versioned"}, ("JD-NOT-YET-R2", "$.write_mode")),
    ({"fields": {"tags": {"type": "list", "of": "string"}}}, ("JD-NOT-YET-R2", "$.fields.tags.type")),
    ({"fields": {"r": {"type": "ref", "collection": "habits", "on_delete": "cascade"}}},
     ("JD-NOT-YET-R2", "$.fields.r.on_delete")),
    ({"fields": {"r": {"type": "ref", "collection": "habits", "pin_version": True}}},
     ("JD-NOT-YET-R2", "$.fields.r.pin_version")),
    ({"fields": {"m": {"type": "message_ref"}}}, ("JD-REQUEST-PATH", "$.fields.m.type")),
])
def test_deferred_collection_features_name_their_release(change, expected):
    errors = collection_errors(with_changes(HABITS, **change))
    assert expected in errors
    # The deferred feature is the whole story; no generic shape noise beside it.
    assert all(code in ("JD-NOT-YET-R2", "JD-REQUEST-PATH") for code, _ in errors)


def test_deferred_error_message_says_not_available_yet():
    with pytest.raises(DefinitionError) as caught:
        d.validate_collection(with_changes(HABITS, write_mode="versioned"))
    assert "not available yet (Release 2)" in caught.value.issues[0].message


def test_errors_carry_value_and_fix_and_serialize():
    with pytest.raises(DefinitionError) as caught:
        d.validate_collection(with_changes(HABITS, fields={"x": {"type": "json"}}))
    found = caught.value.issues[0]
    assert found.value == "json"
    assert "string" in found.fix
    body = caught.value.as_dict()
    assert body["ok"] is False
    assert json.loads(json.dumps(body))["errors"][0]["code"] == "JD-FIELD-TYPE"


def test_all_issues_are_reported_together():
    body = with_changes(HABITS, surprise=1, write_mode="versioned",
                        access={"read": ["everyone"]})
    codes = {code for code, _ in collection_errors(body)}
    assert {"JD-UNKNOWN-KEY", "JD-NOT-YET-R2", "JD-PRINCIPAL"} <= codes


def test_strict_types_do_not_coerce():
    assert ("JD-TYPE", "$.fields.x.max") in collection_errors(
        with_changes(HABITS, fields={"x": {"type": "string", "max": 10.0}}))
    assert ("JD-TYPE", "$.fields.x.required") in collection_errors(
        with_changes(HABITS, fields={"x": {"type": "string", "required": 1}}))
    assert ("JD-TYPE", "$") in errors_of(d.validate_collection, ["not", "a", "dict"])


# --- view errors -------------------------------------------------------------

def view_errors(view, collections=(HABITS,)):
    bundle = {"collections": [copy.deepcopy(c) for c in collections], "views": [view],
              "pages": []}
    return errors_of(d.validate_app, bundle)


def habit_view(**changes):
    return with_changes({"view": "v", "collection": "habits"}, **changes)


def test_view_defaults():
    parsed = d.validate_view({"view": "all", "collection": "habits"})
    assert parsed.limit == 50 and parsed.paging is False
    assert parsed.filter == [] and parsed.sort == [] and parsed.aggregates == []
    assert parsed.is_count is False
    assert d.validate_view(HABIT_VIEWS[1]).is_count is True


@pytest.mark.parametrize("view, expected", [
    (habit_view(collection="ghosts"), ("JD-VIEW-COLLECTION", "$.views[0].collection")),
    (habit_view(fields=["ghost"]), ("JD-VIEW-FIELD", "$.views[0].fields[0]")),
    (habit_view(limit=201), ("JD-BOUNDS", "$.views[0].limit")),
    (habit_view(limit=0), ("JD-BOUNDS", "$.views[0].limit")),
    (habit_view(sort=[{"field": "ghost"}]), ("JD-VIEW-FIELD", "$.views[0].sort[0].field")),
    (habit_view(sort=[{"field": "day", "dir": "up"}]), ("JD-VALUE", "$.views[0].sort[0].dir")),
    (habit_view(sort=[{"field": "day"}, {"field": "day"}]),
     ("JD-SORT-DUPLICATE", "$.views[0].sort[1].field")),
    (habit_view(filter=[{"field": "ghost", "op": "eq", "value": 1}]),
     ("JD-VIEW-FIELD", "$.views[0].filter[0].field")),
    (habit_view(filter=[{"field": "day", "op": "like", "value": "x"}]),
     ("JD-FILTER-OP", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "day", "value": "x"}]),
     ("JD-MISSING", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "done", "op": "eq", "value": "yes"}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "habit", "op": "eq", "value": "swim"}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "day", "op": "gt", "value": "yesterday"}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "done", "op": "gt", "value": True}]),
     ("JD-FILTER-OP-TYPE", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "habit", "op": "lt", "value": "run"}]),
     ("JD-FILTER-OP-TYPE", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "habit", "op": "in", "value": []}]),
     ("JD-BOUNDS", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "habit", "op": "in", "value": "run"}]),
     ("JD-TYPE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "habit", "op": "in", "value": ["run", "swim"]}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value[1]")),
    (habit_view(filter=[{"field": "note", "op": "is_null", "value": "yes"}]),
     ("JD-TYPE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "note", "op": "within_last", "value": "3d"}]),
     ("JD-FILTER-OP-TYPE", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "day", "op": "within_last", "value": "twelve weeks"}]),
     ("JD-FORMAT", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "day", "op": "within_last", "value": "6h"}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "day", "op": "within_last", "value": "3d",
                         "anchor": "latest"}]),
     ("JD-FILTER-ANCHOR", "$.views[0].filter[0].anchor")),
    (habit_view(filter=[{"field": "day", "op": "within_last", "value": "3d",
                         "anchor": "max(ghost)"}]),
     ("JD-FILTER-ANCHOR", "$.views[0].filter[0].anchor")),
    (habit_view(filter=[{"field": "day", "op": "within_last", "value": "3d",
                         "anchor": "max(habit)"}]),
     ("JD-FILTER-ANCHOR", "$.views[0].filter[0].anchor")),
    (habit_view(filter=[{"field": "day", "op": "eq", "value": "2026-10-02",
                         "anchor": "now"}]),
     ("JD-UNKNOWN-KEY", "$.views[0].filter[0].anchor")),
    (habit_view(filter=[{"field": "done", "op": "contains", "value": "x"}]),
     ("JD-FILTER-OP-TYPE", "$.views[0].filter[0].op")),
    (habit_view(filter=[{"field": "note", "op": "contains", "value": ""}]),
     ("JD-BOUNDS", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "note", "op": "eq", "value": "x" * 501}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(filter=[{"field": "habit", "op": "eq", "value": {"param": "h"}}]),
     ("JD-PARAM-UNDECLARED", "$.views[0].filter[0].value.param")),
    (habit_view(params={"h": {"type": "string"}}),
     ("JD-PARAM-UNUSED", "$.views[0].params.h")),
    (habit_view(params={"h": {"type": "bool"}},
                filter=[{"field": "habit", "op": "eq", "value": {"param": "h"}}]),
     ("JD-PARAM-TYPE", "$.views[0].filter[0].value.param")),
    (habit_view(params={"h": {"type": "date"}},
                filter=[{"field": "created_at", "op": "gt", "value": {"param": "h"}}]),
     ("JD-PARAM-TYPE", "$.views[0].filter[0].value.param")),
    (habit_view(params={"h": {"type": "string"}},
                filter=[{"field": "habit", "op": "in", "value": {"param": "h"}}]),
     ("JD-PARAM-OP", "$.views[0].filter[0].value")),
    (habit_view(params={"h": {"type": "string"}},
                filter=[{"field": "day", "op": "within_last", "value": {"param": "h"}}]),
     ("JD-TYPE", "$.views[0].filter[0].value")),
    (habit_view(params={"H": {"type": "string"}}), ("JD-NAME", "$.views[0].params.H")),
    (habit_view(params={"h": {"type": "enum"}}), ("JD-VALUE", "$.views[0].params.h.type")),
    (habit_view(params={"h": {"type": "int", "default": "x"}},
                filter=[{"field": "version", "op": "eq", "value": {"param": "h"}}]),
     ("JD-PARAM-TYPE", "$.views[0].params.h.default")),
    (habit_view(filter=[{"field": "habit", "op": "eq", "value": {"param": "h", "x": 1}}]),
     ("JD-FILTER-VALUE", "$.views[0].filter[0].value")),
    (habit_view(aggregates=[{"fn": "count", "as": "n"}], limit=10),
     ("JD-VIEW-COUNT", "$.views[0].limit")),
    (habit_view(aggregates=[{"fn": "count", "as": "n"}], sort=[{"field": "day"}]),
     ("JD-VIEW-COUNT", "$.views[0].sort")),
    (habit_view(aggregates=[{"fn": "count", "as": "n"}, {"fn": "count", "as": "m"}]),
     ("JD-VIEW-COUNT", "$.views[0].aggregates")),
    (habit_view(aggregates=[{"fn": "count"}]), ("JD-MISSING", "$.views[0].aggregates[0].as")),
    (habit_view(surprise=True), ("JD-UNKNOWN-KEY", "$.views[0].surprise")),
    (habit_view(view="Bad"), ("JD-NAME", "$.views[0].view")),
    (habit_view(filter=[{"field": "day", "op": "eq", "value": "2026-01-01"}] * 11),
     ("JD-BOUNDS", "$.views[0].filter")),
])
def test_invalid_views(view, expected):
    assert expected in view_errors(view)


def test_views_may_use_system_fields_and_typed_params():
    view = habit_view(
        params={"since": {"type": "datetime"}, "v": {"type": "int", "default": 1},
                "who": {"type": "string"}, "d": {"type": "date", "required": True}},
        fields=["habit", "author", "via"],
        filter=[{"field": "created_at", "op": "gte", "value": {"param": "since"}},
                {"field": "version", "op": "gt", "value": {"param": "v"}},
                {"field": "author", "op": "eq", "value": {"param": "who"}},
                {"field": "day", "op": "lte", "value": {"param": "d"}},
                {"field": "id", "op": "ne", "value": "abc"},
                {"field": "updated_at", "op": "within_last", "value": "6h"},
                {"field": "note", "op": "is_null", "value": True}],
        sort=[{"field": "updated_at", "dir": "desc"}, {"field": "id"}])
    d.validate_app({"collections": [copy.deepcopy(HABITS)], "views": [view], "pages": []})


def test_number_fields_accept_int_params_and_values():
    body = {"collection": "c", "fields": {"x": {"type": "number"}, "e": {
        "type": "enum", "values": ["a", "b"]}}}
    view = {"view": "v", "collection": "c", "params": {"n": {"type": "int"}, "e": {
        "type": "string"}},
            "filter": [{"field": "x", "op": "gt", "value": {"param": "n"}},
                       {"field": "x", "op": "lt", "value": 3},
                       {"field": "e", "op": "eq", "value": {"param": "e"}}]}
    d.validate_app({"collections": [body], "views": [view], "pages": []})


@pytest.mark.parametrize("change, expected", [
    ({"group_by": [{"field": "day", "bucket": "week"}]}, ("JD-NOT-YET-R3", "$.group_by")),
    ({"fill_missing": True}, ("JD-NOT-YET-R3", "$.fill_missing")),
    ({"normalize": "first"}, ("JD-NOT-YET-R3", "$.normalize")),
    ({"downsample": 400}, ("JD-NOT-YET-R3", "$.downsample")),
    ({"aggregates": [{"fn": "sum", "field": "x", "as": "total"}]},
     ("JD-NOT-YET-R3", "$.aggregates[0].fn")),
    ({"filter": [{"op": "exists", "collection": "feedback", "filter": []}]},
     ("JD-NOT-YET-R2", "$.filter[0].op")),
    ({"filter": [{"op": "not_exists", "collection": "feedback", "filter": []}]},
     ("JD-NOT-YET-R2", "$.filter[0].op")),
])
def test_deferred_view_features_name_their_release(change, expected):
    errors = errors_of(d.validate_view, habit_view(**change))
    assert expected in errors
    assert all(code.startswith("JD-NOT-YET") for code, _ in errors)


# --- page errors -------------------------------------------------------------

def page_errors(page, bundle=None):
    bundle = bundle or habit_bundle()
    bundle["pages"] = [page]
    return errors_of(d.validate_app, bundle)


def habit_page(**changes):
    return with_changes(HABIT_PAGE, **changes)


def blocks(*items):
    return {"blocks": list(items)}


@pytest.mark.parametrize("page, expected", [
    (habit_page(renderer="typed/v1"), ("JD-VALUE", "$.pages[0].renderer")),
    (habit_page(title=""), ("JD-BOUNDS", "$.pages[0].title")),
    (habit_page(page="Home"), ("JD-NAME", "$.pages[0].page")),
    (habit_page(html="<b>x</b>"), ("JD-UNKNOWN-KEY", "$.pages[0].html")),
    (habit_page(**blocks({"kind": "metric", "view": "ghost", "label": "x"})),
     ("JD-PAGE-VIEW", "$.pages[0].blocks[0].view")),
    (habit_page(**blocks({"kind": "metric", "view": "recent", "label": "x"})),
     ("JD-PAGE-METRIC", "$.pages[0].blocks[0].view")),
    (habit_page(**blocks({"kind": "table", "view": "done_12w", "columns": [{"field": "x"}]})),
     ("JD-PAGE-METRIC", "$.pages[0].blocks[0].view")),
    (habit_page(**blocks({"kind": "detail", "view": "done_12w", "fields": ["habit"]})),
     ("JD-PAGE-METRIC", "$.pages[0].blocks[0].view")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": [{"field": "ghost"}]})),
     ("JD-PAGE-COLUMN", "$.pages[0].blocks[0].columns[0].field")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": [{"field": "id"}]})),
     ("JD-PAGE-COLUMN", "$.pages[0].blocks[0].columns[0].field")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": []})),
     ("JD-BOUNDS", "$.pages[0].blocks[0].columns")),
    (habit_page(**blocks({"kind": "table", "view": "recent",
                          "columns": [{"field": "day", "format": "sparkle"}]})),
     ("JD-VALUE", "$.pages[0].blocks[0].columns[0].format")),
    (habit_page(**blocks({"kind": "detail", "view": "by_habit", "fields": ["ghost"],
                          "params": {"habit": "run"}})),
     ("JD-PAGE-COLUMN", "$.pages[0].blocks[0].fields[0]")),
    (habit_page(**blocks({"kind": "table", "view": "by_habit", "columns": [{"field": "day"}]})),
     ("JD-PAGE-PARAM", "$.pages[0].blocks[0].params")),
    (habit_page(**blocks({"kind": "table", "view": "by_habit", "columns": [{"field": "day"}],
                          "params": {"habit": 3}})),
     ("JD-PAGE-PARAM", "$.pages[0].blocks[0].params.habit")),
    (habit_page(**blocks({"kind": "table", "view": "by_habit", "columns": [{"field": "day"}],
                          "params": {"habit": "run", "other": "x"}})),
     ("JD-PAGE-PARAM", "$.pages[0].blocks[0].params.other")),
    (habit_page(**blocks({"kind": "table", "view": "by_habit", "columns": [{"field": "day"}],
                          "params": {"habit": {"page_param": "ghost"}}})),
     ("JD-PAGE-PARAM", "$.pages[0].blocks[0].params.habit.page_param")),
    (habit_page(params={"habit": {"type": "int"}},
                **blocks({"kind": "table", "view": "by_habit", "columns": [{"field": "day"}],
                          "params": {"habit": {"page_param": "habit"}}})),
     ("JD-PAGE-PARAM", "$.pages[0].blocks[0].params.habit.page_param")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": [{"field": "day"}],
                          "row_link": {"page": "ghost", "param": "id"}})),
     ("JD-PAGE-LINK", "$.pages[0].blocks[0].row_link.page")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": [{"field": "day"}],
                          "row_link": {"page": "home", "param": "ghost"}})),
     ("JD-PAGE-LINK", "$.pages[0].blocks[0].row_link.param")),
    (habit_page(**blocks({"kind": "text", "style": "heading", "text": "x",
                          "link": {"page": "ghost"}})),
     ("JD-PAGE-LINK", "$.pages[0].blocks[0].link.page")),
    (habit_page(**blocks({"kind": "text", "style": "heading", "text": "x",
                          "link": {"path": "https://evil.example"}})),
     ("JD-FORMAT", "$.pages[0].blocks[0].link.path")),
    (habit_page(**blocks({"kind": "text", "style": "heading", "text": "x",
                          "link": {"path": "//evil.example"}})),
     ("JD-FORMAT", "$.pages[0].blocks[0].link.path")),
    (habit_page(**blocks({"kind": "text", "style": "heading", "text": "x",
                          "link": {"path": "/apps", "page": "home"}})),
     ("JD-PAGE-LINK", "$.pages[0].blocks[0].link")),
    (habit_page(**blocks({"kind": "text", "style": "heading", "text": "x",
                          "link": {}})),
     ("JD-PAGE-LINK", "$.pages[0].blocks[0].link")),
    (habit_page(**blocks({"kind": "text", "style": "shout", "text": "x"})),
     ("JD-VALUE", "$.pages[0].blocks[0].style")),
    (habit_page(**blocks({"kind": "text", "style": "paragraph", "text": "x" * 4001})),
     ("JD-BOUNDS", "$.pages[0].blocks[0].text")),
    (habit_page(**blocks({"kind": "html", "text": "x"})),
     ("JD-PAGE-COMPONENT", "$.pages[0].blocks[0].kind")),
    (habit_page(**blocks({"kind": "metric", "view": "done_12w"})),
     ("JD-MISSING", "$.pages[0].blocks[0].label")),
    (habit_page(**blocks({"kind": "table", "view": "recent", "columns": [{"field": "day"}],
                          "actions": ["ghost"]})),
     ("JD-PAGE-ACTION", "$.pages[0].blocks[0].actions[0]")),
    (habit_page(**blocks({"kind": "detail", "view": "by_habit", "fields": ["day"],
                          "params": {"habit": "run"}, "actions": ["log_habit"]})),
     ("JD-PAGE-ACTION", "$.pages[0].blocks[0].actions[0]")),
    (habit_page(params={"P": {"type": "string"}}), ("JD-NAME", "$.pages[0].params.P")),
])
def test_invalid_pages(page, expected):
    assert expected in page_errors(page)


def test_page_blocks_bound():
    page = habit_page(blocks=[{"kind": "text", "style": "paragraph", "text": "x"}] * 51)
    assert ("JD-BOUNDS", "$.pages[0].blocks") in page_errors(page)


@pytest.mark.parametrize("action, expected", [
    ({"name": "a", "kind": "create", "collection": "ghosts", "editable_fields": ["x"]},
     ("JD-PAGE-ACTION", "$.pages[0].actions[0].collection")),
    ({"name": "a", "kind": "create", "collection": "habits", "editable_fields": ["habit"]},
     ("JD-TEMPLATE-REQUIRED", "$.pages[0].actions[0]")),
    ({"name": "a", "kind": "create", "collection": "habits",
      "editable_fields": ["habit", "day", "done", "ghost"]},
     ("JD-TEMPLATE-FIELD", "$.pages[0].actions[0].editable_fields[3]")),
    ({"name": "a", "kind": "create", "collection": "habits",
      "presets": {"done": True, "author": "kyle"}, "editable_fields": ["habit", "day"]},
     ("JD-TEMPLATE-FIELD", "$.pages[0].actions[0].presets.author")),
    ({"name": "a", "kind": "create", "collection": "habits",
      "presets": {"done": True}, "editable_fields": ["habit", "day", "done"]},
     ("JD-TEMPLATE-FIELD", "$.pages[0].actions[0].editable_fields[2]")),
    ({"name": "a", "kind": "create", "collection": "habits",
      "presets": {"done": "yes"}, "editable_fields": ["habit", "day"]},
     ("JD-PRESET-VALUE", "$.pages[0].actions[0].presets.done")),
    ({"name": "a", "kind": "update", "collection": "habits"},
     ("JD-TEMPLATE-EMPTY", "$.pages[0].actions[0]")),
    ({"name": "a", "kind": "delete", "collection": "habits", "presets": {"done": True}},
     ("JD-UNKNOWN-KEY", "$.pages[0].actions[0].presets")),
    ({"name": "a", "kind": "new_version", "collection": "habits"},
     ("JD-NOT-YET-R2", "$.pages[0].actions[0].kind")),
    ({"name": "a", "kind": "tool", "collection": "habits"},
     ("JD-NOT-YET-R2", "$.pages[0].actions[0].kind")),
    ({"name": "a", "kind": "launch", "collection": "habits"},
     ("JD-TEMPLATE-KIND", "$.pages[0].actions[0].kind")),
    ({"name": "A", "kind": "delete", "collection": "habits"},
     ("JD-NAME", "$.pages[0].actions[0].name")),
])
def test_invalid_action_templates(action, expected):
    page = habit_page(blocks=[], actions=[action])
    assert expected in page_errors(page)


def test_update_template_on_immutable_collection_is_refused():
    bundle = judgment_bundle()
    page = copy.deepcopy(JUDGMENT_PAGES[1])
    page["actions"].append({"name": "edit", "kind": "update", "collection": "predictions",
                            "editable_fields": ["rationale"]})
    bundle["pages"] = [copy.deepcopy(JUDGMENT_PAGES[0]), page]
    assert ("JD-TEMPLATE-IMMUTABLE", "$.pages[1].actions[1].kind") in errors_of(
        d.validate_app, bundle)


def test_duplicate_template_names_are_refused():
    page = habit_page(actions=HABIT_PAGE["actions"] + [
        {"name": "log_habit", "kind": "delete", "collection": "habits"}])
    assert ("JD-DUPLICATE-NAME", "$.pages[0].actions[3].name") in page_errors(page)


@pytest.mark.parametrize("block, expected", [
    ({"kind": "chart", "type": "bar", "view": "recent"}, "JD-NOT-YET-R3"),
    ({"kind": "calendar", "view": "recent"}, "JD-NOT-YET-R3"),
    ({"kind": "sparkline", "view": "recent"}, "JD-NOT-YET-R3"),
    ({"kind": "stat_row", "views": ["recent"]}, "JD-NOT-YET-R3"),
    ({"kind": "list_filter", "param": "habit"}, "JD-NOT-YET-R3"),
    ({"kind": "image", "field": "x"}, "JD-NOT-YET-R3"),
])
def test_deferred_page_components_name_their_release(block, expected):
    errors = errors_of(d.validate_page, habit_page(blocks=[block]))
    assert (expected, "$.blocks[0].kind") in errors
    assert all(code == expected for code, _ in errors)


def test_page_refresh_is_release_three():
    assert ("JD-NOT-YET-R3", "$.refresh") in errors_of(
        d.validate_page, habit_page(refresh=30))


def test_detail_history_and_tool_actions_are_release_two():
    errors = errors_of(d.validate_page, habit_page(blocks=[
        {"kind": "detail", "view": "by_habit", "fields": ["day"], "history": True}]))
    assert ("JD-NOT-YET-R2", "$.blocks[0].history") in errors


# --- bundles -----------------------------------------------------------------

def test_bundle_ref_targets_must_be_in_the_app():
    bundle = judgment_bundle()
    bundle["collections"] = [copy.deepcopy(FEEDBACK)]
    bundle["views"] = [v for v in bundle["views"] if v["collection"] == "feedback"]
    bundle["pages"] = []
    assert ("JD-REF-TARGET", "$.collections[0].fields.prediction.collection") in errors_of(
        d.validate_app, bundle)


def test_bundle_duplicate_names():
    bundle = habit_bundle()
    bundle["collections"].append(copy.deepcopy(HABITS))
    bundle["views"].append(copy.deepcopy(HABIT_VIEWS[0]))
    bundle["pages"].append(copy.deepcopy(HABIT_PAGE))
    errors = errors_of(d.validate_app, bundle)
    assert ("JD-DUPLICATE-NAME", "$.collections[1].collection") in errors
    assert ("JD-DUPLICATE-NAME", "$.views[3].view") in errors
    assert ("JD-DUPLICATE-NAME", "$.pages[1].page") in errors


def test_bundle_reports_shape_and_cross_errors_together():
    bundle = habit_bundle()
    bundle["collections"][0]["surprise"] = 1
    bundle["views"].append({"view": "lost", "collection": "ghosts"})
    errors = errors_of(d.validate_app, bundle)
    assert ("JD-UNKNOWN-KEY", "$.collections[0].surprise") in errors
    assert ("JD-VIEW-COLLECTION", "$.views[3].collection") in errors


def test_bundle_views_over_an_invalid_collection_are_not_double_reported():
    bundle = habit_bundle()
    bundle["collections"][0]["write_mode"] = "versioned"
    errors = errors_of(d.validate_app, bundle)
    assert errors == [("JD-NOT-YET-R2", "$.collections[0].write_mode")]


def test_bundle_shape():
    assert ("JD-UNKNOWN-KEY", "$.templates") in errors_of(
        d.validate_app, {"collections": [], "templates": []})
    assert ("JD-TYPE", "$.views") in errors_of(d.validate_app, {"views": {}})
    empty = d.validate_app({})
    assert empty.collections == {} and empty.views == {} and empty.pages == {}


def test_bundle_size_limits():
    many = [{"collection": f"c{i}", "fields": {"x": {"type": "bool"}}} for i in range(51)]
    assert ("JD-BOUNDS", "$.collections") in errors_of(d.validate_app, {"collections": many})


def test_view_param_types_fit_page_literals():
    page = habit_page(blocks=[{"kind": "table", "view": "by_habit",
                               "columns": [{"field": "day"}], "params": {"habit": "run"}}])
    bundle = habit_bundle()
    bundle["pages"] = [page]
    d.validate_app(bundle)


def test_count_view_may_be_parameterized():
    bundle = habit_bundle()
    bundle["views"].append({"view": "count_for", "collection": "habits",
                            "params": {"h": {"type": "string", "required": True}},
                            "filter": [{"field": "habit", "op": "eq",
                                        "value": {"param": "h"}}],
                            "aggregates": [{"fn": "count", "as": "n"}]})
    bundle["pages"][0]["blocks"].append({"kind": "metric", "label": "For",
                                         "view": "count_for",
                                         "params": {"h": {"page_param": "habit"}}})
    d.validate_app(bundle)


# --- schema export -----------------------------------------------------------

def test_json_schema_export_covers_every_kind():
    exported = d.json_schemas()
    assert exported["capabilities_version"] == d.CAPABILITIES_VERSION
    assert set(exported["kinds"]) == {"collection", "view", "page", "tool", "bundle"}
    for schema in exported["kinds"].values():
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        json.dumps(schema)
    collection = exported["kinds"]["collection"]
    assert "fields" in collection["properties"]
    text = json.dumps(exported)
    for field_type in ("string", "text", "int", "number", "bool", "date", "datetime",
                       "enum", "ref", "url", "artifact"):
        assert f'"{field_type}"' in text
    assert '"typed/v2"' in text and '"within_last"' in text and '"contains"' in text


def test_exported_schemas_accept_the_fixtures_and_refuse_bad_shapes():
    jsonschema = pytest.importorskip("jsonschema")
    kinds = d.json_schemas()["kinds"]
    for kind, bundle in (("bundle", habit_bundle()), ("bundle", judgment_bundle())):
        jsonschema.validate(bundle, kinds[kind])
    jsonschema.validate(HABITS, kinds["collection"])
    jsonschema.validate(JUDGMENT_VIEWS[3], kinds["view"])
    jsonschema.validate(JUDGMENT_PAGES[1], kinds["page"])
    for kind, bad in (("collection", with_changes(HABITS, surprise=1)),
                      ("collection", with_changes(HABITS, fields={"x": {"type": "json"}})),
                      ("view", habit_view(limit=500)),
                      ("page", habit_page(blocks=[{"kind": "chart"}]))):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, kinds[kind])


def test_capabilities_list_release_one_and_deferred_features():
    caps = d.capabilities()
    assert caps["version"] == d.CAPABILITIES_VERSION
    assert set(caps["field_types"]) == {"string", "text", "int", "number", "bool", "date",
                                        "datetime", "enum", "ref", "url", "artifact"}
    assert set(caps["components"]) == {"table", "detail", "metric", "text"}
    assert set(caps["filter_ops"]) == {"eq", "ne", "in", "lt", "lte", "gt", "gte",
                                       "is_null", "within_last", "contains"}
    assert caps["deferred"]["versioned"] == 2 and caps["deferred"]["chart"] == 3
    assert caps["limits"]["view_limit"] == 200 and caps["limits"]["indexed_fields"] == 4


def test_json_paths_escape_non_identifiers():
    assert json_path("fields", "a b", 0) == '$.fields["a b"][0]'


def test_presets_respect_field_bounds():
    links = {"collection": "links", "fields": {
        "url": {"type": "url", "max": 100, "link": True},
        "code": {"type": "string", "min": 3, "max": 5},
        "score": {"type": "int", "min": 0, "max": 10}}}

    def page(presets):
        return {"page": "p", "title": "Links", "actions": [
            {"name": "add", "kind": "create", "collection": "links", "presets": presets}]}

    good = {"collections": [links], "views": [],
            "pages": [page({"url": "https://example.com", "code": "abc", "score": 10})]}
    d.validate_app(good)
    for presets, path in (({"code": "ab"}, "code"), ({"score": 11}, "score"),
                          ({"url": "x" * 101}, "url"), ({"score": None}, "score")):
        bundle = {"collections": [links], "views": [], "pages": [page(presets)]}
        assert ("JD-PRESET-VALUE", f"$.pages[0].actions[0].presets.{path}") in errors_of(
            d.validate_app, bundle)


# --- App tools (R1b) -------------------------------------------------------------

LEDGER_TOOL = {"tool": "ledger", "roles": {
    "log": {"collection": "habits", "verbs": ["read", "create"]}}}


def tool_bundle(*tools, collections=None):
    bundle = habit_bundle()
    if collections is not None:
        bundle["collections"] = collections
        bundle["views"], bundle["pages"] = [], []
    bundle["app_tools"] = [copy.deepcopy(t) for t in tools]
    return bundle


def test_an_app_tool_binds_roles_to_the_apps_collections():
    parsed = d.validate_definition("tool", LEDGER_TOOL)
    assert parsed.name == "ledger"
    assert parsed.roles["log"].collection == "habits"
    assert parsed.roles["log"].verbs == ["read", "create"]
    app = d.validate_app(tool_bundle(LEDGER_TOOL))
    assert set(app.app_tools) == {"ledger"}


@pytest.mark.parametrize("body, expected", [
    ({**LEDGER_TOOL, "surprise": 1}, ("JD-UNKNOWN-KEY", "$.surprise")),
    ({"tool": "ledger", "roles": {"log": {"collection": "habits", "verbs": ["read"],
                                          "as": "kyle"}}},
     ("JD-UNKNOWN-KEY", "$.roles.log.as")),
    ({"tool": "Ledger", "roles": LEDGER_TOOL["roles"]}, ("JD-NAME", "$.tool")),
    ({"tool": "l", "roles": LEDGER_TOOL["roles"]}, ("JD-NAME", "$.tool")),
    ({"tool": "ledger", "roles": {}}, ("JD-BOUNDS", "$.roles")),
    ({"tool": "ledger"}, ("JD-MISSING", "$.roles")),
    ({"tool": "ledger", "roles": {"Log": {"collection": "habits", "verbs": ["read"]}}},
     ("JD-NAME", "$.roles.Log")),
    ({"tool": "ledger", "roles": {"log": {"collection": "habits", "verbs": []}}},
     ("JD-BOUNDS", "$.roles.log.verbs")),
    ({"tool": "ledger", "roles": {"log": {"collection": "habits", "verbs": ["share"]}}},
     ("JD-VALUE", "$.roles.log.verbs[0]")),
    ({"tool": "ledger", "roles": {"log": {"collection": "habits",
                                          "verbs": ["read", "read"]}}},
     ("JD-TOOL-VERB", "$.roles.log.verbs[1]")),
])
def test_invalid_app_tools(body, expected):
    assert expected in errors_of(d.validate_definition, "tool", body)


def test_an_app_tool_binds_only_collections_in_the_app():
    other = {"tool": "ledger", "roles": {"log": {"collection": "diary", "verbs": ["read"]}}}
    assert ("JD-TOOL-COLLECTION", "$.app_tools[0].roles.log.collection") in errors_of(
        d.validate_app, tool_bundle(other))


def test_an_app_tools_verbs_stay_within_the_collections():
    """An immutable collection has no update, so no tool may be bound to one."""
    edit = {"tool": "judgment", "roles": {
        "calls": {"collection": "predictions", "verbs": ["read", "update"]}}}
    bundle = judgment_bundle()
    bundle["app_tools"] = [edit]
    assert ("JD-TOOL-VERB", "$.app_tools[0].roles.calls.verbs[1]") in errors_of(
        d.validate_app, bundle)
    edit["roles"]["calls"]["verbs"] = ["read", "create", "delete"]
    d.validate_app(bundle)


def test_app_tool_names_are_distinct():
    assert ("JD-DUPLICATE-NAME", "$.app_tools[1].tool") in errors_of(
        d.validate_app, tool_bundle(LEDGER_TOOL, LEDGER_TOOL))


def test_tool_only_writers_and_app_tools_together_validate():
    habits = with_changes(HABITS, writers={"create": ["tool:ledger"]})
    d.validate_app(tool_bundle(LEDGER_TOOL, collections=[habits]))


def test_app_tools_are_in_the_schema_and_capabilities():
    schemas = d.json_schemas()
    assert "tool" in schemas["kinds"]
    assert "app_tools" in schemas["kinds"]["bundle"]["properties"]
    assert d.capabilities()["version"] >= 2 and "app_tools" in d.capabilities()
