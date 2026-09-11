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


# --- init_db runs in three services at once ----------------------------------

class _FakeConn:
    """Enough AsyncConnection for init_db: a dialect name, and a record of what
    it was asked to do. Stubbed rather than run against a real postgres because
    what is under test is the ORDER of one statement, not its effect."""

    def __init__(self, dialect: str):
        self.dialect = type("D", (), {"name": dialect})()
        self.calls: list[str] = []

    async def execute(self, stmt):
        self.calls.append(str(stmt))

    async def run_sync(self, fn, *args):
        self.calls.append(fn.__name__)


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def begin(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


async def test_init_db_takes_an_advisory_lock_on_postgres():
    """Every service runs init_db at boot, and the one-shot backfills are
    check-then-write — so the lock must be held before ANY of them, for the
    whole transaction."""
    from agentplatform.db import INIT_DB_LOCK_KEY, init_db
    conn = _FakeConn("postgresql")
    await init_db(_FakeEngine(conn))
    assert conn.calls[0] == "SELECT pg_advisory_xact_lock(:k)"
    assert "_ensure_relay_default_grant" in conn.calls
    # The key is a fixed constant: changing it later is the same as no lock.
    assert INIT_DB_LOCK_KEY == -7077053083107605676


async def test_init_db_does_not_try_to_lock_on_sqlite():
    """sqlite has no advisory locks — and one writer at a time is the whole
    story there, so there is nothing to serialize."""
    from agentplatform.db import init_db
    conn = _FakeConn("sqlite")
    await init_db(_FakeEngine(conn))
    assert not any("advisory" in c for c in conn.calls)
    assert conn.calls[0] == "create_all"
