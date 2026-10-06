"""Golden tests for the App authority-fact engine (design 39, "The authority model").

Each fact type has an exact expected tuple set, and each per-kind relax rule
has a widening case and, where the design allows it, a self-publish case.
Definitions use the A2 language (`appdata/definitions.py`); cases marked
"deferred" use syntax A2 refuses today (Release 2, R1b, M6 or the request
path) and run on raw dicts."""
import copy
import random

import pytest

from agentplatform.appdata import definitions as lang
from agentplatform.appdata.authority import (
    compute_facts, describe, digest, initial_approved_facts, ordered,
    settle_new_fields, widening)

from .test_appdata_definitions import HABITS, PREDICTIONS, habit_bundle, judgment_bundle

SYSTEM = ("author", "collection_version", "created_at", "id", "updated_at", "version", "via")


def habits(**over):
    collection = {
        "collection": "habits",
        "fields": {
            "habit": {"type": "enum", "values": ["run", "read"], "required": True},
            "day": {"type": "date", "required": True},
            "note": {"type": "string", "max": 500, "access": {"read": ["owner"]}},
        },
        "write_mode": "editable",
        "access": {"read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"],
                   "delete": ["kyle"]},
        "rules": [{"kind": "unique", "fields": ["habit", "day"]}],
    }
    collection.update(over)
    return collection


def app(*collections, **top):
    return {"collections": list(collections) or [habits()], **top}


def of_kind(facts, *kinds):
    return [f for f in ordered(facts) if f[0] in kinds]


def rules(facts, kind):
    return [f for f in ordered(facts) if f[0] == "rule" and f[1] == kind]


# --- compute_facts: one golden per fact type ---------------------------------

def test_field_access_resolves_defaults_overrides_and_system_fields():
    facts = compute_facts(app())
    expected = ordered(
        [("access", p, "habits", f, "read") for p in ("owner", "kyle") for f in SYSTEM + ("day", "habit")]
        + [("access", "owner", "habits", "note", "read")]
        + [("access", "owner", "habits", f, v) for f in ("day", "habit", "note")
           for v in ("create", "update")])
    assert of_kind(facts, "access") == expected


def test_record_delete_and_declarations():
    facts = compute_facts(app())
    assert of_kind(facts, "delete") == [("delete", "kyle", "habits")]
    assert ("declared", "habits") in facts
    assert ("declared", "habits", "note") in facts and ("declared", "habits", "id") in facts


def test_immutable_collection_has_no_update_facts():
    facts = compute_facts(app(habits(write_mode="immutable")))
    assert not [f for f in facts if f[0] == "access" and f[4] == "update"]


def test_exposing_past_versions_of_an_existing_collection_needs_approval():
    current = compute_facts(app(habits()))
    proposed = compute_facts(app(habits(write_mode="versioned")))
    assert ("history", "owner", "habits") in proposed
    assert ("history", "kyle", "habits") in proposed
    assert any("past versions" in line for line in widening(current, proposed))
    # A new private App may start versioned without a proposal, as with its
    # private read access; sharing its history more widely still needs one.
    assert widening(initial_approved_facts(), proposed) == []
    shared = compute_facts(app(habits(write_mode="versioned",
                                     access={"read": ["owner", "kyle", "agent:qa"]})))
    assert any("agent:qa can read past versions" in line
               for line in widening(initial_approved_facts(), shared))


def test_delete_reach_closure_follows_cascades():
    # Deferred: `on_delete: cascade` is Release 2.
    defs = app(
        {"collection": "experiments", "fields": {"name": {"type": "string"}}},
        {"collection": "series", "fields": {
            "experiment": {"type": "ref", "collection": "experiments", "on_delete": "cascade"}}},
        {"collection": "points", "fields": {
            "series": {"type": "ref", "collection": "series", "on_delete": "unlink"}}},
        {"collection": "notes", "fields": {
            "series": {"type": "ref", "collection": "series"}}},  # restrict by default
    )
    assert of_kind(compute_facts(defs), "delete_reach") == [
        ("delete_reach", "experiments", "notes", "restrict"),
        ("delete_reach", "experiments", "points", "unlink"),
        ("delete_reach", "experiments", "series", "cascade"),
        ("delete_reach", "series", "notes", "restrict"),
        ("delete_reach", "series", "points", "unlink"),
    ]


def test_retention_facts_in_days_and_records():
    facts = compute_facts(app(habits(retention={"max_age": "12w"}),
                              {"collection": "logs", "fields": {"n": {"type": "int"}},
                               "retention": {"max_records": 5000}}))
    assert of_kind(facts, "retention") == [
        ("retention", "habits", "max_age_days", 84),
        ("retention", "logs", "max_records", 5000),
    ]


def test_retention_outside_the_language_is_unmapped():
    for retention in ({"max_age": "1y"}, {"max_age": 90}, {"max_age": "90d", "max_records": 5},
                      {}, {"max_age": "90d", "keep": "all"}):
        assert of_kind(compute_facts(app(habits(retention=retention))), "unmapped") == [
            ("unmapped", "collections.habits.retention")], retention


def test_rules_in_canonical_form():
    # Deferred: required_when and lock are request-path rules.
    defs = app(habits(
        rules=[
            {"kind": "unique", "fields": ["day", "habit"]},
            {"kind": "writer", "field": "habit", "writers": ["kyle", "owner"]},
            {"kind": "writer", "field": "habit", "value": "run", "writers": ["kyle"]},
            {"kind": "immutable_after_create", "fields": ["day", "habit"]},
            {"kind": "required_when", "field": "note", "when": {"habit": "read"}},
            {"kind": "lock", "when": {"field": "habit", "value": "run", "in": "history"},
             "lock": ["note", "day"], "unless_writer": ["kyle"]},
        ],
        writers={"update": ["tool:habit_bot"], "create": ["tool:habit_bot"]}))
    assert [f for f in ordered(compute_facts(defs)) if f[0] == "rule"] == [
        ("rule", "immutable_after_create", "habits", ("day", "habit")),
        ("rule", "lock", "habits", "habit", '"run"', "history", ("day", "note"), ("kyle",)),
        ("rule", "required_when", "habits", "note", '{"habit":"read"}'),
        ("rule", "unique", "habits", ("day", "habit")),
        ("rule", "writer", "habits", "habit", '"run"', ("kyle",)),
        ("rule", "writer", "habits", "habit", None, ("kyle", "owner")),
        ("rule", "writers", "habits", "create", ("tool:habit_bot",)),
        ("rule", "writers", "habits", "update", ("tool:habit_bot",)),
    ]


def page(*actions, name="home", blocks=()):
    return {"page": name, "renderer": "typed/v2", "title": "Home", "blocks": list(blocks),
            "actions": list(actions)}


ADD = {"name": "add_habit", "kind": "create", "collection": "habits",
       "presets": {"habit": "run"}, "editable_fields": ["note", "day"]}


def test_action_templates_bring_kyle_access():
    defs = app(pages=[page(ADD, {"name": "remove", "kind": "delete", "collection": "habits"})])
    facts = compute_facts(defs)
    assert of_kind(facts, "template") == [
        ("template", "home.add_habit", "create", "habits", '{"habit":"run"}', ("day", "note")),
        ("template", "home.remove", "delete", "habits", "{}", ()),
    ]
    for f in ("day", "habit", "note"):
        assert ("access", "kyle", "habits", f, "create") in facts
    assert ("delete", "kyle", "habits") in facts


def test_app_tools_tool_views_actions_and_service_principals():
    # Deferred: App tools and tool views (R1b), tool actions on pages (R2) and
    # service principals (M6).
    defs = app(
        habits(),
        app_tools=[{"tool": "tracker", "roles": {
            "log": {"collection": "habits", "verbs": ["read", "create"]}}}],
        views=[{"view": "streaks", "tool": "tracker", "action": "streaks", "sources": ["log"]}],
        pages=[page({"name": "recount", "kind": "tool", "tool": "tracker",
                     "action": "recount", "sources": ["log"], "verbs": ["update"],
                     "budget": {"calls_per_day": 5}},
                    blocks=[{"kind": "table", "view": "streaks",
                             "columns": [{"field": "streak"}]}])],
        service_principals=[{"principal": "tool:tracker",
                             "collections": {"habits": ["read", "update"]}}],
    )
    facts = compute_facts(defs)
    assert of_kind(facts, "app_tool") == [
        ("app_tool", "tracker", "log", "habits", "create"),
        ("app_tool", "tracker", "log", "habits", "read"),
    ]
    assert of_kind(facts, "tool_view") == [
        ("tool_view", "tracker", "streaks", ("log=habits",), ("read",), None)]
    assert of_kind(facts, "tool_action") == [
        ("tool_action", "tracker", "recount", ("log=habits",), ("update",),
         '{"calls_per_day":5}')]
    assert of_kind(facts, "service_principal") == [
        ("service_principal", "tool:tracker", "habits", "read"),
        ("service_principal", "tool:tracker", "habits", "update"),
    ]


def test_outbound_links_only_for_linked_urls():
    defs = app(habits(fields={
        "source": {"type": "url", "link": True},
        "plain": {"type": "url"},
        "steps": {"type": "list", "items": {"type": "object", "fields": {
            "doc": {"type": "url", "link": True}}}},
    }))
    # Deferred: list fields are Release 2.
    assert of_kind(compute_facts(defs), "link") == [
        ("link", "habits", "source"), ("link", "habits", "steps.doc")]


def test_unknown_keys_are_unmapped_facts():
    defs = app(habits(colour="red", fields={"day": {"type": "date", "secret": True},
                                             "blob": {"type": "json"}}),
               triggers=[{"collection": "habits"}],
               views=[{"view": "v", "collection": "habits", "run_as": "kyle"}],
               pages=[{"page": "p", "title": "P", "blocks": [{"kind": "iframe"}],
                       "actions": [{"name": "x", "operation": "tickets.create@1"}]}])
    assert of_kind(compute_facts(defs), "unmapped") == [
        ("unmapped", "collections.habits.colour"),
        ("unmapped", "collections.habits.fields.blob.type=json"),
        ("unmapped", "collections.habits.fields.day.secret"),
        ("unmapped", "pages.p.actions.x"),
        ("unmapped", "pages.p.blocks.0.kind=iframe"),
        ("unmapped", "triggers"),
        ("unmapped", "views.v.run_as"),
    ]


def test_canonical_ordering_is_stable_under_input_permutation():
    defs = app(
        habits(rules=[{"kind": "unique", "fields": ["habit", "day"]},
                      {"kind": "writer", "field": "habit", "writers": ["owner", "kyle"]}],
               retention={"max_age": "90d"}),
        {"collection": "logs", "fields": {"habit": {"type": "ref", "collection": "habits",
                                                    "on_delete": "unlink"}},
         "access": {"read": ["owner", "agent:bob", "kyle"]}},
        app_tools=[{"tool": "t", "roles": {"r": {"collection": "logs",
                                                 "verbs": ["update", "read"]}}}])
    shuffled = copy.deepcopy(defs)
    rng = random.Random(7)
    rng.shuffle(shuffled["collections"])
    for c in shuffled["collections"]:
        c["fields"] = dict(reversed(list(c["fields"].items())))
        for verbs in c.get("access", {}).values():
            rng.shuffle(verbs)
        for rule in c.get("rules", []):
            for key in ("fields", "writers"):
                if key in rule:
                    rule[key].reverse()
    shuffled["app_tools"][0]["roles"]["r"]["verbs"].reverse()
    assert ordered(compute_facts(shuffled)) == ordered(compute_facts(defs))
    assert digest(compute_facts(shuffled)) == digest(compute_facts(defs))
    assert describe(compute_facts(shuffled)) == describe(compute_facts(defs))


def test_named_maps_are_not_the_language():
    # A2 takes lists only; a {name: definition} map is unknown, so a proposal.
    defs = {"collections": {"habits": habits()}}
    assert of_kind(compute_facts(defs), "unmapped") == [("unmapped", "collections")]


# --- widening: self-publish cases ---------------------------------------------

def test_identical_bundle_self_publishes():
    facts = compute_facts(app())
    assert widening(facts, facts) == []


def test_new_private_app_self_publishes_from_initial_state():
    assert initial_approved_facts() == frozenset()
    assert widening(initial_approved_facts(), compute_facts(app())) == []


def test_new_app_shared_with_another_agent_is_a_proposal():
    shared = habits(access={"read": ["owner", "kyle", "agent:bob"], "create": ["owner"]})
    assert "new: agent:bob can read habits.id" in widening(initial_approved_facts(),
                                                      compute_facts(app(shared)))


def test_adding_a_field_to_a_shared_collection_self_publishes_once_settled():
    approved_defs = app(habits(access={"read": ["owner", "kyle", "agent:bob"],
                                       "create": ["owner", "agent:bob"]}))
    draft = copy.deepcopy(approved_defs)
    draft["collections"][0]["fields"]["mood"] = {"type": "string"}
    approved = compute_facts(approved_defs)
    # Unsettled, the new field would inherit Bob's access: never silently.
    assert "new: agent:bob can read habits.mood" in widening(approved, compute_facts(draft))
    settled = settle_new_fields(draft, approved_defs)
    assert settled["collections"][0]["fields"]["mood"]["access"] == {
        "read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"]}
    assert widening(approved, compute_facts(settled)) == []
    assert "access" not in draft["collections"][0]["fields"]["mood"]  # input untouched


def test_settle_keeps_explicit_access_and_old_fields():
    approved_defs = app()
    draft = copy.deepcopy(approved_defs)
    draft["collections"][0]["fields"]["mood"] = {"type": "string",
                                                 "access": {"read": ["agent:bob"]}}
    settled = settle_new_fields(draft, approved_defs)
    assert settled["collections"][0]["fields"]["mood"]["access"]["read"] == ["agent:bob"]
    assert "access" not in settled["collections"][0]["fields"]["day"]
    assert widening(compute_facts(approved_defs), compute_facts(settled)) == [
        "new: agent:bob can read habits.mood"]


def test_adding_a_private_collection_self_publishes():
    approved = compute_facts(app())
    new = compute_facts(app(habits(), {"collection": "moods",
                                       "fields": {"mood": {"type": "string"}},
                                       "access": {"read": ["owner", "kyle"], "create": ["owner"],
                                                  "delete": ["owner"]}}))
    assert widening(approved, new) == []


def test_adding_rules_self_publishes():
    approved = compute_facts(app())
    new = compute_facts(app(habits(rules=[
        {"kind": "unique", "fields": ["habit", "day"]},
        {"kind": "writer", "field": "habit", "writers": ["owner"]},
        {"kind": "immutable_after_create", "fields": ["day"]},
        {"kind": "required_when", "field": "note", "when": {"habit": "read"}},
        {"kind": "lock", "when": {"field": "habit", "value": "run"}, "lock": ["day"]},
    ])))
    assert widening(approved, new) == []


def test_adding_views_and_plain_pages_self_publishes():
    approved = compute_facts(app())
    new = compute_facts(app(
        views=[{"view": "recent", "collection": "habits",
                "filter": [{"field": "day", "op": "within_last", "value": "12w"}],
                "sort": [{"field": "day", "dir": "desc"}], "limit": 50}],
        pages=[{"page": "home", "renderer": "typed/v2", "title": "Habits",
                "blocks": [{"kind": "table", "view": "recent",
                            "columns": [{"field": "day"}, {"field": "habit"}]},
                           {"kind": "text", "text": "hi"}]}]))
    assert widening(approved, new) == []


def test_narrowing_self_publishes():
    approved = compute_facts(app(habits(retention={"max_age": "90d"})))
    new = compute_facts(app(habits(retention={"max_age": "52w"},
                                   access={"read": ["owner"], "create": ["owner"],
                                           "delete": ["kyle"]})))
    assert widening(approved, new) == []


def test_tool_view_on_an_approved_app_tool_self_publishes(monkeypatch):
    from agentplatform import operation_catalog

    monkeypatch.setattr(operation_catalog, "view_action", lambda tool, action: {
        "target_scope": ["log"], "input_schema": {"type": "object",
                                                   "properties": {}},
        "output_schema": {"type": "object", "properties": {
            "rows": {"type": "array"}}}}
        if (tool, action) == ("tracker", "streaks") else None)
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits", "verbs": ["read"]}}}]
    approved_defs = app(app_tools=tools)
    new_defs = app(app_tools=tools, views=[
        {"view": "streaks", "tool": "tracker", "action": "streaks", "sources": ["log"]}])
    approved = compute_facts(lang.validate_app(approved_defs))
    new = compute_facts(lang.validate_app(new_defs))
    assert widening(approved, new) == []


def test_tool_only_writers_self_publish_for_an_existing_app_tool():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits",
                                                    "verbs": ["read", "create"]}}}]
    approved = compute_facts(app(app_tools=tools))
    new = compute_facts(app(habits(writers={"create": ["tool:tracker"]}), app_tools=tools))
    assert widening(approved, new) == []


# --- widening: always proposals ------------------------------------------------

def test_tool_only_writers_for_a_tool_that_is_not_an_app_tool():
    approved = compute_facts(app())
    new = compute_facts(app(habits(writers={"create": ["tool:tracker"]})))
    assert widening(approved, new) == [
        "rule: habits records can be created only through tool:tracker, "
        "which is not an approved App tool"]


def test_new_and_changed_templates_are_proposals():
    template = {"name": "add", "kind": "create", "collection": "habits",
                "presets": {"habit": "run"}, "editable_fields": ["day"]}
    approved = compute_facts(app())
    assert widening(approved, compute_facts(app(pages=[page(template)]))) == [
        "new: kyle can create habits.day",
        "new: kyle can create habits.habit",
        "new: page action home.add lets Kyle create habits records "
        "(presets {\"habit\":\"run\"}; editable day)",
    ]
    approved = compute_facts(app(pages=[page(template)]))
    changed = dict(template, presets={"habit": "read"})
    assert widening(approved, compute_facts(app(pages=[page(changed)]))) == [
        "new: page action home.add lets Kyle create habits records "
        "(presets {\"habit\":\"read\"}; editable day)"]
    # The same form on another page is another approval.
    assert widening(approved, compute_facts(app(pages=[page(template, name="other")]))) == [
        "new: page action other.add lets Kyle create habits records "
        "(presets {\"habit\":\"run\"}; editable day)"]


def test_outbound_link_is_a_proposal():
    approved = compute_facts(app(habits(fields={"src": {"type": "url"}})))
    new = compute_facts(app(habits(fields={"src": {"type": "url", "link": True}})))
    assert widening(approved, new) == ["new: habits.src renders as an outbound link"]


def test_shorter_or_new_retention_is_a_proposal():
    approved = compute_facts(app(habits(retention={"max_age": "90d"})))
    assert widening(approved, compute_facts(app(habits(retention={"max_age": "30d"})))) == [
        "shorter retention: habits records are pruned after 30 days"]
    assert widening(approved, compute_facts(app(habits(retention={"max_records": 10})))) == [
        "shorter retention: habits keeps only its newest 10 records"]
    assert widening(compute_facts(app()),
                    compute_facts(app(habits(retention={"max_records": 10})))) == [
        "shorter retention: habits keeps only its newest 10 records"]


def _reach(mode):
    return app(habits(), {"collection": "logs", "fields": {
        "habit": {"type": "ref", "collection": "habits", "on_delete": mode}}})


def test_wider_delete_reach_is_a_proposal():
    approved = compute_facts(_reach("restrict"))
    assert widening(approved, compute_facts(_reach("unlink"))) == [
        "wider delete reach: deleting a habits record clears its references in logs"]
    assert widening(compute_facts(_reach("unlink")), compute_facts(_reach("restrict"))) == []


def test_new_collection_with_unlink_ref_is_a_proposal():
    assert widening(compute_facts(app()), compute_facts(_reach("unlink"))) == [
        "wider delete reach: deleting a habits record clears its references in logs"]
    assert widening(compute_facts(app()), compute_facts(_reach("restrict"))) == []


def test_app_tools_new_or_wider_verbs_are_proposals():
    def tools(*verbs):
        return [{"tool": "tracker", "roles": {"log": {"collection": "habits",
                                                       "verbs": list(verbs)}}}]
    assert widening(compute_facts(app()), compute_facts(app(app_tools=tools("read")))) == [
        "new: tool tracker may read habits (role log)"]
    assert widening(compute_facts(app(app_tools=tools("read"))),
                    compute_facts(app(app_tools=tools("read", "update")))) == [
        "new: tool tracker may update habits (role log)"]
    assert widening(compute_facts(app(app_tools=tools("read", "update"))),
                    compute_facts(app(app_tools=tools("read")))) == []


def test_tool_view_without_an_approved_app_tool_is_a_proposal():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits", "verbs": ["read"]}}}]
    new = compute_facts(app(app_tools=tools, views=[
        {"view": "streaks", "tool": "tracker", "action": "streaks", "sources": ["log"]}]))
    assert widening(compute_facts(app()), new) == [
        "new: tool tracker may read habits (role log)",
        "new: tool view tracker.streaks reads log=habits",
    ]


def test_tool_view_with_no_sources_is_a_proposal():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits", "verbs": ["read"]}}}]
    new = compute_facts(app(app_tools=tools, views=[
        {"view": "streaks", "tool": "tracker", "action": "streaks", "sources": []}]))
    assert widening(compute_facts(app(app_tools=tools)), new) == [
        "new: tool view tracker.streaks reads "]


def test_malformed_definitions_are_unmapped_not_errors():
    for defs in ([], {"collections": "x"}, {"collections": [None]},
                 {"collections": [{"collection": "c", "fields": {"f": None}, "rules": [7],
                                   "access": {"read": "owner"}, "retention": 5}]},
                 {"pages": [{"page": "p", "blocks": None}]},
                 {"views": [{"view": "v", "tool": "t", "action": "a", "sources": ["r"]}]},
                 {"app_tools": [{"tool": "t", "roles": {"r": {"collection": "c",
                                                               "verbs": ["own"]}}}]}):
        facts = compute_facts(defs)
        assert any(f[0] == "unmapped" for f in facts), defs
        assert widening(facts, facts)


def test_tool_actions_are_always_proposals():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits",
                                                    "verbs": ["read", "update"]}}}]
    # Deferred: tool actions on pages are Release 2.
    home = page({"name": "recount", "kind": "tool", "tool": "tracker", "action": "recount",
                 "sources": ["log"], "verbs": ["update"], "budget": {"calls_per_day": 5}})
    approved = compute_facts(app(app_tools=tools))
    assert widening(approved, compute_facts(app(app_tools=tools, pages=[home]))) == [
        "new: page tool action tracker.recount may update log=habits "
        "(budget {\"calls_per_day\":5})"]


def test_service_principal_facts_are_proposals():
    sp = [{"principal": "tool:sample", "collections": {"habits": ["read"]}}]
    assert widening(compute_facts(app()), compute_facts(app(service_principals=sp))) == [
        "new: service principal tool:sample may read habits"]


def test_field_override_widening_is_a_proposal():
    approved = compute_facts(app())
    new = compute_facts(app(habits(fields={
        "habit": {"type": "enum", "values": ["run"]},
        "day": {"type": "date"},
        "note": {"type": "string", "access": {"read": ["owner", "kyle"]}}})))
    assert widening(approved, new) == ["new: kyle can read habits.note"]


def test_collection_default_widening_is_a_proposal():
    approved = compute_facts(app())
    new = compute_facts(app(habits(access={"read": ["owner", "kyle"], "create": ["owner"],
                                           "update": ["owner", "agent:bob"],
                                           "delete": ["kyle", "owner"]})))
    assert widening(approved, new) == [
        "new: agent:bob can update habits.day",
        "new: agent:bob can update habits.habit",
        "new: agent:bob can update habits.note",
        "new: owner can delete habits records",
    ]


def test_immutable_to_editable_is_a_proposal():
    approved = compute_facts(app(habits(write_mode="immutable")))
    lines = widening(approved, compute_facts(app()))
    assert lines == ["new: owner can update habits.day", "new: owner can update habits.habit",
                     "new: owner can update habits.note"]


def test_unmapped_keys_are_always_proposals():
    defs = app(habits(colour="red"))
    facts = compute_facts(defs)
    assert widening(facts, facts) == ["unmapped: collections.habits.colour"]


# --- widening: rule relax cases ------------------------------------------------

def _with_rules(*rule_list, **over):
    return compute_facts(app(habits(rules=list(rule_list), **over)))


def test_writer_rule_relaxes():
    base = {"kind": "writer", "field": "habit", "writers": ["owner"]}
    approved = _with_rules(base)
    assert widening(approved, _with_rules(dict(base, writers=["owner", "agent:bob"]))) == [
        "relaxed rule: only owner may set habits.habit"]
    assert widening(approved, _with_rules(dict(base, value="run"))) == [
        "relaxed rule: only owner may set habits.habit"]
    assert widening(approved, _with_rules()) == [
        "relaxed rule: only owner may set habits.habit"]
    valued = dict(base, value="run")
    assert widening(_with_rules(valued), _with_rules(dict(base, value="read"))) == [
        'relaxed rule: only owner may set habits.habit to "run"']
    assert widening(_with_rules(valued), _with_rules(base)) == [
        'relaxed rule: only owner may set habits.habit to "run"']
    assert widening(_with_rules(dict(base, writers=["owner", "kyle"])), approved) == []


def test_immutable_after_create_losing_fields_relaxes():
    approved = _with_rules({"kind": "immutable_after_create", "fields": ["day", "habit"]})
    assert widening(approved, _with_rules(
        {"kind": "immutable_after_create", "fields": ["day"]})) == [
        "relaxed rule: habits.habit can't change after create"]
    # The same fields split across two rules hold the same guard.
    assert widening(approved, _with_rules(
        {"kind": "immutable_after_create", "fields": ["day"]},
        {"kind": "immutable_after_create", "fields": ["habit", "note"]})) == []


def test_unique_dropped_or_changed_relaxes():
    approved = _with_rules({"kind": "unique", "fields": ["habit", "day"]})
    expected = ["relaxed rule: habits records are unique on (day, habit)"]
    assert widening(approved, _with_rules()) == expected
    assert widening(approved, _with_rules({"kind": "unique", "fields": ["habit"]})) == expected
    assert widening(approved, _with_rules(
        {"kind": "unique", "fields": ["habit", "day", "note"]})) == expected


def test_required_when_dropped_or_changed_relaxes():
    rule = {"kind": "required_when", "field": "note", "when": {"habit": "read"}}
    approved = _with_rules(rule)
    expected = ['relaxed rule: habits.note is required when {"habit":"read"}']
    assert widening(approved, _with_rules()) == expected
    assert widening(approved, _with_rules(dict(rule, when={"habit": "read", "day": "x"}))) == \
        expected


def test_lock_relaxes_and_tightens():
    rule = {"kind": "lock", "when": {"field": "habit", "value": "run", "in": "history"},
            "lock": ["day", "note"], "unless_writer": ["kyle"]}
    approved = _with_rules(rule)
    expected = ['relaxed rule: habits day, note lock once habit has been "run" '
                '(unless written by kyle)']
    assert widening(approved, _with_rules(dict(rule, lock=["day"]))) == expected
    assert widening(approved, _with_rules(
        dict(rule, when={"field": "habit", "value": "read", "in": "history"}))) == expected
    assert widening(approved, _with_rules(
        dict(rule, when={"field": "habit", "value": "run", "in": "current"}))) == expected
    assert widening(approved, _with_rules(dict(rule, unless_writer=["kyle", "owner"]))) == \
        expected
    assert widening(approved, _with_rules()) == expected
    stricter = dict(rule, lock=["day", "note", "habit"], unless_writer=[])
    assert widening(approved, _with_rules(stricter)) == []
    current = dict(rule, when={"field": "habit", "value": "run", "in": "current"})
    assert widening(_with_rules(current), approved) == []


def test_tool_only_writers_removed_or_grown_relaxes():
    tools = [{"tool": t, "roles": {"log": {"collection": "habits", "verbs": ["create"]}}}
             for t in ("a", "b")]
    approved = compute_facts(app(habits(writers={"create": ["tool:a"]}), app_tools=tools))
    expected = ["relaxed rule: habits records can be created only through tool:a"]
    assert widening(approved, compute_facts(app(habits(), app_tools=tools))) == expected
    assert widening(approved, compute_facts(app(
        habits(writers={"create": ["tool:a", "tool:b"]}), app_tools=tools))) == expected


# --- describe ------------------------------------------------------------------

def test_describe_is_plain_english_and_grouped():
    defs = app(habits(retention={"max_age": "90d"},
                      fields={"day": {"type": "date"}, "src": {"type": "url", "link": True}}),
               app_tools=[{"tool": "tracker", "roles": {"log": {"collection": "habits",
                                                                 "verbs": ["read"]}}}])
    assert describe(compute_facts(defs)) == [
        "kyle can read habits: author, collection_version, created_at, day, id, src, "
        "updated_at, version, via",
        "owner can create habits: day, src",
        "owner can read habits: author, collection_version, created_at, day, id, src, "
        "updated_at, version, via",
        "owner can update habits: day, src",
        "kyle can delete habits records",
        "habits records are pruned after 90 days",
        "habits records are unique on (day, habit)",
        "tool tracker may read habits (role log)",
        "habits.src renders as an outbound link",
    ]


# --- agreement with the definition language (A2) -------------------------------

def _bundle(*collections, views=(), pages=()):
    return {"collections": [copy.deepcopy(c) for c in collections],
            "views": [copy.deepcopy(v) for v in views],
            "pages": [copy.deepcopy(p) for p in pages]}


# Every bundle the A2 tests accept: the worked examples, plus copies of the
# single definitions its other valid cases build inline.
VALID_BUNDLES = {
    "habit_log": habit_bundle,
    "judgment": judgment_bundle,
    "every_field_type": lambda: _bundle({"collection": "kinds", "fields": {
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
    }}),
    "defaults": lambda: _bundle({"collection": "notes", "fields": {"body": {"type": "text"}}}),
    "principals": lambda: _bundle(dict(copy.deepcopy(HABITS), access={
        "read": ["owner", "kyle", "agent:pai", "login:qa"], "create": ["agent:olu-2"]})),
    "index_slots": lambda: _bundle({"collection": "results", "fields": {
        "run": {"type": "ref", "collection": "results"},
        "case": {"type": "string"},
        "duration": {"type": "number"},
        "at": {"type": "datetime"}},
        "indexed": ["run", "case", "at", "duration"]}),
    "retention_by_age": lambda: _bundle(dict(copy.deepcopy(HABITS),
                                             retention={"max_age": "90d"})),
    "tool_writers_per_verb": lambda: _bundle(dict(copy.deepcopy(PREDICTIONS), writers={
        "create": ["tool:judgment"], "update": ["tool:judgment", "tool:tcms"],
        "delete": ["tool:judgment"]})),
    "writer_rules": lambda: _bundle(dict(copy.deepcopy(HABITS), rules=[
        {"kind": "writer", "field": "habit", "value": "run", "writers": ["kyle"]},
        {"kind": "writer", "field": "done", "value": False, "writers": ["kyle"]},
        {"kind": "writer", "field": "day", "value": "2026-10-02", "writers": ["kyle"]},
        {"kind": "writer", "field": "note", "writers": ["kyle", "agent:kai", "owner"]}])),
    "system_fields_and_params": lambda: _bundle(HABITS, views=[{
        "view": "recent", "collection": "habits",
        "params": {"since": {"type": "datetime"}, "v": {"type": "int", "default": 1},
                   "who": {"type": "string"}, "d": {"type": "date", "required": True}},
        "fields": ["habit", "author", "via"],
        "filter": [{"field": "created_at", "op": "gte", "value": {"param": "since"}},
                   {"field": "version", "op": "gt", "value": {"param": "v"}},
                   {"field": "author", "op": "eq", "value": {"param": "who"}},
                   {"field": "day", "op": "lte", "value": {"param": "d"}},
                   {"field": "id", "op": "ne", "value": "abc"},
                   {"field": "updated_at", "op": "within_last", "value": "6h"},
                   {"field": "note", "op": "is_null", "value": True}],
        "sort": [{"field": "updated_at", "dir": "desc"}, {"field": "id"}]}]),
    "count_view_with_params": lambda: _with_count_view(habit_bundle()),
}


def _with_count_view(bundle):
    bundle["views"].append({"view": "count_for", "collection": "habits",
                            "params": {"h": {"type": "string", "required": True}},
                            "filter": [{"field": "habit", "op": "eq",
                                        "value": {"param": "h"}}],
                            "aggregates": [{"fn": "count", "as": "n"}]})
    bundle["pages"][0]["blocks"].append({"kind": "metric", "label": "For",
                                         "view": "count_for",
                                         "params": {"h": {"page_param": "habit"}}})
    return bundle


def _full_dump(validated):
    """The validated bundle with every default written out."""
    return {plural: [m.model_dump(by_alias=True) for m in getattr(validated, plural).values()]
            for plural in ("collections", "views", "pages")}


@pytest.mark.parametrize("name", sorted(VALID_BUNDLES))
def test_every_valid_a2_bundle_maps_without_unmapped_facts(name):
    raw = VALID_BUNDLES[name]()
    validated = lang.validate_app(copy.deepcopy(raw))
    facts = compute_facts(validated)
    assert of_kind(facts, "unmapped") == []
    # The raw document, the validated bundle and the bundle with its defaults
    # spelled out are the same authority: the engine's defaults are A2's.
    assert compute_facts(raw) == facts
    assert compute_facts(_full_dump(validated)) == facts


def test_habit_log_key_facts():
    facts = compute_facts(lang.validate_app(habit_bundle()))
    fields = ("day", "done", "habit", "note")
    assert ("access", "owner", "habits", "note", "read") in facts
    assert ("access", "kyle", "habits", "note", "read") not in facts
    for f in ("day", "done", "habit") + SYSTEM:
        assert ("access", "kyle", "habits", f, "read") in facts
    for f in fields:
        assert ("access", "owner", "habits", f, "create") in facts
        assert ("access", "owner", "habits", f, "update") in facts
        assert ("access", "kyle", "habits", f, "create") in facts   # the log_habit form
    assert [f for f in of_kind(facts, "access") if f[1] == "kyle" and f[4] == "update"] == [
        ("access", "kyle", "habits", "done", "update")]             # mark_done's preset
    assert of_kind(facts, "delete") == [("delete", "kyle", "habits")]
    assert of_kind(facts, "rule") == [("rule", "unique", "habits", ("day", "habit"))]
    assert of_kind(facts, "template") == [
        ("template", "home.delete_entry", "delete", "habits", "{}", ()),
        ("template", "home.log_habit", "create", "habits", "{}",
         ("day", "done", "habit", "note")),
        ("template", "home.mark_done", "update", "habits", '{"done":true}', ()),
    ]
    assert of_kind(facts, "retention", "link", "delete_reach", "app_tool") == []
    # The App is private, so only its three forms need Kyle.
    assert widening(initial_approved_facts(), facts) == [
        "new: page action home.delete_entry lets Kyle delete habits records",
        "new: page action home.log_habit lets Kyle create habits records "
        "(editable day, done, habit, note)",
        "new: page action home.mark_done lets Kyle update habits records "
        "(presets {\"done\":true})",
    ]


def test_judgment_key_facts():
    facts = compute_facts(lang.validate_app(judgment_bundle()))
    # predictions is immutable: nobody updates it, whatever its access says.
    assert not [f for f in facts if f[0] == "access" and f[2] == "predictions"
                and f[4] == "update"]
    assert ("access", "owner", "predictions", "scenario", "create") in facts
    assert ("access", "kyle", "feedback", "interpretation", "read") not in facts
    assert ("access", "kyle", "feedback", "kyle_words", "update") in facts
    assert of_kind(facts, "delete") == [("delete", "kyle", "feedback"),
                                        ("delete", "kyle", "predictions")]
    assert of_kind(facts, "rule") == [
        ("rule", "immutable_after_create", "feedback", ("prediction", "source_at")),
        ("rule", "writer", "feedback", "confirmed", "true", ("kyle",)),
        ("rule", "writers", "predictions", "create", ("tool:judgment",)),
    ]
    assert of_kind(facts, "retention") == [("retention", "feedback", "max_records", 10000)]
    assert of_kind(facts, "delete_reach") == [
        ("delete_reach", "predictions", "feedback", "restrict")]
    assert of_kind(facts, "template") == [
        ("template", "prediction.delete_prediction", "delete", "predictions", "{}", ()),
        ("template", "review.confirm", "update", "feedback", '{"confirmed":true}',
         ("kyle_words",)),
    ]
    assert widening(initial_approved_facts(), facts) == [
        "shorter retention: feedback keeps only its newest 10000 records",
        "rule: predictions records can be created only through tool:judgment, "
        "which is not an approved App tool",
        "new: page action prediction.delete_prediction lets Kyle delete predictions records",
        "new: page action review.confirm lets Kyle update feedback records "
        "(presets {\"confirmed\":true}; editable kyle_words)",
    ]


def test_default_access_is_the_language_default():
    # A2 lets the owner delete by default; the engine must not understate it.
    facts = compute_facts(app({"collection": "notes", "fields": {"body": {"type": "text"}}}))
    assert of_kind(facts, "delete") == [("delete", "owner", "notes")]
    # Null means "inherit" in A2, for field access and each of its verbs.
    for access in (None, {"read": None}):
        defs = app({"collection": "notes", "fields": {"body": {"type": "text",
                                                               "access": access}}})
        assert of_kind(compute_facts(defs), "unmapped") == []
        assert compute_facts(defs) == facts


def test_syntax_the_language_does_not_have_is_unmapped():
    defs = app(
        habits(title="Habits", fields={
            "day": {"type": "date", "indexed": True},
            "who": {"type": "ref", "ref": "habits"},
            "n": {"type": "int", "values": ["a"]}}),
        templates=[{"template": "add", "kind": "create", "collection": "habits"}],
        pages=[page({"name": "a", "kind": "create", "collection": "habits",
                     "editable_fields": ["day"], "confirm": True},
                    {"name": "d", "kind": "delete", "collection": "habits",
                     "presets": {"day": "2026-10-02"}},
                    blocks=[{"kind": "heading", "text": "x"},
                            {"kind": "table", "view": "v", "columns": [], "tool": "t"}])])
    assert of_kind(compute_facts(defs), "unmapped") == [
        ("unmapped", "collections.habits.fields.day.indexed"),
        ("unmapped", "collections.habits.fields.n.values"),
        ("unmapped", "collections.habits.fields.who.collection"),
        ("unmapped", "collections.habits.fields.who.ref"),
        ("unmapped", "collections.habits.title"),
        ("unmapped", "pages.home.actions.a.confirm"),
        ("unmapped", "pages.home.actions.d.presets"),
        ("unmapped", "pages.home.blocks.0.kind=heading"),
        ("unmapped", "pages.home.blocks.1.tool"),
        ("unmapped", "templates"),
    ]


def test_settled_bundle_still_validates():
    approved_defs = judgment_bundle()
    draft = judgment_bundle()
    draft["collections"][1]["fields"]["mood"] = {"type": "string"}
    settled = settle_new_fields(draft, approved_defs)
    assert settled["collections"][1]["fields"]["mood"]["access"] == {
        "read": ["owner", "kyle"], "create": ["owner", "kyle"], "update": ["owner", "kyle"]}
    validated = lang.validate_app(settled)
    assert widening(compute_facts(approved_defs), compute_facts(validated)) == []
    # A validated bundle settles the same way.
    assert settle_new_fields(lang.validate_app(draft), approved_defs)["collections"][1][
        "fields"]["mood"]["access"] == settled["collections"][1]["fields"]["mood"]["access"]


# --- App tools in the language (R1b, B3) ---------------------------------------

def test_a_validated_app_tool_computes_the_same_facts_as_its_document():
    """The stored `tool` kind reaches the engine as the `app_tools` key, so a
    validated bundle and the document it came from grant the same."""
    doc = app(habits(writers={"create": ["tool:tracker"]}),
              app_tools=[{"tool": "tracker", "roles": {
                  "log": {"collection": "habits", "verbs": ["read", "create"]}}}])
    bundle = lang.validate_app(doc)
    assert compute_facts(bundle) == compute_facts(doc)
    assert of_kind(compute_facts(bundle), "app_tool") == [
        ("app_tool", "tracker", "log", "habits", "create"),
        ("app_tool", "tracker", "log", "habits", "read")]
    assert not of_kind(compute_facts(bundle), "unmapped")


def test_a_tools_key_that_isnt_app_tools_is_unmapped():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits", "verbs": ["read"]}}}]
    assert of_kind(compute_facts(app(tools=tools)), "unmapped") == [("unmapped", "tools")]
