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


# --- the quota grant sweep (docs/design/22) ----------------------------------
# `get_quota_usage` is default-granted, and "default-granted" is implemented as
# rows: new agents get it from the create path, and this is the one-time sweep
# for the agents that predate the tool.

@pytest.fixture
async def bare():
    """Tables and nothing else — the shape init_db finds on the first boot
    after the tool ships."""
    from agentplatform.db import Base, make_engine
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


def _participants(**kw):
    """init_db with every other participant sweep off, so what a test reads
    back is the one sweep under test and nobody else's."""
    return {**dict(default_grant=False, tickets_grant=False, wiki_grant=False,
                   quota_grant=False, artifacts_grant=False), **kw}


async def _tools(sf, name: str) -> list[str]:
    from agentplatform.db import AgentDef
    async with sf() as s:
        return (await s.get(AgentDef, name)).platform_tools


async def test_quota_grant_backfill_covers_the_agents_that_already_exist(bare):
    from agentplatform.db import (QUOTA_GRANT_MARK, AgentDef, AgentVersion,
                                  SchemaMark)
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="news", prompt="p", description="d",
                       platform_tools=["mcp__platform__relay"]))
        s.add(AgentDef(name="retired", prompt="p", description="d",
                       platform_tools=[], enabled=False))
        await s.commit()
    await init_db(bare, **_participants(quota_grant=True))
    assert await _tools(sf, "news") == ["mcp__platform__relay",
                                        "mcp__platform__get_quota_usage"]
    assert await _tools(sf, "retired") == []     # disabled agents are left alone
    async with sf() as s:
        assert await s.get(SchemaMark, QUOTA_GRANT_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "news"))).scalars())
    # The sweep continues the design-15 change log, attributed to itself, so an
    # operator can find out later why an agent holds a tool nobody granted it.
    assert [(v.changed_by, v.changed_via) for v in versions] == [
        ("platform:quota-default-grant", "migration")]


async def test_quota_grant_backfill_honours_the_setting_and_runs_once(bare):
    """Off means the sweep does not run AND does not mark itself, so turning it
    on later still backfills; on means exactly one pass, ever."""
    from agentplatform.db import QUOTA_GRANT_MARK, AgentDef, SchemaMark
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="news", prompt="p", description="d", platform_tools=[]))
        await s.commit()
    await init_db(bare, **_participants(quota_grant=False))
    assert await _tools(sf, "news") == []
    async with sf() as s:
        assert await s.get(SchemaMark, QUOTA_GRANT_MARK) is None
    await init_db(bare, **_participants(quota_grant=True))
    assert await _tools(sf, "news") == ["mcp__platform__get_quota_usage"]
    # An admin taking it away afterwards is not undone by the next boot.
    async with sf() as s:
        (await s.get(AgentDef, "news")).platform_tools = []
        await s.commit()
    await init_db(bare, **_participants(quota_grant=True))
    assert await _tools(sf, "news") == []


async def test_artifacts_grant_backfill_covers_the_agents_that_already_exist(bare):
    """docs/design/23: `artifacts` is as ambient as `relay`, so the agents that
    predate it are swept once, each change a `migration` version of its own."""
    from agentplatform.db import (ARTIFACTS_GRANT_MARK, AgentDef, AgentVersion,
                                  SchemaMark)
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="news", prompt="p", description="d",
                       platform_tools=["mcp__platform__relay"]))
        s.add(AgentDef(name="retired", prompt="p", description="d",
                       platform_tools=[], enabled=False))
        await s.commit()
    await init_db(bare, **_participants(artifacts_grant=True))
    assert await _tools(sf, "news") == ["mcp__platform__relay",
                                        "mcp__platform__artifacts"]
    assert await _tools(sf, "retired") == []
    async with sf() as s:
        assert await s.get(SchemaMark, ARTIFACTS_GRANT_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "news"))).scalars())
    assert [(v.changed_by, v.changed_via) for v in versions] == [
        ("platform:artifacts-default-grant", "migration")]
    # Exactly once: the next boot finds the mark and leaves the rows alone.
    await init_db(bare, **_participants(artifacts_grant=True))
    async with sf() as s:
        assert len(list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "news"))).scalars())) == 1


async def test_artifacts_grant_backfill_honours_the_setting(bare):
    from agentplatform.db import ARTIFACTS_GRANT_MARK, AgentDef, SchemaMark
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="news", prompt="p", description="d", platform_tools=[]))
        await s.commit()
    await init_db(bare, **_participants())
    assert await _tools(sf, "news") == []
    async with sf() as s:
        assert await s.get(SchemaMark, ARTIFACTS_GRANT_MARK) is None
