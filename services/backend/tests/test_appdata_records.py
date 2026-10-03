"""The records engine (design 39, "Collections", "Rules", "Storage"): CRUD
under the write modes, system fields, per-field access, tool-only writers,
rules, refs and their delete plans, side columns and write counters.

Every test runs on SQLite; with AP_TEST_PG_URL naming a scratch database they
run on Postgres too, where the advisory lock and JSONB extraction are real.
"""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import inspect, select

from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import (AppDataApp, AppDataDefinition, AppDataRecord,
                                          AppDataRecordVersion)
from agentplatform.appdata.records import (create_record, delete_record, delete_records,
                                           get_record, get_record_version, load_app, lock_key,
                                           plan_delete, record_history, update_record,
                                           write_counter)
from agentplatform.db import Base, make_engine, make_session_factory

PG_URL = os.environ.get("AP_TEST_PG_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])
APP_TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("app_data_")]

OWNER = Caller("agent:pai")
KYLE = Caller("kyle")
BOB = Caller("agent:bob")
QA = Caller("login:qa")


@pytest.fixture(params=BACKENDS)
async def engine(request, tmp_path):
    # A file, not :memory:, so concurrent sessions share one database.
    url = f"sqlite+aiosqlite:///{tmp_path}/appdata.db" if request.param == "sqlite" \
        else PG_URL
    e = make_engine(url)
    async with e.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=APP_TABLES))
        if request.param == "postgres":
            await conn.exec_driver_sql("TRUNCATE " + ", ".join(t.name for t in APP_TABLES))
    yield e
    await e.dispose()


@pytest.fixture
def sf(engine):
    return make_session_factory(engine)


async def make_app(sf, collections, views=(), *, owner="agent:pai", tz="UTC",
                   status="active"):
    """Publish an App straight into the tables and load it as the engine does."""
    kind, owner_id = ("kyle", "kyle") if owner == "kyle" else ("agent", owner[6:])
    app_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(AppDataApp(id=app_id, name=f"app_{app_id[:12]}", owner_kind=kind,
                         owner_id=owner_id, timezone=tz, status=status))
        for kind_, items in (("collection", collections), ("view", views)):
            for body in items:
                s.add(AppDataDefinition(app_id=app_id, kind=kind_, name=body[kind_],
                                        version=1, body=body, state="published",
                                        author=owner))
        await s.commit()
        return await load_app(s, app_id)


async def refused(code, awaitable):
    with pytest.raises(RecordError) as caught:
        await awaitable
    assert caught.value.code == code, caught.value
    return caught.value


def coll(name="items", fields=None, **extra):
    body = {"collection": name,
            "fields": fields or {"title": {"type": "string", "required": True, "max": 40},
                                 "n": {"type": "int", "min": 0, "max": 10}}}
    body.update(extra)
    return body


# --- schema ----------------------------------------------------------------------------

async def test_records_have_the_created_at_index_retention_and_ordering_use(engine):
    async with engine.connect() as c:
        got = await c.run_sync(lambda sc: {ix["name"]: ix["column_names"] for ix in
                                           inspect(sc).get_indexes("app_data_records")})
    assert got["ix_app_data_records_created"] == ["app_id", "collection", "created_at"]


# --- create and system fields ----------------------------------------------------------

async def test_create_stamps_system_fields_server_side(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, Caller("agent:pai", via_tool="tool:judgment"),
                                  "items", {"title": "a", "n": 3})
    v = row["values"]
    assert row["id"] == v["id"] and len(row["id"]) == 32
    assert (v["author"], v["via"], v["version"], v["collection_version"]) == (
        "agent:pai", "tool:judgment", 1, 1)
    assert v["created_at"] == v["updated_at"] and v["created_at"].endswith("Z")
    assert row["restricted"] == []


@pytest.mark.parametrize("key", ["id", "author", "via", "version", "created_at",
                                 "collection_version", "updated_at"])
async def test_a_system_field_in_input_is_refused_not_ignored(sf, key):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        await refused("AD-SYSTEM-FIELD", create_record(s, ctx, OWNER, "items",
                                                       {"title": "a", key: "x"}))


@pytest.mark.parametrize("values, code", [
    ({"title": "a", "nope": 1}, "AD-UNKNOWN-FIELD"),
    ({"n": 1}, "AD-REQUIRED"),
    ({"title": None}, "AD-REQUIRED"),
    ({"title": "x" * 41}, "AD-INVALID-VALUE"),
    ({"title": 5}, "AD-INVALID-VALUE"),
    ({"title": "a", "n": 11}, "AD-INVALID-VALUE"),
    ({"title": "a", "n": -1}, "AD-INVALID-VALUE"),
    ({"title": "a", "n": 1.5}, "AD-INVALID-VALUE"),
    ({"title": "a", "n": True}, "AD-INVALID-VALUE"),
    ("not an object", "AD-INVALID-VALUE"),
])
async def test_values_are_checked_against_types_and_bounds(sf, values, code):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        await refused(code, create_record(s, ctx, OWNER, "items", values))
        assert (await s.execute(select(AppDataRecord))).first() is None


async def test_every_field_type_validates_and_normalizes(sf):
    fields = {"s": {"type": "string"}, "t": {"type": "text"}, "i": {"type": "int"},
              "x": {"type": "number"}, "b": {"type": "bool"}, "d": {"type": "date"},
              "dt": {"type": "datetime"}, "e": {"type": "enum", "values": ["a", "b"]},
              "u": {"type": "url"}}
    ctx = await make_app(sf, [coll(fields=fields)], tz="America/Toronto")
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {
            "s": "hi", "t": "long", "i": 2, "x": 3, "b": False, "d": "2026-10-02",
            # No offset: read in App time (Toronto is UTC-4 in October).
            "dt": "2026-10-02T08:30:00", "e": "b", "u": "https://x.test"})
        v = row["values"]
        assert v["dt"] == "2026-10-02T12:30:00.000000Z"
        assert v["x"] == 3.0 and v["b"] is False and v["d"] == "2026-10-02"
        offset = await create_record(s, ctx, OWNER, "items",
                                     {"dt": "2026-10-02T08:30:00+02:00"})
        assert offset["values"]["dt"] == "2026-10-02T06:30:00.000000Z"
        for bad in ({"e": "c"}, {"d": "2026-13-01"}, {"dt": "yesterday"}, {"b": "true"},
                    {"x": float("inf")}):
            await refused("AD-INVALID-VALUE", create_record(s, ctx, OWNER, "items", bad))


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hi", "/relative/path", "//host.test/x",
    "example.com", "ftp://x.test", "https://user:pw@x.test/", "https://user@x.test/",
    "https://x.test/\x00", "https://x.test/a\nb", "https://x.test/\x7f", "https:///nohost",
    "http:x.test", " https://x.test"])
async def test_a_url_that_isnt_a_plain_http_or_https_address_is_refused(sf, url):
    ctx = await make_app(sf, [coll(fields={"u": {"type": "url"}})])
    async with sf() as s:
        err = await refused("AD-URL", create_record(s, ctx, OWNER, "items", {"u": url}))
        assert err.detail == {"field": "u"}
        good = await create_record(s, ctx, OWNER, "items", {"u": "https://x.test/a?b=1#c"})
        await refused("AD-URL", update_record(s, ctx, OWNER, "items", good["id"],
                                              {"u": url}, expected_version=1))


async def test_http_and_https_urls_are_stored_as_given(sf):
    ctx = await make_app(sf, [coll(fields={"u": {"type": "url"}})])
    async with sf() as s:
        for url in ("http://x.test", "HTTPS://X.test:8443/p?q=1"):
            row = await create_record(s, ctx, OWNER, "items", {"u": url})
            assert row["values"]["u"] == url


async def test_typed_lists_are_bounded_normalized_and_versioned_with_the_record(sf):
    fields = {
        "tags": {"type": "list", "items": {"type": "string", "max": 12}},
        "steps": {"type": "list", "max_items": 2, "items": {"type": "object", "fields": {
            "action": {"type": "string", "max": 30},
            "at": {"type": "datetime"},
            "score": {"type": "number", "min": 0, "max": 10},
        }}},
    }
    ctx = await make_app(sf, [coll(fields=fields)], tz="America/Toronto")
    first = {"action": "open", "at": "2026-10-02T08:30:00", "score": 3}
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"tags": ["smoke"],
                                                       "steps": [first]})
        assert row["values"]["steps"] == [{"action": "open",
                                           "at": "2026-10-02T12:30:00.000000Z",
                                           "score": 3.0}]
        changed = await update_record(s, ctx, OWNER, "items", row["id"],
                                      {"tags": ["smoke", "manual"]}, expected_version=1)
        assert changed["values"]["tags"] == ["smoke", "manual"]
        history = (await s.execute(select(AppDataRecordVersion))).scalar_one()
        assert history.doc["tags"] == ["smoke"]
        for bad in ({"tags": ["x" * 13]}, {"steps": [first] * 3},
                    {"steps": [{**first, "score": 11}]},
                    {"steps": [{**first, "nested": {}}]},
                    {"steps": [{"action": "missing fields"}]},
                    {"steps": "not a list"}):
            await refused("AD-INVALID-VALUE", create_record(s, ctx, OWNER, "items", bad))


async def test_an_object_list_cannot_amplify_a_record_past_one_mib(sf):
    ctx = await make_app(sf, [coll(fields={"steps": {"type": "list", "items": {
        "type": "object", "fields": {"a": {"type": "text"}, "b": {"type": "text"}}}}})])
    async with sf() as s:
        error = await refused("AD-INVALID-VALUE", create_record(
            s, ctx, OWNER, "items", {"steps": [{"a": "x" * 16000,
                                                  "b": "y" * 16000}] * 40}))
        assert error.status == 413


async def test_list_items_inherit_the_containing_fields_read_access(sf):
    fields = {"private_steps": {"type": "list", "items": {"type": "string"},
                                "access": {"read": ["owner"]}}}
    ctx = await make_app(sf, [coll(fields=fields)])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"private_steps": ["secret note"]})
        visible = await get_record(s, ctx, KYLE, "items", row["id"])
    assert visible["values"]["private_steps"] is None
    assert "private_steps" in visible["restricted"]


async def test_versioned_history_pages_and_masks_past_values_with_current_access(sf):
    fields = {"title": {"type": "string"},
              "private": {"type": "text", "access": {"read": ["owner"]}}}
    ctx = await make_app(sf, [coll(fields=fields, write_mode="versioned")])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "first",
                                                        "private": "old secret"})
        await update_record(s, ctx, OWNER, "items", row["id"],
                            {"title": "second"}, expected_version=1)
        await update_record(s, ctx, OWNER, "items", row["id"],
                            {"title": "third"}, expected_version=2)
        first_page = await record_history(s, ctx, KYLE, "items", row["id"], limit=2)
        assert [v["version"] for v in first_page["versions"]] == [3, 2]
        assert first_page["next_before_version"] == 2
        assert [v["record"]["values"]["title"] for v in first_page["versions"]] == [
            "third", "second"]
        assert all(v["record"]["values"]["private"] is None
                   and "private" in v["record"]["restricted"]
                   for v in first_page["versions"])
        last = await record_history(s, ctx, KYLE, "items", row["id"], limit=2,
                                    before_version=2)
        assert [v["version"] for v in last["versions"]] == [1]
        assert last["next_before_version"] is None
        old = await get_record_version(s, ctx, OWNER, "items", row["id"], 1)
        assert old["values"]["private"] == "old secret"
        assert old["values"]["version"] == 1
        await refused("AD-NOT-FOUND", get_record_version(s, ctx, OWNER, "items",
                                                          row["id"], 4))


async def test_editable_records_keep_internal_audit_without_exposing_history(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        await update_record(s, ctx, OWNER, "items", row["id"],
                            {"title": "b"}, expected_version=1)
        await refused("AD-NOT-VERSIONED", record_history(s, ctx, OWNER,
                                                          "items", row["id"]))
        assert (await s.execute(select(AppDataRecordVersion))).first() is not None


# --- side columns ----------------------------------------------------------------------------

async def test_indexed_fields_fill_their_side_columns(sf):
    fields = {"sym": {"type": "string"}, "kind": {"type": "enum", "values": ["a"]},
              "px": {"type": "number"}, "day": {"type": "date"}, "note": {"type": "string"}}
    ctx = await make_app(sf, [coll(fields=fields, indexed=["sym", "day", "px", "kind"])])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {
            "sym": "XIU", "kind": "a", "px": 31, "day": "2026-10-02", "note": "n"})
        rec = (await s.execute(select(AppDataRecord))).scalar_one()
    assert (rec.ix_text1, rec.ix_text2, rec.ix_num1) == ("XIU", "a", 31.0)
    assert rec.ix_time1.replace(tzinfo=None).isoformat() == "2026-10-02T00:00:00"
    assert rec.doc == {"sym": "XIU", "kind": "a", "px": 31.0, "day": "2026-10-02",
                       "note": "n"}
    assert row["values"]["note"] == "n"


async def test_an_indexed_value_over_256_characters_is_refused(sf):
    fields = {"sym": {"type": "string"}, "free": {"type": "string"}}
    ctx = await make_app(sf, [coll(fields=fields, indexed=["sym"])])
    async with sf() as s:
        await refused("AD-INDEX-TOO-LONG",
                      create_record(s, ctx, OWNER, "items", {"sym": "x" * 257}))
        await create_record(s, ctx, OWNER, "items", {"sym": "x" * 256})
        # Only indexed fields are bounded by the side column.
        await create_record(s, ctx, OWNER, "items", {"free": "x" * 900})


async def test_an_update_refreshes_the_side_columns(sf):
    fields = {"sym": {"type": "string"}, "at": {"type": "datetime"}}
    ctx = await make_app(sf, [coll(fields=fields, indexed=["sym", "at"])])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items",
                                  {"sym": "A", "at": "2026-10-02T10:00:00Z"})
        await update_record(s, ctx, OWNER, "items", row["id"], {"sym": None, "at":
                            "2026-10-03T10:00:00Z"}, expected_version=1)
        rec = (await s.execute(select(AppDataRecord))).scalar_one()
    assert rec.ix_text1 is None
    assert rec.ix_time1.replace(tzinfo=None).isoformat() == "2026-10-03T10:00:00"


# --- write counters -----------------------------------------------------------------------------

async def test_every_committed_write_bumps_the_collection_counter(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        assert await write_counter(s, ctx.app_id, "items") == 0
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        assert await write_counter(s, ctx.app_id, "items") == 1
        await update_record(s, ctx, OWNER, "items", row["id"], {"n": 1},
                            expected_version=1)
        assert await write_counter(s, ctx.app_id, "items") == 2
        await delete_record(s, ctx, OWNER, "items", row["id"])
        assert await write_counter(s, ctx.app_id, "items") == 3
        # A refused write commits nothing, so it bumps nothing.
        await refused("AD-REQUIRED", create_record(s, ctx, OWNER, "items", {}))
        assert await write_counter(s, ctx.app_id, "items") == 3


# --- editable and immutable ----------------------------------------------------------------------

async def test_editable_update_needs_the_current_version(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a", "n": 1})
        updated = await update_record(s, ctx, OWNER, "items", row["id"],
                                      {"n": 2}, expected_version=1)
        assert updated["values"]["version"] == 2 and updated["values"]["n"] == 2
        err = await refused("AD-VERSION-CONFLICT", update_record(
            s, ctx, OWNER, "items", row["id"], {"n": 3}, expected_version=1))
        assert err.status == 409 and err.detail == {"current_version": 2}
        history = (await s.execute(select(AppDataRecordVersion))).scalars().all()
    assert [(h.version, h.doc) for h in history] == [(1, {"title": "a", "n": 1})]


async def test_an_update_that_changes_nothing_keeps_the_version(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        same = await update_record(s, ctx, OWNER, "items", row["id"], {"title": "a"},
                                   expected_version=1)
    assert same["values"]["version"] == 1


async def test_an_update_restamps_author_via_and_updated_at(sf):
    ctx = await make_app(sf, [coll(access={"update": ["owner", "kyle"]})])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        updated = await update_record(s, ctx, KYLE, "items", row["id"], {"title": "b"},
                                      expected_version=1)
    v = updated["values"]
    assert (v["author"], v["via"]) == ("kyle", None)
    assert v["updated_at"] >= v["created_at"] == row["values"]["created_at"]


async def test_clearing_a_required_field_is_refused(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        await refused("AD-REQUIRED", update_record(s, ctx, OWNER, "items", row["id"],
                                                   {"title": None}, expected_version=1))


async def test_immutable_records_are_written_once_but_may_be_deleted(sf):
    ctx = await make_app(sf, [coll(write_mode="immutable")])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        err = await refused("AD-IMMUTABLE", update_record(
            s, ctx, OWNER, "items", row["id"], {"title": "b"}, expected_version=1))
        assert err.status == 409
        await delete_record(s, ctx, OWNER, "items", row["id"])
        await refused("AD-NOT-FOUND", get_record(s, ctx, OWNER, "items", row["id"]))


async def test_unknown_records_and_collections_are_404(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        assert (await refused("AD-NOT-FOUND", get_record(s, ctx, OWNER, "items", "x"))
                ).status == 404
        await refused("AD-NOT-FOUND", update_record(s, ctx, OWNER, "items", "x", {"n": 1},
                                                    expected_version=1))
        await refused("AD-NO-COLLECTION", create_record(s, ctx, OWNER, "nope", {}))


async def test_a_retired_app_is_read_only(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
    ctx.status = "retired"
    async with sf() as s:
        await refused("AD-APP-RETIRED", create_record(s, ctx, OWNER, "items", {"title": "b"}))
        await refused("AD-APP-RETIRED", update_record(s, ctx, OWNER, "items", row["id"],
                                                      {"n": 1}, expected_version=1))
        await refused("AD-APP-RETIRED", delete_record(s, ctx, OWNER, "items", row["id"]))
        assert (await get_record(s, ctx, OWNER, "items", row["id"]))["id"] == row["id"]


async def test_published_definitions_that_no_longer_validate_answer_503(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="collection", name="items",
                                version=2, body={"collection": "items", "fields": {}},
                                state="published", author="agent:pai"))
        await s.commit()
        err = await refused("AD-DEFINITION-INVALID", load_app(s, ctx.app_id))
    assert err.status == 503


async def test_load_app_takes_the_newest_published_version_and_skips_drafts(sf):
    ctx = await make_app(sf, [coll()])
    v2 = coll(fields={"title": {"type": "string"}, "extra": {"type": "bool"}})
    draft = coll(fields={"only_in_draft": {"type": "bool"}})
    async with sf() as s:
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="collection", name="items",
                                version=2, body=v2, state="published", author="kyle"))
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="collection", name="items",
                                version=3, body=draft, state="draft", author="kyle"))
        await s.commit()
        ctx = await load_app(s, ctx.app_id)
        assert set(ctx.collection("items").fields) == {"title", "extra"}
        row = await create_record(s, ctx, OWNER, "items", {"extra": True})
    assert row["values"]["collection_version"] == 2


# --- principals and per-field access -----------------------------------------------------------------

ACCESS = coll(fields={
    "title": {"type": "string"},
    "secret": {"type": "string", "access": {"read": ["kyle"], "create": ["kyle"],
                                             "update": ["kyle"]}},
    "notes": {"type": "string", "access": {"read": ["owner", "kyle", "agent:bob",
                                                    "login:qa"],
                                           "update": ["owner", "agent:bob"]}},
}, access={"read": ["owner", "kyle"], "create": ["owner", "kyle"],
           "update": ["owner", "kyle"], "delete": ["kyle"]})


async def test_reads_null_and_name_the_fields_the_caller_cannot_read(sf):
    ctx = await make_app(sf, [ACCESS])
    async with sf() as s:
        row = await create_record(s, ctx, KYLE, "items",
                                  {"title": "t", "secret": "s", "notes": "n"})
        mine = await get_record(s, ctx, OWNER, "items", row["id"])
        assert mine["values"]["secret"] is None and mine["restricted"] == ["secret"]
        assert mine["values"]["title"] == "t"
        theirs = await get_record(s, ctx, KYLE, "items", row["id"])
        assert theirs["values"]["secret"] == "s" and theirs["restricted"] == []
        # bob and login:qa read only `notes` (a field override wider than the
        # default), so the rest of the row, author and via included, is restricted.
        for caller in (BOB, QA):
            seen = await get_record(s, ctx, caller, "items", row["id"])
            assert seen["values"]["notes"] == "n"
            assert set(seen["restricted"]) == {"title", "secret", "author", "via"}
            assert seen["values"]["version"] == 1   # row metadata stays visible


async def test_a_caller_who_can_read_nothing_is_refused_the_collection(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        err = await refused("AD-FORBIDDEN", get_record(s, ctx, BOB, "items", row["id"]))
    assert err.status == 403


async def test_the_create_response_is_filtered_for_its_own_writer(sf):
    ctx = await make_app(sf, [ACCESS])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "t"})
    assert row["restricted"] == ["secret"]


async def test_writes_to_fields_the_caller_cannot_write_are_refused(sf):
    ctx = await make_app(sf, [ACCESS])
    async with sf() as s:
        err = await refused("AD-FIELD-FORBIDDEN", create_record(
            s, ctx, OWNER, "items", {"title": "t", "secret": "s"}))
        assert err.status == 403 and err.detail == {"field": "secret"}
        row = await create_record(s, ctx, OWNER, "items", {"title": "t"})
        await refused("AD-FIELD-FORBIDDEN", update_record(
            s, ctx, OWNER, "items", row["id"], {"secret": "s"}, expected_version=1))
        # bob holds update on `notes` only: that write passes, `title` doesn't.
        await update_record(s, ctx, BOB, "items", row["id"], {"notes": "bob"},
                            expected_version=1)
        await refused("AD-FIELD-FORBIDDEN", update_record(
            s, ctx, BOB, "items", row["id"], {"title": "x"}, expected_version=2))


async def test_collection_verbs_gate_principals(sf):
    ctx = await make_app(sf, [ACCESS])
    async with sf() as s:
        await refused("AD-FORBIDDEN", create_record(s, ctx, BOB, "items", {"notes": "n"}))
        await refused("AD-FORBIDDEN", create_record(s, ctx, QA, "items", {"notes": "n"}))
        row = await create_record(s, ctx, OWNER, "items", {"title": "t"})
        # Only Kyle deletes here; the owner is not implicitly allowed.
        await refused("AD-FORBIDDEN", delete_record(s, ctx, OWNER, "items", row["id"]))
        await refused("AD-FORBIDDEN", plan_delete(s, ctx, BOB, "items", [row["id"]]))
        await delete_record(s, ctx, KYLE, "items", row["id"])


async def test_owner_means_whoever_owns_the_app(sf):
    ctx = await make_app(sf, [coll()], owner="kyle")
    async with sf() as s:
        await refused("AD-FORBIDDEN", create_record(s, ctx, OWNER, "items", {"title": "a"}))
        row = await create_record(s, ctx, KYLE, "items", {"title": "a"})
    assert row["values"]["author"] == "kyle"


# --- tool-only writers ---------------------------------------------------------------------------------

TOOL_ONLY = coll(writers={"create": ["tool:judgment"], "update": ["tool:judgment"]},
                 access={"read": ["owner", "kyle"], "create": ["owner"], "update": ["owner"],
                         "delete": ["owner"]})
VIA_JUDGMENT = Caller("agent:pai", via_tool="tool:judgment")


async def test_tool_only_collections_refuse_direct_writes(sf):
    ctx = await make_app(sf, [TOOL_ONLY])
    async with sf() as s:
        err = await refused("AD-TOOL-ONLY", create_record(s, ctx, OWNER, "items",
                                                          {"title": "a"}))
        assert err.status == 403
        await refused("AD-TOOL-ONLY", create_record(
            s, ctx, Caller("agent:pai", via_tool="tool:other"), "items", {"title": "a"}))
        row = await create_record(s, ctx, VIA_JUDGMENT, "items", {"title": "a"})
        assert row["values"]["via"] == "tool:judgment"
        await refused("AD-TOOL-ONLY", update_record(s, ctx, OWNER, "items", row["id"],
                                                    {"n": 1}, expected_version=1))
        await update_record(s, ctx, VIA_JUDGMENT, "items", row["id"], {"n": 1},
                            expected_version=1)
        # delete isn't reserved, so the owner deletes directly.
        await delete_record(s, ctx, OWNER, "items", row["id"])


async def test_tool_only_writers_still_need_the_callers_access(sf):
    ctx = await make_app(sf, [TOOL_ONLY])
    async with sf() as s:
        await refused("AD-FORBIDDEN", create_record(
            s, ctx, Caller("agent:bob", via_tool="tool:judgment"), "items", {"title": "a"}))


async def test_tool_only_delete(sf):
    body = coll(writers={"delete": ["tool:judgment"]})
    ctx = await make_app(sf, [body])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        await refused("AD-TOOL-ONLY", delete_record(s, ctx, OWNER, "items", row["id"]))
        await delete_record(s, ctx, VIA_JUDGMENT, "items", row["id"])


# --- rules ---------------------------------------------------------------------------------------------

STATUS = {"title": {"type": "string"},
          "status": {"type": "enum", "values": ["open", "approved"]},
          "owner_note": {"type": "string"}}
RULED = coll(fields=STATUS, access={"read": ["owner", "kyle"], "create": ["owner", "kyle"],
                                    "update": ["owner", "kyle"], "delete": ["owner"]},
             rules=[{"kind": "writer", "field": "status", "value": "approved",
                     "writers": ["kyle"]},
                    {"kind": "writer", "field": "owner_note", "writers": ["owner"]}])


async def test_writer_rule_with_a_value_guards_only_that_value(sf):
    ctx = await make_app(sf, [RULED])
    async with sf() as s:
        err = await refused("AD-RULE-WRITER", create_record(
            s, ctx, OWNER, "items", {"status": "approved"}))
        assert err.status == 403
        row = await create_record(s, ctx, OWNER, "items", {"status": "open"})
        await refused("AD-RULE-WRITER", update_record(
            s, ctx, OWNER, "items", row["id"], {"status": "approved"}, expected_version=1))
        await update_record(s, ctx, KYLE, "items", row["id"], {"status": "approved"},
                            expected_version=1)
        # Re-sending the value it already has isn't setting it.
        await update_record(s, ctx, OWNER, "items", row["id"],
                            {"status": "approved", "title": "x"}, expected_version=2)


async def test_writer_rule_without_a_value_guards_every_change(sf):
    ctx = await make_app(sf, [RULED])
    async with sf() as s:
        await refused("AD-RULE-WRITER", create_record(s, ctx, KYLE, "items",
                                                      {"owner_note": "k"}))
        row = await create_record(s, ctx, OWNER, "items", {"owner_note": "mine"})
        await refused("AD-RULE-WRITER", update_record(
            s, ctx, KYLE, "items", row["id"], {"owner_note": None}, expected_version=1))
        # Fields the rule doesn't name stay open to Kyle.
        await update_record(s, ctx, KYLE, "items", row["id"], {"title": "t"},
                            expected_version=1)


async def test_immutable_after_create(sf):
    body = coll(rules=[{"kind": "immutable_after_create", "fields": ["title"]}])
    ctx = await make_app(sf, [body])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a", "n": 1})
        err = await refused("AD-RULE-IMMUTABLE-FIELD", update_record(
            s, ctx, OWNER, "items", row["id"], {"title": "b"}, expected_version=1))
        assert err.detail == {"fields": ["title"]}
        await update_record(s, ctx, OWNER, "items", row["id"], {"title": "a", "n": 2},
                            expected_version=1)


UNIQUE = coll(fields={"habit": {"type": "string"}, "day": {"type": "date"},
                      "n": {"type": "int"}},
              rules=[{"kind": "unique", "fields": ["habit", "day"]}])


@pytest.mark.parametrize("indexed", [[], ["habit", "day"]])
async def test_unique_on_create_and_update(sf, indexed):
    ctx = await make_app(sf, [dict(UNIQUE, indexed=indexed)])
    async with sf() as s:
        await create_record(s, ctx, OWNER, "items", {"habit": "run", "day": "2026-10-01"})
        other = await create_record(s, ctx, OWNER, "items",
                                    {"habit": "run", "day": "2026-10-02"})
        err = await refused("AD-UNIQUE", create_record(
            s, ctx, OWNER, "items", {"habit": "run", "day": "2026-10-01"}))
        assert err.status == 409 and err.detail == {"fields": ["habit", "day"]}
        await refused("AD-UNIQUE", update_record(s, ctx, OWNER, "items", other["id"],
                                                 {"day": "2026-10-01"}, expected_version=1))
        # A record doesn't collide with itself.
        await update_record(s, ctx, OWNER, "items", other["id"], {"n": 1},
                            expected_version=1)


async def test_unique_treats_missing_values_as_equal(sf):
    ctx = await make_app(sf, [UNIQUE])
    async with sf() as s:
        await create_record(s, ctx, OWNER, "items", {"habit": "run"})
        await refused("AD-UNIQUE", create_record(s, ctx, OWNER, "items", {"habit": "run"}))


async def test_concurrent_creates_cannot_both_pass_unique(sf):
    ctx = await make_app(sf, [UNIQUE])

    async def one():
        async with sf() as s:
            try:
                await create_record(s, ctx, OWNER, "items",
                                    {"habit": "run", "day": "2026-10-01"})
                return "ok"
            except RecordError as exc:
                return exc.code

    results = await asyncio.gather(*(one() for _ in range(6)))
    assert sorted(results) == ["AD-UNIQUE"] * 5 + ["ok"]


def test_the_advisory_lock_key_is_stable_and_per_collection():
    assert lock_key("a", "c") == lock_key("a", "c")
    assert lock_key("a", "c") != lock_key("a", "d") != lock_key("b", "c")
    assert -2**63 <= lock_key("a", "c") < 2**63


# --- refs and delete plans -----------------------------------------------------------------------------

def ref_app(on_delete="restrict", required=False):
    return [coll("runs", fields={"name": {"type": "string"}}),
            coll("results", fields={"run": {"type": "ref", "collection": "runs",
                                            "on_delete": on_delete, "required": required},
                                    "ok": {"type": "bool"}}),
            coll("notes", fields={"run": {"type": "ref", "collection": "runs",
                                          "on_delete": "unlink"}},
                 access={"read": ["kyle"], "create": ["kyle"], "update": ["kyle"],
                         "delete": ["kyle"]})]


async def test_a_ref_must_name_a_record_of_its_collection_in_this_app(sf):
    ctx = await make_app(sf, ref_app())
    other = await make_app(sf, ref_app())
    async with sf() as s:
        foreign = await create_record(s, other, OWNER, "runs", {"name": "x"})
        await refused("AD-REF-MISSING", create_record(s, ctx, OWNER, "results",
                                                      {"run": foreign["id"]}))
        result = await create_record(s, ctx, OWNER, "results", {"ok": True})
        await refused("AD-REF-MISSING", create_record(s, ctx, OWNER, "results",
                                                      {"run": result["id"]}))
        await refused("AD-REF-MISSING", update_record(
            s, ctx, OWNER, "results", result["id"], {"run": "nope"}, expected_version=1))


async def test_restrict_refuses_the_delete_and_shows_the_plan(sf):
    ctx = await make_app(sf, ref_app())
    async with sf() as s:
        run = await create_record(s, ctx, OWNER, "runs", {"name": "r"})
        result = await create_record(s, ctx, OWNER, "results", {"run": run["id"]})
        err = await refused("AD-REF-RESTRICT", delete_record(s, ctx, OWNER, "runs",
                                                             run["id"]))
        assert err.status == 409
        assert err.detail["blocked"] == {"results.run": {"count": 1, "ids": [result["id"]]}}
        assert err.detail["ok"] is False
        # Nothing moved.
        assert (await get_record(s, ctx, OWNER, "runs", run["id"]))["id"] == run["id"]
        assert await write_counter(s, ctx.app_id, "runs") == 1


async def test_deleting_the_referrer_in_the_same_plan_doesnt_block(sf):
    ctx = await make_app(sf, ref_app())
    async with sf() as s:
        run = await create_record(s, ctx, OWNER, "runs", {"name": "r"})
        result = await create_record(s, ctx, OWNER, "results", {"run": run["id"]})
        await delete_record(s, ctx, OWNER, "results", result["id"])
        await delete_record(s, ctx, OWNER, "runs", run["id"])


async def test_unlink_clears_the_reference_and_records_a_version(sf):
    ctx = await make_app(sf, ref_app(on_delete="unlink"))
    async with sf() as s:
        run = await create_record(s, ctx, OWNER, "runs", {"name": "r"})
        result = await create_record(s, ctx, OWNER, "results", {"run": run["id"],
                                                                "ok": True})
        note = await create_record(s, ctx, KYLE, "notes", {"run": run["id"]})
        summary = await delete_record(s, ctx, OWNER, "runs", run["id"])
        # The owner can't read `notes`, so its plan counts that unlink but
        # names no id from it.
        assert summary["unlinks"] == {"results.run": {"count": 1, "ids": [result["id"]]},
                                      "notes.run": {"count": 1, "ids": []}}
        after = await get_record(s, ctx, OWNER, "results", result["id"])
        assert after["values"]["run"] is None and after["values"]["ok"] is True
        assert after["values"]["version"] == 2
        assert (await get_record(s, ctx, KYLE, "notes", note["id"]))["values"]["run"] is None
        for name in ("runs", "results", "notes"):
            assert await write_counter(s, ctx.app_id, name) == 2


async def test_the_plan_preview_changes_nothing(sf):
    ctx = await make_app(sf, ref_app(on_delete="unlink"))
    async with sf() as s:
        run = await create_record(s, ctx, OWNER, "runs", {"name": "r"})
        result = await create_record(s, ctx, OWNER, "results", {"run": run["id"]})
        preview = await plan_delete(s, ctx, OWNER, "runs", [run["id"]])
        assert preview == {"deletes": {"runs": {"count": 1, "ids": [run["id"]]}},
                           "unlinks": {"results.run": {"count": 1, "ids": [result["id"]]}},
                           "blocked": {}, "ok": True}
        still = await get_record(s, ctx, OWNER, "results", result["id"])
        assert still["values"]["run"] == run["id"]
        assert await write_counter(s, ctx.app_id, "runs") == 1


async def test_delete_removes_history_and_checks_expected_versions(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, "items", {"title": "a"})
        await update_record(s, ctx, OWNER, "items", row["id"], {"n": 1},
                            expected_version=1)
        await refused("AD-VERSION-CONFLICT", delete_record(s, ctx, OWNER, "items",
                                                           row["id"], expected_version=1))
        await delete_record(s, ctx, OWNER, "items", row["id"], expected_version=2)
        assert (await s.execute(select(AppDataRecordVersion))).first() is None


async def test_a_multi_record_delete_is_one_transaction(sf):
    ctx = await make_app(sf, ref_app())
    async with sf() as s:
        free = await create_record(s, ctx, OWNER, "runs", {"name": "free"})
        held = await create_record(s, ctx, OWNER, "runs", {"name": "held"})
        await create_record(s, ctx, OWNER, "results", {"run": held["id"]})
        await refused("AD-REF-RESTRICT", delete_records(s, ctx, OWNER, "runs",
                                                        [free["id"], held["id"]]))
        assert (await get_record(s, ctx, OWNER, "runs", free["id"]))["id"] == free["id"]
