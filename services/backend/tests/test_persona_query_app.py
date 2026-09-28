"""_ensure_persona_app_reads (repair R1, docs/design/35): grants `pai`
`query_app` so it can read worker outputs, and does nothing when the row
does not exist yet."""
import pytest
from sqlalchemy import select
from agentplatform.db import (AgentDef, AgentVersion, PERSONA_QUERY_APP_MARK,
                              SchemaMark, init_db, make_engine, make_session_factory)


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
    reflects only `_ensure_persona_app_reads`, not the ambient default grants."""
    return {**dict(default_grant=False, tickets_grant=False, wiki_grant=False,
                   quota_grant=False, artifacts_grant=False, memory_grant=False), **kw}


async def _agent(sf, name: str):
    async with sf() as s:
        return await s.get(AgentDef, name)


async def test_fresh_apply_grants_query_app(bare):
    sf = make_session_factory(bare)
    async with sf() as s:
        # Skip migrate_authority: it auto-owns the default chat identity for
        # a persona named "pai" and appends mcp__platform__discord for that
        # reason alone, which would otherwise pollute this migration's own
        # before/after comparison.
        s.add(SchemaMark(name="persona-external-authority-v1"))
        s.add(AgentDef(name="pai", prompt="You are Pai.", description="d",
                       agent_type="persona", platform_tools=["mcp__platform__relay"]))
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "pai")
    assert row.platform_tools == ["mcp__platform__relay", "mcp__platform__query_app"]
    async with sf() as s:
        assert await s.get(SchemaMark, PERSONA_QUERY_APP_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "pai",
            AgentVersion.changed_via == "persona-query-app-migration"))).scalars())
    assert len(versions) == 1
    assert versions[0].changed_by == "platform:persona-query-app-migration"
    assert versions[0].snapshot["platform_tools"] == row.platform_tools


async def test_reapply_is_a_no_op_and_preserves_a_later_revoke(bare):
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(SchemaMark(name="persona-external-authority-v1"))
        s.add(AgentDef(name="pai", prompt="You are Pai.", description="d",
                       agent_type="persona", platform_tools=["mcp__platform__relay"]))
        await s.commit()
    await init_db(bare, **_participants())
    async with sf() as s:
        row = await s.get(AgentDef, "pai")
        # An operator revokes the tool by hand after the migration ran once.
        row.platform_tools = ["mcp__platform__relay"]
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "pai")
    # The mark stops a second pass cold: the revoke sticks.
    assert row.platform_tools == ["mcp__platform__relay"]
    async with sf() as s:
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "pai",
            AgentVersion.changed_via == "persona-query-app-migration"))).scalars())
    assert len(versions) == 1


async def test_absent_agent_is_a_true_no_op(bare):
    sf = make_session_factory(bare)
    await init_db(bare, **_participants())
    async with sf() as s:
        assert await s.get(AgentDef, "pai") is None
        # No mark either: a later boot, once the row exists, still catches it.
        assert await s.get(SchemaMark, PERSONA_QUERY_APP_MARK) is None


async def test_existing_query_app_is_preserved_and_a_no_op(bare):
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(SchemaMark(name="persona-external-authority-v1"))
        s.add(AgentDef(name="pai", prompt="You are Pai.", description="d",
                       agent_type="persona", platform_tools=["mcp__platform__relay",
                                                             "mcp__platform__query_app"]))
        await s.commit()
    await init_db(bare, **_participants())
    row = await _agent(sf, "pai")
    assert row.platform_tools == ["mcp__platform__relay", "mcp__platform__query_app"]
    async with sf() as s:
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "pai",
            AgentVersion.changed_via == "persona-query-app-migration"))).scalars())
        assert versions == []
        assert await s.get(SchemaMark, PERSONA_QUERY_APP_MARK) is not None


async def test_full_init_db_on_a_fresh_db_leaves_query_app_on_pai(bare):
    """The realistic boot: every sweep on, migrate_authority and
    reconcile_system_agents both run after this migration in `init_db`'s
    order. Neither removes a tool it does not itself own, so the grant
    this migration made survives to the final state."""
    sf = make_session_factory(bare)
    async with sf() as s:
        s.add(AgentDef(name="pai", prompt="You are Pai.", description="d",
                       agent_type="persona", platform_tools=["mcp__platform__relay"]))
        await s.commit()
    await init_db(bare)
    row = await _agent(sf, "pai")
    assert "mcp__platform__query_app" in row.platform_tools
