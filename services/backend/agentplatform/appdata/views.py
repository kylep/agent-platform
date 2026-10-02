"""The view query builder, Release 1 (design 39, "Views").

A view is a filter, a sort and a limit over one collection, or an ungrouped
`count` for a metric. It compiles to one portable SQL query: an indexed field
reads its side column (so the fixed indexes serve it), anything else is
extracted from `doc` (JSON_EXTRACT on SQLite, `->>` on Postgres).

- **Access.** Results come back per field for the caller (`null` plus a
  `restricted` marker). Filtering, sorting or anchoring on a field the caller
  can't read is refused, because a predicate answers questions about the
  value: "how many records have salary > 100k" reads salary.
- **Parameters** are declared and typed. Query strings arrive as text and are
  coerced to the declared type; an optional parameter left unset with no
  default drops the filters that use it.
- **Paging** is keyset: the cursor carries the last row's sort values and id,
  nulls sort last in either direction, and a page is at most 200 rows.
- **`within_last`** buckets in the App's timezone with Monday weeks: `12w` is
  the current, partial week plus the 11 full weeks before it. `anchor:
  max(<field>)` measures back from the newest value among the records the
  view's other filters select, instead of from now.
- **`contains`** is a case-insensitive substring match with `%`, `_` and `\\`
  escaped, so the caller's text is never a pattern.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, false, func, or_, select

from agentplatform.appdata.access import Access, Caller, RecordError
from agentplatform.appdata.definitions import (
    VIEW_LIMIT, CollectionDef, ContainsFilter, InFilter, IsNullFilter,
    ViewDef, WithinLastFilter, _ANCHOR_RE, _fits_param, _is_param_ref)
from agentplatform.appdata.records import (
    R, AppContext, field_expr, field_type, format_datetime, parse_datetime, present,
    scope)
from agentplatform.db import utcnow

_DEFAULT_SORT = (("created_at", "desc"),)


# --- parameters -----------------------------------------------------------------------

def _coerce(ptype: str, value: Any) -> Any:
    if isinstance(value, str) and ptype in ("int", "number", "bool"):
        text = value.strip()
        try:
            if ptype == "int":
                return int(text)
            if ptype == "number":
                return float(text)
        except ValueError:
            return value
        return {"true": True, "false": False}.get(text, value)
    return value


def resolve_params(view: ViewDef, raw: dict | None) -> dict:
    """Declared parameters with their typed values; unset optional ones are absent."""
    raw = raw or {}
    unknown = sorted(set(raw) - set(view.params))
    if unknown:
        raise RecordError("AD-PARAM-UNKNOWN", f"{view.view} takes no parameter "
                          f"{', '.join(unknown)}", 422, {"params": unknown})
    out: dict[str, Any] = {}
    for name, spec in view.params.items():
        if name in raw and raw[name] is not None:
            value = _coerce(spec.type, raw[name])
            if not _fits_param(spec.type, value):
                raise RecordError("AD-PARAM-TYPE", f"{name} must be a {spec.type}", 422,
                                  {"param": name})
            out[name] = value
        elif "default" in spec.model_fields_set:
            out[name] = spec.default
        elif spec.required:
            raise RecordError("AD-PARAM-REQUIRED", f"{view.view} needs parameter {name}",
                              422, {"param": name})
    return out


# --- filters -------------------------------------------------------------------------------

def _literal(ctx: AppContext, c: CollectionDef, name: str, value: Any) -> Any:
    """A filter value in the same normalized form the field is stored in."""
    if field_type(c, name) == "datetime":
        return format_datetime(parse_datetime(value, ctx.tz))
    return value


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _compare(expr, op: str, value):
    if op == "eq":
        return expr == value
    if op == "ne":
        # "not equal to X" holds for a record with no value, too.
        return or_(expr != value, expr.is_(None))
    return {"lt": expr < value, "lte": expr <= value, "gt": expr > value,
            "gte": expr >= value}[op]


def _window(unit: str, count: int, anchor: datetime) -> tuple[datetime, datetime]:
    """[start, end) in the anchor's timezone. Periods are calendar buckets, so
    `count` of them is the anchor's own (partial) one plus `count - 1` before."""
    if unit == "h":
        return anchor - timedelta(hours=count), anchor + timedelta(microseconds=1)
    day = datetime.combine(anchor.date(), time(), tzinfo=anchor.tzinfo)
    if unit == "d":
        return day - timedelta(days=count - 1), day + timedelta(days=1)
    if unit == "w":
        monday = day - timedelta(days=day.weekday())
        return monday - timedelta(weeks=count - 1), monday + timedelta(weeks=1)
    if unit == "m":
        months = anchor.year * 12 + anchor.month - 1
        start, end = months - (count - 1), months + 1
        return (day.replace(year=start // 12, month=start % 12 + 1, day=1),
                day.replace(year=end // 12, month=end % 12 + 1, day=1))
    start = day.replace(year=anchor.year - (count - 1), month=1, day=1)
    return start, day.replace(year=anchor.year + 1, month=1, day=1)


def _localize(ctx: AppContext, value: Any, ftype: str) -> datetime | None:
    """A max() result as an aware datetime in App time."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if ftype == "date":
            # A date's side column is its midnight UTC; the date is the point.
            return datetime.combine(dt.date(), time(), tzinfo=ctx.tz)
        return dt.astimezone(ctx.tz)
    if ftype == "date":
        return datetime.combine(date.fromisoformat(value), time(), tzinfo=ctx.tz)
    return parse_datetime(value, ctx.tz).astimezone(ctx.tz)


class _Query:
    def __init__(self, ctx: AppContext, caller: Caller, view: ViewDef, params: dict,
                 now: datetime):
        self.ctx, self.caller, self.view, self.params = ctx, caller, view, params
        self.now = now
        self.c = ctx.collection(view.collection)
        self.access: Access = ctx.access(self.c, caller)

    def check_access(self) -> None:
        self.access.require_rows()
        for item in self.view.filter:
            self.access.require_readable(item.field, "filter")
            if isinstance(item, WithinLastFilter):
                anchor = _ANCHOR_RE.fullmatch(item.anchor).group(2)
                if anchor is not None:
                    self.access.require_readable(anchor, "anchor a window")
        for key in self.view.sort:
            self.access.require_readable(key.field, "sort")

    def _value(self, item) -> tuple[bool, Any]:
        value = item.value
        if _is_param_ref(value):
            if value["param"] not in self.params:
                return False, None
            value = self.params[value["param"]]
        return True, value

    def _plain(self, item):
        """A non-window filter's condition, or None when its parameter is unset."""
        expr, conv = field_expr(self.c, item.field)
        if isinstance(item, IsNullFilter):
            return expr.is_(None) if item.value else expr.is_not(None)
        if isinstance(item, InFilter):
            return expr.in_([conv(_literal(self.ctx, self.c, item.field, v))
                             for v in item.value])
        present, value = self._value(item)
        if not present:
            return None
        if isinstance(item, ContainsFilter):
            pattern = f"%{escape_like(str(value).lower())}%"
            return func.lower(expr).like(pattern, escape="\\")
        return _compare(expr, item.op, conv(_literal(self.ctx, self.c, item.field, value)))

    async def conditions(self, session) -> list:
        conds = [scope(self.ctx, self.c.collection)]
        for item in self.view.filter:
            if not isinstance(item, WithinLastFilter):
                cond = self._plain(item)
                if cond is not None:
                    conds.append(cond)
        base = list(conds)
        for item in self.view.filter:
            if isinstance(item, WithinLastFilter):
                conds.append(await self._within(session, item, base))
        return conds

    async def _within(self, session, item: WithinLastFilter, base: list):
        anchor_field = _ANCHOR_RE.fullmatch(item.anchor).group(2)
        if anchor_field is None:
            anchor = self.now.astimezone(self.ctx.tz)
        else:
            expr, _ = field_expr(self.c, anchor_field)
            newest = (await session.execute(select(func.max(expr)).where(*base))).scalar()
            anchor = _localize(self.ctx, newest, field_type(self.c, anchor_field))
            if anchor is None:
                return false()
        start, end = _window(item.value[-1], int(item.value[:-1]), anchor)
        expr, conv = field_expr(self.c, item.field)
        if field_type(self.c, item.field) == "date":
            low, high = start.date().isoformat(), end.date().isoformat()
        else:
            low, high = format_datetime(start), format_datetime(end)
        return and_(expr >= conv(low), expr < conv(high))


# --- cursors -------------------------------------------------------------------------------

def _sort_keys(view: ViewDef) -> list[tuple[str, str]]:
    keys = [(k.field, k.dir) for k in view.sort] or list(_DEFAULT_SORT)
    # The id breaks ties, so the order (and with it the paging) is total.
    if all(name != "id" for name, _ in keys):
        keys.append(("id", keys[-1][1]))
    return keys


def _signature(view: ViewDef, keys) -> str:
    return hashlib.sha256(json.dumps([view.view, view.collection, keys]).encode()
                          ).hexdigest()[:16]


def _encode_cursor(sig: str, values: list) -> str:
    raw = json.dumps({"s": sig, "k": values}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str, sig: str, size: int) -> list:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        body = json.loads(raw)
        values = body["k"]
        ok = body["s"] == sig and isinstance(values, list) and len(values) == size
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise RecordError("AD-CURSOR", "the cursor doesn't belong to this view", 422)
    return values


def _raw(record, name: str) -> Any:
    system = {"id": record.id, "author": record.author, "via": record.via,
              "version": record.current_version,
              "collection_version": record.collection_version}
    if name in system:
        return system[name]
    if name in ("created_at", "updated_at"):
        return format_datetime(getattr(record, name))
    return record.doc.get(name)


def _after(c: CollectionDef, keys, values: list):
    """Rows strictly after the cursor row in (keys) order, nulls last."""
    options = []
    equal: list = []
    for (name, direction), value in zip(keys, values):
        expr, conv = field_expr(c, name)
        if value is None:
            beyond = false()
            same = expr.is_(None)
        else:
            bound = conv(value)
            beyond = or_(expr > bound if direction == "asc" else expr < bound,
                         expr.is_(None))
            same = expr == bound
        options.append(and_(*equal, beyond) if equal else beyond)
        equal.append(same)
    return or_(*options)


# --- entry points ------------------------------------------------------------------------------

def _as_of(now: datetime) -> str:
    return format_datetime(now)


async def execute_view(session, ctx: AppContext, caller: Caller, view: ViewDef,
                       params: dict | None = None, *, limit: int | None = None,
                       cursor: str | None = None, now: datetime | None = None) -> dict:
    """Run a validated view for one caller: `{rows, next_cursor, as_of, stale}`
    or, for a count view, `{count, as_of, stale}`."""
    now = now or utcnow()
    q = _Query(ctx, caller, view, resolve_params(view, params), now)
    q.check_access()
    conds = await q.conditions(session)
    if view.is_count:
        count = (await session.execute(select(func.count()).select_from(R)
                                       .where(*conds))).scalar_one()
        return {"count": count, "as_of": _as_of(now), "stale": False}

    if limit is not None and not 1 <= limit <= VIEW_LIMIT:
        raise RecordError("AD-LIMIT", f"limit must be 1-{VIEW_LIMIT}", 422)
    size = limit or view.limit
    keys = _sort_keys(view)
    sig = _signature(view, keys)
    if cursor is not None:
        if not view.paging:
            raise RecordError("AD-CURSOR", f"{view.view} doesn't page", 422)
        conds.append(_after(q.c, keys, _decode_cursor(cursor, sig, len(keys))))
    order = []
    for name, direction in keys:
        expr, _ = field_expr(q.c, name)
        order.append((expr.asc() if direction == "asc" else expr.desc()).nulls_last())
    records = (await session.execute(select(R).where(*conds).order_by(*order)
                                     .limit(size + 1))).scalars().all()
    more = len(records) > size
    records = records[:size]
    next_cursor = None
    if more and view.paging:
        next_cursor = _encode_cursor(sig, [_raw(records[-1], name) for name, _ in keys])
    rows = [present(q.access, record, view.fields) for record in records]
    return {"rows": rows, "next_cursor": next_cursor, "as_of": _as_of(now), "stale": False}


def check_view_access(ctx: AppContext, caller: Caller, view: ViewDef) -> None:
    """Refuse unless the caller could run the view itself: it sees the rows
    and may read every field the view filters, sorts or anchors on. Previews
    rendered as another principal check the builder this way first."""
    _Query(ctx, caller, view, {}, utcnow()).check_access()


async def run_view(session, ctx: AppContext, caller: Caller, view_name: str,
                   params: dict | None = None, **kwargs) -> dict:
    """Run one of the App's published views by name."""
    view = ctx.bundle.views.get(view_name)
    if view is None:
        raise RecordError("AD-NO-VIEW", f"no published view {view_name}", 404)
    return await execute_view(session, ctx, caller, view, params, **kwargs)
