"""Retention pruning (design 39, "Collections" → "Retention"): `max_age` and
`max_records` cuts, run through the delete plan so `restrict` refs keep what
they hold and `unlink` refs are cleared."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from agentplatform.appdata import retention
from agentplatform.appdata.models import AppDataRecord, AppDataRecordVersion
from agentplatform.appdata.records import (create_record, get_record, update_record,
                                           write_counter)
from agentplatform.appdata.retention import prune_app, prune_collection
from tests.test_appdata_records import OWNER, coll, engine, make_app, sf  # noqa: F401

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


async def aged(sf, ctx, collection, values, days_old: float):
    async with sf() as s:
        row = await create_record(s, ctx, OWNER, collection, values)
        await s.execute(update(AppDataRecord).where(AppDataRecord.id == row["id"])
                        .values(created_at=NOW - timedelta(days=days_old)))
        await s.commit()
    return row["id"]


async def remaining(sf, collection="items"):
    async with sf() as s:
        return sorted((await s.execute(select(AppDataRecord.doc["title"].as_string())
                                       .where(AppDataRecord.collection == collection))
                       ).scalars().all())


async def test_max_age_prunes_by_created_at(sf):
    ctx = await make_app(sf, [coll(retention={"max_age": "30d"})])
    for title, days in (("old", 31), ("edge", 30.01), ("young", 29), ("new", 0)):
        await aged(sf, ctx, "items", {"title": title}, days)
    async with sf() as s:
        result = await prune_collection(s, ctx, "items", now=NOW)
    assert result.as_dict() == {"collection": "items", "deleted": 2, "unlinked": 0,
                                "kept": 0}
    assert await remaining(sf) == ["new", "young"]


async def test_max_age_in_weeks_and_an_aware_now_in_any_zone(sf):
    ctx = await make_app(sf, [coll(retention={"max_age": "2w"})])
    await aged(sf, ctx, "items", {"title": "15d"}, 15)
    await aged(sf, ctx, "items", {"title": "13d"}, 13)
    toronto = NOW.astimezone(timezone(timedelta(hours=-4)))
    async with sf() as s:
        await prune_collection(s, ctx, "items", now=toronto)
    assert await remaining(sf) == ["13d"]


async def test_max_records_keeps_the_newest(sf):
    ctx = await make_app(sf, [coll(retention={"max_records": 2})])
    for i in range(5):
        await aged(sf, ctx, "items", {"title": f"r{i}"}, 10 - i)
    async with sf() as s:
        result = await prune_collection(s, ctx, "items", now=NOW)
    assert result.deleted == 3
    assert await remaining(sf) == ["r3", "r4"]


async def test_max_records_breaks_created_at_ties_by_id(sf):
    ctx = await make_app(sf, [coll(retention={"max_records": 1})])
    ids = [await aged(sf, ctx, "items", {"title": f"t{i}"}, 1) for i in range(3)]
    async with sf() as s:
        await prune_collection(s, ctx, "items", now=NOW)
        left = (await s.execute(select(AppDataRecord.id))).scalars().all()
    assert left == [max(ids)]


def runs_and_results(on_delete):
    return [coll("runs", retention={"max_age": "7d"}),
            coll("results", fields={"title": {"type": "string"},
                                    "run": {"type": "ref", "collection": "runs",
                                            "on_delete": on_delete}})]


async def test_retention_respects_restrict(sf):
    ctx = await make_app(sf, runs_and_results("restrict"))
    held = await aged(sf, ctx, "runs", {"title": "held"}, 30)
    await aged(sf, ctx, "runs", {"title": "free"}, 30)
    await aged(sf, ctx, "results", {"title": "res", "run": held}, 0)
    async with sf() as s:
        result = await prune_collection(s, ctx, "runs", now=NOW)
    assert (result.deleted, result.kept) == (1, 1)
    assert await remaining(sf, "runs") == ["held"]
    # Once the referrer goes, the next run takes it.
    async with sf() as s:
        await s.execute(AppDataRecord.__table__.delete().where(
            AppDataRecord.collection == "results"))
        await s.commit()
        result = await prune_collection(s, ctx, "runs", now=NOW)
    assert (result.deleted, result.kept) == (1, 0)


async def test_retention_unlinks_and_records_who_did_it(sf):
    ctx = await make_app(sf, runs_and_results("unlink"))
    run = await aged(sf, ctx, "runs", {"title": "old"}, 30)
    res = await aged(sf, ctx, "results", {"title": "res", "run": run}, 0)
    async with sf() as s:
        result = await prune_collection(s, ctx, "runs", now=NOW)
        assert (result.deleted, result.unlinked) == (1, 1)
        row = await get_record(s, ctx, OWNER, "results", res)
        assert row["values"]["run"] is None
        assert (row["values"]["author"], row["values"]["version"]) == ("system:retention", 2)
        assert await write_counter(s, ctx.app_id, "runs") == 2
        assert await write_counter(s, ctx.app_id, "results") == 2


async def test_a_referrer_expiring_with_its_target_doesnt_hold_it(sf):
    body = coll(fields={"title": {"type": "string"},
                        "parent": {"type": "ref", "collection": "items"}},
                retention={"max_age": "7d"})
    ctx = await make_app(sf, [body])
    parent = await aged(sf, ctx, "items", {"title": "parent"}, 30)
    await aged(sf, ctx, "items", {"title": "child", "parent": parent}, 20)
    async with sf() as s:
        result = await prune_collection(s, ctx, "items", now=NOW)
    assert (result.deleted, result.kept) == (2, 0)


async def test_pruning_runs_in_chunks_and_removes_history(sf, monkeypatch):
    monkeypatch.setattr(retention, "CHUNK", 3)
    ctx = await make_app(sf, runs_and_results("restrict"))
    old = [await aged(sf, ctx, "runs", {"title": f"o{i}"}, 30 + i) for i in range(8)]
    async with sf() as s:
        await update_record(s, ctx, OWNER, "runs", old[0], {"title": "edited"},
                            expected_version=1)
    # A held record in the first chunk doesn't stop later chunks.
    await aged(sf, ctx, "results", {"title": "r", "run": old[7]}, 0)
    await aged(sf, ctx, "runs", {"title": "young"}, 1)
    async with sf() as s:
        result = await prune_collection(s, ctx, "runs", now=NOW)
        assert (result.deleted, result.kept) == (7, 1)
        assert (await s.execute(select(AppDataRecordVersion))).first() is None
    assert await remaining(sf, "runs") == ["o7", "young"]


async def test_prune_app_covers_only_collections_with_retention(sf):
    ctx = await make_app(sf, runs_and_results("unlink"))
    await aged(sf, ctx, "runs", {"title": "old"}, 30)
    await aged(sf, ctx, "results", {"title": "old result"}, 300)
    async with sf() as s:
        results = await prune_app(s, ctx, now=NOW)
    assert results == [{"collection": "runs", "deleted": 1, "unlinked": 0, "kept": 0}]
    assert await remaining(sf, "results") == ["old result"]


async def test_retention_prunes_immutable_and_tool_only_collections(sf):
    # Retention acts under the App's approved facts, not as a principal, so
    # write modes and tool-only writers don't stop it.
    body = coll(write_mode="immutable", retention={"max_age": "1d"},
                writers={"delete": ["tool:judgment"]})
    ctx = await make_app(sf, [body])
    await aged(sf, ctx, "items", {"title": "old"}, 2)
    async with sf() as s:
        assert (await prune_collection(s, ctx, "items", now=NOW)).deleted == 1
