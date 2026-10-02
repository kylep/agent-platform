"""The App definition language, Release 1 (design 39).

Collections, views and `typed/v2` pages are data an agent writes and the
platform interprets; nothing here executes. Validation runs in three passes:

1. a scan for syntax the design defers to a later release, so a builder
   reaching for `versioned` or a chart hears "not available yet (Release N)"
   rather than a generic shape error;
2. pydantic shape validation (closed models, `extra="forbid"`, strict types);
3. semantic checks that need sibling definitions: a view's fields against
   its collection, a page's columns against its views, refs inside the App.

Every failure is a `DefinitionIssue` with a stable code, a JSON path into the
submitted document, the offending value and a suggested fix. All issues are
reported together, so one `apps validate` round trip shows the whole picture.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError,
                      field_validator, model_validator)
from pydantic_core import PydanticCustomError

from agentplatform.appdata.errors import (CODES, DefinitionError, DefinitionIssue, issue,
                                          join_path)

# Bumped whenever the language accepts something new, so builders (and the
# skill) can check `apps schema` for a capability before relying on it.
CAPABILITIES_VERSION = 3   # 3: tool views (R1b)

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_AGENT_RE = re.compile(r"^agent:[a-z0-9][a-z0-9-]{0,62}$")   # agentspec._NAME_RE
_TOOL_RE = re.compile(r"^tool:[a-z][a-z0-9_]{1,40}$")        # toolregistry._NAME
APP_VERBS = ("read", "create", "update", "delete")             # toolregistry.APP_VERBS
_ANCHOR_RE = re.compile(r"^(now|max\(([a-z][a-z0-9_]{0,39})\))$")

FIELD_TYPES = ("string", "text", "int", "number", "bool", "date", "datetime", "enum",
               "ref", "url", "artifact")
# Read-only fields every record has; views and pages may use them, rules may not.
SYSTEM_FIELDS = {"id": "id", "created_at": "datetime", "updated_at": "datetime",
                 "author": "principal", "via": "principal", "version": "int",
                 "collection_version": "int"}
FILTER_OPS = ("eq", "ne", "in", "lt", "lte", "gt", "gte", "is_null", "within_last",
              "contains")
COMPONENTS = ("table", "detail", "metric", "text")
TEMPLATE_KINDS = ("create", "update", "delete")

MAX_STRING = 1_000
MAX_TEXT = 16_000
MAX_URL = 2_000
MAX_SAFE_INT = 2**53 - 1
VIEW_LIMIT = 200
MAX_INDEXED = 4
MAX_RETENTION_DAYS = 3_650
MAX_RETENTION_RECORDS = 10_000_000

# Index side columns by value family, in the order slots fill. The fixed
# composite indexes all lead with ix_text1, so text slots fill in field order.
INDEX_SLOTS = {"text": ("ix_text1", "ix_text2"), "num": ("ix_num1",),
               "time": ("ix_time1",)}
_INDEX_FAMILY = {"string": "text", "enum": "text", "ref": "text", "int": "num",
                 "number": "num", "date": "time", "datetime": "time"}

# Syntax the design schedules for later. The value is the release number, or
# None for features cut to the App Builder request path.
DEFERRED: dict[str, int | None] = {
    "versioned": 2, "list": 2, "pin_version": 2, "cascade": 2, "exists": 2,
    "not_exists": 2, "new_version": 2, "tool_actions": 2, "detail_history": 2,
    "group_by": 3, "aggregates": 3, "fill_missing": 3, "normalize": 3, "downsample": 3,
    "chart": 3, "calendar": 3, "sparkline": 3, "stat_row": 3, "list_filter": 3,
    "image": 3, "refresh": 3,
    "required_when": None, "lock": None, "message_ref": None,
}
_DEFERRED_VIEW_KEYS = ("group_by", "fill_missing", "normalize", "downsample")
_DEFERRED_AGGREGATES = ("sum", "min", "max", "avg")
_DEFERRED_COMPONENTS = ("chart", "calendar", "sparkline", "stat_row", "list_filter", "image")


def _err(code: str, message: str, rel: list | None = None, fix: str | None = None):
    # Messages are pydantic templates: never interpolate user input into them.
    context: dict[str, Any] = {"rel": rel or []}
    if fix is not None:
        context["fix"] = fix
    return PydanticCustomError(code, message, context)


# --- scalar types --------------------------------------------------------------

def _name(value: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise _err("JD-NAME", "not a valid name")
    return value


def _field_name(value: str) -> str:
    _name(value)
    if value in SYSTEM_FIELDS:
        raise _err("JD-FIELD-RESERVED", "this name is a system field")
    return value


def _principal(value: str, *, read: bool) -> str:
    if value.startswith("tool:"):
        raise _err("JD-PRINCIPAL", "tools are not access principals",
                   fix="Restrict writes to a tool with the collection's `writers`.")
    if value in ("owner", "kyle") or _AGENT_RE.fullmatch(value):
        return value
    if value == "login:qa":
        if not read:
            raise _err("JD-PRINCIPAL-VERB", "login:qa can only read")
        return value
    raise _err("JD-PRINCIPAL", "not a known principal")


def _read_principal(value: str) -> str:
    return _principal(value, read=True)


def _write_principal(value: str) -> str:
    return _principal(value, read=False)


def _tool_writer(value: str) -> str:
    if not _TOOL_RE.fullmatch(value):
        raise _err("JD-WRITERS", "tool-only writers must be tool:<name>")
    return value


def _tool_name(value: str) -> str:
    # A tool's registry name, and a definition name too (it keys the
    # definition row): toolregistry._NAME's shape inside NAME_RE's.
    if not (NAME_RE.fullmatch(value) and _TOOL_RE.fullmatch("tool:" + value)):
        raise _err("JD-NAME", "not a valid tool name")
    return value


def _distinct_verbs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value in seen:
            raise _err("JD-TOOL-VERB", "verb listed twice", rel=[index])
        seen.add(value)
    return values


def _distinct_principals(values: list[str]) -> list[str]:
    seen: set[str] = set()
    for index, value in enumerate(values):
        if value in seen:
            raise _err("JD-PRINCIPAL-DUPLICATE", "principal listed twice", rel=[index])
        seen.add(value)
    return values


def _anchor(value: str) -> str:
    if not _ANCHOR_RE.fullmatch(value):
        raise _err("JD-FILTER-ANCHOR", "anchor must be now or max(<field>)")
    return value


Name = Annotated[str, AfterValidator(_name)]
FieldName = Annotated[str, AfterValidator(_field_name)]
ToolName = Annotated[str, AfterValidator(_tool_name)]
Label = Annotated[str, Field(min_length=1, max_length=80)]
Description = Annotated[str, Field(max_length=500)]
ReadPrincipals = Annotated[
    list[Annotated[str, AfterValidator(_read_principal)]],
    Field(max_length=20), AfterValidator(_distinct_principals)]
WritePrincipals = Annotated[
    list[Annotated[str, AfterValidator(_write_principal)]],
    Field(max_length=20), AfterValidator(_distinct_principals)]
ToolWriters = Annotated[
    list[Annotated[str, AfterValidator(_tool_writer)]],
    Field(min_length=1, max_length=10), AfterValidator(_distinct_principals)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# --- collections ---------------------------------------------------------------

class FieldAccess(_Model):
    """Per-field overrides of the collection's defaults; None inherits."""
    read: ReadPrincipals | None = None
    create: WritePrincipals | None = None
    update: WritePrincipals | None = None


class CollectionAccess(_Model):
    read: ReadPrincipals = Field(default_factory=lambda: ["owner", "kyle"])
    create: WritePrincipals = Field(default_factory=lambda: ["owner"])
    update: WritePrincipals = Field(default_factory=lambda: ["owner"])
    delete: WritePrincipals = Field(default_factory=lambda: ["owner"])


class Writers(_Model):
    """Tool-only writers: these verbs succeed only through the named tools'
    call credentials, so code-enforced guards can't be bypassed."""
    create: ToolWriters | None = None
    update: ToolWriters | None = None
    delete: ToolWriters | None = None

    @model_validator(mode="after")
    def _some_verb(self):
        if self.create is None and self.update is None and self.delete is None:
            raise _err("JD-WRITERS", "writers names no verb")
        return self


class _FieldBase(_Model):
    required: bool = False
    label: Label | None = None
    description: Description | None = None
    access: FieldAccess | None = None


def _check_range(model, low, high):
    if low is not None and high is not None and low > high:
        raise _err("JD-FIELD-BOUNDS", "min is larger than max", rel=["min"])
    return model


class StringField(_FieldBase):
    type: Literal["string"]
    min: Annotated[int, Field(ge=0, le=MAX_STRING)] | None = None
    max: Annotated[int, Field(ge=1, le=MAX_STRING)] = MAX_STRING

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, self.min, self.max)


class TextField(_FieldBase):
    type: Literal["text"]
    min: Annotated[int, Field(ge=0, le=MAX_TEXT)] | None = None
    max: Annotated[int, Field(ge=1, le=MAX_TEXT)] = MAX_TEXT

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, self.min, self.max)


class IntField(_FieldBase):
    type: Literal["int"]
    min: Annotated[int, Field(ge=-MAX_SAFE_INT, le=MAX_SAFE_INT)] | None = None
    max: Annotated[int, Field(ge=-MAX_SAFE_INT, le=MAX_SAFE_INT)] | None = None

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, self.min, self.max)


class NumberField(_FieldBase):
    model_config = ConfigDict(allow_inf_nan=False)
    type: Literal["number"]
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _range(self):
        return _check_range(self, self.min, self.max)


class BoolField(_FieldBase):
    type: Literal["bool"]


class DateField(_FieldBase):
    type: Literal["date"]


class DatetimeField(_FieldBase):
    type: Literal["datetime"]


class EnumField(_FieldBase):
    type: Literal["enum"]
    values: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]],
                      Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _distinct(self):
        if len(set(self.values)) != len(self.values):
            raise _err("JD-ENUM-VALUES", "enum values repeat", rel=["values"])
        return self


class RefField(_FieldBase):
    """A reference to a record in the same App. `cascade` is Release 2."""
    type: Literal["ref"]
    collection: Name
    on_delete: Literal["restrict", "unlink"] = "restrict"

    @model_validator(mode="after")
    def _unlink_clears(self):
        if self.on_delete == "unlink" and self.required:
            raise _err("JD-REF-UNLINK-REQUIRED", "unlink would clear a required field",
                       rel=["on_delete"])
        return self


class UrlField(_FieldBase):
    """Plain text unless `link: true`, which is an outbound-link fact."""
    type: Literal["url"]
    max: Annotated[int, Field(ge=1, le=MAX_URL)] = MAX_URL
    link: bool = False


class ArtifactField(_FieldBase):
    type: Literal["artifact"]


FieldSpec = Annotated[
    Union[StringField, TextField, IntField, NumberField, BoolField, DateField,
          DatetimeField, EnumField, RefField, UrlField, ArtifactField],
    Field(discriminator="type")]


class WriterRule(_Model):
    """Only `writers` may set `field` (or, with `value`, set it to that value)."""
    kind: Literal["writer"]
    field: Name
    value: Any = None
    writers: Annotated[WritePrincipals, Field(min_length=1)]


class ImmutableAfterCreateRule(_Model):
    kind: Literal["immutable_after_create"]
    fields: Annotated[list[Name], Field(min_length=1, max_length=64)]


class UniqueRule(_Model):
    kind: Literal["unique"]
    fields: Annotated[list[Name], Field(min_length=1, max_length=4)]


Rule = Annotated[Union[WriterRule, ImmutableAfterCreateRule, UniqueRule],
                 Field(discriminator="kind")]


class Retention(_Model):
    """Pruned daily by age (`90d`, `12w`) or count. An authority fact."""
    max_age: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,3}[dw]$")] | None = None
    max_records: Annotated[int, Field(ge=1, le=MAX_RETENTION_RECORDS)] | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.max_age is None) == (self.max_records is None):
            raise _err("JD-RETENTION", "set exactly one of max_age or max_records")
        if self.max_age is not None and retention_days(self) > MAX_RETENTION_DAYS:
            raise _err("JD-BOUNDS", "max_age is longer than ten years", rel=["max_age"],
                       fix=f"Keep max_age at or under {MAX_RETENTION_DAYS}d.")
        return self


class CollectionDef(_Model):
    collection: Name
    description: Description | None = None
    fields: Annotated[dict[FieldName, FieldSpec], Field(min_length=1, max_length=64)]
    write_mode: Literal["editable", "immutable"] = "editable"
    access: CollectionAccess = Field(default_factory=CollectionAccess)
    writers: Writers | None = None
    rules: Annotated[list[Rule], Field(max_length=20)] = Field(default_factory=list)
    indexed: Annotated[list[Name], Field(max_length=MAX_INDEXED)] = Field(
        default_factory=list)
    retention: Retention | None = None

    @property
    def name(self) -> str:
        return self.collection


def retention_days(retention: Retention) -> int | None:
    if retention.max_age is None:
        return None
    count, unit = int(retention.max_age[:-1]), retention.max_age[-1]
    return count * (7 if unit == "w" else 1)


def index_columns(collection: CollectionDef) -> dict[str, str]:
    """Indexed field -> side column, filled per type family in listed order.

    Only meaningful for a validated collection (overflow is a JD-INDEX-SLOTS
    error there)."""
    free = {family: list(slots) for family, slots in INDEX_SLOTS.items()}
    out: dict[str, str] = {}
    for name in collection.indexed:
        family = _INDEX_FAMILY.get(collection.fields[name].type)
        if family is not None and free[family]:
            out[name] = free[family].pop(0)
    return out


# --- views ---------------------------------------------------------------------

ParamType = Literal["string", "int", "number", "bool", "date", "datetime"]


class ParamSpec(_Model):
    type: ParamType
    required: bool = False
    default: Any = None

    @model_validator(mode="after")
    def _default_fits(self):
        if "default" in self.model_fields_set and not _fits_param(self.type, self.default):
            raise _err("JD-PARAM-TYPE", "default does not match the parameter type",
                       rel=["default"])
        return self


class SortKey(_Model):
    field: Name
    dir: Literal["asc", "desc"] = "asc"


_VALUE_SCHEMA = {"description": "A literal of the field's type, or {\"param\": name}."}


class CompareFilter(_Model):
    field: Name
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte"]
    value: Any = Field(json_schema_extra=_VALUE_SCHEMA)


class InFilter(_Model):
    field: Name
    op: Literal["in"]
    value: Annotated[list[Any], Field(min_length=1, max_length=50)]

    @field_validator("value", mode="before")
    @classmethod
    def _no_param(cls, value):
        if _is_param_ref(value):
            raise _err("JD-PARAM-OP", "in takes a literal list, not a parameter")
        return value


class IsNullFilter(_Model):
    field: Name
    op: Literal["is_null"]
    value: bool


class WithinLastFilter(_Model):
    """`12w` is the current partial week plus 11 full weeks, in App time.
    `anchor: max(<field>)` measures back from the newest value instead of now."""
    field: Name
    op: Literal["within_last"]
    value: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,3}[hdwmy]$")]
    anchor: Annotated[str, AfterValidator(_anchor)] = "now"


class ContainsFilter(_Model):
    """Escaped, case-insensitive substring match."""
    field: Name
    op: Literal["contains"]
    value: Any = Field(json_schema_extra=_VALUE_SCHEMA)


Filter = Annotated[Union[CompareFilter, InFilter, IsNullFilter, WithinLastFilter,
                         ContainsFilter], Field(discriminator="op")]


class Aggregate(_Model):
    fn: Literal["count"]
    as_: Name = Field(alias="as")


class ViewDef(_Model):
    view: Name
    collection: Name
    description: Description | None = None
    fields: Annotated[list[Name], Field(min_length=1, max_length=64)] | None = None
    params: Annotated[dict[Name, ParamSpec], Field(max_length=10)] = Field(
        default_factory=dict)
    filter: Annotated[list[Filter], Field(max_length=10)] = Field(default_factory=list)
    sort: Annotated[list[SortKey], Field(max_length=3)] = Field(default_factory=list)
    limit: Annotated[int, Field(ge=1, le=VIEW_LIMIT)] = 50
    paging: bool = False
    aggregates: Annotated[list[Aggregate], Field(max_length=5)] = Field(
        default_factory=list)

    @property
    def name(self) -> str:
        return self.view

    @property
    def is_count(self) -> bool:
        """Release 1's only aggregate: one ungrouped count, for a metric."""
        return bool(self.aggregates)


class MaterializeDef(_Model):
    every: Annotated[str, Field(pattern=r"^[1-9][0-9]*m$")]
    # A source collection's indexed field. It also names the view parameter
    # filled with each distinct value when the materializer refreshes.
    domain: Name

    @model_validator(mode="after")
    def _minimum_interval(self):
        if int(self.every[:-1]) < 5:
            raise _err("JD-TOOL-VIEW-INTERVAL", "materialize.every is at least 5m",
                       rel=["every"])
        return self


class ToolViewDef(_Model):
    """A reviewed tool read action bound to this App's approved source roles."""
    view: Name
    tool: ToolName
    action: Name
    sources: Annotated[list[Name], Field(min_length=1, max_length=20)]
    description: Description | None = None
    params: Annotated[dict[Name, ParamSpec], Field(max_length=10)] = Field(
        default_factory=dict)
    cache: Literal["default", "none"] = "default"
    materialize: MaterializeDef | None = None

    @property
    def name(self) -> str:
        return self.view

    @property
    def is_count(self) -> bool:
        from agentplatform.operation_catalog import view_action
        item = view_action(self.tool, self.action)
        props = (item or {}).get("output_schema", {}).get("properties", {})
        return "count" in props and props["count"].get("type") in ("integer", "number")


# --- pages ---------------------------------------------------------------------

class Column(_Model):
    field: Name
    label: Label | None = None
    format: Literal["auto", "text", "number", "percent", "date", "datetime",
                    "relative_time", "bool"] = "auto"


class RowLink(_Model):
    """Opens another page of the App with the row's record id as `param`."""
    page: Name
    param: Name


class Link(_Model):
    """Links stay inside the platform: an App page or a platform path."""
    page: Name | None = None
    path: Annotated[str, Field(pattern=r"^/([a-z0-9_-][a-z0-9/_-]{0,126})?$")] | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.page is None) == (self.path is None):
            raise _err("JD-PAGE-LINK", "a link needs exactly one of page or path")
        return self


BlockParams = Annotated[dict[Name, Any], Field(max_length=10)]


class TextBlock(_Model):
    kind: Literal["text"]
    style: Literal["heading", "paragraph"] = "paragraph"
    text: Annotated[str, Field(min_length=1, max_length=4000)]
    link: Link | None = None


class MetricBlock(_Model):
    kind: Literal["metric"]
    label: Label
    view: Name
    params: BlockParams = Field(default_factory=dict)


class TableBlock(_Model):
    kind: Literal["table"]
    view: Name
    title: Label | None = None
    columns: Annotated[list[Column], Field(min_length=1, max_length=12)]
    params: BlockParams = Field(default_factory=dict)
    row_link: RowLink | None = None
    actions: Annotated[list[Name], Field(max_length=5)] = Field(default_factory=list)


class DetailBlock(_Model):
    kind: Literal["detail"]
    view: Name
    title: Label | None = None
    fields: Annotated[list[Name], Field(min_length=1, max_length=64)]
    params: BlockParams = Field(default_factory=dict)
    actions: Annotated[list[Name], Field(max_length=5)] = Field(default_factory=list)


Block = Annotated[Union[TextBlock, MetricBlock, TableBlock, DetailBlock],
                  Field(discriminator="kind")]


class _WriteTemplate(_Model):
    name: Name
    collection: Name
    label: Label | None = None
    presets: Annotated[dict[Name, Any], Field(max_length=64)] = Field(default_factory=dict)
    editable_fields: Annotated[list[Name], Field(max_length=64)] = Field(
        default_factory=list)


class CreateTemplate(_WriteTemplate):
    kind: Literal["create"]


class UpdateTemplate(_WriteTemplate):
    """Applies to the current record, with `expected_version`."""
    kind: Literal["update"]


class DeleteTemplate(_Model):
    name: Name
    kind: Literal["delete"]
    collection: Name
    label: Label | None = None


ActionTemplate = Annotated[Union[CreateTemplate, UpdateTemplate, DeleteTemplate],
                           Field(discriminator="kind")]


class PageDef(_Model):
    page: Name
    renderer: Literal["typed/v2"] = "typed/v2"
    title: Annotated[str, Field(min_length=1, max_length=128)]
    description: Description | None = None
    params: Annotated[dict[Name, ParamSpec], Field(max_length=8)] = Field(
        default_factory=dict)
    blocks: Annotated[list[Block], Field(max_length=50)] = Field(default_factory=list)
    actions: Annotated[list[ActionTemplate], Field(max_length=10)] = Field(
        default_factory=list)

    @property
    def name(self) -> str:
        return self.page


# --- App tools (R1b) --------------------------------------------------------------

class ToolRole(_Model):
    collection: Name
    verbs: Annotated[list[Literal["read", "create", "update", "delete"]],
                     Field(min_length=1, max_length=len(APP_VERBS)),
                     AfterValidator(_distinct_verbs)]


class ToolDef(_Model):
    """An App tool (design 39, "The authority model"): the collection each
    of a tool's manifest roles binds in this App, and the verbs it may use
    there. A tool-call credential's scope is this, cut down to the manifest
    and the calling agent's own access; a tool with no App tool here gets
    nothing in this App. Adding one or widening its verbs is a proposal."""
    tool: ToolName
    roles: Annotated[dict[Name, ToolRole], Field(min_length=1, max_length=20)]

    @property
    def name(self) -> str:
        return self.tool


KIND_MODELS: dict[str, type[_Model]] = {
    "collection": CollectionDef, "view": ViewDef, "page": PageDef, "tool": ToolDef}
_NAME_KEY = {"collection": "collection", "view": "view", "page": "page", "tool": "tool"}


# --- value checks --------------------------------------------------------------

def _is_param_ref(value: Any) -> bool:
    return isinstance(value, dict) and "param" in value


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not 10 < len(value) <= 40:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (_is_int(value) or isinstance(value, float)) and not isinstance(
        value, bool) and math.isfinite(value)


def _fits_param(param_type: str, value: Any) -> bool:
    return {"string": lambda v: isinstance(v, str) and 0 < len(v) <= MAX_STRING,
            "int": _is_int, "number": _is_number, "bool": lambda v: isinstance(v, bool),
            "date": _is_date, "datetime": _is_datetime}[param_type](value)


def _field_type(collection: CollectionDef, name: str) -> str | None:
    if name in collection.fields:
        return collection.fields[name].type
    return SYSTEM_FIELDS.get(name)


def _fits_field(collection: CollectionDef, name: str, value: Any, *,
                bounds: bool) -> bool:
    """Does a literal fit the field? `bounds` also applies min/max (writes)."""
    spec = collection.fields.get(name)
    kind = _field_type(collection, name)
    if kind in ("string", "text", "url"):
        if not isinstance(value, str) or len(value) > spec.max:
            return False
        minimum = getattr(spec, "min", None)
        return not bounds or minimum is None or len(value) >= minimum
    if kind == "enum":
        return isinstance(value, str) and value in spec.values
    if kind in ("ref", "artifact", "id", "principal"):
        return isinstance(value, str) and 0 < len(value) <= 200
    if kind in ("int", "number"):
        if not (_is_int(value) if kind == "int" else _is_number(value)):
            return False
        if bounds and spec is not None:
            return ((spec.min is None or value >= spec.min)
                    and (spec.max is None or value <= spec.max))
        return True
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "date":
        return _is_date(value)
    if kind == "datetime":
        return _is_datetime(value)
    return False


# Which declared parameter types a field of each type accepts.
_PARAM_FITS = {
    "string": {"string", "text", "url", "enum", "ref", "artifact", "id", "principal"},
    "int": {"int", "number"}, "number": {"number"}, "bool": {"bool"},
    "date": {"date"}, "datetime": {"datetime"}}
_ORDERED = {"string", "int", "number", "date", "datetime"}
_OP_TYPES = {
    "lt": _ORDERED, "lte": _ORDERED, "gt": _ORDERED, "gte": _ORDERED,
    "within_last": {"date", "datetime"}, "contains": {"string", "text", "url"},
}


# --- pass 1: deferred syntax -----------------------------------------------------

def _not_yet(feature: str, path: str, value: Any, what: str) -> DefinitionIssue:
    release = DEFERRED[feature]
    if release is None:
        return issue("JD-REQUEST-PATH", path,
                     f"{what} is not planned; it needs an App Builder request", value)
    return issue(f"JD-NOT-YET-R{release}", path,
                 f"{what} is not available yet (Release {release})", value)


def _deferred(kind: str, raw: Any, base: str) -> list[tuple[DefinitionIssue, str]]:
    """(issue, suppressed path prefix) for each deferred feature in `raw`."""
    if not isinstance(raw, dict):
        return []
    found: list[tuple[DefinitionIssue, str]] = []

    def add(feature, segments, value, what, suppress=None):
        path = join_path(base, *segments)
        found.append((_not_yet(feature, path, value, what),
                      join_path(base, *(suppress if suppress is not None else segments))))

    if kind == "collection":
        if raw.get("write_mode") == "versioned":
            add("versioned", ["write_mode"], "versioned", "versioned write mode")
        fields = raw.get("fields")
        for name, spec in (fields.items() if isinstance(fields, dict) else ()):
            if not isinstance(spec, dict):
                continue
            if spec.get("type") == "list":
                add("list", ["fields", name, "type"], "list", "list fields",
                    suppress=["fields", name])
            elif spec.get("type") == "message_ref":
                add("message_ref", ["fields", name, "type"], "message_ref",
                    "message_ref fields", suppress=["fields", name])
            if "pin_version" in spec:
                add("pin_version", ["fields", name, "pin_version"], spec["pin_version"],
                    "pin_version on refs")
            if spec.get("on_delete") == "cascade":
                add("cascade", ["fields", name, "on_delete"], "cascade",
                    "on_delete: cascade")
        for index, rule in enumerate(_list(raw.get("rules"))):
            if isinstance(rule, dict) and rule.get("kind") in ("required_when", "lock"):
                add(rule["kind"], ["rules", index, "kind"], rule["kind"],
                    f"the {rule['kind']} rule", suppress=["rules", index])
    elif kind == "view":
        for key in _DEFERRED_VIEW_KEYS:
            if key in raw:
                add(key, [key], raw[key], key)
        for index, agg in enumerate(_list(raw.get("aggregates"))):
            if isinstance(agg, dict) and agg.get("fn") in _DEFERRED_AGGREGATES:
                add("aggregates", ["aggregates", index, "fn"], agg["fn"],
                    f"the {agg['fn']} aggregate", suppress=["aggregates", index])
        for index, item in enumerate(_list(raw.get("filter"))):
            if isinstance(item, dict) and item.get("op") in ("exists", "not_exists"):
                add(item["op"], ["filter", index, "op"], item["op"],
                    f"the {item['op']} filter", suppress=["filter", index])
    elif kind == "page":
        if "refresh" in raw:
            add("refresh", ["refresh"], raw["refresh"], "page refresh")
        for index, block in enumerate(_list(raw.get("blocks"))):
            if not isinstance(block, dict):
                continue
            if block.get("kind") in _DEFERRED_COMPONENTS:
                add(block["kind"], ["blocks", index, "kind"], block["kind"],
                    f"the {block['kind']} component", suppress=["blocks", index])
            elif block.get("kind") == "detail" and "history" in block:
                add("detail_history", ["blocks", index, "history"], block["history"],
                    "record history on detail")
        for index, action in enumerate(_list(raw.get("actions"))):
            if isinstance(action, dict) and action.get("kind") in ("new_version", "tool"):
                feature = "new_version" if action["kind"] == "new_version" else "tool_actions"
                add(feature, ["actions", index, "kind"], action["kind"],
                    "new_version templates" if feature == "new_version"
                    else "tool actions on pages", suppress=["actions", index])
    return found


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


# --- pass 2: shape ---------------------------------------------------------------

_TAG_CODES = {"fields": "JD-FIELD-TYPE", "blocks": "JD-PAGE-COMPONENT",
              "rules": "JD-RULE-KIND", "filter": "JD-FILTER-OP",
              "actions": "JD-TEMPLATE-KIND"}
_BOUNDS = {"too_short", "too_long", "string_too_short", "string_too_long",
           "greater_than", "greater_than_equal", "less_than", "less_than_equal"}


def _clean_loc(raw: Any, loc: tuple) -> list:
    """Drop the segments pydantic adds for union tags and dict keys.

    A segment is kept only if it indexes the submitted document, so the path
    always points at something the builder wrote."""
    out: list = []
    node = raw
    tagged = False
    for segment in loc:
        if tagged and isinstance(node, dict) and segment in (
                node.get("type"), node.get("kind"), node.get("op")):
            tagged = False  # the union's tag, not a key the builder wrote
            continue
        entering_union = bool(out) and out[-1] in _TAG_CODES
        tagged = False
        if isinstance(node, dict) and isinstance(segment, str) and segment in node:
            out.append(segment)
            node = node[segment]
            tagged = entering_union
        elif isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
            out.append(segment)
            node = node[segment]
            tagged = entering_union
    return out


def _shape_issue(raw: Any, error: dict, base: str) -> DefinitionIssue:
    kind = error["type"]
    loc = _clean_loc(raw, error["loc"])
    ctx = error.get("ctx") or {}
    value = error.get("input")
    if kind in CODES:
        return issue(kind, join_path(base, *loc, *ctx.get("rel", [])), error["msg"],
                     _at(raw, loc + list(ctx.get("rel", []))), ctx.get("fix"))
    if kind in ("union_tag_invalid", "union_tag_not_found"):
        tag_key = str(ctx.get("discriminator", "")).strip("'")
        path = join_path(base, *loc, tag_key)
        if kind == "union_tag_not_found":
            return issue("JD-MISSING", path, f"missing `{tag_key}`")
        container = next((s for s in reversed(loc[:-1]) if isinstance(s, str)), "")
        code = _TAG_CODES.get(container, "JD-VALUE")
        return issue(code, path, f"unknown {tag_key} {ctx.get('tag')!r}", ctx.get("tag"))
    path = join_path(base, *loc)
    if kind == "extra_forbidden":
        return issue("JD-UNKNOWN-KEY", path, "unknown key", value)
    if kind == "missing":
        key = error["loc"][-1]
        return issue("JD-MISSING", join_path(base, *loc, key), "required key is missing")
    if kind in ("literal_error", "enum"):
        return issue("JD-VALUE", path, error["msg"], value,
                     f"Use one of {ctx.get('expected')}.")
    if kind == "string_pattern_mismatch":
        return issue("JD-FORMAT", path, "value does not match the expected format", value,
                     f"Match the pattern {ctx.get('pattern')}.")
    if kind in _BOUNDS:
        return issue("JD-BOUNDS", path, error["msg"], value)
    if kind.endswith("_type") or kind.endswith("_parsing") or kind in (
            "model_attributes_type", "is_instance_of"):
        return issue("JD-TYPE", path, error["msg"], value)
    return issue("JD-INVALID", path, error["msg"], value)


def _at(raw: Any, segments: list) -> Any:
    node = raw
    for segment in segments:
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _related(path: str, prefix: str) -> bool:
    for a, b in ((path, prefix), (prefix, path)):
        if a == b or a.startswith(b + ".") or a.startswith(b + "["):
            return True
    return False


def _parse(kind: str, raw: Any, base: str) -> tuple[Any, list[DefinitionIssue]]:
    deferred = _deferred(kind, raw, base)
    issues = [found for found, _ in deferred]
    if not isinstance(raw, dict):
        return None, [issue("JD-TYPE", base, f"a {kind} definition must be an object",
                            raw)]
    try:
        model_type = ToolViewDef if kind == "view" and "tool" in raw else KIND_MODELS[kind]
        model = model_type.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            found = _shape_issue(raw, error, base)
            if not any(_related(found.path, prefix) for _, prefix in deferred):
                issues.append(found)
        return None, issues
    return (None if issues else model), issues


# --- pass 3: semantics -----------------------------------------------------------

def _check_collection(c: CollectionDef, base: str) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []
    seen_rules: dict[Any, int] = {}
    for i, rule in enumerate(c.rules):
        where = join_path(base, "rules", i)
        if isinstance(rule, WriterRule):
            if rule.field not in c.fields:
                out.append(issue("JD-RULE-FIELD", join_path(where, "field"),
                                 "writer rule names an unknown field", rule.field))
            elif "value" in rule.model_fields_set and not _fits_field(
                    c, rule.field, rule.value, bounds=True):
                out.append(issue("JD-RULE-VALUE", join_path(where, "value"),
                                 "value does not fit the field", rule.value))
            key = ("writer", rule.field, repr(rule.value)
                   if "value" in rule.model_fields_set else None)
        else:
            seen: set[str] = set()
            for j, name in enumerate(rule.fields):
                if name not in c.fields or name in seen:
                    out.append(issue("JD-RULE-FIELD", join_path(where, "fields", j),
                                     "unknown or repeated field", name))
                seen.add(name)
            if isinstance(rule, ImmutableAfterCreateRule) and c.write_mode == "immutable":
                out.append(issue("JD-RULE-REDUNDANT", where,
                                 "immutable collections never update records"))
            key = (rule.kind, frozenset(rule.fields))
        if key in seen_rules:
            out.append(issue("JD-RULE-DUPLICATE", where,
                             f"repeats rules[{seen_rules[key]}]"))
        seen_rules.setdefault(key, i)

    free = {family: len(slots) for family, slots in INDEX_SLOTS.items()}
    seen_index: set[str] = set()
    for i, name in enumerate(c.indexed):
        where = join_path(base, "indexed", i)
        if name in seen_index:
            out.append(issue("JD-INDEX-DUPLICATE", where, "field indexed twice", name))
            continue
        seen_index.add(name)
        if name not in c.fields:
            out.append(issue("JD-INDEX-FIELD", where, "unknown field", name))
            continue
        family = _INDEX_FAMILY.get(c.fields[name].type)
        if family is None:
            out.append(issue("JD-INDEX-TYPE", where,
                             f"{c.fields[name].type} fields can't be indexed", name))
        elif free[family] == 0:
            out.append(issue("JD-INDEX-SLOTS", where,
                             f"no {family} index column left", name))
        else:
            free[family] -= 1
    return out


def _check_view_alone(v: ViewDef, base: str) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []
    used: set[str] = set()
    for i, item in enumerate(v.filter):
        if isinstance(item, (CompareFilter, ContainsFilter)):
            where = join_path(base, "filter", i, "value")
            if isinstance(item.value, dict):
                if set(item.value) != {"param"} or not isinstance(item.value["param"], str):
                    out.append(issue("JD-FILTER-VALUE", where,
                                     "an object value must be exactly {param: name}",
                                     item.value))
                elif item.value["param"] not in v.params:
                    out.append(issue("JD-PARAM-UNDECLARED", join_path(where, "param"),
                                     "parameter is not declared", item.value["param"]))
                else:
                    used.add(item.value["param"])
    for name in v.params:
        if name not in used:
            out.append(issue("JD-PARAM-UNUSED", join_path(base, "params", name),
                             "parameter is never used", name))
    if v.aggregates:
        if len(v.aggregates) != 1:
            out.append(issue("JD-VIEW-COUNT", join_path(base, "aggregates"),
                             "Release 1 views have at most one count", len(v.aggregates)))
        for key in ("fields", "sort", "limit", "paging"):
            if key in v.model_fields_set:
                out.append(issue("JD-VIEW-COUNT", join_path(base, key),
                                 f"a count view can't set {key}"))
    seen: set[str] = set()
    for i, key in enumerate(v.sort):
        if key.field in seen:
            out.append(issue("JD-SORT-DUPLICATE", join_path(base, "sort", i, "field"),
                             "sorted twice", key.field))
        seen.add(key.field)
    return out


def _check_view(v: ViewDef, c: CollectionDef, base: str) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []

    def known(name: str) -> bool:
        return name in c.fields or name in SYSTEM_FIELDS

    for i, name in enumerate(v.fields or []):
        if not known(name):
            out.append(issue("JD-VIEW-FIELD", join_path(base, "fields", i),
                             "unknown field", name))
    for i, key in enumerate(v.sort):
        if not known(key.field):
            out.append(issue("JD-VIEW-FIELD", join_path(base, "sort", i, "field"),
                             "unknown field", key.field))
    for i, item in enumerate(v.filter):
        where = join_path(base, "filter", i)
        if not known(item.field):
            out.append(issue("JD-VIEW-FIELD", join_path(where, "field"), "unknown field",
                             item.field))
            continue
        ftype = _field_type(c, item.field)
        allowed = _OP_TYPES.get(item.op)
        if allowed is not None and ftype not in allowed:
            out.append(issue("JD-FILTER-OP-TYPE", join_path(where, "op"),
                             f"{item.op} doesn't apply to {ftype} fields", item.op))
            continue
        if isinstance(item, (CompareFilter, ContainsFilter)):
            out += _check_filter_value(v, c, item, join_path(where, "value"))
        elif isinstance(item, InFilter):
            for j, value in enumerate(item.value):
                if not _fits_field(c, item.field, value, bounds=False):
                    out.append(issue("JD-FILTER-VALUE", join_path(where, "value", j),
                                     "value does not fit the field", value))
        elif isinstance(item, WithinLastFilter):
            if item.value.endswith("h") and ftype != "datetime":
                out.append(issue("JD-FILTER-VALUE", join_path(where, "value"),
                                 "hours apply only to datetime fields", item.value,
                                 "Use days or longer for a date field."))
            anchor = _ANCHOR_RE.fullmatch(item.anchor).group(2)
            if anchor is not None and _field_type(c, anchor) not in ("date", "datetime"):
                out.append(issue("JD-FILTER-ANCHOR", join_path(where, "anchor"),
                                 "anchor must name a date or datetime field", item.anchor))
    return out


def _check_filter_value(v: ViewDef, c: CollectionDef, item, where: str):
    if isinstance(item.value, dict):
        name = item.value.get("param")
        spec = v.params.get(name) if isinstance(name, str) else None
        if spec is not None and _field_type(c, item.field) not in _PARAM_FITS[spec.type]:
            return [issue("JD-PARAM-TYPE", join_path(where, "param"),
                          f"a {spec.type} parameter can't filter a "
                          f"{_field_type(c, item.field)} field", name)]
        return []
    if isinstance(item, ContainsFilter):
        if not isinstance(item.value, str):
            return [issue("JD-FILTER-VALUE", where, "contains takes text", item.value)]
        if not 1 <= len(item.value) <= 200:
            return [issue("JD-BOUNDS", where, "contains text must be 1-200 characters",
                          item.value)]
        return []
    if not _fits_field(c, item.field, item.value, bounds=False):
        return [issue("JD-FILTER-VALUE", where, "value does not fit the field", item.value)]
    return []


def _check_page_alone(p: PageDef, base: str) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []
    templates: dict[str, Any] = {}
    for i, template in enumerate(p.actions):
        if template.name in templates:
            out.append(issue("JD-DUPLICATE-NAME", join_path(base, "actions", i, "name"),
                             "action template name repeats", template.name))
        templates.setdefault(template.name, template)
    for i, block in enumerate(p.blocks):
        where = join_path(base, "blocks", i)
        for j, name in enumerate(getattr(block, "actions", [])):
            template = templates.get(name)
            if template is None:
                out.append(issue("JD-PAGE-ACTION", join_path(where, "actions", j),
                                 "the page declares no such action", name))
            elif isinstance(block, DetailBlock) and template.kind == "create":
                out.append(issue("JD-PAGE-ACTION", join_path(where, "actions", j),
                                 "a detail acts on its record; create belongs on a table",
                                 name))
        for key, value in getattr(block, "params", {}).items():
            if isinstance(value, dict):
                if set(value) != {"page_param"} or not isinstance(value["page_param"], str):
                    out.append(issue("JD-PAGE-PARAM", join_path(where, "params", key),
                                     "an object must be exactly {page_param: name}", value))
                elif value["page_param"] not in p.params:
                    out.append(issue("JD-PAGE-PARAM",
                                     join_path(where, "params", key, "page_param"),
                                     "the page declares no such parameter",
                                     value["page_param"]))
    return out


@dataclass
class AppBundle:
    collections: dict[str, CollectionDef] = dc_field(default_factory=dict)
    views: dict[str, ViewDef | ToolViewDef] = dc_field(default_factory=dict)
    pages: dict[str, PageDef] = dc_field(default_factory=dict)
    app_tools: dict[str, ToolDef] = dc_field(default_factory=dict)


def _check_tool(t: ToolDef, app: AppBundle, names, base: str) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []
    for role, binding in t.roles.items():
        where = join_path(base, "roles", role)
        if binding.collection not in names["collections"]:
            out.append(issue("JD-TOOL-COLLECTION", join_path(where, "collection"),
                             "no such collection in this App", binding.collection))
            continue
        c = app.collections.get(binding.collection)
        if c is not None and c.write_mode == "immutable" and "update" in binding.verbs:
            out.append(issue("JD-TOOL-VERB",
                             join_path(where, "verbs", binding.verbs.index("update")),
                             "the collection is immutable", "update"))
    return out


def _check_tool_view(v: ToolViewDef, app: AppBundle, base: str) -> list[DefinitionIssue]:
    from agentplatform.operation_catalog import view_action

    out: list[DefinitionIssue] = []
    action = view_action(v.tool, v.action)
    if action is None:
        return [issue("JD-TOOL-VIEW-ACTION", join_path(base, "action"),
                      "this tool action is not a reviewed view action", v.action)]
    declared = set(action["target_scope"])
    app_tool = app.app_tools.get(v.tool)
    for i, role in enumerate(v.sources):
        where = join_path(base, "sources", i)
        if role not in declared:
            out.append(issue("JD-TOOL-VIEW-SOURCE", where,
                             "the action does not declare this source role", role))
        binding = app_tool.roles.get(role) if app_tool else None
        if binding is None or "read" not in binding.verbs:
            out.append(issue("JD-TOOL-VIEW-BINDING", where,
                             "the App tool must approve this role for reading", role))
    if len(set(v.sources)) != len(v.sources):
        out.append(issue("JD-TOOL-VIEW-SOURCE", join_path(base, "sources"),
                         "a source role is listed twice", v.sources))
    schema = action["input_schema"] or {}
    allowed = schema.get("properties", {})
    required = set(schema.get("required", []))
    for name, spec in v.params.items():
        shape = allowed.get(name)
        where = join_path(base, "params", name)
        if shape is None:
            out.append(issue("JD-TOOL-VIEW-PARAM", where,
                             "the action does not declare this parameter", name))
        elif shape.get("type") != {"int": "integer", "number": "number",
                                      "bool": "boolean"}.get(spec.type, "string"):
            out.append(issue("JD-TOOL-VIEW-PARAM", where,
                             "parameter type differs from the reviewed action", name))
    for name in sorted(required - set(v.params)):
        out.append(issue("JD-TOOL-VIEW-PARAM", join_path(base, "params", name),
                         "required action parameter is not declared", name))
    if v.materialize is not None:
        if v.cache == "none":
            out.append(issue("JD-TOOL-VIEW-MATERIALIZE", join_path(base, "cache"),
                             "a materialized view cannot disable its cache", v.cache))
        field = v.materialize.domain
        if field not in v.params:
            out.append(issue("JD-TOOL-VIEW-DOMAIN", join_path(base, "materialize", "domain"),
                             "the domain must also name a view parameter", field))
        indexed = any((app_tool is not None and role in app_tool.roles
                       and (c := app.collections.get(app_tool.roles[role].collection))
                       is not None and field in c.indexed) for role in v.sources)
        if not indexed:
            out.append(issue("JD-TOOL-VIEW-DOMAIN", join_path(base, "materialize", "domain"),
                             "the domain must be an indexed field of a source", field))
    return out


def _check_page(p: PageDef, app: AppBundle, names: dict[str, set[str]],
                base: str, *, allow_unavailable_tool_views: bool = False) -> list[DefinitionIssue]:
    out: list[DefinitionIssue] = []
    templates = {t.name: t for t in p.actions}
    for i, template in enumerate(p.actions):
        out += _check_template(template, app, names, join_path(base, "actions", i))
    for i, block in enumerate(p.blocks):
        where = join_path(base, "blocks", i)
        if isinstance(block, TextBlock):
            if block.link is not None and block.link.page is not None and (
                    block.link.page not in names["pages"]):
                out.append(issue("JD-PAGE-LINK", join_path(where, "link", "page"),
                                 "no such page in this App", block.link.page))
            continue
        if block.view not in names["views"]:
            out.append(issue("JD-PAGE-VIEW", join_path(where, "view"),
                             "no such view in this App", block.view))
            continue
        view = app.views.get(block.view)
        if isinstance(view, ToolViewDef):
            from agentplatform.operation_catalog import view_action
            unavailable = (allow_unavailable_tool_views
                           and view_action(view.tool, view.action) is None)
            if not unavailable and isinstance(block, MetricBlock) != view.is_count:
                out.append(issue("JD-PAGE-METRIC", join_path(where, "view"),
                                 "metrics need a count result; tables and details need rows",
                                 block.view))
            out += _check_block_params(block, view, p, where)
            if getattr(block, "actions", []):
                out.append(issue("JD-PAGE-ACTION", join_path(where, "actions"),
                                 "a tool view cannot carry a collection action"))
            if isinstance(block, TableBlock) and block.row_link is not None:
                out += _check_row_link(block.row_link, app, names,
                                       join_path(where, "row_link"))
            continue
        collection = app.collections.get(view.collection) if view else None
        if view is None or collection is None:
            continue  # already reported against the view or its collection
        if isinstance(block, MetricBlock) != view.is_count:
            out.append(issue("JD-PAGE-METRIC", join_path(where, "view"),
                             "metrics need a count view; tables and details need records",
                             block.view))
            continue
        out += _check_block_params(block, view, p, where)
        available = set(view.fields) if view.fields else set(collection.fields) | set(
            SYSTEM_FIELDS)
        if isinstance(block, TableBlock):
            for j, column in enumerate(block.columns):
                if column.field not in available:
                    out.append(issue("JD-PAGE-COLUMN",
                                     join_path(where, "columns", j, "field"),
                                     "the view doesn't return this field", column.field))
            if block.row_link is not None:
                out += _check_row_link(block.row_link, app, names,
                                       join_path(where, "row_link"))
        if isinstance(block, DetailBlock):
            for j, name in enumerate(block.fields):
                if name not in available:
                    out.append(issue("JD-PAGE-COLUMN", join_path(where, "fields", j),
                                     "the view doesn't return this field", name))
        for j, name in enumerate(getattr(block, "actions", [])):
            template = templates.get(name)
            if template is not None and template.collection != view.collection:
                out.append(issue("JD-PAGE-ACTION", join_path(where, "actions", j),
                                 "the template writes a different collection", name))
    return out


def _check_block_params(block, view: ViewDef | ToolViewDef, page: PageDef, where: str):
    out = []
    for key, value in block.params.items():
        spec = view.params.get(key)
        if spec is None:
            out.append(issue("JD-PAGE-PARAM", join_path(where, "params", key),
                             "the view has no such parameter", key))
        elif isinstance(value, dict):
            source = page.params.get(value.get("page_param"))
            if source is not None and spec.type not in (
                    source.type, "number" if source.type == "int" else source.type):
                out.append(issue("JD-PAGE-PARAM",
                                 join_path(where, "params", key, "page_param"),
                                 f"a {source.type} page parameter can't feed a "
                                 f"{spec.type} view parameter", value["page_param"]))
        elif not _fits_param(spec.type, value):
            out.append(issue("JD-PAGE-PARAM", join_path(where, "params", key),
                             f"value is not a {spec.type}", value))
    missing = [name for name, spec in view.params.items()
               if spec.required and "default" not in spec.model_fields_set
               and name not in block.params]
    if missing:
        out.append(issue("JD-PAGE-PARAM", join_path(where, "params"),
                         "required view parameters are unbound", missing))
    return out


def _check_row_link(link: RowLink, app: AppBundle, names, where: str):
    if link.page not in names["pages"]:
        return [issue("JD-PAGE-LINK", join_path(where, "page"), "no such page in this App",
                      link.page)]
    target = app.pages.get(link.page)
    if target is not None:
        spec = target.params.get(link.param)
        if spec is None or spec.type != "string":
            return [issue("JD-PAGE-LINK", join_path(where, "param"),
                          "the target page needs a string parameter of this name",
                          link.param)]
    return []


def _check_template(t, app: AppBundle, names, where: str) -> list[DefinitionIssue]:
    if t.collection not in names["collections"]:
        return [issue("JD-PAGE-ACTION", join_path(where, "collection"),
                      "no such collection in this App", t.collection)]
    c = app.collections.get(t.collection)
    if c is None:
        return []
    if c.writers is not None and getattr(c.writers, t.kind) is not None:
        return [issue("JD-TEMPLATE-TOOL-ONLY", join_path(where, "collection"),
                      "a page template cannot bypass this collection's tool-only writer",
                      t.collection)]
    if isinstance(t, DeleteTemplate):
        return []
    out: list[DefinitionIssue] = []
    if isinstance(t, UpdateTemplate) and c.write_mode == "immutable":
        out.append(issue("JD-TEMPLATE-IMMUTABLE", join_path(where, "kind"),
                         "the collection is immutable", t.kind))
    for name, value in t.presets.items():
        if name not in c.fields:
            out.append(issue("JD-TEMPLATE-FIELD", join_path(where, "presets", name),
                             "unknown or system field", name))
        elif not _fits_field(c, name, value, bounds=True):
            out.append(issue("JD-PRESET-VALUE", join_path(where, "presets", name),
                             "preset does not fit the field", value))
    seen: set[str] = set()
    for j, name in enumerate(t.editable_fields):
        if name not in c.fields or name in seen or name in t.presets:
            out.append(issue("JD-TEMPLATE-FIELD", join_path(where, "editable_fields", j),
                             "unknown, system, repeated or preset field", name))
        seen.add(name)
    if not t.presets and not t.editable_fields:
        out.append(issue("JD-TEMPLATE-EMPTY", where, "the template writes nothing"))
    elif isinstance(t, CreateTemplate):
        missing = [name for name, spec in c.fields.items()
                   if spec.required and name not in t.presets and name not in seen]
        if missing:
            out.append(issue("JD-TEMPLATE-REQUIRED", where,
                             "required fields are neither preset nor editable", missing))
    return out


# --- entry points ----------------------------------------------------------------

def _single(kind: str, raw: Any, base: str = "$"):
    model, issues = _parse(kind, raw, base)
    if model is not None:
        check = {"collection": _check_collection, "view": _check_view_alone,
                 "page": _check_page_alone, "tool": lambda *_: []}[kind]
        if not isinstance(model, ToolViewDef):
            issues += check(model, base)
    return model, issues


def validate_definition(kind: str, raw: Any):
    """Validate one definition on its own (no cross-definition checks)."""
    if kind not in KIND_MODELS:
        raise ValueError(f"unknown definition kind {kind!r}")
    model, issues = _single(kind, raw)
    if issues:
        raise DefinitionError(issues)
    return model


def validate_collection(raw: Any) -> CollectionDef:
    return validate_definition("collection", raw)


def validate_view(raw: Any) -> ViewDef:
    return validate_definition("view", raw)


def validate_page(raw: Any) -> PageDef:
    return validate_definition("page", raw)


_BUNDLE_LIMITS = {"collections": 50, "views": 100, "pages": 50, "app_tools": 20}
_BUNDLE_KIND = {"collections": "collection", "views": "view", "pages": "page",
                "app_tools": "tool"}
# Definition kind -> its key in a bundle document: where lifecycle and
# records put each stored definition. App tools are `app_tools`, the
# authority engine's name for them, not `tools`.
BUNDLE_KEYS = {kind: key for key, kind in _BUNDLE_KIND.items()}


def validate_app(bundle: Any, *, allow_unavailable_tool_views: bool = False) -> AppBundle:
    """Validate a whole App: every definition, then the references between them.

    `bundle` is `{"collections": [...], "views": [...], "pages": [...],
    "app_tools": [...]}`."""
    if not isinstance(bundle, dict):
        raise DefinitionError([issue("JD-TYPE", "$", "a bundle must be an object", bundle)])
    issues: list[DefinitionIssue] = []
    for key in bundle:
        if key not in _BUNDLE_LIMITS:
            issues.append(issue("JD-UNKNOWN-KEY", join_path("$", key), "unknown key", key))
    app = AppBundle()
    names: dict[str, set[str]] = {}
    for plural, kind in _BUNDLE_KIND.items():
        items = bundle.get(plural, [])
        names[plural] = set()
        if not isinstance(items, list):
            issues.append(issue("JD-TYPE", join_path("$", plural), "must be a list", items))
            continue
        if len(items) > _BUNDLE_LIMITS[plural]:
            issues.append(issue("JD-BOUNDS", join_path("$", plural),
                                f"at most {_BUNDLE_LIMITS[plural]} {plural}", len(items)))
            continue
        parsed: dict[str, Any] = getattr(app, plural)
        for i, raw in enumerate(items):
            base = join_path("$", plural, i)
            name = raw.get(_NAME_KEY[kind]) if isinstance(raw, dict) else None
            if isinstance(name, str) and name in names[plural]:
                issues.append(issue("JD-DUPLICATE-NAME", join_path(base, _NAME_KEY[kind]),
                                    f"another {kind} has this name", name))
                continue
            if isinstance(name, str):
                names[plural].add(name)
            model, found = _single(kind, raw, base)
            issues += found
            if model is not None:
                parsed[model.name] = model

    for i, raw in enumerate(_list(bundle.get("collections"))):
        c = app.collections.get(raw.get("collection") if isinstance(raw, dict) else None)
        if c is None or raw is not _raw_for(bundle, "collections", c.name):
            continue
        for fname, spec in c.fields.items():
            if isinstance(spec, RefField) and spec.collection not in names["collections"]:
                issues.append(issue("JD-REF-TARGET", join_path(
                    "$", "collections", i, "fields", fname, "collection"),
                    "refs must target a collection in the same App", spec.collection))
    for i, raw in enumerate(_list(bundle.get("views"))):
        v = app.views.get(raw.get("view") if isinstance(raw, dict) else None)
        if v is None or raw is not _raw_for(bundle, "views", v.name):
            continue
        base = join_path("$", "views", i)
        if isinstance(v, ToolViewDef):
            found = _check_tool_view(v, app, base)
            if allow_unavailable_tool_views:
                found = [item for item in found if item.code != "JD-TOOL-VIEW-ACTION"]
            issues += found
            continue
        if v.collection not in names["collections"]:
            issues.append(issue("JD-VIEW-COLLECTION", join_path(base, "collection"),
                                "no such collection in this App", v.collection))
        elif v.collection in app.collections:
            issues += _check_view(v, app.collections[v.collection], base)
    for i, raw in enumerate(_list(bundle.get("pages"))):
        p = app.pages.get(raw.get("page") if isinstance(raw, dict) else None)
        if p is None or raw is not _raw_for(bundle, "pages", p.name):
            continue
        issues += _check_page(p, app, names, join_path("$", "pages", i),
                              allow_unavailable_tool_views=allow_unavailable_tool_views)
    for i, raw in enumerate(_list(bundle.get("app_tools"))):
        t = app.app_tools.get(raw.get("tool") if isinstance(raw, dict) else None)
        if t is None or raw is not _raw_for(bundle, "app_tools", t.name):
            continue
        issues += _check_tool(t, app, names, join_path("$", "app_tools", i))
    if issues:
        raise DefinitionError(issues)
    return app


def _raw_for(bundle: dict, plural: str, name: str) -> Any:
    """The first submitted definition with this name (later duplicates are refused)."""
    key = _NAME_KEY[_BUNDLE_KIND[plural]]
    return next((raw for raw in bundle[plural]
                 if isinstance(raw, dict) and raw.get(key) == name), None)


# --- schema export -----------------------------------------------------------------

class _BundleSchema(_Model):
    """Shape of `validate_app`'s input, for the schema export only."""
    collections: Annotated[list[CollectionDef], Field(max_length=50)] = Field(
        default_factory=list)
    views: Annotated[list[ViewDef | ToolViewDef], Field(max_length=100)] = Field(
        default_factory=list)
    pages: Annotated[list[PageDef], Field(max_length=50)] = Field(default_factory=list)
    app_tools: Annotated[list[ToolDef], Field(max_length=20)] = Field(default_factory=list)


def capabilities() -> dict:
    return {
        "version": CAPABILITIES_VERSION,
        "field_types": list(FIELD_TYPES),
        "system_fields": sorted(SYSTEM_FIELDS),
        "write_modes": ["editable", "immutable"],
        "principals": ["owner", "kyle", "agent:<name>", "login:qa (read only)"],
        "rules": ["writer", "immutable_after_create", "unique"],
        "on_delete": ["restrict", "unlink"],
        "filter_ops": list(FILTER_OPS),
        "aggregates": ["count (ungrouped)"],
        "components": list(COMPONENTS),
        "action_templates": list(TEMPLATE_KINDS),
        "app_tools": {"roles": "role -> {collection, verbs}", "verbs": list(APP_VERBS)},
        "tool_views": {"source": "reviewed view action + approved App tool read role",
                       "cache": ["default", "none"], "minimum_refresh": "5m"},
        "limits": {"view_limit": VIEW_LIMIT, "indexed_fields": MAX_INDEXED,
                   "string_max": MAX_STRING, "text_max": MAX_TEXT,
                   "retention_days_max": MAX_RETENTION_DAYS,
                   "retention_records_max": MAX_RETENTION_RECORDS},
        "index_columns": {family: list(slots) for family, slots in INDEX_SLOTS.items()},
        "deferred": {feature: release for feature, release in DEFERRED.items()
                     if release is not None},
        "request_path": sorted(f for f, release in DEFERRED.items() if release is None),
    }


def json_schemas() -> dict:
    """JSON Schema for every definition kind, for `apps schema`."""
    kinds = {kind: model.model_json_schema() for kind, model in KIND_MODELS.items()}
    # A single adapter keeps both variants' $refs in the same $defs scope.
    kinds["view"] = TypeAdapter(ViewDef | ToolViewDef).json_schema()
    kinds["view"]["type"] = "object"
    kinds["bundle"] = _BundleSchema.model_json_schema()
    return {"capabilities_version": CAPABILITIES_VERSION,
            "kinds": kinds,
            "capabilities": capabilities()}
