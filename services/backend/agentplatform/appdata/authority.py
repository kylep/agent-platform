"""App authority facts: what an App's definitions let anyone see or do.

Design 39, "The authority model". A bundle self-publishes when every fact it
computes is present in the approved state's facts, or narrower; anything else
is a widening Kyle approves. This module is pure.

It reads the definition language of `appdata/definitions.py` (A2): either a
validated `AppBundle` (the normal path, after `validate_app`) or the same
`{"collections": [...], "views": [...], "pages": [...], "app_tools": [...]}`
document. App tools are stored as the `tool` definition kind and sit under
`app_tools` in both (`definitions.BUNDLE_KEYS`). The key sets and defaults
below are derived from A2's models, so a key the language gains is known here
at once, and a key it doesn't have is `unmapped`, which is always a proposal:
unknown means proposal.

A few fact types cover syntax A2 refuses today. The engine keeps them, keyed
to the shape A2 will use, so their facts are settled before the language
opens them; until then they reach it only as raw documents:

- `on_delete: cascade`, `write_mode: versioned`, `list` fields (one level of
  `items: {type: object, fields: {...}}`) and `new_version` templates
  (Release 2);
- page actions with `kind: "tool"`, `tool`, `action`, `sources`, `verbs` and
  `budget` (Release 2);
- tool views `{view, tool, action, sources}` (R1b);
- top-level `service_principals: [{principal: "tool:<name>", collections:
  {collection: [verbs]}}]` (M6);
- the `required_when` and `lock` rules (request path).

Principals are strings: the symbolic `owner`, `kyle`, and named principals
such as `agent:bob` or `login:qa`. A verb missing from a collection's `access`
gets A2's default; `null` anywhere A2 allows it means "inherit".
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import typing

from pydantic import BaseModel, ValidationError

from agentplatform.appdata import definitions as lang


def _keys(model: type[BaseModel]) -> frozenset[str]:
    """The keys a definition model accepts, as written (aliases included)."""
    return frozenset(info.alias or name for name, info in model.model_fields.items())


def _tagged(union, tag: str) -> dict[str, type[BaseModel]]:
    """Tag value -> model for one of A2's discriminated unions."""
    members = typing.get_args(typing.get_args(union)[0])
    return {value: model for model in members
            for value in typing.get_args(model.model_fields[tag].annotation)}


def _literal(model: type[BaseModel], name: str) -> tuple:
    return typing.get_args(model.model_fields[name].annotation)


SYSTEM_FIELDS = tuple(lang.SYSTEM_FIELDS)
FIELD_VERBS = tuple(lang.FieldAccess.model_fields)
COLLECTION_VERBS = tuple(lang.CollectionAccess.model_fields)
WRITER_VERBS = tuple(lang.Writers.model_fields)
DEFAULT_ACCESS = {verb: tuple(principals)
                  for verb, principals in lang.CollectionAccess().model_dump().items()}
# New fields and collections start private: owner and Kyle only. Anything
# beyond is a proposal. Kyle writes only through templates, which are always
# proposals, so his facts here open nothing by themselves.
PRIVATE = {verb: {"owner", "kyle"} for verb in COLLECTION_VERBS}

TOP_KEYS = frozenset(f.name for f in dataclasses.fields(lang.AppBundle)) | {
    "service_principals"}                                               # M6
COLLECTION_KEYS = _keys(lang.CollectionDef)
DEFAULT_WRITE_MODE = lang.CollectionDef.model_fields["write_mode"].default
WRITE_MODES = frozenset(_literal(lang.CollectionDef, "write_mode"))

FIELD_KEYS = {kind: _keys(model) for kind, model in _tagged(lang.FieldSpec, "type").items()}
DEFAULT_ON_DELETE = lang.RefField.model_fields["on_delete"].default
ON_DELETE = frozenset(_literal(lang.RefField, "on_delete")) | {"cascade"}   # R2
# Release 2 lists: one level of declared sub-fields, each a scalar type.
LIST_KEYS = _keys(lang.ListField)
OBJECT_ITEM_KEYS = frozenset({"type", "fields"})

RULE_KEYS = {kind: _keys(model) for kind, model in _tagged(lang.Rule, "kind").items()}
RULE_KEYS.update({                                                     # request path
    "required_when": frozenset({"kind", "field", "when"}),
    "lock": frozenset({"kind", "when", "lock", "unless_writer"})})

VIEW_KEYS = _keys(lang.ViewDef)
TOOL_VIEW_KEYS = frozenset({"view", "tool", "action", "sources", "description", "params",
                            "cache", "materialize"})                    # R1b
PAGE_KEYS = _keys(lang.PageDef)
BLOCK_KEYS = {kind: _keys(model) for kind, model in _tagged(lang.Block, "kind").items()}
TEMPLATE_KEYS = {kind: _keys(model)
                 for kind, model in _tagged(lang.ActionTemplate, "kind").items()}
TEMPLATE_KEYS["new_version"] = TEMPLATE_KEYS["update"]                 # R2
TOOL_ACTION_KEYS = frozenset({"name", "kind", "label", "tool", "action", "sources", "verbs",
                              "budget"})                                # R2

KIND_ORDER = ("access", "history", "delete", "delete_reach", "retention", "rule", "template", "app_tool",
              "tool_view", "tool_action", "service_principal", "link", "unmapped")
_PAST = {"create": "created", "update": "updated", "delete": "deleted"}


def as_definitions(definitions) -> dict:
    """A validated `AppBundle` as the document it was validated from, keeping
    only what the author set: defaults stay A2's to fill in."""
    if isinstance(definitions, lang.AppBundle):
        return {f.name: [model.model_dump(by_alias=True, exclude_unset=True)
                         for model in getattr(definitions, f.name).values()]
                for f in dataclasses.fields(definitions)}
    return definitions


def canon(value) -> str:
    """Canonical JSON for values that sit inside a fact (presets, budgets, rule values)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sort_key(fact) -> str:
    return json.dumps(fact, separators=(",", ":"), ensure_ascii=False)


def ordered(facts) -> list:
    """Facts in canonical order: stable across input order, so it can be hashed and shown."""
    return sorted(facts, key=sort_key)


def digest(facts) -> str:
    return hashlib.sha256(sort_key(ordered(facts)).encode()).hexdigest()


def initial_approved_facts() -> frozenset:
    """A new App's approved state. Empty, so only the private set self-publishes:
    owner and Kyle access, no templates, links, tools, retention or reach."""
    return frozenset()


def _items(value):
    """A definition list, or None: A2 takes lists only, never {name: item} maps."""
    return list(value) if isinstance(value, list) else None


def _strings(value):
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(sorted(set(value)))
    return None


def _set(mapping, key):
    """A2 treats an explicit null like a missing key: the default applies."""
    return isinstance(mapping, dict) and mapping.get(key) is not None


class _Facts:
    def __init__(self):
        self.facts = set()

    def add(self, *fact):
        self.facts.add(fact)

    def unmapped(self, path):
        self.facts.add(("unmapped", path))

    def unknown_keys(self, item, known, path):
        for key in item:
            if key not in known:
                self.unmapped(f"{path}.{key}")


def compute_facts(definitions) -> frozenset:
    """Every authority fact the definitions grant, as a hashable set of tuples.

    `definitions` is a validated `AppBundle` or the document A2 validates."""
    definitions = as_definitions(definitions)
    out = _Facts()
    if not isinstance(definitions, dict):
        out.unmapped("definitions")
        return frozenset(out.facts)
    for key in definitions:
        if key not in TOP_KEYS:
            out.unmapped(key)
    lists = {}
    for key in ("collections", "views", "pages", "app_tools", "service_principals"):
        lists[key] = _items(definitions.get(key, []))
        if lists[key] is None:
            out.unmapped(key)
            lists[key] = []

    tools = _app_tools(out, lists["app_tools"])
    edges = {}
    for c in lists["collections"]:
        _collection(out, c, edges)
    _delete_reach(out, edges)
    for view in lists["views"]:
        _view(out, view, tools)
    for page in lists["pages"]:
        _page(out, page, tools)
    for sp in lists["service_principals"]:
        _service_principal(out, sp)
    return frozenset(out.facts)


def _name(item, key):
    return item.get(key) if isinstance(item, dict) else None


def _collection(out, c, edges):
    name = _name(c, "collection")
    path = f"collections.{name}"
    if not isinstance(name, str):
        out.unmapped(path)
        return
    out.unknown_keys(c, COLLECTION_KEYS, path)
    out.add("declared", name)
    mode = c["write_mode"] if _set(c, "write_mode") else DEFAULT_WRITE_MODE
    if mode not in WRITE_MODES:
        out.unmapped(f"{path}.write_mode={mode}")
    # An immutable collection grants no update, so making it editable is a widening.
    verbs = FIELD_VERBS if mode != "immutable" else ("read", "create")

    access = c.get("access", {})
    if not isinstance(access, dict):
        out.unmapped(f"{path}.access")
        access = {}
    out.unknown_keys(access, COLLECTION_VERBS, f"{path}.access")
    defaults = {}
    for verb in COLLECTION_VERBS:
        principals = _strings(access[verb]) if _set(access, verb) else DEFAULT_ACCESS[verb]
        if principals is None:
            out.unmapped(f"{path}.access.{verb}")
            principals = ()
        defaults[verb] = principals
    for principal in defaults["delete"]:
        out.add("delete", principal, name)
    for field in SYSTEM_FIELDS:
        out.add("declared", name, field)
        for principal in defaults["read"]:
            out.add("access", principal, name, field, "read")

    fields = c.get("fields", {})
    if not isinstance(fields, dict):
        out.unmapped(f"{path}.fields")
        fields = {}
    for field, spec in fields.items():
        fpath = f"{path}.fields.{field}"
        if not isinstance(spec, dict):
            out.unmapped(fpath)
            continue
        out.add("declared", name, field)
        override = spec.get("access") if _set(spec, "access") else {}
        if not isinstance(override, dict):
            out.unmapped(f"{fpath}.access")
            override = {}
        out.unknown_keys(override, FIELD_VERBS, f"{fpath}.access")
        for verb in verbs:
            principals = _strings(override[verb]) if _set(override, verb) else defaults[verb]
            if principals is None:
                out.unmapped(f"{fpath}.access.{verb}")
                continue
            for principal in principals:
                out.add("access", principal, name, field, verb)
        _field_shape(out, name, field, spec, fpath, edges, top=True)

    if mode == "versioned":
        readers = {fact[1] for fact in out.facts
                   if fact[0] == "access" and fact[2] == name and fact[4] == "read"}
        for principal in readers:
            out.add("history", principal, name)

    rules = c.get("rules", [])
    if not isinstance(rules, list):
        out.unmapped(f"{path}.rules")
        rules = []
    for i, rule in enumerate(rules):
        _rule(out, name, rule, f"{path}.rules.{i}")
    writers = c.get("writers") if _set(c, "writers") else {}
    if not isinstance(writers, dict):
        out.unmapped(f"{path}.writers")
        writers = {}
    for verb, tools in writers.items():
        if tools is None and verb in WRITER_VERBS:
            continue
        tools = _strings(tools)
        if verb not in WRITER_VERBS or tools is None:
            out.unmapped(f"{path}.writers.{verb}")
            continue
        out.add("rule", "writers", name, verb, tools)
    if _set(c, "retention"):
        _retention(out, name, c["retention"], f"{path}.retention")


def _field_shape(out, collection, field, spec, path, edges, top):
    """Links and refs, including inside lists of objects (Release 2). Only
    top-level fields carry access; a sub-field can't widen its parent's readers."""
    kind = spec.get("type")
    if kind == "list" and top:
        known = LIST_KEYS
    elif kind in FIELD_KEYS:
        known = FIELD_KEYS[kind]
    else:
        out.unmapped(f"{path}.type={kind}")
        return
    out.unknown_keys(spec, known if top else known - {"access"}, path)
    if spec.get("link") is True:
        out.add("link", collection, field)
    elif "link" in spec and spec["link"] is not False:
        out.unmapped(f"{path}.link")
    if kind == "ref":
        mode = spec["on_delete"] if _set(spec, "on_delete") else DEFAULT_ON_DELETE
        target = spec.get("collection")
        if mode not in ON_DELETE or not isinstance(target, str):
            out.unmapped(f"{path}.collection")
        else:
            edges.setdefault(target, set()).add((collection, mode))
    if kind == "list":
        _list_items(out, collection, field, spec.get("items"), f"{path}.items", edges)


def _list_items(out, collection, field, items, path, edges):
    if not isinstance(items, dict):
        out.unmapped(path)
        return
    if items.get("type") != "object":
        _field_shape(out, collection, field, items, path, edges, top=False)
        return
    out.unknown_keys(items, OBJECT_ITEM_KEYS, path)
    subfields = items.get("fields")
    if not isinstance(subfields, dict):
        out.unmapped(f"{path}.fields")
        return
    for sub, subspec in subfields.items():
        if isinstance(subspec, dict):
            _field_shape(out, collection, f"{field}.{sub}", subspec,
                         f"{path}.fields.{sub}", edges, top=False)
        else:
            out.unmapped(f"{path}.fields.{sub}")


def _delete_reach(out, edges):
    """Deleting a record reaches every collection that references it; a cascade
    deletes those records too, so the reach continues from them."""
    for start in sorted(set(edges) | {s for refs in edges.values() for s, _ in refs}):
        seen, frontier = {start}, [start]
        while frontier:
            current = frontier.pop()
            for source, mode in edges.get(current, ()):
                out.add("delete_reach", start, source, mode)
                if mode == "cascade" and source not in seen:
                    seen.add(source)
                    frontier.append(source)


def _retention(out, collection, retention, path):
    # A2's own model decides the syntax, so the two can't disagree on a duration.
    try:
        parsed = lang.Retention.model_validate(retention)
    except ValidationError:
        out.unmapped(path)
        return
    if parsed.max_age is not None:
        out.add("retention", collection, "max_age_days", lang.retention_days(parsed))
    if parsed.max_records is not None:
        out.add("retention", collection, "max_records", parsed.max_records)


def _rule(out, collection, rule, path):
    kind = rule.get("kind") if isinstance(rule, dict) else None
    if kind not in RULE_KEYS:
        out.unmapped(f"{path}.kind={kind}")
        return
    out.unknown_keys(rule, RULE_KEYS[kind], path)
    if kind == "writer":
        writers = _strings(rule.get("writers"))
        if writers is None or not isinstance(rule.get("field"), str):
            out.unmapped(path)
            return
        value = canon(rule["value"]) if _set(rule, "value") else None
        out.add("rule", "writer", collection, rule["field"], value, writers)
    elif kind in ("immutable_after_create", "unique"):
        fields = _strings(rule.get("fields"))
        if not fields:
            out.unmapped(path)
            return
        out.add("rule", kind, collection, fields)
    elif kind == "required_when":
        if not isinstance(rule.get("field"), str) or not isinstance(rule.get("when"), dict):
            out.unmapped(path)
            return
        out.add("rule", kind, collection, rule["field"], canon(rule["when"]))
    else:
        when = rule.get("when")
        locked = _strings(rule.get("lock"))
        unless = _strings(rule.get("unless_writer", []))
        if (not isinstance(when, dict) or not isinstance(when.get("field"), str)
                or "value" not in when or set(when) - {"field", "value", "in"}
                or when.get("in", "current") not in ("current", "history")
                or not locked or unless is None):
            out.unmapped(path)
            return
        out.add("rule", "lock", collection, when["field"], canon(when["value"]),
                when.get("in", "current"), locked, unless)


def _template(out, page, t, path):
    """A page's action template. Its name is page-scoped in A2, so the fact
    names it `<page>.<name>`: the same form on another page is another approval."""
    kind = t["kind"]
    out.unknown_keys(t, TEMPLATE_KEYS[kind], path)
    if kind == "tool_action":
        from agentplatform.appdata.page_tool_actions import REVIEWED
        operation = t.get("operation")
        reviewed = REVIEWED.get(operation)
        if reviewed is None or reviewed.collection != t.get("collection"):
            out.unmapped(path)
            return
        verbs = (("delete",) if "_delete_" in operation else
                 ("update", "create") if operation.endswith("_belief") else
                 ("update",))
        out.add("tool_action", reviewed.app, operation,
                (f"collection={reviewed.collection}",), verbs,
                canon({"principal_per_hour": 30, "app_per_hour": 120}))
        return
    presets = t.get("presets", {})
    editable = _strings(t.get("editable_fields", []))
    if not isinstance(t.get("name"), str) or not isinstance(t.get("collection"), str) \
            or not isinstance(presets, dict) or editable is None:
        out.unmapped(path)
        return
    collection = t["collection"]
    out.add("template", f"{page}.{t['name']}", kind, collection, canon(presets), editable)
    # Approving the form approves the access it needs to work.
    if kind == "delete":
        out.add("delete", "kyle", collection)
        return
    verb = "create" if kind == "create" else "update"
    for field in set(editable) | set(presets):
        out.add("access", "kyle", collection, field, verb)


def _app_tools(out, entries):
    """App tool facts (R1b), plus each tool's role map for resolving view and
    action sources."""
    roles_by_tool = {}
    for entry in entries:
        tool = _name(entry, "tool")
        if not isinstance(tool, str) or not isinstance(entry.get("roles"), dict):
            out.unmapped(f"app_tools.{tool}")
            continue
        out.unknown_keys(entry, {"tool", "roles"}, f"app_tools.{tool}")
        for role, binding in entry["roles"].items():
            rpath = f"app_tools.{tool}.roles.{role}"
            verbs = _strings(binding.get("verbs")) if isinstance(binding, dict) else None
            if verbs is None or not isinstance(binding.get("collection"), str) \
                    or set(verbs) - set(COLLECTION_VERBS):
                out.unmapped(rpath)
                continue
            out.unknown_keys(binding, {"collection", "verbs"}, rpath)
            roles_by_tool.setdefault(tool, {})[role] = binding["collection"]
            for verb in verbs:
                out.add("app_tool", tool, role, binding["collection"], verb)
    return roles_by_tool


def _sources(out, tool, sources, tools, path):
    names = _strings(sources)
    if names is None:
        out.unmapped(f"{path}.sources")
        return None
    resolved = []
    for role in names:
        collection = tools.get(tool, {}).get(role)
        if collection is None:
            out.unmapped(f"{path}.sources.{role}")
            return None
        resolved.append(f"{role}={collection}")
    return tuple(resolved)


def _view(out, view, tools):
    path = f"views.{_name(view, 'view')}"
    if not isinstance(view, dict):
        out.unmapped(path)
        return
    if "tool" not in view:
        out.unknown_keys(view, VIEW_KEYS, path)
        return
    # A tool view (R1b): a reviewed tool read action instead of a collection.
    out.unknown_keys(view, TOOL_VIEW_KEYS, path)
    tool, action = view.get("tool"), view.get("action")
    if not isinstance(tool, str) or not isinstance(action, str):
        out.unmapped(path)
        return
    sources = _sources(out, tool, view.get("sources", []), tools, path)
    if sources is not None:
        out.add("tool_view", tool, action, sources, ("read",), None)


def _page(out, page, tools):
    name = _name(page, "page")
    path = f"pages.{name}"
    if not isinstance(name, str):
        out.unmapped(path)
        return
    out.unknown_keys(page, PAGE_KEYS, path)
    blocks, actions = page.get("blocks", []), page.get("actions", [])
    if not isinstance(blocks, list) or not isinstance(actions, list):
        out.unmapped(path)
        return
    for i, block in enumerate(blocks):
        kind = block.get("kind") if isinstance(block, dict) else None
        if kind not in BLOCK_KEYS:
            out.unmapped(f"{path}.blocks.{i}.kind={kind}")
        else:
            # A block's `tool` is unknown here: tool bindings live in views and
            # actions, where they're facts.
            out.unknown_keys(block, BLOCK_KEYS[kind], f"{path}.blocks.{i}")
    for i, action in enumerate(actions):
        kind = action.get("kind") if isinstance(action, dict) else None
        apath = f"{path}.actions.{_name(action, 'name') or i}"
        if kind in TEMPLATE_KEYS:
            _template(out, name, action, apath)
        elif kind == "tool":
            _tool_action(out, action, tools, apath)
        else:
            out.unmapped(apath)


def _tool_action(out, action, tools, path):
    """A page button backed by a reviewed tool write action (Release 2)."""
    out.unknown_keys(action, TOOL_ACTION_KEYS, path)
    tool, act = action.get("tool"), action.get("action")
    verbs = _strings(action.get("verbs", []))
    if not isinstance(tool, str) or not isinstance(act, str) or verbs is None:
        out.unmapped(path)
        return
    sources = _sources(out, tool, action.get("sources", []), tools, path)
    if sources is not None:
        out.add("tool_action", tool, act, sources, verbs, canon(action.get("budget")))


def _service_principal(out, sp):
    """A tool's own identity's App facts (M6)."""
    principal = _name(sp, "principal")
    path = f"service_principals.{principal}"
    if not isinstance(principal, str) or not isinstance(sp.get("collections"), dict):
        out.unmapped(path)
        return
    out.unknown_keys(sp, {"principal", "collections"}, path)
    for collection, verbs in sp["collections"].items():
        verbs = _strings(verbs)
        if verbs is None or set(verbs) - set(COLLECTION_VERBS):
            out.unmapped(f"{path}.collections.{collection}")
            continue
        for verb in verbs:
            out.add("service_principal", principal, collection, verb)


# --- settle -------------------------------------------------------------------

def settle_new_fields(definitions, approved_definitions) -> dict:
    """Pin explicit private access on fields new to an existing collection.

    A field with no access of its own inherits the collection's defaults, so a
    field added to a shared collection would quietly reach every reader.
    Settling narrows each unset verb to its default intersected with the
    private set; publish stores the settled bundle so the field stays private
    until a proposal shares it. New collections are left alone: all their facts
    are new and checked as such. Either argument may be a validated
    `AppBundle`; the result is the definition document."""
    settled = copy.deepcopy(as_definitions(definitions))
    approved = {_name(c, "collection"): c
                for c in _items(as_definitions(approved_definitions).get("collections", []))
                or [] if isinstance(c, dict)}
    for c in _items(settled.get("collections", [])) or []:
        name = _name(c, "collection")
        if name not in approved or not isinstance(c.get("fields"), dict):
            continue
        old_fields = approved[name].get("fields") or {}
        access = c.get("access") if isinstance(c.get("access"), dict) else {}
        for field, spec in c["fields"].items():
            if field in old_fields or not isinstance(spec, dict):
                continue
            pinned = dict(spec.get("access") or {})
            for verb in FIELD_VERBS:
                if pinned.get(verb) is None:
                    default = access[verb] if _set(access, verb) else DEFAULT_ACCESS[verb]
                    pinned[verb] = [p for p in default if p in PRIVATE[verb]]
            spec["access"] = pinned
    return settled


# --- widening -----------------------------------------------------------------

def widening(approved_facts, new_facts) -> list[str]:
    """Plain-English widenings of `new_facts` over `approved_facts`; empty means
    the bundle self-publishes."""
    approved, new = frozenset(approved_facts), frozenset(new_facts)
    found = []
    approved_tools = {(f[1], f[3], f[4]) for f in approved if f[0] == "app_tool"}

    for fact in new:
        kind = fact[0]
        if kind == "unmapped":
            found.append((fact, f"unmapped: {fact[1]}"))
            continue
        if fact in approved or kind in ("declared", "rule"):
            continue
        if kind == "access" and ("declared", fact[2], fact[3]) not in approved \
                and fact[1] in PRIVATE[fact[4]]:
            continue
        if kind == "history" and ("declared", fact[2]) not in approved \
                and fact[1] in PRIVATE["read"]:
            continue
        if kind == "delete" and ("declared", fact[2]) not in approved \
                and fact[1] in PRIVATE["delete"]:
            continue
        if kind == "delete_reach":
            # A restrict blocks deletes; it never reaches anything.
            if fact[3] != "restrict":
                found.append((fact, f"wider delete reach: {_line(fact)}"))
            continue
        if kind == "retention":
            if not any(a[:3] == fact[:3] and fact[3] >= a[3]
                       for a in approved if a[0] == "retention"):
                found.append((fact, f"shorter retention: {_line(fact)}"))
            continue
        if kind == "tool_view" and fact[3] and all(
                (fact[1], source.split("=", 1)[1], "read") in approved_tools
                for source in fact[3]):
            continue
        found.append((fact, f"new: {_line(fact)}"))

    approved_rules = [f for f in approved if f[0] == "rule"]
    new_rules = [f for f in new if f[0] == "rule"]
    for rule in approved_rules:
        if rule[1] == "immutable_after_create":
            continue
        if not any(_holds(rule, candidate) for candidate in new_rules):
            found.append((rule, f"relaxed rule: {_line(rule)}"))
    for collection in {r[2] for r in approved_rules if r[1] == "immutable_after_create"}:
        lost = _frozen(approved_rules, collection) - _frozen(new_rules, collection)
        if lost:
            rule = ("rule", "immutable_after_create", collection, tuple(sorted(lost)))
            found.append((rule, f"relaxed rule: {_line(rule)}"))
    for rule in new_rules:
        if rule[1] == "writers" and rule not in approved:
            missing = [t for t in rule[4]
                       if not any(a[0] == t.removeprefix("tool:") and a[1] == rule[2]
                                  for a in approved_tools)]
            if missing:
                found.append((rule, f"rule: {_line(rule)}, which is not an approved App tool"))

    return [line for _, line in sorted(set(found), key=lambda item: (sort_key(item[0]), item[1]))]


def _frozen(rules, collection):
    return {f for r in rules if r[1] == "immutable_after_create" and r[2] == collection
            for f in r[3]}


def _holds(old, new) -> bool:
    """Whether `new` keeps at least the guard `old` gave (per-kind relax rules)."""
    if old[1] != new[1] or old[2] != new[2]:
        return False
    kind = old[1]
    if kind == "writer":
        # Same field and the same value qualifier; the writer set may only shrink.
        return old[3:5] == new[3:5] and set(new[5]) <= set(old[5])
    if kind == "writers":
        return old[3] == new[3] and set(new[4]) <= set(old[4])
    if kind == "lock":
        same_when = old[3:5] == new[3:5]
        scope_ok = old[5] == new[5] or (old[5] == "current" and new[5] == "history")
        return same_when and scope_ok and set(new[6]) >= set(old[6]) and set(new[7]) <= set(old[7])
    # unique and required_when: any change is a removal plus an addition.
    return old == new


# --- describe -----------------------------------------------------------------

def _line(fact) -> str:
    kind = fact[0]
    if kind == "access":
        return f"{fact[1]} can {fact[4]} {fact[2]}.{fact[3]}"
    if kind == "history":
        return f"{fact[1]} can read past versions of {fact[2]} records"
    if kind == "delete":
        return f"{fact[1]} can delete {fact[2]} records"
    if kind == "delete_reach":
        _, origin, reached, mode = fact
        if mode == "unlink":
            return f"deleting a {origin} record clears its references in {reached}"
        if mode == "cascade":
            return f"deleting a {origin} record deletes the {reached} records it reaches"
        return f"a referencing {reached} record blocks deleting a {origin} record"
    if kind == "retention":
        if fact[2] == "max_age_days":
            return f"{fact[1]} records are pruned after {fact[3]} days"
        return f"{fact[1]} keeps only its newest {fact[3]} records"
    if kind == "rule":
        return _rule_line(fact)
    if kind == "template":
        _, name, tkind, collection, presets, editable = fact
        parts = ([f"presets {presets}"] if presets != "{}" else []) + (
            [f"editable {', '.join(editable)}"] if editable else [])
        suffix = f" ({'; '.join(parts)})" if parts else ""
        return f"page action {name} lets Kyle {tkind.replace('_', ' ')} {collection} records{suffix}"
    if kind == "app_tool":
        return f"tool {fact[1]} may {fact[4]} {fact[3]} (role {fact[2]})"
    if kind == "tool_view":
        return f"tool view {fact[1]}.{fact[2]} reads {', '.join(fact[3])}"
    if kind == "tool_action":
        budget = f" (budget {fact[5]})" if fact[5] != "null" else ""
        return (f"page tool action {fact[1]}.{fact[2]} may {', '.join(fact[4])} "
                f"{', '.join(fact[3])}{budget}")
    if kind == "service_principal":
        return f"service principal {fact[1]} may {fact[3]} {fact[2]}"
    if kind == "link":
        return f"{fact[1]}.{fact[2]} renders as an outbound link"
    if kind == "unmapped":
        return f"unmapped: {fact[1]}"
    return repr(fact)


def _rule_line(fact) -> str:
    kind, collection = fact[1], fact[2]
    if kind == "writer":
        value = f" to {fact[4]}" if fact[4] is not None else ""
        return f"only {', '.join(fact[5])} may set {collection}.{fact[3]}{value}"
    if kind == "immutable_after_create":
        return f"{', '.join(f'{collection}.{f}' for f in fact[3])} can't change after create"
    if kind == "unique":
        return f"{collection} records are unique on ({', '.join(fact[3])})"
    if kind == "required_when":
        return f"{collection}.{fact[3]} is required when {fact[4]}"
    if kind == "lock":
        _, _, _, field, value, scope, locked, unless = fact
        when = f"once {field} has been {value}" if scope == "history" else f"while {field} is {value}"
        tail = f" (unless written by {', '.join(unless)})" if unless else ""
        return f"{collection} {', '.join(locked)} lock {when}{tail}"
    return f"{collection} records can be {_PAST[fact[3]]} only through {', '.join(fact[4])}"


def describe(facts) -> list[str]:
    """The facts in plain words, for `apps authority` and proposal deltas.
    Field access is grouped per principal, collection and verb."""
    by_kind = {}
    for fact in ordered(facts):
        by_kind.setdefault(fact[0], []).append(fact)
    lines = []
    for kind in KIND_ORDER:
        if kind == "access":
            groups = {}
            for fact in by_kind.get(kind, []):
                groups.setdefault((fact[1], fact[2], fact[4]), []).append(fact[3])
            lines += [f"{p} can {verb} {c}: {', '.join(sorted(fields))}"
                      for (p, c, verb), fields in sorted(groups.items())]
        else:
            lines += [_line(fact) for fact in by_kind.get(kind, [])]
    return lines
