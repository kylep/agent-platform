"""The App data store's tables (docs/design/39, "Storage"): created by init_db,
holding the identities the records engine leans on, with the design's fixed
indexes. SQLite runs everywhere; Postgres runs when AP_TEST_PG_URL names a
scratch database (no Postgres in CI, so the compiled DDL is the assertion
there, as in test_wiki_store)."""
import os

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import init_db, make_engine, make_session_factory

APP_DATA_TABLES = {
    "app_data_apps", "app_data_definitions", "app_data_records",
    "app_data_record_versions", "app_data_build_ops", "app_data_staging_sets",
    "app_data_staged_records", "app_data_quotas", "app_data_write_counters",
    "app_data_artifacts", "app_data_artifact_refs", "app_data_tool_calls",
    "app_data_scan_leases",
}

# The design's composite indexes ("Collections" → "Indexed fields"), by name.
FIXED_RECORD_INDEXES = {
    "ix_app_data_records_t1_time": ["app_id", "collection", "ix_text1", "ix_time1"],
    "ix_app_data_records_t1_t2_time": ["app_id", "collection", "ix_text1", "ix_text2",
                                       "ix_time1"],
    "ix_app_data_records_t1_num": ["app_id", "collection", "ix_text1", "ix_num1"],
    "ix_app_data_records_time": ["app_id", "collection", "ix_time1"],
}


@pytest.fixture
async def engine():
    e = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(e)
    yield e
    await e.dispose()


@pytest.fixture
def sf(engine):
    return make_session_factory(engine)


async def _indexes(engine, table):
    async with engine.connect() as c:
        return await c.run_sync(lambda sc: {ix["name"]: ix for ix in
                                            inspect(sc).get_indexes(table)})


async def test_init_db_creates_every_app_data_table(engine):
    async with engine.connect() as c:
        names = set(await c.run_sync(lambda sc: inspect(sc).get_table_names()))
    assert APP_DATA_TABLES <= names


async def test_init_db_is_idempotent_over_the_app_data_tables(engine):
    await init_db(engine)  # a second boot must not trip over its own tables


async def test_records_carry_the_fixed_indexes_and_no_doc_index_on_sqlite(engine):
    got = await _indexes(engine, "app_data_records")
    for name, cols in FIXED_RECORD_INDEXES.items():
        assert got[name]["column_names"] == cols
    # The GIN index is Postgres-only; SQLite has nothing to index a JSON blob with.
    assert "ix_app_data_records_doc" not in got


def test_the_postgres_ddl_has_no_doc_index_and_two_partial_composites():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable
    from agentplatform.appdata.models import AppDataRecord
    t = AppDataRecord.__table__
    by_name = {i.name: str(CreateIndex(i).compile(dialect=postgresql.dialect()))
               for i in t.indexes}
    # Nothing builds a containment query, so the GIN index was 12% of the table
    # for no reader (appdata-perf-2026-10, "Index and side-column findings").
    assert "ix_app_data_records_doc" not in by_name
    assert not any("gin" in ddl.lower() for ddl in by_name.values())
    assert by_name["ix_app_data_records_t1_num"].endswith("WHERE ix_num1 IS NOT NULL")
    assert by_name["ix_app_data_records_t1_t2_time"].endswith("WHERE ix_text2 IS NOT NULL")
    assert "WHERE" not in by_name["ix_app_data_records_t1_time"]
    table_ddl = str(CreateTable(t).compile(dialect=postgresql.dialect()))
    assert "doc JSONB NOT NULL" in table_ddl


async def test_sqlite_keeps_full_composites_and_the_migration_is_a_noop(engine):
    got = await _indexes(engine, "app_data_records")
    assert got["ix_app_data_records_t1_num"]["column_names"] == \
        FIXED_RECORD_INDEXES["ix_app_data_records_t1_num"]
    await init_db(engine)  # the mark-gated migration must not trip on SQLite
    async with engine.connect() as c:
        marks = set((await c.exec_driver_sql("SELECT name FROM schema_marks")).scalars())
    assert "app-data-index-diet-v1" in marks


async def test_app_names_are_never_reused_even_after_retirement(sf):
    from agentplatform.appdata.models import AppDataApp
    from agentplatform.db import utcnow
    async with sf() as s:
        s.add(AppDataApp(name="news", owner_kind="agent", owner_id="pai",
                         status="retired", retired_at=utcnow()))
        await s.commit()
    async with sf() as s:
        s.add(AppDataApp(name="news", owner_kind="kyle", owner_id="kyle"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_new_app_starts_active_and_unapproved(sf):
    from agentplatform.appdata.models import AppDataApp
    async with sf() as s:
        s.add(AppDataApp(name="running", owner_kind="agent", owner_id="olu"))
        await s.commit()
        app = (await s.execute(select(AppDataApp))).scalar_one()
    assert len(app.id) == 32 and app.status == "active"
    assert app.timezone == "UTC" and app.notes == "" and app.notes_revision == 0
    assert app.approved_version is None and app.authority_generation == 0
    assert app.retired_at is None and app.created_at is not None


async def test_a_definition_version_is_unique_per_app_kind_and_name(sf):
    from agentplatform.appdata.models import AppDataDefinition
    async with sf() as s:
        s.add(AppDataDefinition(app_id="a1", kind="collection", name="bars", version=1,
                                body={"fields": {}}, author="agent:pai"))
        # Same name, other kind or other version: distinct definitions.
        s.add(AppDataDefinition(app_id="a1", kind="view", name="bars", version=1,
                                body={}, author="agent:pai"))
        s.add(AppDataDefinition(app_id="a1", kind="collection", name="bars", version=2,
                                body={}, author="agent:pai"))
        await s.commit()
    async with sf() as s:
        s.add(AppDataDefinition(app_id="a1", kind="collection", name="bars", version=1,
                                body={}, author="agent:pai"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_definition_starts_as_a_first_revision_draft(sf):
    from agentplatform.appdata.models import AppDataDefinition
    async with sf() as s:
        s.add(AppDataDefinition(app_id="a1", kind="page", name="home", version=1,
                                body={}, author="kyle"))
        await s.commit()
        d = (await s.execute(select(AppDataDefinition))).scalar_one()
    assert d.state == "draft" and d.revision == 1 and d.run_id is None and d.reason == ""


async def test_a_record_id_is_unique_per_collection_not_per_app(sf):
    from agentplatform.appdata.models import AppDataRecord
    async with sf() as s:
        s.add(AppDataRecord(app_id="a1", collection="bars", id="r1", author="agent:pai",
                            collection_version=1, doc={"symbol": "XIU"}))
        s.add(AppDataRecord(app_id="a1", collection="symbols", id="r1", author="agent:pai",
                            collection_version=1, doc={}))
        s.add(AppDataRecord(app_id="a2", collection="bars", id="r1", author="agent:pai",
                            collection_version=1, doc={}))
        await s.commit()
    async with sf() as s:
        s.add(AppDataRecord(app_id="a1", collection="bars", id="r1", author="agent:pai",
                            collection_version=1, doc={}))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_record_round_trips_its_doc_and_side_columns(sf):
    from datetime import datetime, timezone
    from agentplatform.appdata.models import AppDataRecord
    day = datetime(2026, 10, 2, tzinfo=timezone.utc)
    async with sf() as s:
        s.add(AppDataRecord(app_id="a1", collection="bars", id="r1", author="agent:pai",
                            via="tool:stockmarket", collection_version=3,
                            ix_text1="XIU", ix_num1=41.5, ix_time1=day,
                            doc={"symbol": "XIU", "close": 41.5}))
        await s.commit()
    async with sf() as s:
        r = (await s.execute(select(AppDataRecord))).scalar_one()
    assert r.doc == {"symbol": "XIU", "close": 41.5} and r.current_version == 1
    assert (r.ix_text1, r.ix_text2, r.ix_num1) == ("XIU", None, 41.5)
    assert r.via == "tool:stockmarket" and r.created_at is not None


async def test_a_record_version_is_unique_per_record(sf):
    from agentplatform.appdata.models import AppDataRecordVersion
    async with sf() as s:
        s.add(AppDataRecordVersion(app_id="a1", collection="bars", record_id="r1", version=1,
                                   author="agent:pai", collection_version=1, doc={}))
        s.add(AppDataRecordVersion(app_id="a1", collection="bars", record_id="r1", version=2,
                                   author="agent:pai", collection_version=1, doc={}))
        await s.commit()
    async with sf() as s:
        s.add(AppDataRecordVersion(app_id="a1", collection="bars", record_id="r1", version=2,
                                   author="agent:pai", collection_version=1, doc={}))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_build_op_request_id_is_unique_per_principal(sf):
    from agentplatform.appdata.models import AppDataBuildOp
    async with sf() as s:
        s.add(AppDataBuildOp(principal="agent:pai", request_id="req-1", op="create",
                             args_hash="h", receipt={"app_id": "a1"}))
        s.add(AppDataBuildOp(principal="agent:kai", request_id="req-1", op="create",
                             args_hash="h", receipt={}))
        await s.commit()
    async with sf() as s:
        s.add(AppDataBuildOp(principal="agent:pai", request_id="req-1", op="publish",
                             args_hash="other", receipt={}))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_staging_set_holds_ordered_invisible_records(sf):
    from agentplatform.appdata.models import AppDataStagedRecord, AppDataStagingSet
    from agentplatform.db import utcnow
    async with sf() as s:
        st = AppDataStagingSet(app_id="a1", creator="agent:tcms-runner", tool="tcms",
                               call_id="c1", collection_versions={"results": 2},
                               expires_at=utcnow())
        s.add(st)
        await s.flush()
        s.add(AppDataStagedRecord(set_id=st.id, seq=1, collection="results",
                                  mode="insert", doc={"case": "k"}))
        s.add(AppDataStagedRecord(set_id=st.id, seq=2, collection="results",
                                  mode="insert", doc={"case": "k2"}))
        await s.commit()
        assert st.state == "open" and st.record_count == 0 and st.bytes == 0
    async with sf() as s:
        s.add(AppDataStagedRecord(set_id=st.id, seq=2, collection="results",
                                  mode="insert", doc={}))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_quotas_and_write_counters_are_keyed_by_their_scope(sf):
    from agentplatform.appdata.models import AppDataQuota, AppDataWriteCounter
    async with sf() as s:
        s.add(AppDataQuota(scope_kind="app", scope_id="a1", max_records=2_000_000,
                           max_bytes=1 << 30, set_by="kyle"))
        s.add(AppDataQuota(scope_kind="owner", scope_id="agent:pai", max_bytes=3 << 30,
                           set_by="kyle"))
        s.add(AppDataWriteCounter(app_id="a1", collection="bars"))
        await s.commit()
        q = await s.get(AppDataQuota, ("app", "a1"))
        w = await s.get(AppDataWriteCounter, ("a1", "bars"))
    assert q.used_records == 0 and q.used_bytes == 0 and q.max_bytes == 1 << 30
    assert w.counter == 0
    async with sf() as s:
        s.add(AppDataWriteCounter(app_id="a1", collection="bars"))
        with pytest.raises(IntegrityError):
            await s.commit()


# --- Postgres, when there is one ----------------------------------------------

PG_URL = os.environ.get("AP_TEST_PG_URL")


async def _pg_defs(e):
    async with e.connect() as c:
        rows = await c.exec_driver_sql(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = "
            "'app_data_records'")
        return dict(rows.all())


async def _pg_fresh(e):
    """Start from a database that has never booted this code's init_db."""
    from agentplatform.appdata.models import AppDataRecord
    async with e.begin() as c:
        await c.run_sync(lambda sc: AppDataRecord.__table__.drop(sc, checkfirst=True))
        if await c.run_sync(lambda sc: inspect(sc).has_table("schema_marks")):
            await c.exec_driver_sql("DELETE FROM schema_marks WHERE name = "
                                    "'app-data-index-diet-v1'")


@pytest.mark.skipif(not PG_URL, reason="AP_TEST_PG_URL names no scratch Postgres")
async def test_postgres_gets_jsonb_the_fixed_indexes_and_partial_composites():
    e = make_engine(PG_URL)
    try:
        await _pg_fresh(e)
        await init_db(e)
        got = await _indexes(e, "app_data_records")
        for name, cols in FIXED_RECORD_INDEXES.items():
            assert got[name]["column_names"] == cols
        defs = await _pg_defs(e)
        assert "ix_app_data_records_doc" not in defs
        assert defs["ix_app_data_records_t1_num"].endswith("WHERE (ix_num1 IS NOT NULL)")
        assert defs["ix_app_data_records_t1_t2_time"].endswith("WHERE (ix_text2 IS NOT NULL)")
        async with e.connect() as c:
            doc_type = (await c.exec_driver_sql(
                "SELECT data_type FROM information_schema.columns WHERE "
                "table_name = 'app_data_records' AND column_name = 'doc'")).scalar_one()
        assert doc_type == "jsonb"
    finally:
        await e.dispose()


# What a database booted before this change holds: full composites and the GIN index.
LEGACY_DDL = [
    "DROP INDEX IF EXISTS ix_app_data_records_t1_num",
    "DROP INDEX IF EXISTS ix_app_data_records_t1_t2_time",
    "CREATE INDEX ix_app_data_records_t1_num ON app_data_records "
    "(app_id, collection, ix_text1, ix_num1)",
    "CREATE INDEX ix_app_data_records_t1_t2_time ON app_data_records "
    "(app_id, collection, ix_text1, ix_text2, ix_time1)",
    "CREATE INDEX ix_app_data_records_doc ON app_data_records "
    "USING gin (doc jsonb_path_ops)",
    "DELETE FROM schema_marks WHERE name = 'app-data-index-diet-v1'",
]


@pytest.mark.skipif(not PG_URL, reason="AP_TEST_PG_URL names no scratch Postgres")
async def test_postgres_migration_converts_a_database_with_the_old_indexes():
    e = make_engine(PG_URL)
    try:
        await _pg_fresh(e)
        await init_db(e)
        async with e.begin() as c:
            for stmt in LEGACY_DDL:
                await c.exec_driver_sql(stmt)
        assert "WHERE" not in (await _pg_defs(e))["ix_app_data_records_t1_num"]
        await init_db(e)
        defs = await _pg_defs(e)
        assert "ix_app_data_records_doc" not in defs
        assert defs["ix_app_data_records_t1_num"].endswith("WHERE (ix_num1 IS NOT NULL)")
        assert defs["ix_app_data_records_t1_t2_time"].endswith("WHERE (ix_text2 IS NOT NULL)")
        # Idempotent: another boot changes nothing, even with the mark gone.
        async with e.begin() as c:
            await c.exec_driver_sql("DELETE FROM schema_marks WHERE name = "
                                    "'app-data-index-diet-v1'")
        await init_db(e)
        assert await _pg_defs(e) == defs
    finally:
        await e.dispose()


@pytest.mark.skipif(not PG_URL, reason="AP_TEST_PG_URL names no scratch Postgres")
async def test_postgres_plans_the_bars_and_results_queries_on_the_partial_indexes():
    e = make_engine(PG_URL)
    try:
        await _pg_fresh(e)
        await init_db(e)
        async with e.begin() as c:
            # 'bars' fills ix_num1 and 'results' fills ix_text2, as the real Apps do.
            await c.exec_driver_sql(
                "INSERT INTO app_data_records (app_id, collection, id, author, "
                "collection_version, current_version, size_bytes, doc, ix_text1, ix_num1, ix_time1, "
                "created_at, updated_at) SELECT 'a1', 'bars', 'b' || g, 'x', 1, 1, 2, '{}', "
                "'S' || (g % 60), g, now() - (g || ' minutes')::interval, now(), now() "
                "FROM generate_series(1, 24000) g")
            await c.exec_driver_sql(
                "INSERT INTO app_data_records (app_id, collection, id, author, "
                "collection_version, current_version, size_bytes, doc, ix_text1, ix_text2, ix_time1, "
                "created_at, updated_at) SELECT 'a1', 'results', 'r' || g, 'x', 1, 1, 2, "
                "'{}', 'run' || (g % 60), 'ref' || g, now() - (g || ' minutes')::interval, "
                "now(), now() FROM generate_series(1, 24000) g")
            await c.exec_driver_sql("ANALYZE app_data_records")

        async def plan(sql):
            async with e.connect() as c:
                rows = await c.exec_driver_sql("EXPLAIN " + sql)
                return "\n".join(r[0] for r in rows)
        bars = await plan("SELECT id FROM app_data_records WHERE app_id = 'a1' AND "
                          "collection = 'bars' AND ix_text1 = 'S7' AND ix_num1 >= 100")
        assert "ix_app_data_records_t1_num" in bars, bars
        results = await plan("SELECT id FROM app_data_records WHERE app_id = 'a1' AND "
                             "collection = 'results' AND ix_text1 = 'run7' AND "
                             "ix_text2 = 'ref127'")
        assert "ix_app_data_records_t1_t2_time" in results, results
    finally:
        await e.dispose()
