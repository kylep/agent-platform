"""_ensure_backtest_worker (docs/design/35): grants stockmarket-data the
backtest tool, appends its conversation-loop prompt section once, and does
nothing when the row does not exist yet."""
import pytest
from sqlalchemy import select
from agentplatform.db import (AgentDef, AgentVersion, BACKTEST_WORKER_MARK,
                              BACKTEST_PROMPT_HEADING, SchemaMark, init_db,
                              make_engine, make_session_factory)


@pytest.fixture
async def bare():
    """Tables and nothing else — the shape init_db finds on the first boot
    after this migration ships."""
    from agentplatform.db import Base
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


def _participants(**kw):
    """init_db with every other sweep off, so the row this test reads back
    reflects only `_ensure_backtest_worker`, not the ambient default grants."""
    return {**dict(default_grant=False, tickets_grant=False, wiki_grant=False,
                   quota_grant=False, artifacts_grant=False, memory_grant=False,
                   self_grant=False, tasks_grant=False), **kw}


async def _agent(sf, name: str):
    async with sf() as s:
        return await s.get(AgentDef, name)


async def test_fresh_apply_grants_tools_and_appends_prompt_once(bare):
    from agentplatform.db import AGENT_POLICY_SPLIT_MARK
    sf = make_session_factory(bare)
    async with sf() as s:
        # Skip the unrelated policy-split migration so this test isolates the
        # backtest-worker one: it also names stockmarket-data and would
        # otherwise reset platform_tools to ["mcp__platform__prices"] first.
        s.add(SchemaMark(name=AGENT_POLICY_SPLIT_MARK))
        s.add(AgentDef(name="stockmarket-data", prompt="You load prices.",
                       description="d", platform_tools=["mcp__platform__prices"]))
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "stockmarket-data")
    assert row.platform_tools == ["mcp__platform__prices", "mcp__platform__backtest",
                                  "mcp__platform__tickets"]
    assert row.prompt.startswith("You load prices.")
    assert row.prompt.count(BACKTEST_PROMPT_HEADING) == 1
    async with sf() as s:
        assert await s.get(SchemaMark, BACKTEST_WORKER_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "stockmarket-data",
            AgentVersion.changed_via == "backtest-worker-migration"))).scalars())
    assert len(versions) == 1
    assert versions[0].changed_by == "platform:backtest-worker-migration"
    assert versions[0].changed_via == "backtest-worker-migration"
    assert versions[0].snapshot["platform_tools"] == row.platform_tools
    assert BACKTEST_PROMPT_HEADING in versions[0].snapshot["prompt"]


async def test_reapply_is_a_no_op_and_preserves_a_later_revoke(bare):
    from agentplatform.db import AGENT_POLICY_SPLIT_MARK
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(SchemaMark(name=AGENT_POLICY_SPLIT_MARK))
        s.add(AgentDef(name="stockmarket-data", prompt="You load prices.",
                       description="d", platform_tools=["mcp__platform__prices"]))
        await s.commit()
    await init_db(bare, **_participants())
    async with sf() as s:
        row = await s.get(AgentDef, "stockmarket-data")
        # An operator revokes the tool by hand after the migration ran once.
        row.platform_tools = ["mcp__platform__prices"]
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "stockmarket-data")
    # The mark stops a second pass cold: the revoke sticks.
    assert row.platform_tools == ["mcp__platform__prices"]
    async with sf() as s:
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "stockmarket-data",
            AgentVersion.changed_via == "backtest-worker-migration"))).scalars())
    assert len(versions) == 1


async def test_absent_agent_is_a_true_no_op(bare):
    sf = make_session_factory(bare)
    await init_db(bare, **_participants())
    async with sf() as s:
        assert await s.get(AgentDef, "stockmarket-data") is None
        # No mark either: a later boot, once the row exists, still catches it.
        assert await s.get(SchemaMark, BACKTEST_WORKER_MARK) is None


async def test_existing_custom_tool_is_preserved(bare):
    from agentplatform.db import AGENT_POLICY_SPLIT_MARK
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(SchemaMark(name=AGENT_POLICY_SPLIT_MARK))
        s.add(AgentDef(name="stockmarket-data", prompt="You load prices.",
                       description="d", platform_tools=["mcp__platform__prices",
                                                        "mcp__platform__query_app"]))
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "stockmarket-data")
    assert row.platform_tools == ["mcp__platform__prices", "mcp__platform__query_app",
                                  "mcp__platform__backtest", "mcp__platform__tickets"]


async def test_prompt_already_holding_the_section_is_left_alone(bare):
    from agentplatform.db import AGENT_POLICY_SPLIT_MARK, BACKTEST_PROMPT_SECTION
    sf = make_session_factory(bare)
    hand_written = "You load prices.\n\n" + BACKTEST_PROMPT_SECTION.strip() + "\n"
    async with sf() as s:
        s.add(SchemaMark(name=AGENT_POLICY_SPLIT_MARK))
        s.add(AgentDef(name="stockmarket-data", prompt=hand_written,
                       description="d", platform_tools=["mcp__platform__prices",
                                                        "mcp__platform__backtest",
                                                        "mcp__platform__tickets"]))
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "stockmarket-data")
    assert row.prompt == hand_written
    assert row.platform_tools == ["mcp__platform__prices", "mcp__platform__backtest",
                                  "mcp__platform__tickets"]
    async with sf() as s:
        # Nothing changed, so no version was written and the mark still lands.
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "stockmarket-data",
            AgentVersion.changed_via == "backtest-worker-migration"))).scalars())
        assert versions == []
        assert await s.get(SchemaMark, BACKTEST_WORKER_MARK) is not None
