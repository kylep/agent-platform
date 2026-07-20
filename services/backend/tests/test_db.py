import pytest
from sqlalchemy import select
from agentplatform.db import Run, RunState, make_engine, make_session_factory, init_db

@pytest.fixture
async def sf():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_session_factory(engine)
    await engine.dispose()

async def test_run_defaults(sf):
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="admin", prompt="hi")
        s.add(run); await s.commit()
        got = (await s.execute(select(Run))).scalar_one()
        assert got.state == RunState.QUEUED == "queued"
        assert len(got.id) == 32 and got.created_at is not None


async def test_init_db_adds_missing_columns_to_legacy_table():
    from agentplatform.db import make_engine, init_db
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as c:
        await c.exec_driver_sql(
            "CREATE TABLE runs (id VARCHAR(32) PRIMARY KEY, agent VARCHAR(128), "
            "trigger VARCHAR(32), requested_by VARCHAR(128), state VARCHAR(16), "
            "prompt TEXT, created_at TIMESTAMP, started_at TIMESTAMP, finished_at TIMESTAMP, "
            "exit_code INTEGER, error TEXT, tokens_in INTEGER, tokens_out INTEGER, tool_calls INTEGER)")
    await init_db(e)  # create_all won't touch existing table; _ensure_columns must
    async with e.begin() as c:
        cols = {r[1] for r in (await c.exec_driver_sql("PRAGMA table_info(runs)")).all()}
    assert "summary" in cols and "tags" in cols
    await e.dispose()
