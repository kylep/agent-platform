"""The view query builder (design 39, "Views"): every Release 1 filter over
side columns and over `doc`, typed parameters, sort and keyset paging, the
ungrouped count, `within_last` in App time with both anchors, escaped
`contains`, and per-field access on results and predicates."""
from datetime import datetime, timezone

import pytest

from agentplatform.appdata.access import Caller
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.records import create_record
from agentplatform.appdata.views import execute_view, run_view
from tests.test_appdata_records import (BOB, KYLE, OWNER, QA, engine, make_app,  # noqa: F401
                                        refused, sf)

FIELDS = {
    "name": {"type": "string"}, "n": {"type": "int"}, "x": {"type": "number"},
    "day": {"type": "date"}, "at": {"type": "datetime"},
    "tag": {"type": "enum", "values": ["a", "b"]}, "flag": {"type": "bool"},
    "note": {"type": "text"},
    "secret": {"type": "string", "access": {"read": ["kyle"], "create": ["owner"]}},
}
# No index, then two layouts that move every filtered field into a side column.
INDEXINGS = [[], ["name", "n", "at"], ["tag", "x", "day"]]


def events(indexed=(), **extra):
    return {"collection": "events", "fields": FIELDS, "indexed": list(indexed),
            "access": {"read": ["owner", "kyle", "login:qa"], "create": ["owner"],
                       "update": ["owner"], "delete": ["owner"]}, **extra}


def view_of(collection: dict, **body):
    body.setdefault("view", "v")
    body.setdefault("collection", collection["collection"])
    return validate_app({"collections": [collection], "views": [body]}).views[body["view"]]


async def seed(sf, ctx, rows):
    async with sf() as s:
        return [await create_record(s, ctx, OWNER, "events", r) for r in rows]


async def names(sf, ctx, view, caller=OWNER, **kwargs):
    async with sf() as s:
        result = await execute_view(s, ctx, caller, view, **kwargs)
    return [row["values"]["name"] for row in result["rows"]]


ROWS = [
    {"name": "alpha", "n": 1, "x": 1.5, "day": "2026-10-01", "tag": "a", "flag": True,
     "at": "2026-10-01T10:00:00Z", "note": "Hello World"},
    {"name": "bravo", "n": 2, "x": 2.5, "day": "2026-10-02", "tag": "b", "flag": False,
     "at": "2026-10-02T10:00:00Z", "note": "100% sure"},
    {"name": "charlie", "n": 3, "x": 3.5, "day": "2026-10-03", "tag": "a",
     "at": "2026-10-03T10:00:00Z", "note": "a_b back\\slash"},
    {"name": "delta", "note": "axb 100 percent"},
]


@pytest.mark.parametrize("indexed", INDEXINGS)
@pytest.mark.parametrize("flt, expected", [
    ({"field": "n", "op": "eq", "value": 2}, ["bravo"]),
    ({"field": "name", "op": "eq", "value": "charlie"}, ["charlie"]),
    ({"field": "tag", "op": "eq", "value": "a"}, ["alpha", "charlie"]),
    ({"field": "flag", "op": "eq", "value": False}, ["bravo"]),
    # ne holds for records with no value at all.
    ({"field": "tag", "op": "ne", "value": "a"}, ["bravo", "delta"]),
    ({"field": "n", "op": "in", "value": [1, 3, 9]}, ["alpha", "charlie"]),
    ({"field": "n", "op": "lt", "value": 2}, ["alpha"]),
    ({"field": "n", "op": "lte", "value": 2}, ["alpha", "bravo"]),
    ({"field": "x", "op": "gt", "value": 2.5}, ["charlie"]),
    ({"field": "x", "op": "gte", "value": 2}, ["bravo", "charlie"]),
    ({"field": "day", "op": "gte", "value": "2026-10-02"}, ["bravo", "charlie"]),
    ({"field": "day", "op": "lt", "value": "2026-10-02"}, ["alpha"]),
    ({"field": "at", "op": "gt", "value": "2026-10-02T10:00:00Z"}, ["charlie"]),
    # Offsets are normalized before comparing: 06:00-04:00 is 10:00Z.
    ({"field": "at", "op": "lte", "value": "2026-10-02T06:00:00-04:00"},
     ["alpha", "bravo"]),
    ({"field": "name", "op": "lt", "value": "bz"}, ["alpha", "bravo"]),
    ({"field": "at", "op": "is_null", "value": True}, ["delta"]),
    ({"field": "n", "op": "is_null", "value": False}, ["alpha", "bravo", "charlie"]),
    ({"field": "created_at", "op": "is_null", "value": False},
     ["alpha", "bravo", "charlie", "delta"]),
])
async def test_filters(sf, indexed, flt, expected):
    c = events(indexed)
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ROWS)
    view = view_of(c, filter=[flt], sort=[{"field": "name"}])
    assert await names(sf, ctx, view) == expected


@pytest.mark.parametrize("indexed", [[], ["name"]])
@pytest.mark.parametrize("field", ["note", "name"])
@pytest.mark.parametrize("needle, expected", [
    ("hello", ["Hello World"]), ("WORLD", ["Hello World"]),
    # Pattern characters are the caller's text, not wildcards.
    ("%", ["100% sure"]), ("_", ["a_b back\\slash"]), ("\\", ["a_b back\\slash"]),
    ("100", ["100% sure", "axb 100 percent"]), ("a%b", []), ("zzz", []),
])
async def test_contains_is_escaped_and_case_insensitive(sf, indexed, field, needle,
                                                        expected):
    c = events(indexed)
    ctx = await make_app(sf, [c])
    async with sf() as s:
        for r in ROWS:
            await create_record(s, ctx, OWNER, "events", {field: r["note"]})
    view = view_of(c, filter=[{"field": field, "op": "contains", "value": needle}],
                   sort=[{"field": field}])
    async with sf() as s:
        result = await execute_view(s, ctx, OWNER, view)
    assert [row["values"][field] for row in result["rows"]] == expected


# --- within_last ---------------------------------------------------------------------------

# Wednesday 2026-10-07 12:00 in Toronto (UTC-4): weeks start Monday 10-05.
NOW = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
TIMED = [
    {"name": "sun-late", "at": "2026-10-04T23:59:00-04:00", "day": "2026-10-04"},
    {"name": "mon-early", "at": "2026-10-05T00:30:00-04:00", "day": "2026-10-05"},
    {"name": "tue-night", "at": "2026-10-06T22:00:00-04:00", "day": "2026-10-06"},
    {"name": "today", "at": "2026-10-07T09:30:00-04:00", "day": "2026-10-07"},
    {"name": "tomorrow", "at": "2026-10-08T09:00:00-04:00", "day": "2026-10-08"},
    {"name": "august", "at": "2026-08-31T12:00:00-04:00", "day": "2026-08-31"},
    {"name": "last-year", "at": "2025-12-31T23:00:00-05:00", "day": "2025-12-31"},
]


@pytest.mark.parametrize("indexed", INDEXINGS)
@pytest.mark.parametrize("field, span, expected", [
    # The current, partial week: Sunday 23:59 local is the week before, even
    # though it is Monday in UTC. A window is whole buckets, so the rest of
    # the current one (tomorrow) is inside it, as it is in that week's group.
    ("at", "1w", ["mon-early", "tue-night", "today", "tomorrow"]),
    ("at", "2w", ["mon-early", "sun-late", "today", "tomorrow", "tue-night"]),
    ("day", "1w", ["mon-early", "today", "tomorrow", "tue-night"]),
    # Today, local: Tuesday 22:00 is Wednesday in UTC but not here.
    ("at", "1d", ["today"]),
    ("day", "2d", ["today", "tue-night"]),
    # Hours aren't buckets: the last three of them, up to now.
    ("at", "3h", ["today"]),
    ("at", "2m", ["mon-early", "sun-late", "today", "tomorrow", "tue-night"]),
    ("at", "3m", ["august", "mon-early", "sun-late", "today", "tomorrow", "tue-night"]),
    ("day", "1y", ["august", "mon-early", "sun-late", "today", "tomorrow", "tue-night"]),
    ("at", "2y", ["august", "last-year", "mon-early", "sun-late", "today", "tomorrow",
                  "tue-night"]),
])
async def test_within_last_buckets_in_app_time(sf, indexed, field, span, expected):
    c = events(indexed)
    ctx = await make_app(sf, [c], tz="America/Toronto")
    await seed(sf, ctx, TIMED)
    view = view_of(c, filter=[{"field": field, "op": "within_last", "value": span}],
                   sort=[{"field": "name"}])
    assert await names(sf, ctx, view, now=NOW) == sorted(expected)


async def test_within_last_follows_the_app_timezone(sf):
    c = events()
    utc = await make_app(sf, [c], tz="UTC")
    await seed(sf, utc, TIMED)
    view = view_of(c, filter=[{"field": "at", "op": "within_last", "value": "1d"}],
                   sort=[{"field": "name"}])
    # In UTC, Tuesday 22:00 Toronto is already Wednesday.
    assert await names(sf, utc, view, now=NOW) == ["today", "tue-night"]
    ctx = await make_app(sf, [c], tz="America/Toronto")
    await seed(sf, ctx, TIMED)
    assert await names(sf, ctx, view, now=NOW) == ["today"]


async def test_within_last_on_created_at(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": "now"}])
    view = view_of(c, filter=[{"field": "created_at", "op": "within_last", "value": "1h"}])
    assert await names(sf, ctx, view) == ["now"]
    assert await names(sf, ctx, view, now=datetime(2020, 1, 1, tzinfo=timezone.utc)) == []


ANCHORED = [
    {"name": "x", "day": "2026-03-08", "at": "2026-03-08T12:00:00Z"},   # a Sunday
    {"name": "x", "day": "2026-03-02", "at": "2026-03-02T12:00:00Z"},   # its Monday
    {"name": "x", "day": "2026-02-28", "at": "2026-02-28T12:00:00Z"},
    {"name": "y", "day": "2026-06-01", "at": "2026-06-01T12:00:00Z"},
]


@pytest.mark.parametrize("indexed", INDEXINGS)
@pytest.mark.parametrize("field, anchor", [("day", "max(day)"), ("at", "max(at)"),
                                           ("day", "max(at)")])
async def test_within_last_anchored_on_the_newest_value(sf, indexed, field, anchor):
    c = events(indexed)
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ANCHORED)
    # The anchor is the newest among what the other filters select: x's
    # newest is 03-08, so its week is 03-02..03-08, wherever "now" is.
    view = view_of(c, filter=[{"field": "name", "op": "eq", "value": "x"},
                              {"field": field, "op": "within_last", "value": "1w",
                               "anchor": anchor}], sort=[{"field": "day"}])
    async with sf() as s:
        rows = (await execute_view(s, ctx, OWNER, view, now=NOW))["rows"]
    assert [r["values"]["day"] for r in rows] == ["2026-03-02", "2026-03-08"]
    unfiltered = view_of(c, filter=[{"field": field, "op": "within_last", "value": "1w",
                                     "anchor": anchor}])
    assert await names(sf, ctx, unfiltered, now=NOW) == ["y"]


async def test_an_anchor_over_no_records_selects_nothing(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": "no-day"}])
    view = view_of(c, filter=[{"field": "day", "op": "within_last", "value": "1w",
                               "anchor": "max(day)"}])
    assert await names(sf, ctx, view) == []


# --- parameters ---------------------------------------------------------------------------

PARAMS = {"who": {"type": "string"}, "min": {"type": "int", "default": 2},
          "f": {"type": "bool"}, "since": {"type": "date"}}


def param_view(c):
    return view_of(c, params=PARAMS, sort=[{"field": "name"}], filter=[
        {"field": "name", "op": "eq", "value": {"param": "who"}},
        {"field": "n", "op": "gte", "value": {"param": "min"}},
        {"field": "flag", "op": "eq", "value": {"param": "f"}},
        {"field": "day", "op": "gte", "value": {"param": "since"}}])


async def test_typed_params_coerce_query_strings(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ROWS)
    view = param_view(c)
    # Unset optional params drop their filters; `min` falls back to its default.
    assert await names(sf, ctx, view) == ["bravo", "charlie"]
    assert await names(sf, ctx, view, params={"min": "1"}) == ["alpha", "bravo", "charlie"]
    assert await names(sf, ctx, view, params={"min": 1, "f": "false"}) == ["bravo"]
    assert await names(sf, ctx, view, params={"who": "charlie"}) == ["charlie"]
    assert await names(sf, ctx, view, params={"since": "2026-10-03", "min": "0"}) == [
        "charlie"]


@pytest.mark.parametrize("params, code", [
    ({"min": "two"}, "AD-PARAM-TYPE"), ({"min": True}, "AD-PARAM-TYPE"),
    ({"f": "yes"}, "AD-PARAM-TYPE"), ({"since": "Monday"}, "AD-PARAM-TYPE"),
    ({"nope": 1}, "AD-PARAM-UNKNOWN"),
])
async def test_bad_params_are_422(sf, params, code):
    c = events()
    ctx = await make_app(sf, [c])
    async with sf() as s:
        err = await refused(code, execute_view(s, ctx, OWNER, param_view(c), params))
    assert err.status == 422


async def test_a_required_param_must_be_given(sf):
    c = events()
    ctx = await make_app(sf, [c])
    view = view_of(c, params={"who": {"type": "string", "required": True}},
                   filter=[{"field": "name", "op": "eq", "value": {"param": "who"}}])
    async with sf() as s:
        await refused("AD-PARAM-REQUIRED", execute_view(s, ctx, OWNER, view))


# --- sort, limit, paging, count -------------------------------------------------------------

@pytest.mark.parametrize("indexed", INDEXINGS)
async def test_sort_puts_nulls_last_both_ways(sf, indexed):
    c = events(indexed)
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ROWS)
    asc = view_of(c, sort=[{"field": "n"}])
    desc = view_of(c, sort=[{"field": "n", "dir": "desc"}])
    assert await names(sf, ctx, asc) == ["alpha", "bravo", "charlie", "delta"]
    assert await names(sf, ctx, desc) == ["charlie", "bravo", "alpha", "delta"]


@pytest.mark.parametrize("field, required, nulls_last", [
    ("created_at", False, False), ("id", False, False), ("day", True, False),
    ("day", False, True), ("via", False, True)])
def test_a_descending_key_that_is_never_null_keeps_the_index_order(field, required,
                                                                   nulls_last):
    """`DESC NULLS LAST` is not an order a btree index can be read in, so
    Postgres would sort every match of a newest-first view (a symbol's 2,500
    bars, a collection's million records) to return one page. A key that can't
    be null orders the same either way, so it is left plain."""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    from agentplatform.appdata.records import R
    from agentplatform.appdata.views import order_by
    c = events(["day"])
    c["fields"] = dict(FIELDS, day={"type": "date", "required": required})
    coll = validate_app({"collections": [c]}).collections["events"]
    sql = str(select(R.id).order_by(*order_by(coll, [(field, "desc")])).compile(
        dialect=postgresql.dialect()))
    assert sql.endswith("DESC NULLS LAST" if nulls_last else "DESC"), sql
    asc = str(select(R.id).order_by(*order_by(coll, [(field, "asc")])).compile(
        dialect=postgresql.dialect()))
    assert asc.endswith("ASC NULLS LAST"), asc


def test_a_cursor_on_a_never_null_key_is_a_range_the_index_can_seek():
    """Without a plain bound on the leading key, the cursor's OR is only a
    filter: every page re-reads every row before it, and a 1M-row scan goes
    quadratic (it ran out of its 60 seconds at 611k rows)."""
    from sqlalchemy.dialects import postgresql

    from agentplatform.appdata.views import _after
    c = events(["at"])
    c["fields"] = dict(FIELDS, at={"type": "datetime", "required": True})
    coll = validate_app({"collections": [c]}).collections["events"]
    cond = str(_after(coll, [("at", "asc"), ("id", "asc")],
                      ["2026-10-01T00:00:00.000000Z", "abc"]).compile(
        dialect=postgresql.dialect()))
    assert "ix_time1 >= " in cond and "IS NULL" not in cond, cond
    desc = str(_after(coll, [("created_at", "desc"), ("id", "desc")],
                      ["2026-10-01T00:00:00.000000Z", "abc"]).compile(
        dialect=postgresql.dialect()))
    assert "created_at <= " in desc and "IS NULL" not in desc, desc
    # A key that can be null keeps its null branch and gets no range.
    nullable = str(_after(coll, [("n", "asc"), ("id", "asc")], [3, "abc"]).compile(
        dialect=postgresql.dialect()))
    assert "IS NULL" in nullable and ">=" not in nullable, nullable


@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_paging_on_a_required_key_with_ties(sf, direction):
    c = events(["at"])
    c["fields"] = dict(FIELDS, at={"type": "datetime", "required": True})
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": f"r{i:02}", "at": f"2026-10-0{1 + i % 3}T10:00:00Z"}
                         for i in range(17)])
    view = view_of(c, sort=[{"field": "at", "dir": direction}], paging=True, limit=200)
    everything = await names(sf, ctx, view)
    assert len(everything) == 17
    seen, cursor = [], None
    async with sf() as s:
        while True:
            page = await execute_view(s, ctx, OWNER, view, limit=4, cursor=cursor)
            seen += [r["values"]["name"] for r in page["rows"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert seen == everything


async def test_the_default_order_is_newest_first(sf):
    c = events()
    ctx = await make_app(sf, [c])
    created = await seed(sf, ctx, [{"name": str(i)} for i in range(5)])
    expected = sorted(created, key=lambda r: (r["values"]["created_at"], r["id"]),
                      reverse=True)
    assert await names(sf, ctx, view_of(c)) == [r["values"]["name"] for r in expected]


@pytest.mark.parametrize("indexed", INDEXINGS)
@pytest.mark.parametrize("sort", [
    [{"field": "n"}], [{"field": "n", "dir": "desc"}],
    [{"field": "tag"}, {"field": "x", "dir": "desc"}], [{"field": "day", "dir": "desc"}],
    [],
])
async def test_paging_visits_every_row_once_in_order(sf, indexed, sort):
    c = events(indexed)
    ctx = await make_app(sf, [c])
    # Ties and nulls on every sort key, so the id tiebreak and the null
    # handling both carry the cursor.
    rows = [{"name": f"r{i:02}", **({"n": i % 3} if i % 4 else {}),
             **({"tag": "ab"[i % 2]} if i % 5 else {}),
             **({"x": float(i % 2)} if i % 3 else {}),
             **({"day": f"2026-10-0{1 + i % 3}"} if i % 2 else {})} for i in range(23)]
    await seed(sf, ctx, rows)
    view = view_of(c, sort=sort, paging=True, limit=200)
    everything = await names(sf, ctx, view)
    assert len(everything) == 23
    seen, cursor = [], None
    async with sf() as s:
        while True:
            page = await execute_view(s, ctx, OWNER, view, limit=4, cursor=cursor)
            seen += [r["values"]["name"] for r in page["rows"]]
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert seen == everything


async def test_limits_and_cursors_are_checked(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": str(i)} for i in range(3)])
    paged = view_of(c, view="paged", paging=True, sort=[{"field": "name"}])
    other = view_of(c, view="other", paging=True, sort=[{"field": "n"}])
    flat = view_of(c, view="flat", limit=2)
    async with sf() as s:
        first = await execute_view(s, ctx, OWNER, paged, limit=1)
        assert first["next_cursor"] and len(first["rows"]) == 1
        await refused("AD-CURSOR", execute_view(s, ctx, OWNER, other,
                                                cursor=first["next_cursor"]))
        await refused("AD-CURSOR", execute_view(s, ctx, OWNER, paged, cursor="garbage"))
        await refused("AD-CURSOR", execute_view(s, ctx, OWNER, flat,
                                                cursor=first["next_cursor"]))
        await refused("AD-LIMIT", execute_view(s, ctx, OWNER, paged, limit=201))
        await refused("AD-LIMIT", execute_view(s, ctx, OWNER, paged, limit=0))
        # A view without paging returns its first page and no cursor.
        result = await execute_view(s, ctx, OWNER, flat)
        assert len(result["rows"]) == 2 and result["next_cursor"] is None


async def test_rows_match_the_web_contract(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": "a", "n": 1, "secret": "s"}])
    view = view_of(c, fields=["name", "secret", "created_at"])
    async with sf() as s:
        result = await execute_view(s, ctx, OWNER, view)
    assert set(result) == {"rows", "next_cursor", "as_of", "stale"}
    assert result["stale"] is False and result["as_of"].endswith("Z")
    (row,) = result["rows"]
    assert set(row) == {"id", "values", "restricted"}
    assert set(row["values"]) == {"name", "secret", "created_at"}
    assert row["values"]["secret"] is None and row["restricted"] == ["secret"]


async def test_count_views(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ROWS)
    count = view_of(c, aggregates=[{"fn": "count", "as": "total"}],
                    filter=[{"field": "tag", "op": "eq", "value": "a"}])
    async with sf() as s:
        result = await execute_view(s, ctx, OWNER, count)
    assert result["count"] == 2 and set(result) == {"count", "as_of", "stale"}


async def test_run_view_reads_the_published_view(sf):
    c = events()
    published = {"view": "recent", "collection": "events", "sort": [{"field": "name"}]}
    ctx = await make_app(sf, [c], [published])
    await seed(sf, ctx, ROWS[:2])
    async with sf() as s:
        result = await run_view(s, ctx, OWNER, "recent")
        assert [r["values"]["name"] for r in result["rows"]] == ["alpha", "bravo"]
        await refused("AD-NO-VIEW", run_view(s, ctx, OWNER, "nope"))


# --- access on results and predicates ----------------------------------------------------------

@pytest.mark.parametrize("body, use", [
    ({"filter": [{"field": "secret", "op": "eq", "value": "s"}]}, "filter"),
    ({"filter": [{"field": "secret", "op": "is_null", "value": True}]}, "filter"),
    ({"filter": [{"field": "secret", "op": "contains", "value": "s"}]}, "filter"),
    ({"sort": [{"field": "secret"}]}, "sort"),
    ({"aggregates": [{"fn": "count", "as": "n"}],
      "filter": [{"field": "secret", "op": "in", "value": ["s"]}]}, "filter"),
])
async def test_predicates_on_unreadable_fields_are_refused(sf, body, use):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": "a", "secret": "s"}])
    view = view_of(c, **body)
    async with sf() as s:
        err = await refused("AD-PREDICATE-FORBIDDEN", execute_view(s, ctx, OWNER, view))
        assert err.status == 403 and err.detail == {"field": "secret"} and use in err.message
        # Kyle reads `secret`, so the same view runs for him.
        await execute_view(s, ctx, KYLE, view)


async def test_anchoring_on_an_unreadable_field_is_refused(sf):
    fields = dict(FIELDS, hidden_day={"type": "date", "access": {"read": ["kyle"]}})
    c = dict(events(), fields=fields)
    ctx = await make_app(sf, [c])
    view = view_of(c, filter=[{"field": "day", "op": "within_last", "value": "1w",
                               "anchor": "max(hidden_day)"}])
    async with sf() as s:
        await refused("AD-PREDICATE-FORBIDDEN", execute_view(s, ctx, OWNER, view))
        await execute_view(s, ctx, KYLE, view)


async def test_principal_fields_follow_the_collection_read_default(sf):
    c = dict(events(), fields=dict(FIELDS, public={
        "type": "string", "access": {"read": ["agent:bob"]}}))
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, [{"name": "a", "public": "p"}])
    async with sf() as s:
        await refused("AD-PREDICATE-FORBIDDEN", execute_view(
            s, ctx, BOB, view_of(c, filter=[{"field": "author", "op": "eq",
                                             "value": "agent:pai"}])))
        await refused("AD-PREDICATE-FORBIDDEN", execute_view(
            s, ctx, BOB, view_of(c, sort=[{"field": "name"}])))
        rows = (await execute_view(s, ctx, BOB, view_of(c)))["rows"]
    assert rows[0]["values"]["public"] == "p"
    assert {"name", "author", "via"} <= set(rows[0]["restricted"])


async def test_a_reader_principal_reads_and_a_stranger_is_refused(sf):
    c = events()
    ctx = await make_app(sf, [c])
    await seed(sf, ctx, ROWS)
    view = view_of(c, sort=[{"field": "name"}])
    assert await names(sf, ctx, view, caller=QA) == ["alpha", "bravo", "charlie", "delta"]
    async with sf() as s:
        await refused("AD-FORBIDDEN", execute_view(s, ctx, Caller("agent:eve"), view))
