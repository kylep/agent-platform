"""Who may do what to an App's records (design 39, "Collections" → "Access",
"Tool-only collections").

Effective access is computed straight from the validated collection
definition: the collection's `access` gives a default per verb, a field's own
`access` overrides read, create or update for that field, and `delete` is
collection-wide. `appdata/authority.py` computes the same facts for the
publish-time subset test; the records engine reads the definition directly so
it checks the caller against exactly what was published, with no second
interpretation in between.

A caller is a principal plus, for a write made through a tool-call credential,
the tool it came through. `owner` in a definition resolves to the App's owner
at check time, so an ownership transfer moves access with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentplatform.appdata.definitions import CollectionDef

# System fields any caller who can see a row may read: they describe the row,
# not its content. `author` and `via` name principals, so they follow the
# collection's read default like a field would.
ROW_FIELDS = ("id", "created_at", "updated_at", "version", "collection_version")
PRINCIPAL_FIELDS = ("author", "via")


@dataclass(frozen=True)
class Caller:
    """`principal` is `kyle`, `agent:<name>`, `login:qa`, or a service
    principal `tool:<name>`; `via_tool` is `tool:<name>` when the call came
    through that tool's call credential. `system` is the platform itself
    (retention), which acts under the App's approved facts, not a principal's.
    """
    principal: str
    via_tool: str | None = None
    system: bool = False

    @property
    def author(self) -> str:
        return self.principal

    @property
    def via(self) -> str | None:
        return self.via_tool


SYSTEM_RETENTION = Caller(principal="system:retention", system=True)


class RecordError(Exception):
    """A refused record operation. `code` is stable (agents act on it);
    `status` is the HTTP status the API answers with."""

    def __init__(self, code: str, message: str, status: int = 422,
                 detail: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


def matches(caller: Caller, names: list[str] | None, owner: str) -> bool:
    """Does the caller hold one of the principals a definition lists?"""
    if caller.system:
        return True
    for name in names or ():
        if name == "owner":
            if caller.principal == owner:
                return True
        elif name == caller.principal:
            return True
    return False


class Access:
    """One collection's effective access for one caller."""

    def __init__(self, collection: CollectionDef, caller: Caller, owner: str):
        self.collection = collection
        self.caller = caller
        self.owner = owner

    def _field_list(self, field: str, verb: str) -> list[str]:
        spec = self.collection.fields.get(field)
        override = getattr(spec.access, verb) if spec is not None and spec.access else None
        return override if override is not None else getattr(self.collection.access, verb)

    def can_read(self, field: str) -> bool:
        if field in ROW_FIELDS:
            return self.can_see_rows()
        if field in PRINCIPAL_FIELDS:
            return matches(self.caller, self.collection.access.read, self.owner)
        return matches(self.caller, self._field_list(field, "read"), self.owner)

    def can_write(self, field: str, verb: str) -> bool:
        return matches(self.caller, self._field_list(field, verb), self.owner)

    def can_see_rows(self) -> bool:
        """A caller sees a collection's rows if it may read the collection or
        at least one field of it; everything else in the row is per field."""
        if matches(self.caller, self.collection.access.read, self.owner):
            return True
        return any(matches(self.caller, self._field_list(f, "read"), self.owner)
                   for f in self.collection.fields)

    def can_verb(self, verb: str) -> bool:
        """The collection-level verb: who may create, update or delete at all.
        Create and update also pass when the caller may write some field, so a
        field-level grant narrower than the default is usable."""
        if verb == "delete":
            return matches(self.caller, self.collection.access.delete, self.owner)
        if matches(self.caller, getattr(self.collection.access, verb), self.owner):
            return True
        return any(self.can_write(f, verb) for f in self.collection.fields)

    def tool_allowed(self, verb: str) -> bool:
        """Tool-only writers: a verb the collection reserves to named tools
        succeeds only through one of those tools' credentials."""
        writers = self.collection.writers
        tools = getattr(writers, verb) if writers is not None else None
        if tools is None or self.caller.system:
            return True
        tool = self.caller.via_tool
        if tool is None and self.caller.principal.startswith("tool:"):
            tool = self.caller.principal
        return tool in tools

    def require_verb(self, verb: str) -> None:
        if not self.can_verb(verb):
            raise RecordError("AD-FORBIDDEN", f"{self.caller.principal} may not {verb} "
                              f"records in {self.collection.collection}", 403)
        if not self.tool_allowed(verb):
            tools = getattr(self.collection.writers, verb)
            raise RecordError("AD-TOOL-ONLY", f"{self.collection.collection} records are "
                              f"{verb}d only through {', '.join(tools)}", 403)

    def require_rows(self) -> None:
        if not self.can_see_rows():
            raise RecordError("AD-FORBIDDEN", f"{self.caller.principal} may not read "
                              f"{self.collection.collection}", 403)

    def require_readable(self, field: str, use: str) -> None:
        """Filtering, sorting or anchoring on a field is reading it: refused
        outright, never silently dropped, so a predicate can't be used to infer
        a value the caller couldn't see."""
        if not self.can_read(field):
            raise RecordError("AD-PREDICATE-FORBIDDEN",
                              f"{self.caller.principal} may not {use} on {field}, "
                              "which it can't read", 403, {"field": field})
