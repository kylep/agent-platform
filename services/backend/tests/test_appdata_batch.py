"""Batch writes and batch jobs (design 39, "Collections" → "Batch writes").

`batch` writes up to 5,000 records or 5 MiB in one transaction under the
`insert`, `upsert` and `skip_existing` modes; a batch job stages larger writes
invisibly and publishes them in one commit that re-validates the creator's
authority, the schema versions and the quota hook.

Every test runs on SQLite; with AP_TEST_PG_URL naming a scratch database they
run on Postgres too.
"""
import os
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.appdata import batch as batch_mod
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.batch import (MAX_BATCH_BYTES, MAX_BATCH_RECORDS, abandon_staging_set,
                                         batch, commit_staging_set, open_staging_set,
                                         prune_staging_sets, stage)
from agentplatform.appdata.models import (AppDataApp, AppDataDefinition, AppDataRecord,
                                          AppDataRecordVersion, AppDataStagedRecord,
                                          AppDataStagingSet)
from agentplatform.appdata.records import (create_record, get_record, load_app,
                                           write_counter)
from agentplatform.appdata.views import run_view
from agentplatform.db import Base, make_engine, make_session_factory, utcnow

PG_URL = os.environ.get("AP_TEST_PG_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])
APP_TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("app_data_")]

OWNER = Caller("agent:pai")
KYLE = Caller("kyle")
BOB = Caller("agent:bob")
VIA_JUDGMENT = Caller("agent:pai", via_tool="tool:judgment")


@pytest.fixture(params=BACKENDS)
async def engine(request, tmp_path):
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


async def make_app(sf, collections, views=(), *, owner="agent:pai"):
    kind, owner_id = ("kyle", "kyle") if owner == "kyle" else ("agent", owner[6:])
    app_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(AppDataApp(id=app_id, name=f"app_{app_id[:12]}", owner_kind=kind,
                         owner_id=owner_id))
        for kind_, items in (("collection", collections), ("view", views)):
            for body in items:
                s.add(AppDataDefinition(app_id=app_id, kind=kind_, name=body[kind_],
                                        version=1, body=body, state="published",
                                        author=owner))
        await s.commit()
        return await load_app(s, app_id)


async def reload(sf, ctx):
    async with sf() as s:
        return await load_app(s, ctx.app_id)


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


BARS = coll("bars", fields={"symbol": {"type": "string", "required": True},
                            "day": {"type": "date", "required": True},
                            "close": {"type": "number"}},
            indexed=["symbol", "day"],
            access={"read": ["owner", "kyle"], "create": ["owner", "kyle"],
                    "update": ["owner", "kyle"], "delete": ["owner"]},
            rules=[{"kind": "unique", "fields": ["symbol", "day"]}])
IMMUTABLE_BARS = dict(BARS, write_mode="immutable")


async def count(sf, model=AppDataRecord):
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def docs(sf, ctx, collection):
    async with sf() as s:
        rows = (await s.execute(select(AppDataRecord).where(
            AppDataRecord.app_id == ctx.app_id, AppDataRecord.collection == collection)
        )).scalars().all()
    return rows


# --- limits ---------------------------------------------------------------------------------

async def test_a_call_holds_at_most_5000_records(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        err = await refused("AD-BATCH-TOO-LARGE", batch(
            s, ctx, OWNER, "items", [{"title": "a"}] * (MAX_BATCH_RECORDS + 1)))
        assert err.status == 413
        result = await batch(s, ctx, OWNER, "items", [{"title": "a"}] * MAX_BATCH_RECORDS)
    assert result["inserted"] == MAX_BATCH_RECORDS
    assert await count(sf) == MAX_BATCH_RECORDS


async def test_a_call_holds_at_most_5_mib(sf):
    ctx = await make_app(sf, [coll(fields={"t": {"type": "text"}})])
    big = "x" * (1024 * 1024)
    async with sf() as s:
        err = await refused("AD-BATCH-TOO-LARGE", batch(
            s, ctx, OWNER, "items", [{"t": big} for _ in range(6)]))
    assert err.status == 413 and err.detail["max_bytes"] == MAX_BATCH_BYTES
    assert await count(sf) == 0


@pytest.mark.parametrize("records", ["nope", [1, 2], None])
async def test_records_must_be_a_list_of_objects(sf, records):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        await refused("AD-INVALID-VALUE", batch(s, ctx, OWNER, "items", records))


async def test_an_unknown_mode_is_refused(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        await refused("AD-BATCH-MODE", batch(s, ctx, OWNER, "items", [{"title": "a"}],
                                             mode="replace"))
        await refused("AD-BATCH-MODE", batch(s, ctx, OWNER, "items", [{"title": "a"}],
                                             on_error="ignore"))


# --- insert and per-record errors -------------------------------------------------------------

async def test_insert_writes_every_record_in_one_transaction(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        result = await batch(s, ctx, VIA_JUDGMENT, "items",
                             [{"title": "a", "n": 1}, {"title": "b"}])
        assert result["inserted"] == 2 and result["errors"] == []
        assert len(result["ids"]) == 2 and all(result["ids"])
        row = await get_record(s, ctx, OWNER, "items", result["ids"][1])
        assert (row["values"]["title"], row["values"]["via"]) == ("b", "tool:judgment")
        # One call is one committed write: the counter moves once.
        assert await write_counter(s, ctx.app_id, "items") == 1


async def test_any_record_error_refuses_the_whole_call_by_default(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        err = await refused("AD-BATCH-REJECTED", batch(s, ctx, OWNER, "items", [
            {"title": "ok"}, {"n": 1}, {"title": "ok"}, {"title": "a", "n": 99},
            {"title": "a", "id": "x"}]))
    assert err.status == 422
    got = [(e["index"], e["code"]) for e in err.detail["errors"]]
    assert got == [(1, "AD-REQUIRED"), (3, "AD-INVALID-VALUE"), (4, "AD-SYSTEM-FIELD")]
    assert all(e["message"] for e in err.detail["errors"])
    assert err.detail["error_count"] == 3
    assert await count(sf) == 0


async def test_on_error_skip_commits_the_valid_records_and_reports_the_rest(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        result = await batch(s, ctx, OWNER, "items",
                             [{"title": "ok"}, {"n": 1}, {"title": "fine"}],
                             on_error="skip")
    assert result["inserted"] == 2
    assert [(e["index"], e["code"]) for e in result["errors"]] == [(1, "AD-REQUIRED")]
    assert result["ids"][1] is None and result["ids"][0] and result["ids"][2]
    assert await count(sf) == 2


async def test_insert_reports_unique_clashes_within_the_call_and_with_stored_records(sf):
    ctx = await make_app(sf, [BARS])
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [{"symbol": "X", "day": "2026-10-01"}])
        err = await refused("AD-BATCH-REJECTED", batch(s, ctx, OWNER, "bars", [
            {"symbol": "X", "day": "2026-10-01"},
            {"symbol": "X", "day": "2026-10-02"},
            {"symbol": "X", "day": "2026-10-02"}]))
    assert [(e["index"], e["code"]) for e in err.detail["errors"]] == [
        (0, "AD-UNIQUE"), (2, "AD-UNIQUE")]
    assert await count(sf) == 1


async def test_reported_errors_are_capped_but_counted(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        err = await refused("AD-BATCH-REJECTED", batch(s, ctx, OWNER, "items",
                                                       [{"n": 1}] * 500))
    assert err.detail["error_count"] == 500
    assert len(err.detail["errors"]) == batch_mod.MAX_REPORTED_ERRORS


# --- access, rules, refs and tool-only writers --------------------------------------------------

async def test_a_caller_without_create_is_refused_the_call(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        await refused("AD-FORBIDDEN", batch(s, ctx, BOB, "items", [{"title": "a"}]))


async def test_tool_only_writers_apply_to_batches(sf):
    ctx = await make_app(sf, [coll(writers={"create": ["tool:judgment"]})])
    async with sf() as s:
        await refused("AD-TOOL-ONLY", batch(s, ctx, OWNER, "items", [{"title": "a"}]))
        result = await batch(s, ctx, VIA_JUDGMENT, "items", [{"title": "a"}])
    assert result["inserted"] == 1


async def test_field_access_writer_rules_and_refs_are_per_record_errors(sf):
    ctx = await make_app(sf, [
        coll("runs", fields={"name": {"type": "string"}}),
        coll("items", fields={
            "title": {"type": "string"},
            "secret": {"type": "string", "access": {"create": ["kyle"]}},
            "status": {"type": "enum", "values": ["open", "approved"]},
            "run": {"type": "ref", "collection": "runs"}},
            rules=[{"kind": "writer", "field": "status", "value": "approved",
                    "writers": ["kyle"]}])])
    async with sf() as s:
        run = await create_record(s, ctx, OWNER, "runs", {"name": "r"})
        err = await refused("AD-BATCH-REJECTED", batch(s, ctx, OWNER, "items", [
            {"title": "a", "secret": "s"},
            {"status": "approved"},
            {"run": "missing"},
            {"run": run["id"], "status": "open"}]))
    assert [(e["index"], e["code"]) for e in err.detail["errors"]] == [
        (0, "AD-FIELD-FORBIDDEN"), (1, "AD-RULE-WRITER"), (2, "AD-REF-MISSING")]


async def test_a_retired_app_takes_no_batches(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        app = await s.get(AppDataApp, ctx.app_id)
        app.status = "retired"
        await s.commit()
    ctx = await reload(sf, ctx)
    async with sf() as s:
        await refused("AD-APP-RETIRED", batch(s, ctx, OWNER, "items", [{"title": "a"}]))


# --- upsert and skip_existing ----------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["upsert", "skip_existing"])
async def test_keyed_modes_need_a_unique_rule(sf, mode):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        err = await refused("AD-BATCH-NO-UNIQUE", batch(s, ctx, OWNER, "items",
                                                        [{"title": "a"}], mode=mode))
    assert err.status == 422


async def test_upsert_names_its_key_when_several_unique_rules_exist(sf):
    two = dict(BARS, fields={**BARS["fields"], "ref": {"type": "string"}},
               rules=[*BARS["rules"], {"kind": "unique", "fields": ["ref"]}])
    ctx = await make_app(sf, [two])
    rec = {"symbol": "X", "day": "2026-10-01", "ref": "r1"}
    async with sf() as s:
        await refused("AD-BATCH-KEY", batch(s, ctx, OWNER, "bars", [rec], mode="upsert"))
        await refused("AD-BATCH-KEY", batch(s, ctx, OWNER, "bars", [rec], mode="upsert",
                                            key=["close"]))
        result = await batch(s, ctx, OWNER, "bars", [rec], mode="upsert", key=["ref"])
        assert result["inserted"] == 1
        # Order doesn't matter: a key names the rule's fields as a set.
        again = await batch(s, ctx, OWNER, "bars", [dict(rec, close=2.0)], mode="upsert",
                            key=["day", "symbol"])
    assert again["updated"] == 1


async def test_upsert_into_an_editable_collection_updates_with_history(sf):
    ctx = await make_app(sf, [BARS])
    async with sf() as s:
        first = await batch(s, ctx, OWNER, "bars", [
            {"symbol": "X", "day": "2026-10-01", "close": 1.0},
            {"symbol": "Y", "day": "2026-10-01", "close": 1.0}], mode="upsert")
        result = await batch(s, ctx, KYLE, "bars", [
            {"symbol": "X", "day": "2026-10-01", "close": 2.0},
            {"symbol": "Y", "day": "2026-10-01", "close": 1.0},
            {"symbol": "Z", "day": "2026-10-01", "close": 3.0}], mode="upsert")
        assert (result["inserted"], result["updated"], result["unchanged"]) == (1, 1, 1)
        assert result["ids"][:2] == first["ids"]
        x = await get_record(s, ctx, OWNER, "bars", first["ids"][0])
        assert (x["values"]["close"], x["values"]["version"], x["values"]["author"]) == (
            2.0, 2, "kyle")
    assert await count(sf, AppDataRecordVersion) == 1


async def test_upsert_into_an_immutable_collection_replaces_only_changed_values(sf):
    ctx = await make_app(sf, [IMMUTABLE_BARS])
    async with sf() as s:
        first = await batch(s, ctx, OWNER, "bars", [
            {"symbol": "X", "day": "2026-10-01", "close": 1.0},
            {"symbol": "Y", "day": "2026-10-01", "close": 1.0}], mode="upsert")
        result = await batch(s, ctx, KYLE, "bars", [
            {"symbol": "X", "day": "2026-10-01"},          # close dropped: a replace
            {"symbol": "Y", "day": "2026-10-01", "close": 1}], mode="upsert")
        assert (result["updated"], result["unchanged"]) == (1, 1)
        x = await get_record(s, ctx, OWNER, "bars", first["ids"][0])
        y = await get_record(s, ctx, OWNER, "bars", first["ids"][1])
    # Replaced whole, not patched; the version moves so readers see the change.
    assert x["values"]["close"] is None and x["values"]["version"] == 2
    assert x["values"]["author"] == "kyle"
    assert (y["values"]["version"], y["values"]["author"]) == (1, "agent:pai")
    # Nothing goes to history for an immutable collection.
    assert await count(sf, AppDataRecordVersion) == 0


async def test_immutable_replace_needs_update_access(sf):
    body = dict(IMMUTABLE_BARS, access={"read": ["owner"], "create": ["owner", "agent:bob"],
                                        "update": ["owner"], "delete": ["owner"]})
    ctx = await make_app(sf, [body])
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [{"symbol": "X", "day": "2026-10-01"}])
        err = await refused("AD-BATCH-REJECTED", batch(
            s, ctx, BOB, "bars", [{"symbol": "X", "day": "2026-10-01", "close": 5.0}],
            mode="upsert"))
    assert err.detail["errors"][0]["code"] == "AD-FORBIDDEN"


async def test_skip_existing_skips_unique_clashes(sf):
    ctx = await make_app(sf, [IMMUTABLE_BARS])
    async with sf() as s:
        first = await batch(s, ctx, OWNER, "bars",
                            [{"symbol": "X", "day": "2026-10-01", "close": 1.0}])
        result = await batch(s, ctx, OWNER, "bars", [
            {"symbol": "X", "day": "2026-10-01", "close": 9.0},
            {"symbol": "X", "day": "2026-10-02", "close": 2.0},
            {"symbol": "X", "day": "2026-10-02", "close": 3.0}], mode="skip_existing")
        assert (result["inserted"], result["skipped"]) == (1, 2)
        # A skipped record answers with the id of the record that was kept.
        assert result["ids"][0] == first["ids"][0]
        assert result["ids"][2] == result["ids"][1]
        kept = await get_record(s, ctx, OWNER, "bars", first["ids"][0])
    assert kept["values"]["close"] == 1.0


# --- batch jobs: staging --------------------------------------------------------------------------

async def test_staged_records_are_invisible_until_commit(sf):
    ctx = await make_app(sf, [coll()], [{"view": "all", "collection": "items"}])
    async with sf() as s:
        opened = await open_staging_set(s, ctx, VIA_JUDGMENT, ["items"], call_id="c1")
        set_id = opened["set_id"]
        staged = await stage(s, ctx, VIA_JUDGMENT, set_id, "items",
                             [{"title": "a"}, {"title": "b"}], call_id="c1")
        assert (staged["staged"], staged["record_count"]) == (2, 2)
        assert (await run_view(s, ctx, OWNER, "all"))["rows"] == []
        assert await write_counter(s, ctx.app_id, "items") == 0
    assert await count(sf) == 0
    async with sf() as s:
        result = await commit_staging_set(s, VIA_JUDGMENT, set_id, call_id="c1")
        assert result["inserted"] == 2
        rows = (await run_view(s, ctx, OWNER, "all"))["rows"]
        assert sorted(r["values"]["title"] for r in rows) == ["a", "b"]
        assert all(r["values"]["via"] == "tool:judgment" for r in rows)
        assert await write_counter(s, ctx.app_id, "items") == 1
        st = await s.get(AppDataStagingSet, set_id)
        assert st.state == "committed" and st.committed_at is not None
    assert await count(sf, AppDataStagedRecord) == 0
    async with sf() as s:
        await refused("AD-STAGING-CLOSED", commit_staging_set(s, VIA_JUDGMENT, set_id,
                                                              call_id="c1"))
        await refused("AD-STAGING-CLOSED", stage(s, ctx, VIA_JUDGMENT, set_id, "items",
                                                 [{"title": "c"}], call_id="c1"))


async def test_open_binds_the_creator_and_collection_versions(sf):
    ctx = await make_app(sf, [coll(), coll("other")])
    async with sf() as s:
        opened = await open_staging_set(s, ctx, VIA_JUDGMENT, ["items"], call_id="c1",
                                        run_id="r1")
        st = await s.get(AppDataStagingSet, opened["set_id"])
        assert (st.creator, st.tool, st.call_id, st.run_id) == (
            "agent:pai", "tool:judgment", "c1", "r1")
        assert st.collection_versions == {"items": 1} == opened["collection_versions"]
        assert st.state == "open"
        await refused("AD-NO-COLLECTION", open_staging_set(s, ctx, OWNER, ["nope"]))
        await refused("AD-FORBIDDEN", open_staging_set(s, ctx, BOB, ["items"]))
        await refused("AD-STAGING-SCHEMA-CHANGED",
                      open_staging_set(s, ctx, OWNER, {"items": 7}))
        # Staging into a collection the set wasn't opened for is refused.
        await refused("AD-STAGING-COLLECTION", stage(
            s, ctx, VIA_JUDGMENT, opened["set_id"], "other", [{"title": "a"}],
            call_id="c1"))


@pytest.mark.parametrize("caller, call_id", [
    (OWNER, "c1"),                                       # no tool
    (Caller("agent:pai", via_tool="tool:other"), "c1"),  # another tool
    (Caller("agent:bob", via_tool="tool:judgment"), "c1"),
    (VIA_JUDGMENT, "c2"),                                # another call
])
async def test_only_the_creator_stages_commits_or_abandons(sf, caller, call_id):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        set_id = (await open_staging_set(s, ctx, VIA_JUDGMENT, ["items"],
                                         call_id="c1"))["set_id"]
        await refused("AD-STAGING-CREATOR", stage(s, ctx, caller, set_id, "items",
                                                  [{"title": "a"}], call_id=call_id))
        await refused("AD-STAGING-CREATOR", commit_staging_set(s, caller, set_id,
                                                               call_id=call_id))
        await refused("AD-STAGING-CREATOR", abandon_staging_set(s, caller, set_id,
                                                                call_id=call_id))
        await refused("AD-STAGING-NOT-FOUND", commit_staging_set(s, VIA_JUDGMENT, "nope",
                                                                 call_id="c1"))


async def test_stage_checks_shape_and_limits_and_stages_nothing_on_error(sf):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        set_id = (await open_staging_set(s, ctx, OWNER, ["items"]))["set_id"]
        err = await refused("AD-BATCH-REJECTED", stage(s, ctx, OWNER, set_id, "items", [
            {"title": "a"}, {"nope": 1}, {"title": 5}, {"version": 2}]))
        assert [(e["index"], e["code"]) for e in err.detail["errors"]] == [
            (1, "AD-UNKNOWN-FIELD"), (2, "AD-INVALID-VALUE"), (3, "AD-SYSTEM-FIELD")]
        await refused("AD-BATCH-TOO-LARGE", stage(
            s, ctx, OWNER, set_id, "items", [{"title": "a"}] * (MAX_BATCH_RECORDS + 1)))
        await refused("AD-BATCH-NO-UNIQUE", stage(s, ctx, OWNER, set_id, "items",
                                                  [{"title": "a"}], mode="upsert"))
        await refused("AD-BATCH-MODE", stage(s, ctx, OWNER, set_id, "items",
                                             [{"title": "a"}], mode="bogus"))
        st = await s.get(AppDataStagingSet, set_id)
        assert st.record_count == 0
    assert await count(sf, AppDataStagedRecord) == 0


async def test_commit_replays_modes_in_arrival_order(sf):
    ctx = await make_app(sf, [BARS])
    async with sf() as s:
        existing = await batch(s, ctx, OWNER, "bars",
                               [{"symbol": "X", "day": "2026-10-01", "close": 1.0}])
        set_id = (await open_staging_set(s, ctx, OWNER, ["bars"]))["set_id"]
        await stage(s, ctx, OWNER, set_id, "bars",
                    [{"symbol": "X", "day": "2026-10-01", "close": 2.0}], mode="upsert")
        await stage(s, ctx, OWNER, set_id, "bars",
                    [{"symbol": "X", "day": "2026-10-01", "close": 3.0},
                     {"symbol": "Y", "day": "2026-10-01", "close": 3.0}],
                    mode="skip_existing")
        result = await commit_staging_set(s, OWNER, set_id)
        assert (result["updated"], result["skipped"], result["inserted"]) == (1, 1, 1)
        x = await get_record(s, ctx, OWNER, "bars", existing["ids"][0])
    assert x["values"]["close"] == 2.0


async def test_upsert_key_is_kept_with_the_staged_records(sf):
    two = dict(BARS, fields={**BARS["fields"], "ref": {"type": "string"}},
               rules=[*BARS["rules"], {"kind": "unique", "fields": ["ref"]}])
    ctx = await make_app(sf, [two])
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [{"symbol": "X", "day": "2026-10-01",
                                             "ref": "r1"}])
        set_id = (await open_staging_set(s, ctx, OWNER, ["bars"]))["set_id"]
        await refused("AD-BATCH-KEY", stage(s, ctx, OWNER, set_id, "bars",
                                            [{"ref": "r1"}], mode="upsert"))
        await stage(s, ctx, OWNER, set_id, "bars",
                    [{"symbol": "X", "day": "2026-10-02", "ref": "r1"}], mode="upsert",
                    key=["ref"])
        result = await commit_staging_set(s, OWNER, set_id)
    assert result["updated"] == 1


# --- batch jobs: commit re-validation ---------------------------------------------------------------

async def staged_set(sf, ctx, caller=OWNER, records=({"title": "a"},)):
    async with sf() as s:
        set_id = (await open_staging_set(s, ctx, caller, ["items"]))["set_id"]
        await stage(s, ctx, caller, set_id, "items", list(records))
    return set_id


async def assert_still_open(sf, set_id, staged=1):
    async with sf() as s:
        assert (await s.get(AppDataStagingSet, set_id)).state == "open"
    assert await count(sf, AppDataStagedRecord) == staged
    assert await count(sf) == 0


async def test_commit_refuses_when_the_creators_authority_is_gone(sf):
    ctx = await make_app(sf, [coll()])
    set_id = await staged_set(sf, ctx)
    # The App changes hands: `owner` in its access no longer means pai, though
    # no collection version moved.
    async with sf() as s:
        app = await s.get(AppDataApp, ctx.app_id)
        app.owner_id = "bob"
        await s.commit()
    async with sf() as s:
        err = await refused("AD-FORBIDDEN", commit_staging_set(s, OWNER, set_id))
    assert err.status == 403
    await assert_still_open(sf, set_id)


async def test_commit_refuses_when_a_collection_version_changed(sf):
    ctx = await make_app(sf, [coll()])
    set_id = await staged_set(sf, ctx)
    # Becoming tool-only is a new published version, caught as a schema change.
    async with sf() as s:
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="collection", name="items",
                                version=2, body=coll(writers={"create": ["tool:judgment"]}),
                                state="published", author="agent:pai"))
        await s.commit()
    async with sf() as s:
        err = await refused("AD-STAGING-SCHEMA-CHANGED", commit_staging_set(s, OWNER, set_id))
    assert err.status == 409
    assert err.detail == {"changed": {"items": {"opened": 1, "published": 2}}}
    await assert_still_open(sf, set_id)


async def test_commit_refuses_a_retired_app(sf):
    ctx = await make_app(sf, [coll()])
    set_id = await staged_set(sf, ctx)
    async with sf() as s:
        app = await s.get(AppDataApp, ctx.app_id)
        app.status = "retired"
        await s.commit()
    async with sf() as s:
        await refused("AD-APP-RETIRED", commit_staging_set(s, OWNER, set_id))
    await assert_still_open(sf, set_id)


async def test_commit_runs_the_quota_hook_and_honours_its_refusal(sf, monkeypatch):
    ctx = await make_app(sf, [coll()])
    set_id = await staged_set(sf, ctx, records=[{"title": "a"}, {"title": "b"}])
    seen = []

    async def over_quota(session, app, *, records, bytes):
        seen.append((app.app_id, records, bytes))
        raise RecordError("AD-QUOTA", "over quota", 429)

    monkeypatch.setattr(batch_mod, "check_quotas", over_quota)
    async with sf() as s:
        await refused("AD-QUOTA", commit_staging_set(s, OWNER, set_id))
    assert seen[0][:2] == (ctx.app_id, 2) and seen[0][2] > 0
    await assert_still_open(sf, set_id, staged=2)


async def test_commit_is_all_or_nothing_on_record_errors(sf):
    ctx = await make_app(sf, [coll(rules=[{"kind": "unique", "fields": ["title"]}])])
    set_id = await staged_set(sf, ctx, records=[{"title": "a"}, {"title": "b"}])
    async with sf() as s:
        await create_record(s, ctx, OWNER, "items", {"title": "b"})
    async with sf() as s:
        err = await refused("AD-BATCH-REJECTED", commit_staging_set(s, OWNER, set_id))
    assert [(e["index"], e["collection"], e["code"]) for e in err.detail["errors"]] == [
        (1, "items", "AD-UNIQUE")]
    assert await count(sf) == 1    # only the record written outside the set


# --- batch jobs: abandon and expiry ---------------------------------------------------------------

async def test_abandon_drops_the_staged_records(sf):
    ctx = await make_app(sf, [coll()])
    set_id = await staged_set(sf, ctx)
    async with sf() as s:
        result = await abandon_staging_set(s, OWNER, set_id)
        assert result == {"set_id": set_id, "state": "aborted", "dropped": 1}
        assert (await s.get(AppDataStagingSet, set_id)).state == "aborted"
        await refused("AD-STAGING-CLOSED", commit_staging_set(s, OWNER, set_id))
        await refused("AD-STAGING-CLOSED", abandon_staging_set(s, OWNER, set_id))
    assert await count(sf, AppDataStagedRecord) == 0


async def test_sets_expire_after_24_hours(sf):
    ctx = await make_app(sf, [coll()])
    old = await staged_set(sf, ctx)
    async with sf() as s:
        st = await s.get(AppDataStagingSet, old)
        opened = st.created_at
        assert st.expires_at - opened == timedelta(hours=24)
        later = utcnow() + timedelta(hours=24, minutes=1)
        err = await refused("AD-STAGING-EXPIRED", commit_staging_set(s, OWNER, old,
                                                                     now=later))
        assert err.status == 410
    async with sf() as s:
        assert (await s.get(AppDataStagingSet, old)).state == "expired"
    assert await count(sf, AppDataStagedRecord) == 0 and await count(sf) == 0


async def test_prune_expires_only_sets_past_their_24_hours(sf):
    ctx = await make_app(sf, [coll()])
    old = await staged_set(sf, ctx)
    fresh = await staged_set(sf, ctx)
    async with sf() as s:
        st = await s.get(AppDataStagingSet, old)
        st.expires_at = utcnow() - timedelta(minutes=1)
        await s.commit()
    async with sf() as s:
        assert await prune_staging_sets(s) == 1
        assert await prune_staging_sets(s) == 0
        assert (await s.get(AppDataStagingSet, old)).state == "expired"
        assert (await s.get(AppDataStagingSet, fresh)).state == "open"
    assert await count(sf, AppDataStagedRecord) == 1
    async with sf() as s:
        assert (await commit_staging_set(s, OWNER, fresh))["inserted"] == 1


# --- scale --------------------------------------------------------------------------------------------

async def test_a_20k_record_staged_commit(sf):
    ctx = await make_app(sf, [coll("results", fields={
        "run": {"type": "string"}, "ref": {"type": "string"},
        "ok": {"type": "bool"}, "ms": {"type": "int"}}, indexed=["run", "ref"],
        write_mode="immutable")])
    async with sf() as s:
        set_id = (await open_staging_set(s, ctx, VIA_JUDGMENT, ["results"],
                                         call_id="c1"))["set_id"]
        for chunk in range(4):
            await stage(s, ctx, VIA_JUDGMENT, set_id, "results", [
                {"run": f"run{chunk}", "ref": f"t{i}", "ok": i % 7 != 0, "ms": i}
                for i in range(5_000)], call_id="c1")
        started = time.monotonic()
        result = await commit_staging_set(s, VIA_JUDGMENT, set_id, call_id="c1")
        elapsed = time.monotonic() - started
    assert result["inserted"] == 20_000 and result["errors"] == []
    assert await count(sf) == 20_000
    assert await count(sf, AppDataStagedRecord) == 0
    print(f"20k staged commit: {elapsed:.1f}s")
