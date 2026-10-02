"""Quotas and scan budgets (design 39, "Storage" → "Quotas", "Tool views" →
"Bulk reads"): per-App and per-owner limits Kyle sets, usage charged in the
write's own transaction, fixed-window write and scan budgets, and scan
leases. Every breach fails closed with an AD-QUOTA-* code.

Every test runs on SQLite; with AP_TEST_PG_URL naming a scratch database they
run on Postgres too.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.batch import (batch, commit_staging_set, open_staging_set,
                                         stage)
from agentplatform.appdata.models import (AppDataApp, AppDataBuildOp, AppDataDefinition,
                                          AppDataQuota, AppDataRecord, AppDataScanLease,
                                          AppDataStagingSet)
from agentplatform.appdata.quotas import (check_new_app, check_new_draft,
                                          check_new_proposal, describe, doc_bytes,
                                          prune_build_ops, scan_budget, set_quota, settle)
from agentplatform.appdata.records import (create_record, delete_record, delete_records,
                                           update_record)
from agentplatform.appdata.retention import prune_collection
from agentplatform.appdata.views import execute_view, scan_view
from agentplatform.config import Settings
from tests.test_appdata_batch import (BARS, IMMUTABLE_BARS, KYLE, OWNER, coll,  # noqa: F401
                                      engine, make_app, refused, reload, sf)

NOW = datetime(2026, 10, 2, 12, 30, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch):
    """The defaults, swappable per test."""
    current = {"s": Settings()}
    monkeypatch.setattr(quotas, "_settings", lambda: current["s"])

    def use(**overrides):
        current["s"] = Settings(**overrides)
    return use


async def kyle_sets(sf, scope_kind, scope_id, **limits):
    async with sf() as s:
        return await set_quota(s, scope_kind, scope_id, limits, set_by="kyle", is_kyle=True)


async def usage(sf, scope_kind, scope_id):
    async with sf() as s:
        return (await describe(s, scope_kind, scope_id))["used"]


async def create(sf, ctx, values, caller=OWNER, collection="items"):
    async with sf() as s:
        return await create_record(s, ctx, caller, collection, values)


def size_of(values):
    return doc_bytes({k: v for k, v in values.items() if v is not None})


# --- defaults and the setter -------------------------------------------------------------------

def test_defaults_come_from_config():
    s = Settings()
    assert quotas.defaults("app", s) == {
        "max_records": 250_000, "max_bytes": 256 * 1024 ** 2, "writes_per_hour": 200_000,
        "scan_rows_per_hour": 20_000_000, "max_concurrent_scans": 2,
        "max_open_drafts": 100, "max_open_proposals": 5}
    assert quotas.defaults("owner", s) == {
        "max_records": 5_000_000, "max_bytes": 4 * 1024 ** 3,
        "writes_per_hour": 1_000_000, "scan_rows_per_hour": 60_000_000,
        "max_apps": 20, "max_open_proposals": 20}
    assert s.app_data_scan_max_rows == 2_000_000
    assert s.app_data_scan_max_seconds == 60
    assert s.app_data_build_ops_retention_days == 90


def test_defaults_follow_the_environment(monkeypatch):
    monkeypatch.setenv("AP_APP_DATA_APP_MAX_RECORDS", "7")
    assert quotas.defaults("app", Settings())["max_records"] == 7


async def test_an_unset_scope_reads_the_defaults(sf, settings):
    settings(app_data_app_max_records=11)
    async with sf() as s:
        out = await describe(s, "app", "nobody")
    assert out["limits"]["max_records"] == 11 and out["set"] == {}
    assert out["used"] == {"records": 0, "bytes": 0, "writes_this_hour": 0,
                           "scan_rows_this_hour": 0}


async def test_only_kyle_sets_a_quota(sf, settings):
    async with sf() as s:
        err = await refused("AD-QUOTA-FORBIDDEN", set_quota(
            s, "app", "a1", {"max_records": 10 ** 9}, set_by="agent:pai", is_kyle=False))
    assert err.status == 403
    async with sf() as s:
        assert await s.get(AppDataQuota, ("app", "a1")) is None


async def test_kyle_sets_and_resets_a_limit(sf, settings):
    out = await kyle_sets(sf, "app", "a1", max_records=2_000_000, max_bytes=1 << 30)
    assert out["limits"]["max_records"] == 2_000_000
    assert out["set"] == {"max_records": 2_000_000, "max_bytes": 1 << 30}
    assert out["set_by"] == "kyle"
    out = await kyle_sets(sf, "app", "a1", max_records=None)
    assert out["limits"]["max_records"] == 250_000 and out["set"] == {"max_bytes": 1 << 30}


@pytest.mark.parametrize("scope_kind,limits", [
    ("app", {"max_apps": 3}),             # an owner limit
    ("owner", {"max_open_drafts": 3}),    # an App limit
    ("app", {"max_records": -1}),
    ("app", {"max_records": 1.5}),
    ("app", {"max_records": True}),
    ("app", {}),
    ("tenant", {"max_records": 1}),
])
async def test_the_setter_refuses_what_isnt_a_limit(sf, settings, scope_kind, limits):
    async with sf() as s:
        err = await refused("AD-QUOTA-INVALID", set_quota(
            s, scope_kind, "x", limits, set_by="kyle", is_kyle=True))
    assert err.status == 422


# --- storage: records and bytes -----------------------------------------------------------------

async def test_each_write_charges_the_app_and_its_owner(sf, settings):
    ctx = await make_app(sf, [coll()])
    await create(sf, ctx, {"title": "a", "n": 1})
    await create(sf, ctx, {"title": "bb"})
    want = size_of({"title": "a", "n": 1}) + size_of({"title": "bb"})
    for scope_kind, scope_id in (("app", ctx.app_id), ("owner", "agent:pai")):
        used = await usage(sf, scope_kind, scope_id)
        assert (used["records"], used["bytes"], used["writes_this_hour"]) == (2, want, 2)


async def test_the_stored_size_is_what_was_charged(sf, settings):
    ctx = await make_app(sf, [coll()])
    row = await create(sf, ctx, {"title": "a", "n": 3})
    async with sf() as s:
        record = (await s.execute(select(AppDataRecord))).scalar_one()
    assert record.size_bytes == doc_bytes(record.doc) == size_of({"title": "a", "n": 3})
    assert row["id"] == record.id


async def test_the_record_limit_refuses_with_its_use(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_records=2)
    await create(sf, ctx, {"title": "a"})
    await create(sf, ctx, {"title": "b"})
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", create_record(s, ctx, OWNER, "items",
                                                              {"title": "c"}))
    assert err.status == 413
    assert err.detail == {"scope": "app", "scope_id": ctx.app_id, "limit": "max_records",
                          "max": 2, "used": 2, "requested": 1}
    assert (await usage(sf, "app", ctx.app_id))["records"] == 2
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))
                ).scalar_one() == 2


async def test_the_byte_limit_refuses(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_bytes=size_of({"title": "a"}) + 5)
    await create(sf, ctx, {"title": "a"})
    async with sf() as s:
        err = await refused("AD-QUOTA-BYTES", create_record(s, ctx, OWNER, "items",
                                                            {"title": "b"}))
    assert err.status == 413 and err.detail["limit"] == "max_bytes"
    assert err.detail["used"] == size_of({"title": "a"})


async def test_an_update_charges_only_the_growth(sf, settings):
    ctx = await make_app(sf, [coll()])
    row = await create(sf, ctx, {"title": "a"})
    async with sf() as s:
        await update_record(s, ctx, OWNER, "items", row["id"], {"title": "abcdef", "n": 9},
                            expected_version=1)
    used = await usage(sf, "app", ctx.app_id)
    assert used["bytes"] == size_of({"title": "abcdef", "n": 9}) and used["records"] == 1
    assert used["writes_this_hour"] == 2
    async with sf() as s:
        await update_record(s, ctx, OWNER, "items", row["id"], {"n": None},
                            expected_version=2)
    assert (await usage(sf, "app", ctx.app_id))["bytes"] == size_of({"title": "abcdef"})


async def test_an_update_that_grows_past_the_byte_limit_is_refused(sf, settings):
    ctx = await make_app(sf, [coll()])
    row = await create(sf, ctx, {"title": "a"})
    await kyle_sets(sf, "app", ctx.app_id, max_bytes=size_of({"title": "a"}))
    async with sf() as s:
        await refused("AD-QUOTA-BYTES", update_record(
            s, ctx, OWNER, "items", row["id"], {"title": "much longer"}, expected_version=1))
    async with sf() as s:
        record = (await s.execute(select(AppDataRecord))).scalar_one()
    assert record.doc == {"title": "a"} and record.current_version == 1


async def test_a_delete_releases_exactly_what_was_charged(sf, settings):
    ctx = await make_app(sf, [coll()])
    a = await create(sf, ctx, {"title": "a", "n": 1})
    b = await create(sf, ctx, {"title": "bbb"})
    async with sf() as s:
        await delete_record(s, ctx, OWNER, "items", a["id"])
    used = await usage(sf, "app", ctx.app_id)
    assert (used["records"], used["bytes"]) == (1, size_of({"title": "bbb"}))
    async with sf() as s:
        await delete_records(s, ctx, OWNER, "items", [b["id"]])
    for scope_kind, scope_id in (("app", ctx.app_id), ("owner", "agent:pai")):
        used = await usage(sf, scope_kind, scope_id)
        assert (used["records"], used["bytes"]) == (0, 0)


async def test_a_delete_goes_through_for_a_scope_over_a_lowered_limit(sf, settings):
    ctx = await make_app(sf, [coll()])
    rows = [await create(sf, ctx, {"title": t}) for t in "abc"]
    await kyle_sets(sf, "app", ctx.app_id, max_records=1)
    async with sf() as s:
        await delete_record(s, ctx, OWNER, "items", rows[0]["id"])
    assert (await usage(sf, "app", ctx.app_id))["records"] == 2
    async with sf() as s:
        await refused("AD-QUOTA-RECORDS", create_record(s, ctx, OWNER, "items",
                                                        {"title": "d"}))


async def test_an_unlink_releases_the_bytes_it_clears(sf, settings):
    ctx = await make_app(sf, [
        coll("runs", fields={"name": {"type": "string"}}),
        coll("notes", fields={"run": {"type": "ref", "collection": "runs",
                                      "on_delete": "unlink"},
                              "text": {"type": "string"}})])
    run = await create(sf, ctx, {"name": "r"}, collection="runs")
    await create(sf, ctx, {"run": run["id"], "text": "t"}, collection="notes")
    async with sf() as s:
        await delete_record(s, ctx, OWNER, "runs", run["id"])
    used = await usage(sf, "app", ctx.app_id)
    assert (used["records"], used["bytes"]) == (1, size_of({"text": "t"}))


async def test_retention_releases_usage(sf, settings):
    ctx = await make_app(sf, [coll(retention={"max_records": 1})])
    for title in "abc":
        await create(sf, ctx, {"title": title})
        await asyncio.sleep(0.002)
    async with sf() as s:
        result = await prune_collection(s, ctx, "items")
    assert result.deleted == 2
    used = await usage(sf, "app", ctx.app_id)
    assert used["records"] == 1 and used["bytes"] == size_of({"title": "c"})


async def test_retired_apps_keep_counting(sf, settings):
    first = await make_app(sf, [coll()])
    await create(sf, first, {"title": "a"})
    async with sf() as s:
        app = await s.get(AppDataApp, first.app_id)
        app.status = "retired"
        await s.commit()
    await kyle_sets(sf, "owner", "agent:pai", max_records=1, max_apps=1)
    second = await make_app(sf, [coll()])
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", create_record(s, second, OWNER, "items",
                                                              {"title": "b"}))
    assert err.detail["scope"] == "owner" and err.detail["used"] == 1
    async with sf() as s:
        err = await refused("AD-QUOTA-APPS", check_new_app(s, "agent:pai"))
    assert err.detail["used"] == 2


# --- per-owner aggregation -----------------------------------------------------------------------

async def test_the_owner_limit_spans_its_apps(sf, settings):
    one = await make_app(sf, [coll()])
    two = await make_app(sf, [coll()])
    other = await make_app(sf, [coll()], owner="agent:bob")
    await kyle_sets(sf, "owner", "agent:pai", max_records=3)
    await create(sf, one, {"title": "a"})
    await create(sf, one, {"title": "b"})
    await create(sf, two, {"title": "c"})
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", create_record(s, two, OWNER, "items",
                                                              {"title": "d"}))
    assert err.detail == {"scope": "owner", "scope_id": "agent:pai",
                          "limit": "max_records", "max": 3, "used": 3, "requested": 1}
    # Another owner's App is untouched by pai's total.
    await create(sf, other, {"title": "e"}, caller=Caller("agent:bob"))
    assert (await usage(sf, "owner", "agent:pai"))["records"] == 3
    assert (await usage(sf, "owner", "agent:bob"))["records"] == 1
    assert (await usage(sf, "app", two.app_id))["records"] == 1


async def test_kyle_owned_apps_charge_the_kyle_scope(sf, settings):
    ctx = await make_app(sf, [coll()], owner="kyle")
    await create(sf, ctx, {"title": "a"}, caller=KYLE)
    assert (await usage(sf, "owner", "kyle"))["records"] == 1


async def test_an_ownership_transfer_moves_the_usage(sf, settings):
    ctx = await make_app(sf, [coll()])
    await create(sf, ctx, {"title": "a"})
    async with sf() as s:
        await quotas.transfer_owner(s, ctx.app_id, "agent:pai", "kyle")
        await s.commit()
    assert (await usage(sf, "owner", "agent:pai"))["records"] == 0
    kyle = await usage(sf, "owner", "kyle")
    assert (kyle["records"], kyle["bytes"]) == (1, size_of({"title": "a"}))


async def test_deleting_an_owner_agent_moves_its_usage_to_kyle(sf, settings):
    """transfer_owned_apps hands the Apps to Kyle and their storage with them,
    so the deleted agent's scope is freed and Kyle's limit sees the App."""
    from agentplatform.appdata import lifecycle as L
    one = await make_app(sf, [coll()])
    two = await make_app(sf, [coll()])
    bobs = await make_app(sf, [coll()], owner="agent:bob")
    for ctx in (one, two, two):
        await create(sf, ctx, {"title": "a"})
    await create(sf, bobs, {"title": "b"}, caller=Caller("agent:bob"))
    async with sf() as s:
        assert await L.transfer_owned_apps(s, "pai") == 2
        await s.commit()
    pai, kyle = await usage(sf, "owner", "agent:pai"), await usage(sf, "owner", "kyle")
    assert (pai["records"], pai["bytes"]) == (0, 0)
    assert (kyle["records"], kyle["bytes"]) == (3, 3 * size_of({"title": "a"}))
    assert (await usage(sf, "owner", "agent:bob"))["records"] == 1


# --- writes per hour ---------------------------------------------------------------------------

async def test_writes_per_hour_is_a_fixed_window(sf, settings, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(quotas, "utcnow", lambda: clock["now"])
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, writes_per_hour=2)
    row = await create(sf, ctx, {"title": "a"})
    async with sf() as s:
        await update_record(s, ctx, OWNER, "items", row["id"], {"n": 1}, expected_version=1)
    async with sf() as s:
        err = await refused("AD-QUOTA-WRITES", create_record(s, ctx, OWNER, "items",
                                                             {"title": "b"}))
    assert err.status == 429
    assert err.detail["limit"] == "writes_per_hour" and err.detail["used"] == 2
    assert err.detail["retry_after"] == 30 * 60
    # A delete isn't a write, so it still goes through.
    async with sf() as s:
        await delete_record(s, ctx, OWNER, "items", row["id"])
    clock["now"] = NOW + timedelta(minutes=30)
    await create(sf, ctx, {"title": "b"})
    assert (await usage(sf, "app", ctx.app_id))["records"] == 1


async def test_a_retry_after_a_write_quota_refusal_can_succeed(sf, settings, monkeypatch):
    """A quota refusal describes the moment, not the call: it leaves no
    receipt, so once Kyle raises the limit the same request_id goes through
    instead of replaying the stale refusal."""
    from agentplatform.appdata import lifecycle as L
    monkeypatch.setattr(quotas, "utcnow", lambda: NOW)
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, writes_per_hour=1)
    pai = L.Actor("agent:pai")

    async def write(request_id, title):
        async with sf() as s:
            return await L.record_create(s, pai, ctx.app_id, request_id=request_id,
                                         collection="items", values={"title": title})

    await write("w1", "a")
    err = await refused("AD-QUOTA-WRITES", write("w2", "b"))
    assert err.status == 429
    async with sf() as s:
        assert (await s.execute(select(AppDataBuildOp).where(
            AppDataBuildOp.request_id == "w2"))).scalar_one_or_none() is None
    await kyle_sets(sf, "app", ctx.app_id, writes_per_hour=5)
    out = await write("w2", "b")
    assert out["version"] == 1 and not out.get("replayed")
    assert (await write("w2", "b"))["replayed"] is True
    assert (await usage(sf, "app", ctx.app_id))["records"] == 2


async def test_a_definitions_moved_refusal_leaves_no_receipt(sf, settings, monkeypatch):
    from agentplatform.appdata import lifecycle as L
    from agentplatform.appdata import records as rec
    ctx = await make_app(sf, [coll()])
    real = rec.write_lock
    calls = {"n": 0}

    def moved_once(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RecordError("AD-DEFINITIONS-MOVED", "moved", 409)
        return real(*a, **kw)
    monkeypatch.setattr(rec, "write_lock", moved_once)

    async def write():
        async with sf() as s:
            return await L.record_create(s, L.Actor("agent:pai"), ctx.app_id,
                                         request_id="m1", collection="items",
                                         values={"title": "a"})
    await refused("AD-DEFINITIONS-MOVED", write())
    assert (await write())["version"] == 1


async def test_the_owner_write_budget_spans_its_apps(sf, settings, monkeypatch):
    monkeypatch.setattr(quotas, "utcnow", lambda: NOW)
    one = await make_app(sf, [coll()])
    two = await make_app(sf, [coll()])
    await kyle_sets(sf, "owner", "agent:pai", writes_per_hour=1)
    await create(sf, one, {"title": "a"})
    async with sf() as s:
        err = await refused("AD-QUOTA-WRITES", create_record(s, two, OWNER, "items",
                                                             {"title": "b"}))
    assert err.detail["scope"] == "owner"


# --- concurrency ------------------------------------------------------------------------------

async def test_concurrent_writers_cannot_overshoot(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_records=3)

    async def one(i):
        async with sf() as s:
            try:
                await create_record(s, ctx, OWNER, "items", {"title": f"t{i}"})
                return "ok"
            except RecordError as exc:
                return exc.code

    results = await asyncio.gather(*(one(i) for i in range(8)))
    assert sorted(results) == ["AD-QUOTA-RECORDS"] * 5 + ["ok"] * 3
    assert (await usage(sf, "app", ctx.app_id))["records"] == 3
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))
                ).scalar_one() == 3


async def test_concurrent_batches_cannot_overshoot_the_owner(sf, settings):
    apps = [await make_app(sf, [coll()]) for _ in range(3)]
    await kyle_sets(sf, "owner", "agent:pai", max_records=25)

    async def one(ctx):
        async with sf() as s:
            try:
                await batch(s, ctx, OWNER, "items", [{"title": "x"}] * 10)
                return "ok"
            except RecordError as exc:
                return exc.code

    results = await asyncio.gather(*(one(ctx) for ctx in apps))
    assert sorted(results) == ["AD-QUOTA-RECORDS", "ok", "ok"]
    assert (await usage(sf, "owner", "agent:pai"))["records"] == 20


# --- batch and batch-job integration ----------------------------------------------------------

async def test_a_batch_charges_what_it_inserted(sf, settings):
    ctx = await make_app(sf, [BARS])
    bars = [{"symbol": "X", "day": f"2026-10-0{d}", "close": 1.0} for d in (1, 2)]
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", bars, mode="upsert")
    first = await usage(sf, "app", ctx.app_id)
    assert first["records"] == 2 and first["writes_this_hour"] == 2
    # The same bars again change nothing: no records, no bytes, no writes.
    async with sf() as s:
        out = await batch(s, ctx, OWNER, "bars", bars, mode="upsert")
    assert out["unchanged"] == 2
    assert await usage(sf, "app", ctx.app_id) == first
    # A changed close is a write and re-measures the record.
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [dict(bars[0], close=1234.5)], mode="upsert")
    used = await usage(sf, "app", ctx.app_id)
    assert used["records"] == 2 and used["writes_this_hour"] == 3
    assert used["bytes"] == first["bytes"] + len("1234.5") - len("1.0")


async def test_an_immutable_replace_re_measures_the_record(sf, settings):
    ctx = await make_app(sf, [IMMUTABLE_BARS])
    bar = {"symbol": "X", "day": "2026-10-01", "close": 1.0}
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [bar], mode="upsert")
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [dict(bar, close=100.25)], mode="upsert")
    used = await usage(sf, "app", ctx.app_id)
    assert used["bytes"] == size_of(dict(bar, close=100.25))


async def test_a_batch_over_the_limit_writes_nothing(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_records=5)
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", batch(s, ctx, OWNER, "items",
                                                      [{"title": "a"}] * 6))
    assert err.detail["requested"] == 6
    assert (await usage(sf, "app", ctx.app_id))["records"] == 0
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))
                ).scalar_one() == 0


async def test_an_exhausted_app_is_refused_before_the_batch_replays(sf, settings,
                                                                   monkeypatch):
    ctx = await make_app(sf, [coll()])
    await create(sf, ctx, {"title": "a"})
    await kyle_sets(sf, "app", ctx.app_id, max_records=1)
    replayed = []
    import agentplatform.appdata.batch as batch_mod
    real = batch_mod._apply

    async def spy(*args, **kwargs):
        replayed.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(batch_mod, "_apply", spy)
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", batch(s, ctx, OWNER, "items",
                                                      [{"title": "b"}]))
    assert replayed == [] and err.detail["used"] == 1


async def test_an_exhausted_app_still_takes_an_upsert_that_adds_nothing(sf, settings):
    ctx = await make_app(sf, [BARS])
    bar = {"symbol": "X", "day": "2026-10-01", "close": 1.0}
    async with sf() as s:
        await batch(s, ctx, OWNER, "bars", [bar], mode="upsert")
    await kyle_sets(sf, "app", ctx.app_id, max_records=1)
    async with sf() as s:
        await refused("AD-QUOTA-RECORDS", batch(s, ctx, OWNER, "bars",
                                                [bar, dict(bar, day="2026-10-02")],
                                                mode="upsert"))
    # The pre-check refuses only what is certainly over; this one is decided
    # at settle, after the replay shows it inserts a record.
    assert (await usage(sf, "app", ctx.app_id))["records"] == 1


async def test_a_batch_job_commit_charges_and_refuses(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_records=3)
    async with sf() as s:
        set_id = (await open_staging_set(s, ctx, OWNER, ["items"]))["set_id"]
        await stage(s, ctx, OWNER, set_id, "items", [{"title": "a"}] * 2)
        await stage(s, ctx, OWNER, set_id, "items", [{"title": "b"}] * 2)
    async with sf() as s:
        err = await refused("AD-QUOTA-RECORDS", commit_staging_set(s, OWNER, set_id))
    assert err.detail["requested"] == 4
    async with sf() as s:
        assert (await s.get(AppDataStagingSet, set_id)).state == "open"
    await kyle_sets(sf, "app", ctx.app_id, max_records=4)
    async with sf() as s:
        out = await commit_staging_set(s, OWNER, set_id)
    assert out["inserted"] == 4
    used = await usage(sf, "app", ctx.app_id)
    assert used["records"] == 4
    assert used["bytes"] == 2 * size_of({"title": "a"}) + 2 * size_of({"title": "b"})


async def test_a_noted_write_that_skips_settle_cannot_commit(sf, settings):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        quotas.note(s, ctx, records=1, bytes=10)
        with pytest.raises(RuntimeError, match="never settled"):
            await s.commit()
        await s.rollback()
        # The tally belonged to the refused transaction, not the next one.
        await s.commit()
    assert (await usage(sf, "app", ctx.app_id))["records"] == 0


async def test_a_rolled_back_write_charges_nothing(sf, settings):
    ctx = await make_app(sf, [coll()])
    async with sf() as s:
        quotas.note(s, ctx, records=5)
        await s.rollback()
        await settle(s)
        await s.commit()
    assert (await usage(sf, "app", ctx.app_id))["records"] == 0


async def test_add_bytes_is_the_artifact_hook(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_bytes=1000)
    async with sf() as s:
        quotas.add_bytes(s, ctx, 600)
        await settle(s)
        await s.commit()
    async with sf() as s:
        quotas.add_bytes(s, ctx, 600)
        err = await refused("AD-QUOTA-BYTES", settle(s))
        await s.rollback()
    assert err.detail["used"] == 600 and err.detail["requested"] == 600
    async with sf() as s:
        quotas.add_bytes(s, ctx, -600)
        await settle(s)
        await s.commit()
    assert (await usage(sf, "app", ctx.app_id))["bytes"] == 0


# --- counted things: Apps, drafts, proposals ---------------------------------------------------

async def test_apps_per_owner(sf, settings):
    settings(app_data_owner_max_apps=2)
    await make_app(sf, [coll()])
    async with sf() as s:
        await check_new_app(s, "agent:pai")
        await s.rollback()
    await make_app(sf, [coll()])
    await make_app(sf, [coll()], owner="agent:bob")
    async with sf() as s:
        err = await refused("AD-QUOTA-APPS", check_new_app(s, "agent:pai"))
        await s.rollback()
        await check_new_app(s, "agent:bob")
        await s.rollback()
    assert err.status == 413
    assert err.detail == {"scope": "owner", "scope_id": "agent:pai", "limit": "max_apps",
                          "max": 2, "used": 2, "requested": 1}
    await kyle_sets(sf, "owner", "agent:pai", max_apps=3)
    async with sf() as s:
        await check_new_app(s, "agent:pai")


async def test_kyle_owned_apps_count_under_kyle(sf, settings):
    settings(app_data_owner_max_apps=1)
    await make_app(sf, [coll()], owner="kyle")
    async with sf() as s:
        await refused("AD-QUOTA-APPS", check_new_app(s, "kyle"))


async def test_open_drafts_per_app(sf, settings):
    ctx = await make_app(sf, [coll()])
    await kyle_sets(sf, "app", ctx.app_id, max_open_drafts=1)
    async with sf() as s:
        await check_new_draft(s, ctx.app_id)
        s.add(AppDataDefinition(app_id=ctx.app_id, kind="view", name="v", version=1,
                                body={}, state="draft", author="agent:pai"))
        await s.commit()
    async with sf() as s:
        err = await refused("AD-QUOTA-DRAFTS", check_new_draft(s, ctx.app_id))
    assert err.detail["max"] == 1 and err.detail["used"] == 1


async def _lifecycle_create(sf, actor, name):
    from agentplatform.appdata import lifecycle as L
    async with sf() as s:
        return await L.create(s, actor, request_id=f"c-{name}", name=name)


async def _lifecycle_draft(sf, actor, app_id, body, request_id, revision=None):
    from agentplatform.appdata import lifecycle as L
    async with sf() as s:
        return await L.draft(s, actor, app_id, request_id=request_id, kind="collection",
                             definition=body, expected_revision=revision)


async def test_lifecycle_create_counts_against_the_owners_apps(sf, settings):
    from agentplatform.appdata.lifecycle import Actor
    settings(app_data_owner_max_apps=1)
    pai, bob = Actor("agent:pai"), Actor("agent:bob")
    await _lifecycle_create(sf, pai, "first")
    err = await refused("AD-QUOTA-APPS", _lifecycle_create(sf, pai, "second"))
    assert err.status == 413 and err.detail["scope_id"] == "agent:pai"
    async with sf() as s:
        names = (await s.execute(select(AppDataApp.name))).scalars().all()
        # A quota refusal leaves no receipt: a retry can outgrow it.
        receipt = (await s.execute(select(AppDataBuildOp).where(
            AppDataBuildOp.request_id == "c-second"))).scalar_one_or_none()
    assert names == ["first"] and receipt is None
    # Another owner's slots are its own.
    await _lifecycle_create(sf, bob, "bobs")
    await kyle_sets(sf, "owner", "agent:pai", max_apps=3)
    await _lifecycle_create(sf, pai, "second")
    await _lifecycle_create(sf, pai, "third")


async def test_lifecycle_draft_counts_new_open_drafts_only(sf, settings):
    from agentplatform.appdata.lifecycle import Actor
    settings(app_data_app_max_open_drafts=1)
    pai = Actor("agent:pai")
    app_id = (await _lifecycle_create(sf, pai, "drafty"))["app_id"]
    await _lifecycle_draft(sf, pai, app_id, coll("one"), "d1")
    # Editing the open draft in place isn't a new one.
    out = await _lifecycle_draft(sf, pai, app_id, coll("one", indexed=["title"]), "d2",
                                 revision=1)
    assert out["revision"] == 2
    err = await refused("AD-QUOTA-DRAFTS", _lifecycle_draft(sf, pai, app_id, coll("two"),
                                                            "d3"))
    assert err.status == 413 and err.detail["scope_id"] == app_id
    async with sf() as s:
        drafts = (await s.execute(select(AppDataDefinition.name).where(
            AppDataDefinition.app_id == app_id))).scalars().all()
    assert drafts == ["one"]


@pytest.mark.parametrize("kind", ["app", "draft"])
async def test_lifecycle_concurrent_creates_cannot_share_the_last_quota_slot(sf, settings, kind):
    import asyncio
    from agentplatform.appdata.lifecycle import Actor

    pai = Actor("agent:pai")
    settings(app_data_owner_max_apps=1, app_data_app_max_open_drafts=1)
    if kind == "draft":
        app_id = (await _lifecycle_create(sf, pai, "draft-race"))["app_id"]

    async def create_one(name):
        try:
            if kind == "app":
                await _lifecycle_create(sf, pai, name)
            else:
                await _lifecycle_draft(sf, pai, app_id, coll(name), f"draft-{name}")
            return "created"
        except RecordError as exc:
            return exc.code

    results = await asyncio.gather(create_one("first"), create_one("second"))
    expected = "AD-QUOTA-APPS" if kind == "app" else "AD-QUOTA-DRAFTS"
    assert sorted(results) == sorted(["created", expected])
    async with sf() as s:
        model = AppDataApp if kind == "app" else AppDataDefinition
        assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 1


async def test_health_measures_against_the_enforced_limits(sf, settings):
    from agentplatform.appdata import lifecycle as L
    from agentplatform.appdata.lifecycle import Actor
    settings(app_data_app_max_records=4, app_data_app_max_bytes=1 << 20)
    ctx = await make_app(sf, [coll()])
    for title in "abcd":
        await create(sf, ctx, {"title": title})
    async with sf() as s:
        health = await L.health(s, Actor("kyle"), ctx.app_id)
    assert health["quota"] == {"records": 4, "records_limit": 4,
                               "bytes": (await usage(sf, "app", ctx.app_id))["bytes"],
                               "bytes_limit": 1 << 20}
    assert health["status"] == "warn"
    # Without a setting, the configured defaults, which is what writes meet.
    settings()
    async with sf() as s:
        health = await L.health(s, Actor("kyle"), ctx.app_id)
    assert health["quota"]["records_limit"] == 250_000
    assert health["quota"]["bytes_limit"] == 256 * 1024 ** 2
    assert health["status"] == "ok"
    await kyle_sets(sf, "app", ctx.app_id, max_records=4)
    async with sf() as s:
        health = await L.health(s, Actor("kyle"), ctx.app_id)
    assert health["quota"]["records_limit"] == 4 and health["status"] == "warn"


async def test_open_proposals_per_app_and_owner(sf, settings):
    settings(app_data_app_max_open_proposals=2, app_data_owner_max_open_proposals=3)
    async with sf() as s:
        await check_new_proposal(s, "a1", "agent:pai", open_for_app=1, open_for_owner=2)
        err = await refused("AD-QUOTA-PROPOSALS", check_new_proposal(
            s, "a1", "agent:pai", open_for_app=2, open_for_owner=2))
        assert err.detail["scope"] == "app"
        err = await refused("AD-QUOTA-PROPOSALS", check_new_proposal(
            s, "a1", "agent:pai", open_for_app=0, open_for_owner=3))
        assert err.detail["scope"] == "owner"


async def test_build_ops_are_kept_90_days(sf, settings):
    async with sf() as s:
        for days, rid in ((91, "old"), (89, "young")):
            s.add(AppDataBuildOp(principal="agent:pai", request_id=rid, op="create",
                                 args_hash="h", created_at=NOW - timedelta(days=days)))
        await s.commit()
    async with sf() as s:
        assert await prune_build_ops(s, now=NOW) == 1
    async with sf() as s:
        left = (await s.execute(select(AppDataBuildOp.request_id))).scalars().all()
    assert left == ["young"]


# --- scan budgets ------------------------------------------------------------------------------

LIST_VIEW = {"view": "all", "collection": "items", "sort": [{"field": "title"}],
             "limit": 50}
COUNT_VIEW = {"view": "n", "collection": "items", "aggregates": [{"fn": "count", "as": "n"}]}


async def scan_app(sf, n):
    ctx = await make_app(sf, [coll()], [LIST_VIEW, COUNT_VIEW])
    async with sf() as s:
        await batch(s, ctx, OWNER, "items", [{"title": f"t{i:04d}"} for i in range(n)])
    return ctx


async def scan_all(sf, ctx, caller=OWNER):
    async with sf() as s:
        async with scan_budget(s, ctx) as budget:
            rows = [row async for row in scan_view(s, ctx, caller,
                                                   ctx.bundle.views["all"],
                                                   budget=budget)]
    return rows, budget


async def test_a_scan_streams_every_row_and_charges_them(sf, settings, monkeypatch):
    monkeypatch.setattr("agentplatform.appdata.views.SCAN_CHUNK", 7)
    ctx = await scan_app(sf, 30)
    rows, budget = await scan_all(sf, ctx)
    assert [r["values"]["title"] for r in rows] == [f"t{i:04d}" for i in range(30)]
    assert budget.scanned == 30
    assert (await usage(sf, "app", ctx.app_id))["scan_rows_this_hour"] == 30
    assert (await usage(sf, "owner", "agent:pai"))["scan_rows_this_hour"] == 30
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataScanLease))
                ).scalar_one() == 0


async def test_one_execution_stops_at_its_row_cap(sf, settings):
    settings(app_data_scan_max_rows=10)
    ctx = await scan_app(sf, 25)
    with pytest.raises(RecordError) as caught:
        await scan_all(sf, ctx)
    err = caught.value
    assert err.code == "AD-QUOTA-SCAN-EXECUTION" and err.status == 413
    assert err.detail["limit"] == "scan_max_rows" and err.detail["max"] == 10
    # Charged what it read, up to the cap.
    assert (await usage(sf, "app", ctx.app_id))["scan_rows_this_hour"] == 10


async def test_the_app_scan_budget_per_hour(sf, settings, monkeypatch):
    monkeypatch.setattr(quotas, "utcnow", lambda: NOW)
    ctx = await scan_app(sf, 25)
    await kyle_sets(sf, "app", ctx.app_id, scan_rows_per_hour=40)
    await scan_all(sf, ctx)
    with pytest.raises(RecordError) as caught:
        await scan_all(sf, ctx)
    assert caught.value.code == "AD-QUOTA-SCAN-ROWS" and caught.value.status == 429
    assert caught.value.detail["scope"] == "app"
    assert (await usage(sf, "app", ctx.app_id))["scan_rows_this_hour"] == 40
    # Spent: the next scan is refused before it reads anything.
    with pytest.raises(RecordError) as caught:
        await scan_all(sf, ctx)
    assert caught.value.detail["used"] == 40 and caught.value.detail["retry_after"] > 0


async def test_the_owner_scan_budget_spans_its_apps(sf, settings, monkeypatch):
    monkeypatch.setattr(quotas, "utcnow", lambda: NOW)
    one = await scan_app(sf, 20)
    two = await scan_app(sf, 20)
    await kyle_sets(sf, "owner", "agent:pai", scan_rows_per_hour=30)
    await scan_all(sf, one)
    with pytest.raises(RecordError) as caught:
        await scan_all(sf, two)
    assert caught.value.code == "AD-QUOTA-SCAN-ROWS"
    assert caught.value.detail["scope"] == "owner"


async def test_a_scan_stops_at_its_deadline(sf, settings):
    settings(app_data_scan_max_seconds=0.05)
    ctx = await scan_app(sf, 3)
    async with sf() as s:
        with pytest.raises(RecordError) as caught:
            async with scan_budget(s, ctx) as budget:
                async for _ in scan_view(s, ctx, OWNER, ctx.bundle.views["all"],
                                         budget=budget):
                    await asyncio.sleep(0.2)
    assert caught.value.code == "AD-QUOTA-SCAN-TIME" and caught.value.status == 413
    async with sf() as s:
        assert (await s.execute(select(func.count()).select_from(AppDataScanLease))
                ).scalar_one() == 0


async def test_a_slow_query_is_cut_off_too(sf, settings):
    settings(app_data_scan_max_seconds=0.05)
    ctx = await scan_app(sf, 1)
    async with sf() as s:
        with pytest.raises(RecordError) as caught:
            async with scan_budget(s, ctx):
                await asyncio.sleep(1)
    assert caught.value.code == "AD-QUOTA-SCAN-TIME"


async def test_two_concurrent_scans_per_app(sf, settings):
    ctx = await scan_app(sf, 1)
    other = await scan_app(sf, 1)
    release = asyncio.Event()
    entered = []

    async def hold():
        async with sf() as s:
            async with scan_budget(s, ctx):
                entered.append(1)
                await release.wait()

    holders = [asyncio.create_task(hold()) for _ in range(2)]
    while len(entered) < 2:
        await asyncio.sleep(0.01)
    async with sf() as s:
        err = await refused("AD-QUOTA-SCAN-CONCURRENCY", scan_budget(s, ctx).__aenter__())
    assert err.status == 429 and err.detail["max"] == 2 and err.detail["used"] == 2
    # Another App's slots are its own.
    await scan_all(sf, other)
    release.set()
    await asyncio.gather(*holders)
    await scan_all(sf, ctx)


async def test_concurrent_scans_reserve_and_cannot_overspend(sf, settings, monkeypatch):
    monkeypatch.setattr(quotas, "utcnow", lambda: NOW)
    ctx = await scan_app(sf, 1)
    await kyle_sets(sf, "app", ctx.app_id, scan_rows_per_hour=30)
    async with sf() as a, sf() as b:
        async with scan_budget(a, ctx) as first:
            assert first.cap == 30
            # The first holds the whole hour's headroom until it's done.
            await refused("AD-QUOTA-SCAN-ROWS", scan_budget(b, ctx).__aenter__())
            first.charge(12)
        async with scan_budget(b, ctx) as second:
            assert second.cap == 18


async def test_a_crashed_scans_lease_lapses(sf, settings, monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(quotas, "utcnow", lambda: clock["now"])
    ctx = await scan_app(sf, 1)
    async with sf() as s:
        for _ in range(2):
            s.add(AppDataScanLease(app_id=ctx.app_id, owner="agent:pai", reserved_rows=5,
                                   started_at=NOW, expires_at=NOW + timedelta(seconds=90)))
        await s.commit()
    async with sf() as s:
        await refused("AD-QUOTA-SCAN-CONCURRENCY", scan_budget(s, ctx).__aenter__())
    clock["now"] = NOW + timedelta(seconds=91)
    await scan_all(sf, ctx)


async def test_a_view_run_under_a_budget_is_charged(sf, settings):
    ctx = await scan_app(sf, 12)
    async with sf() as s:
        async with scan_budget(s, ctx) as budget:
            page = await execute_view(s, ctx, OWNER, ctx.bundle.views["all"], limit=5,
                                      budget=budget)
            count = await execute_view(s, ctx, OWNER, ctx.bundle.views["n"],
                                       budget=budget)
    assert len(page["rows"]) == 5 and count["count"] == 12
    # The page read one row past its limit to find the next cursor.
    assert budget.scanned == 6 + 12


async def test_a_count_view_cannot_be_scanned(sf, settings):
    ctx = await scan_app(sf, 1)
    async with sf() as s:
        async with scan_budget(s, ctx) as budget:
            with pytest.raises(RecordError) as caught:
                async for _ in scan_view(s, ctx, OWNER, ctx.bundle.views["n"],
                                         budget=budget):
                    pass
    assert caught.value.code == "AD-SCAN-COUNT-VIEW"


async def test_a_scan_applies_the_viewers_access(sf, settings):
    ctx = await make_app(sf, [coll(access={"read": ["owner"], "create": ["owner"]})],
                         [LIST_VIEW])
    await create(sf, ctx, {"title": "secret"})
    async with sf() as s:
        async with scan_budget(s, ctx) as budget:
            with pytest.raises(RecordError) as caught:
                async for _ in scan_view(s, ctx, Caller("agent:bob"),
                                         ctx.bundle.views["all"], budget=budget):
                    pass
    assert caught.value.status == 403
