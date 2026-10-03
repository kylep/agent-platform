"""Running's state copy keeps activity IDs and weekly brief receipts intact."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataDefinition, AppDataRecord
from agentplatform.appdata.running_migration import bundle, convert_snapshot, import_snapshot
from agentplatform.appdata import running_cutover
from agentplatform.db import Base, make_engine, make_session_factory


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def snapshot():
    return {
        "activities": [{"id": 123456789, "day": "2026-09-30", "name": "Morning run",
                        "type": "Run", "distance_m": 5000, "moving_time_s": 1800,
                        "elevation_m": 12.5, "avg_hr": 145.0, "max_hr": 162.0,
                        "created_at": NOW}],
        "briefs": [{"week_start": "2026-09-21", "body": "Steady progress",
                    "highlights": ["A good week"], "tags": ["consistent"],
                    "distance_m": 5000, "runs": 1, "run_id": "r" * 32,
                    "posted": True, "created_at": NOW}],
    }


def test_definition_and_mapping_preserve_receipt():
    definitions = validate_app(bundle())
    assert set(definitions.collections) == {"activities", "briefs", "sync_state"}
    result = convert_snapshot(snapshot())
    assert result["activities"][0].doc["strava_id"] == "123456789"
    assert result["briefs"][0].doc["posted"] is True
    assert result["briefs"][0].doc["week_start"] == "2026-09-21"


def test_duplicate_external_key_is_refused():
    source = snapshot()
    source["activities"].append(dict(source["activities"][0]))
    with pytest.raises(ValueError, match="duplicate Strava"):
        convert_snapshot(source)


@pytest.fixture
async def sf(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/migration.db")
    tables = [table for name, table in Base.metadata.tables.items()
              if name.startswith("app_data_")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = make_session_factory(engine)
    yield factory
    await engine.dispose()


async def test_import_is_atomic_and_refuses_overwrite(sf):
    definitions = bundle()
    async with sf() as s:
        s.add(AppDataApp(id="a" * 32, name="running", owner_kind="agent",
                         owner_id="running-coach", approved_version=1))
        for kind, plural in (("collection", "collections"), ("view", "views"),
                             ("page", "pages"), ("tool", "app_tools")):
            for body in definitions[plural]:
                s.add(AppDataDefinition(app_id="a" * 32, kind=kind,
                                        name=body[kind], version=1, body=body,
                                        state="published", author="agent:running-coach"))
        await s.commit()
    async with sf() as s:
        assert (await import_snapshot(s, snapshot()))["counts"] == {
            "activities": 1, "briefs": 1}
        assert (await s.execute(select(func.count()).select_from(AppDataRecord))) \
            .scalar_one() == 0
        await import_snapshot(s, snapshot(), dry_run=False)
        await s.commit()
    async with sf() as s:
        records = (await s.execute(select(AppDataRecord))).scalars().all()
        assert len(records) == 2
        assert next(r for r in records if r.collection == "briefs").doc["posted"] is True
        with pytest.raises(ValueError, match="destination is not empty"):
            await import_snapshot(s, snapshot(), dry_run=False)


async def test_one_shot_cutover_bootstraps_only_on_apply(tmp_path, monkeypatch):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/cutover.db"
    engine = make_engine(db_url)
    tables = [table for name, table in Base.metadata.tables.items()
              if name.startswith("app_data_")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    await engine.dispose()
    monkeypatch.setenv("AP_DB_URL", db_url)

    async def source(_session):
        return snapshot()

    monkeypatch.setattr(running_cutover, "read_source", source)
    assert (await running_cutover.copy())["counts"] == {"activities": 1, "briefs": 1}
    result = await running_cutover.copy(apply=True)
    assert result["verified_records"] == 2
    with pytest.raises(ValueError, match="destination is not empty"):
        await running_cutover.copy(apply=True)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    async with factory() as session:
        app = (await session.execute(select(AppDataApp).where(
            AppDataApp.name == "running"))).scalar_one()
        assert app.owner_id == "running-coach" and app.approved_version == 1
        assert (await session.execute(select(func.count()).select_from(AppDataRecord))) \
            .scalar_one() == 2
    await engine.dispose()
