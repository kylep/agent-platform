"""Golden tests for the App authority-fact engine (design 39, "The authority model").

Each fact type has an exact expected tuple set, and each per-kind relax rule
has a widening case and, where the design allows it, a self-publish case."""
import copy
import random

from agentplatform.appdata.authority import (
    compute_facts, describe, digest, initial_approved_facts, ordered,
    settle_new_fields, widening)

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


def test_delete_reach_closure_follows_cascades():
    defs = app(
        {"collection": "experiments", "fields": {"name": {"type": "string"}}},
        {"collection": "series", "fields": {
            "experiment": {"type": "ref", "ref": "experiments", "on_delete": "cascade"}}},
        {"collection": "points", "fields": {
            "series": {"type": "ref", "ref": "series", "on_delete": "unlink"}}},
        {"collection": "notes", "fields": {
            "series": {"type": "ref", "ref": "series"}}},  # restrict by default
    )
    assert of_kind(compute_facts(defs), "delete_reach") == [
        ("delete_reach", "experiments", "notes", "restrict"),
        ("delete_reach", "experiments", "points", "unlink"),
        ("delete_reach", "experiments", "series", "cascade"),
        ("delete_reach", "series", "notes", "restrict"),
        ("delete_reach", "series", "points", "unlink"),
    ]


def test_retention_facts_in_days_and_records():
    facts = compute_facts(app(habits(retention={"max_age": "12w", "max_records": 5000})))
    assert of_kind(facts, "retention") == [
        ("retention", "habits", "max_age_days", 84),
        ("retention", "habits", "max_records", 5000),
    ]


def test_rules_in_canonical_form():
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
        writers={"update": ["tool:habit-bot"], "create": ["tool:habit-bot"]}))
    assert [f for f in ordered(compute_facts(defs)) if f[0] == "rule"] == [
        ("rule", "immutable_after_create", "habits", ("day", "habit")),
        ("rule", "lock", "habits", "habit", '"run"', "history", ("day", "note"), ("kyle",)),
        ("rule", "required_when", "habits", "note", '{"habit":"read"}'),
        ("rule", "unique", "habits", ("day", "habit")),
        ("rule", "writer", "habits", "habit", '"run"', ("kyle",)),
        ("rule", "writer", "habits", "habit", None, ("kyle", "owner")),
        ("rule", "writers", "habits", "create", ("tool:habit-bot",)),
        ("rule", "writers", "habits", "update", ("tool:habit-bot",)),
    ]


def test_action_templates_bring_kyle_access():
    defs = app(templates=[
        {"template": "add_habit", "kind": "create", "collection": "habits",
         "presets": {"habit": "run"}, "editable_fields": ["note", "day"]},
        {"template": "remove", "kind": "delete", "collection": "habits"},
    ])
    facts = compute_facts(defs)
    assert of_kind(facts, "template") == [
        ("template", "add_habit", "create", "habits", '{"habit":"run"}', ("day", "note")),
        ("template", "remove", "delete", "habits", "{}", ()),
    ]
    for f in ("day", "habit", "note"):
        assert ("access", "kyle", "habits", f, "create") in facts
    assert ("delete", "kyle", "habits") in facts


def test_app_tools_tool_views_actions_and_service_principals():
    defs = app(
        habits(),
        app_tools=[{"tool": "tracker", "roles": {
            "log": {"collection": "habits", "verbs": ["read", "create"]}}}],
        views=[{"view": "streaks", "tool": "tracker", "action": "streaks", "sources": ["log"]}],
        pages=[{"page": "home", "renderer": "typed/v2", "title": "Home",
                "blocks": [{"kind": "table", "view": "streaks"}],
                "actions": [{"alias": "recount", "tool": "tracker", "action": "recount",
                             "sources": ["log"], "verbs": ["update"],
                             "budget": {"calls_per_day": 5}}]}],
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
    assert of_kind(compute_facts(defs), "link") == [
        ("link", "habits", "source"), ("link", "habits", "steps.doc")]


def test_unknown_keys_are_unmapped_facts():
    defs = app(habits(colour="red", fields={"day": {"type": "date", "secret": True},
                                             "blob": {"type": "json"}}),
               triggers=[{"collection": "habits"}],
               views=[{"view": "v", "collection": "habits", "run_as": "kyle"}],
               pages=[{"page": "p", "title": "P", "blocks": [{"kind": "iframe"}],
                       "actions": [{"alias": "x", "operation": "tickets.create@1"}]}])
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
        {"collection": "logs", "fields": {"habit": {"type": "ref", "ref": "habits",
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
    collections_as_map = dict(defs, collections={c["collection"]: c for c in defs["collections"]})
    assert ordered(compute_facts(shuffled)) == ordered(compute_facts(defs))
    assert digest(compute_facts(shuffled)) == digest(compute_facts(defs))
    assert digest(compute_facts(collections_as_map)) == digest(compute_facts(defs))
    assert describe(compute_facts(shuffled)) == describe(compute_facts(defs))


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
                "blocks": [{"kind": "table", "view": "recent", "columns": ["day", "habit"]},
                           {"kind": "text", "text": "hi"}]}]))
    assert widening(approved, new) == []


def test_narrowing_self_publishes():
    approved = compute_facts(app(habits(retention={"max_age": "90d"})))
    new = compute_facts(app(habits(retention={"max_age": "1y"},
                                   access={"read": ["owner"], "create": ["owner"]})))
    assert widening(approved, new) == []


def test_tool_view_on_an_approved_app_tool_self_publishes():
    tools = [{"tool": "tracker", "roles": {"log": {"collection": "habits", "verbs": ["read"]}}}]
    approved = compute_facts(app(app_tools=tools))
    new = compute_facts(app(app_tools=tools, views=[
        {"view": "streaks", "tool": "tracker", "action": "streaks", "sources": ["log"]}]))
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
    template = {"template": "add", "kind": "create", "collection": "habits",
                "presets": {"habit": "run"}, "editable_fields": ["day"]}
    approved = compute_facts(app())
    assert widening(approved, compute_facts(app(templates=[template]))) == [
        "new: kyle can create habits.day",
        "new: kyle can create habits.habit",
        "new: page action add lets Kyle create habits records "
        "(presets {\"habit\":\"run\"}; editable day)",
    ]
    approved = compute_facts(app(templates=[template]))
    changed = dict(template, presets={"habit": "read"})
    assert widening(approved, compute_facts(app(templates=[changed]))) == [
        "new: page action add lets Kyle create habits records "
        "(presets {\"habit\":\"read\"}; editable day)"]


def test_outbound_link_is_a_proposal():
    approved = compute_facts(app(habits(fields={"src": {"type": "url"}})))
    new = compute_facts(app(habits(fields={"src": {"type": "url", "link": True}})))
    assert widening(approved, new) == ["new: habits.src renders as an outbound link"]


def test_shorter_or_new_retention_is_a_proposal():
    approved = compute_facts(app(habits(retention={"max_age": "90d"})))
    assert widening(approved, compute_facts(app(habits(retention={"max_age": "30d"})))) == [
        "shorter retention: habits records are pruned after 30 days"]
    assert widening(approved, compute_facts(app(habits(retention={"max_age": "90d",
                                                                  "max_records": 10})))) == [
        "shorter retention: habits keeps only its newest 10 records"]
    assert widening(compute_facts(app()),
                    compute_facts(app(habits(retention={"max_records": 10})))) == [
        "shorter retention: habits keeps only its newest 10 records"]


def _reach(mode):
    return app(habits(), {"collection": "logs", "fields": {
        "habit": {"type": "ref", "ref": "habits", "on_delete": mode}}})


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
    page = {"page": "home", "title": "Home", "actions": [
        {"alias": "recount", "tool": "tracker", "action": "recount", "sources": ["log"],
         "verbs": ["update"], "budget": {"calls_per_day": 5}}]}
    approved = compute_facts(app(app_tools=tools))
    assert widening(approved, compute_facts(app(app_tools=tools, pages=[page]))) == [
        "new: page tool action tracker.recount may update log=habits "
        "(budget {\"calls_per_day\":5})"]


def test_service_principal_facts_are_proposals():
    sp = [{"principal": "tool:ttrpg", "collections": {"habits": ["read"]}}]
    assert widening(compute_facts(app()), compute_facts(app(service_principals=sp))) == [
        "new: service principal tool:ttrpg may read habits"]


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
