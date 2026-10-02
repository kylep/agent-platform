"""Stable error codes for App definitions (design 39).

Builders act on these codes, so a code never changes meaning once shipped:
add a new code rather than repurpose an old one. Every issue carries a JSON
path into the submitted document, the offending value and a suggested fix,
which is what `apps validate` returns.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# code -> the fix suggested when a check has nothing more specific to say.
CODES: dict[str, str] = {
    # Shape (mapped from pydantic).
    "JD-UNKNOWN-KEY": "Remove the key; `apps schema` lists every key a definition accepts.",
    "JD-MISSING": "Add the required key.",
    "JD-TYPE": "Use the JSON type the schema declares for this key.",
    "JD-BOUNDS": "Keep the value inside the bounds the schema declares.",
    "JD-VALUE": "Use one of the values the schema allows here.",
    "JD-NAME": "Use lowercase letters, digits and underscores, starting with a letter "
               "(at most 40 characters).",
    "JD-FORMAT": "Write the value in the format the schema's pattern describes.",
    "JD-INVALID": "Fix the value so it matches the schema.",
    # Releases: syntax that is designed but not yet built.
    "JD-NOT-YET-R2": "This arrives in Release 2; model it with Release 1 features for now.",
    "JD-NOT-YET-R3": "This arrives in Release 3; model it with Release 1 features for now.",
    "JD-REQUEST-PATH": "This isn't planned; file an App Builder request if the App needs it.",
    # Collections.
    "JD-FIELD-TYPE": "Use one of: string, text, int, number, bool, date, datetime, enum, "
                     "ref, url, artifact, list.",
    "JD-LIST-ITEM": "Declare a bounded scalar item or an object with 1-32 scalar fields.",
    "JD-FIELD-RESERVED": "Rename the field; system fields are provided by the platform.",
    "JD-FIELD-BOUNDS": "Make `min` no larger than `max`, both inside the type's limits.",
    "JD-ENUM-VALUES": "List 1-100 distinct, non-empty values.",
    "JD-PRINCIPAL": "Name `owner`, `kyle`, `agent:<name>` or `login:qa`.",
    "JD-PRINCIPAL-VERB": "`login:qa` is a read-only login; list it under `read` only.",
    "JD-PRINCIPAL-DUPLICATE": "List each principal once.",
    "JD-WRITERS": "List tools as `tool:<name>` under `create`, `update` or `delete`.",
    "JD-REF-TARGET": "Point the ref at a collection defined in the same App.",
    "JD-REF-UNLINK-REQUIRED": "Use `on_delete: restrict`, or make the field optional so "
                              "`unlink` can clear it.",
    "JD-RULE-KIND": "Use writer, immutable_after_create or unique.",
    "JD-RULE-FIELD": "Name a field the collection defines (not a system field).",
    "JD-RULE-VALUE": "Use a value the field's type accepts.",
    "JD-RULE-DUPLICATE": "Remove the repeated rule.",
    "JD-RULE-REDUNDANT": "Remove the rule; an immutable collection never updates records.",
    "JD-INDEX-FIELD": "Index a field the collection defines.",
    "JD-INDEX-TYPE": "Index string, enum, ref, int, number, date or datetime fields only.",
    "JD-INDEX-SLOTS": "Index at most two text-like fields, one numeric field and one "
                      "date/datetime field (four in all).",
    "JD-INDEX-DUPLICATE": "List each indexed field once.",
    "JD-RETENTION": "Set exactly one of `max_age` (like `90d` or `12w`) or `max_records`.",
    # Views.
    "JD-VIEW-COLLECTION": "Name a collection defined in the same App.",
    "JD-VIEW-FIELD": "Name a field of the view's collection, or a system field.",
    "JD-VIEW-COUNT": "A count view returns one number: drop `sort`, `limit`, `paging` and "
                     "`fields`, and give it exactly one `{fn: count}` aggregate.",
    "JD-FILTER-OP": "Use eq, ne, in, lt, lte, gt, gte, is_null, within_last or contains.",
    "JD-FILTER-OP-TYPE": "Use an operator that fits the field's type.",
    "JD-FILTER-VALUE": "Use a value of the field's type.",
    "JD-FILTER-ANCHOR": "Use `now` or `max(<field>)` with a date or datetime field.",
    "JD-PARAM-UNDECLARED": "Declare the parameter under `params`.",
    "JD-PARAM-UNUSED": "Use the parameter in a filter, or remove it.",
    "JD-PARAM-TYPE": "Declare the parameter with a type that fits the field.",
    "JD-PARAM-OP": "Use a literal value with this operator.",
    "JD-SORT-DUPLICATE": "Sort on each field once.",
    "JD-SORT-TYPE": "Sort on a scalar or system field, not a list.",
    "JD-TOOL-VIEW-ACTION": "Use an action admitted as a read-only tool view in the operation catalog.",
    "JD-TOOL-VIEW-SOURCE": "Name each source role declared by that action once.",
    "JD-TOOL-VIEW-BINDING": "Approve the tool's source role for read in this App first.",
    "JD-TOOL-VIEW-PARAM": "Declare parameters with the reviewed action's names and types.",
    "JD-TOOL-VIEW-DOMAIN": "Use an indexed source field that is also a view parameter.",
    "JD-TOOL-VIEW-INTERVAL": "Refresh materialized views no more often than every 5 minutes.",
    "JD-TOOL-VIEW-MATERIALIZE": "Keep caching enabled for a materialized view.",
    # Pages.
    "JD-PAGE-COMPONENT": "Use one of the Release 1 components: table, detail, metric, text.",
    "JD-PAGE-VIEW": "Name a view defined in the same App.",
    "JD-PAGE-COLUMN": "Name a field the bound view returns.",
    "JD-PAGE-METRIC": "Bind metrics to count views, and tables or details to record views.",
    "JD-PAGE-PARAM": "Bind every required view parameter to a literal or a page parameter "
                     "of a matching type.",
    "JD-PAGE-LINK": "Link to a page of this App that declares the parameter, or to a "
                    "platform path such as `/tickets`.",
    "JD-PAGE-ACTION": "Name an action template the page declares, on the block's collection.",
    "JD-TEMPLATE-KIND": "Use create, update or delete.",
    "JD-TEMPLATE-FIELD": "Name a field the template's collection defines (not a system field), "
                         "once, in either `presets` or `editable_fields`.",
    "JD-PRESET-VALUE": "Preset a value the field accepts, inside its bounds.",
    "JD-TEMPLATE-REQUIRED": "Preset or make editable every required field.",
    "JD-TEMPLATE-IMMUTABLE": "Immutable collections can't be updated; use create or delete.",
    "JD-TEMPLATE-TOOL-ONLY": "Use the approved tool for this collection's write; a page template cannot bypass it.",
    "JD-TEMPLATE-EMPTY": "Preset or make editable at least one field.",
    # App tools (R1b).
    "JD-TOOL-COLLECTION": "Bind the role to a collection defined in the same App.",
    "JD-TOOL-VERB": "List each verb once, from read, create, update and delete; an "
                    "immutable collection has no update.",
    # Bundles.
    "JD-DUPLICATE-NAME": "Give each definition of a kind a distinct name.",
}


@dataclass(frozen=True)
class DefinitionIssue:
    code: str
    path: str
    message: str
    value: Any = None
    fix: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "path": self.path, "message": self.message,
                "value": _jsonable(self.value), "fix": self.fix}


class DefinitionError(ValueError):
    """One or more definition issues, reported together."""

    def __init__(self, issues: list[DefinitionIssue]):
        self.issues = list(issues)
        super().__init__("; ".join(f"{i.code} at {i.path}: {i.message}" for i in self.issues))

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def as_dict(self) -> dict:
        return {"ok": False, "errors": [i.as_dict() for i in self.issues]}


def issue(code: str, path: str, message: str, value: Any = None,
          fix: str | None = None) -> DefinitionIssue:
    if code not in CODES:
        raise KeyError(f"unregistered definition error code {code}")
    return DefinitionIssue(code=code, path=path, message=message, value=value,
                           fix=fix if fix is not None else CODES[code])


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def json_path(*segments: str | int) -> str:
    """`$`-rooted JSON path; non-identifier keys use bracket notation."""
    out = "$"
    for segment in segments:
        if isinstance(segment, int):
            out += f"[{segment}]"
        elif _IDENT.fullmatch(segment):
            out += f".{segment}"
        else:
            out += f"[{json.dumps(segment)}]"
    return out


def join_path(base: str, *segments: str | int) -> str:
    return base + json_path(*segments)[1:]


def _jsonable(value: Any) -> Any:
    # Offending values echo back to agents; keep them JSON and bounded.
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)[:200]
    return value if len(text) <= 500 else text[:500] + "…"
