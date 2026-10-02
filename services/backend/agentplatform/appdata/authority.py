"""App authority facts: what an App's definitions let anyone see or do.

Design 39, "The authority model". A bundle self-publishes when every fact it
computes is present in the approved state's facts, or narrower; anything else
is a widening Kyle approves. This module is pure: it works on plain dict
definitions so it stays independent of the definition models
(`appdata/definitions.py`), which validate the same shape.

Expected definition shape (lists may also be given as {name: item} maps):

    {
      "collections": [{
        "collection": "habits",
        "write_mode": "editable" | "immutable" | "versioned",   # default editable
        "access": {"read": [...], "create": [...], "update": [...], "delete": [...]},
        "fields": {
          "day":  {"type": "date", "access": {"read": ["owner"]}},   # per-verb override
          "src":  {"type": "url", "link": true},                     # outbound link
          "run":  {"type": "ref", "ref": "runs", "on_delete": "restrict" | "unlink" | "cascade"},
          "steps": {"type": "list", "items": {"type": "object", "fields": {...}}}
        },
        "rules": [{"kind": "writer", "field": "f", "value": v?, "writers": [...]},
                  {"kind": "immutable_after_create", "fields": [...]},
                  {"kind": "unique", "fields": [...]},
                  {"kind": "required_when", "field": "f", "when": {"g": v}},
                  {"kind": "lock", "when": {"field": "f", "value": v, "in": "current" | "history"},
                   "lock": [...], "unless_writer": [...]}],
        "writers": {"create": ["tool:x"], "update": ["tool:x"]},   # tool-only writers
        "retention": {"max_age": "90d" | "12w" | "1y" | <days>, "max_records": <n>},
        "indexed": [...]
      }],
      "views": [{"view": "v", "collection": "habits", "filter": [...], ...}
                | {"view": "v", "tool": "t", "action": "a", "sources": ["role"], ...}],
      "pages": [{"page": "p", "renderer": "typed/v2", "title": "...",
                 "blocks": [{"kind": "table", ...}],
                 "actions": [{"alias": "x", "template": "add"}
                             | {"alias": "x", "tool": "t", "action": "a", "sources": ["role"],
                                "verbs": [...], "budget": {...}}]}],
      "templates": [{"template": "add", "kind": "create" | "update" | "delete" | "new_version",
                     "collection": "habits", "presets": {...}, "editable_fields": [...]}],
      "app_tools": [{"tool": "t", "roles": {"role": {"collection": "habits", "verbs": [...]}}}],
      "service_principals": [{"principal": "tool:t", "collections": {"habits": [...]}}]
    }

Principals are strings: the symbolic `owner`, `kyle`, and named principals
such as `agent:bob` or `login:qa`. A verb missing from a collection's `access`
gets the narrowest default (owner and Kyle read, the owner writes, nobody
deletes). Keys the engine doesn't know become `unmapped` facts, which are
always a proposal: unknown means proposal.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

SYSTEM_FIELDS = ("id", "created_at", "updated_at", "author", "via", "version",
                 "collection_version")
FIELD_VERBS = ("read", "create", "update")
COLLECTION_VERBS = FIELD_VERBS + ("delete",)
DEFAULT_ACCESS = {"read": ("owner", "kyle"), "create": ("owner",), "update": ("owner",),
                  "delete": ()}
# New fields and collections start private: owner and Kyle only. Anything
# beyond is a proposal. Kyle writes only through templates, which are always
# proposals, so his facts here open nothing by themselves.
PRIVATE = {verb: {"owner", "kyle"} for verb in ("read", "create", "update", "delete")}

FIELD_TYPES = {"string", "text", "int", "number", "bool", "date", "datetime", "enum", "ref",
               "url", "artifact", "list", "object"}
WRITE_MODES = {"editable", "immutable", "versioned"}
ON_DELETE = {"restrict", "unlink", "cascade"}
TEMPLATE_KINDS = {"create", "update", "delete", "new_version"}
BLOCK_KINDS = {"table", "detail", "metric", "text", "heading", "paragraph", "chart",
               "calendar", "sparkline", "stat_row", "list_filter", "image", "refresh"}

TOP_KEYS = {"collections", "views", "pages", "templates", "app_tools", "service_principals",
            "notes", "timezone"}
COLLECTION_KEYS = {"collection", "title", "description", "fields", "write_mode", "access",
                   "rules", "writers", "retention", "indexed"}
FIELD_KEYS = {"type", "required", "description", "label", "title", "max", "min", "values",
              "default", "ref", "on_delete", "link", "access", "indexed", "items", "fields",
              "pin_version", "unit", "format"}
RULE_KEYS = {"writer": {"kind", "field", "value", "writers"},
             "immutable_after_create": {"kind", "fields"},
             "unique": {"kind", "fields"},
             "required_when": {"kind", "field", "when"},
             "lock": {"kind", "when", "lock", "unless_writer"}}
TEMPLATE_KEYS = {"template", "kind", "collection", "presets", "editable_fields", "label",
                 "title", "description", "copy_current"}
VIEW_KEYS = {"view", "collection", "title", "description", "filter", "sort", "limit", "params",
             "group_by", "aggregates", "fill_missing", "expand", "fields", "page_size",
             "normalize", "downsample"}
TOOL_VIEW_KEYS = {"view", "tool", "action", "sources", "params", "cache", "materialize",
                  "title", "description", "limit"}
PAGE_KEYS = {"page", "renderer", "title", "description", "blocks", "actions", "params", "nav"}
TEMPLATE_ACTION_KEYS = {"alias", "template", "label", "confirm"}
TOOL_ACTION_KEYS = {"alias", "tool", "action", "sources", "verbs", "budget", "label", "confirm"}

KIND_ORDER = ("access", "delete", "delete_reach", "retention", "rule", "template", "app_tool",
              "tool_view", "tool_action", "service_principal", "link", "unmapped")
_DURATION = re.compile(r"^(\d+)([dwy])$")
_DAYS = {"d": 1, "w": 7, "y": 365}
_PAST = {"create": "created", "update": "updated", "delete": "deleted"}


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


def _items(value, name_key):
    if isinstance(value, dict):
        return [(name, item) for name, item in value.items()]
    if isinstance(value, list):
        return [(item.get(name_key) if isinstance(item, dict) else None, item) for item in value]
    return None


def _strings(value):
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(sorted(set(value)))
    return None


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


def compute_facts(definitions: dict) -> frozenset:
    """Every authority fact the definitions grant, as a hashable set of tuples."""
    out = _Facts()
    if not isinstance(definitions, dict):
        out.unmapped("definitions")
        return frozenset(out.facts)
    for key in definitions:
        if key not in TOP_KEYS:
            out.unmapped(key)

    tools = _app_tools(out, definitions.get("app_tools", []))
    edges = {}
    for name, collection in _items(definitions.get("collections", []), "collection") or []:
        _collection(out, name, collection, edges)
    _delete_reach(out, edges)
    for name, template in _items(definitions.get("templates", []), "template") or []:
        _template(out, name, template)
    for name, view in _items(definitions.get("views", []), "view") or []:
        _view(out, name, view, tools)
    for name, page in _items(definitions.get("pages", []), "page") or []:
        _page(out, name, page, tools)
    for _, sp in _items(definitions.get("service_principals", []), "principal") or []:
        _service_principal(out, sp)
    for key in ("collections", "views", "pages", "templates", "app_tools", "service_principals"):
        if key in definitions and _items(definitions[key], "") is None:
            out.unmapped(key)
    return frozenset(out.facts)


def _collection(out, name, c, edges):
    path = f"collections.{name}"
    if not isinstance(name, str) or not isinstance(c, dict):
        out.unmapped(path)
        return
    out.unknown_keys(c, COLLECTION_KEYS, path)
    out.add("declared", name)
    mode = c.get("write_mode", "editable")
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
        principals = _strings(access[verb]) if verb in access else DEFAULT_ACCESS[verb]
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
        override = spec.get("access", {})
        if not isinstance(override, dict):
            out.unmapped(f"{fpath}.access")
            override = {}
        out.unknown_keys(override, FIELD_VERBS, f"{fpath}.access")
        for verb in verbs:
            principals = _strings(override[verb]) if verb in override else defaults[verb]
            if principals is None:
                out.unmapped(f"{fpath}.access.{verb}")
                continue
            for principal in principals:
                out.add("access", principal, name, field, verb)
        _field_shape(out, name, field, spec, fpath, edges, top=True)

    rules = c.get("rules", [])
    if not isinstance(rules, list):
        out.unmapped(f"{path}.rules")
        rules = []
    for i, rule in enumerate(rules):
        _rule(out, name, rule, f"{path}.rules.{i}")
    writers = c.get("writers", {})
    if not isinstance(writers, dict):
        out.unmapped(f"{path}.writers")
        writers = {}
    for verb, tools in writers.items():
        tools = _strings(tools)
        if verb not in COLLECTION_VERBS or tools is None:
            out.unmapped(f"{path}.writers.{verb}")
            continue
        out.add("rule", "writers", name, verb, tools)
    _retention(out, name, c.get("retention"), f"{path}.retention")


def _field_shape(out, collection, field, spec, path, edges, top):
    """Links and refs, including inside lists of objects. Only top-level fields
    carry access; a sub-field can't widen its parent's readers."""
    out.unknown_keys(spec, FIELD_KEYS if top else FIELD_KEYS - {"access"}, path)
    kind = spec.get("type")
    if kind not in FIELD_TYPES:
        out.unmapped(f"{path}.type={kind}")
    if spec.get("link") is True:
        out.add("link", collection, field)
    elif "link" in spec and spec["link"] is not False:
        out.unmapped(f"{path}.link")
    if kind == "ref":
        mode = spec.get("on_delete", "restrict")
        target = spec.get("ref")
        if mode not in ON_DELETE or not isinstance(target, str):
            out.unmapped(f"{path}.ref")
        else:
            edges.setdefault(target, set()).add((collection, mode))
    items = spec.get("items")
    if isinstance(items, dict):
        _field_shape(out, collection, field, items, f"{path}.items", edges, top=False)
    elif items is not None:
        out.unmapped(f"{path}.items")
    subfields = spec.get("fields")
    if isinstance(subfields, dict):
        for sub, subspec in subfields.items():
            if isinstance(subspec, dict):
                _field_shape(out, collection, f"{field}.{sub}", subspec,
                             f"{path}.fields.{sub}", edges, top=False)
            else:
                out.unmapped(f"{path}.fields.{sub}")
    elif subfields is not None:
        out.unmapped(f"{path}.fields")


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
    if retention is None:
        return
    if not isinstance(retention, dict):
        out.unmapped(path)
        return
    out.unknown_keys(retention, {"max_age", "max_records"}, path)
    if "max_age" in retention:
        age = retention["max_age"]
        match = _DURATION.match(age) if isinstance(age, str) else None
        if isinstance(age, int) and not isinstance(age, bool) and age > 0:
            out.add("retention", collection, "max_age_days", age)
        elif match:
            out.add("retention", collection, "max_age_days",
                    int(match.group(1)) * _DAYS[match.group(2)])
        else:
            out.unmapped(f"{path}.max_age")
    if "max_records" in retention:
        count = retention["max_records"]
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            out.add("retention", collection, "max_records", count)
        else:
            out.unmapped(f"{path}.max_records")


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
        value = canon(rule["value"]) if "value" in rule else None
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


def _template(out, name, t):
    path = f"templates.{name}"
    if not isinstance(t, dict) or t.get("kind") not in TEMPLATE_KINDS \
            or not isinstance(t.get("collection"), str):
        out.unmapped(path)
        return
    out.unknown_keys(t, TEMPLATE_KEYS, path)
    presets = t.get("presets", {})
    editable = _strings(t.get("editable_fields", []))
    if not isinstance(presets, dict) or editable is None:
        out.unmapped(path)
        return
    kind, collection = t["kind"], t["collection"]
    out.add("template", name, kind, collection, canon(presets), editable)
    # Approving the form approves the access it needs to work.
    if kind == "delete":
        out.add("delete", "kyle", collection)
        return
    verb = "create" if kind == "create" else "update"
    for field in set(editable) | set(presets):
        out.add("access", "kyle", collection, field, verb)


def _app_tools(out, value):
    """App tool facts, plus each tool's role map for resolving view and action sources."""
    roles_by_tool = {}
    for _, entry in _items(value, "tool") or []:
        tool = entry.get("tool") if isinstance(entry, dict) else None
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


def _view(out, name, view, tools):
    path = f"views.{name}"
    if not isinstance(view, dict):
        out.unmapped(path)
        return
    if "tool" not in view:
        out.unknown_keys(view, VIEW_KEYS, path)
        return
    out.unknown_keys(view, TOOL_VIEW_KEYS, path)
    tool, action = view.get("tool"), view.get("action")
    if not isinstance(tool, str) or not isinstance(action, str):
        out.unmapped(path)
        return
    sources = _sources(out, tool, view.get("sources", []), tools, path)
    if sources is not None:
        out.add("tool_view", tool, action, sources, ("read",), None)


def _page(out, name, page, tools):
    path = f"pages.{name}"
    if not isinstance(page, dict):
        out.unmapped(path)
        return
    out.unknown_keys(page, PAGE_KEYS, path)
    blocks, actions = page.get("blocks", []), page.get("actions", [])
    if not isinstance(blocks, list) or not isinstance(actions, list):
        out.unmapped(path)
        return
    for i, block in enumerate(blocks):
        kind = block.get("kind") if isinstance(block, dict) else None
        if kind not in BLOCK_KINDS:
            out.unmapped(f"{path}.blocks.{i}.kind={kind}")
        elif "tool" in block:
            # Tool bindings live in views and actions, where they're facts.
            out.unmapped(f"{path}.blocks.{i}.tool")
    for i, action in enumerate(actions):
        alias = action.get("alias", i) if isinstance(action, dict) else i
        apath = f"{path}.actions.{alias}"
        if isinstance(action, dict) and "template" in action:
            out.unknown_keys(action, TEMPLATE_ACTION_KEYS, apath)
        elif isinstance(action, dict) and "tool" in action:
            out.unknown_keys(action, TOOL_ACTION_KEYS, apath)
            tool, act = action.get("tool"), action.get("action")
            verbs = _strings(action.get("verbs", []))
            if not isinstance(tool, str) or not isinstance(act, str) or verbs is None:
                out.unmapped(apath)
                continue
            sources = _sources(out, tool, action.get("sources", []), tools, apath)
            if sources is not None:
                out.add("tool_action", tool, act, sources, verbs, canon(action.get("budget")))
        else:
            out.unmapped(apath)


def _service_principal(out, sp):
    principal = sp.get("principal") if isinstance(sp, dict) else None
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

def settle_new_fields(definitions: dict, approved_definitions: dict) -> dict:
    """Pin explicit private access on fields new to an existing collection.

    A field with no access of its own inherits the collection's defaults, so a
    field added to a shared collection would quietly reach every reader.
    Settling narrows each unset verb to its default intersected with the
    private set; publish stores the settled bundle so the field stays private
    until a proposal shares it. New collections are left alone: all their facts
    are new and checked as such."""
    settled = copy.deepcopy(definitions)
    approved = {name: c for name, c in _items(approved_definitions.get("collections", []),
                                               "collection") or [] if isinstance(c, dict)}
    for name, c in _items(settled.get("collections", []), "collection") or []:
        if name not in approved or not isinstance(c, dict) or not isinstance(c.get("fields"), dict):
            continue
        old_fields = approved[name].get("fields") or {}
        access = c.get("access") if isinstance(c.get("access"), dict) else {}
        for field, spec in c["fields"].items():
            if field in old_fields or not isinstance(spec, dict):
                continue
            pinned = dict(spec.get("access") or {})
            for verb in FIELD_VERBS:
                if verb not in pinned:
                    default = access.get(verb, DEFAULT_ACCESS[verb])
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
