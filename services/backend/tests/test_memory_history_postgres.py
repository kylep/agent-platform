"""Memory version history and the dream loss limit (docs/design/44 C), on
real Postgres: the trigger is the whole mechanism. bin/ap-persona-postgres-test
owns the database."""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(not os.environ.get('AP_PERSONA_TEST_POSTGRES_URL'),
                                reason='isolated pgvector database required')
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_history_attribution_loss_limit_restore_and_tool(tmp_path):
    import httpx
    from agentplatform import dreams as D
    from agentplatform.agents import AgentStore
    from agentplatform.api.app import create_app
    from agentplatform.appprovisioner import ToolProvisioner
    from agentplatform.config import Settings
    from agentplatform.db import AgentDef, Memory, Run, init_db, make_engine, make_session_factory, utcnow
    from agentplatform.events import FakeProducer
    from agentplatform.memory_history import MemoryVersion, attribute
    from agentplatform.secrets import InMemorySecretStore
    url = os.environ['AP_PERSONA_TEST_POSTGRES_URL']
    engine = make_engine(url)
    await init_db(engine)
    await init_db(engine)  # idempotent trigger install
    sf = make_session_factory(engine)
    agent = 'hist-' + uuid.uuid4().hex[:8]
    run_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(AgentDef(name=agent, agent_type='persona', platform_tools=['mcp__platform__memory']))
        s.add(Run(id=run_id, agent=agent, trigger='schedule', requested_by='dreamer', prompt='',
                  state='running', definition_snapshot={'platform_tools': ['mcp__platform__memory']}))
        await s.commit()

    # Every insert, update and delete leaves a version, attributed to its writer.
    async with sf() as s:
        await attribute(s, 'rest:admin')
        m = Memory(agent=agent, key='preferences.tea', content='green tea')
        s.add(m)
        await s.commit()
    async with sf() as s:
        await attribute(s, 'rest:admin')
        row = await s.get(Memory, m.id)
        row.content = 'oolong'
        await s.commit()
    async with sf() as s:
        versions = (await s.execute(select(MemoryVersion).where(MemoryVersion.memory_id == m.id)
                                    .order_by(MemoryVersion.at))).scalars().all()
        assert [(v.op, v.content, v.run_id) for v in versions] == [
            ('insert', 'green tea', 'rest:admin'), ('update', 'green tea', 'rest:admin')]
    # set_config is transaction-local: the next transaction is unattributed.
    async with sf() as s:
        s.add(Memory(agent=agent, content='plain note'))
        await s.commit()
        assert (await s.execute(select(MemoryVersion.run_id).where(
            MemoryVersion.agent == agent, MemoryVersion.content == 'plain note'))).scalar_one() is None

    # A running dream may replace or delete at most loss_budget memories.
    others = [Memory(agent=agent, content=f'receipt {i}') for i in range(3)]
    async with sf() as s:
        s.add_all(others)
        s.add(D.Dream(agent=agent, run_id=run_id, state='running', loss_budget=2,
                      window_start=utcnow(), window_end=utcnow()))
        await s.commit()
    for i, expect_ok in ((0, True), (1, True), (2, False)):
        async with sf() as s:
            await attribute(s, run_id)
            await s.delete(await s.get(Memory, others[i].id))
            try:
                await s.commit()
                assert expect_ok
            except Exception as exc:
                assert not expect_ok and 'memory_dream_loss_limit' in str(exc)
    # Inserts never count against the budget.
    async with sf() as s:
        await attribute(s, run_id)
        s.add(Memory(agent=agent, content='merged lesson'))
        await s.commit()

    # The memory Tool path: its own DB role, its own transaction, same trigger.
    settings = Settings(db_url=url, checkout_root=str(tmp_path), skills_root=str(tmp_path / 'skills'),
                        secrets_root=str(ROOT / 'secrets'), tools_root=str(ROOT / 'tools'),
                        apps_root=str(ROOT / 'apps'), reports_root=str(ROOT / 'reports'))
    secrets_store = InMemorySecretStore()
    await ToolProvisioner(None, engine, secrets_store, settings)._ensure_database('memory')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AP_', 'PERSONA_'))}
    env.update(TOOL_CALLER_AGENT=agent, TOOL_RUN_ID=run_id,
               TOOL_DB_URL=(await secrets_store.get('tool-memory-db'))['TOOL_DB_URL'])

    def tool(args):
        return subprocess.run([sys.executable, str(ROOT / 'tools/memory/run.py')], input=json.dumps(args),
                              text=True, capture_output=True, env=env, timeout=10)
    blocked = await asyncio.to_thread(tool, {'action': 'save', 'key': 'preferences.tea', 'content': 'stub'})
    assert blocked.returncode == 0, blocked.stderr
    assert json.loads(blocked.stdout)['error']['code'] == 'memory_dream_loss_limit'
    async with sf() as s:
        assert (await s.get(Memory, m.id)).content == 'oolong'
    fresh = await asyncio.to_thread(tool, {'action': 'save', 'key': 'identity.new', 'content': 'a new lesson'})
    assert fresh.returncode == 0 and json.loads(fresh.stdout)['content'] == 'a new lesson'
    async with sf() as s:
        assert (await s.execute(select(MemoryVersion.run_id).where(
            MemoryVersion.agent == agent, MemoryVersion.key == 'identity.new'))).scalar_one() == run_id
        # The Tool role holds DML on tool_memory but nothing on the history.
        assert (await s.execute(text(
            "SELECT has_table_privilege('tool_memory', 'public.memory_versions', 'DELETE')"))).scalar() is False

    # Parallel writers of one dream cannot each pass the same budget count.
    race_run = uuid.uuid4().hex
    async with sf() as s:
        s.add(Run(id=race_run, agent=agent, trigger='schedule', requested_by='dreamer', prompt='',
                  state='running', definition_snapshot={'platform_tools': ['mcp__platform__memory']}))
        s.add_all(Memory(agent=agent, key=f'race.{i}', content=f'original {i}') for i in range(6))
        s.add(D.Dream(agent=agent, run_id=race_run, state='running', loss_budget=2,
                      window_start=utcnow(), window_end=utcnow()))
        await s.commit()
    race_env = {**env, 'TOOL_RUN_ID': race_run}
    def overwrite(i):
        return subprocess.run([sys.executable, str(ROOT / 'tools/memory/run.py')],
                              input=json.dumps({'action': 'save', 'key': f'race.{i}', 'content': 'stub'}),
                              text=True, capture_output=True, env=race_env, timeout=20)
    results = await asyncio.gather(*(asyncio.to_thread(overwrite, i) for i in range(6)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    landed = [r for r in results if '"ok": false' not in r.stdout]
    assert len(landed) == 2
    async with sf() as s:
        stubs = (await s.execute(select(Memory).where(Memory.agent == agent, Memory.key.like('race.%'),
                                                      Memory.content == 'stub'))).scalars().all()
        assert len(stubs) == 2

    # Admin: list history, restore a deleted note, purge on erasure.
    store = AgentStore(sf)
    await store.reload()
    app = create_app(settings, sf, FakeProducer(), secret_store=secrets_store, agent_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        # A shared database may already have an admin: use an admin key.
        from agentplatform.apikeys import generate_token, hash_token, token_prefix
        from agentplatform.db import ApiKey
        token = generate_token()
        async with sf() as s:
            s.add(ApiKey(name='hist-' + uuid.uuid4().hex[:6], role='admin', key_hash=hash_token(token),
                         prefix=token_prefix(token)))
            await s.commit()
        c.headers['Authorization'] = 'Bearer ' + token
        hist = (await c.get(f'/api/memory-history?run_id={run_id}')).json()
        assert sorted(v['op'] for v in hist) == ['delete', 'delete', 'insert', 'insert']
        deleted = next(v for v in hist if v['op'] == 'delete')
        restored = await c.post(f"/api/memory-versions/{deleted['version_id']}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()['content'] == deleted['content']
        gone = await c.delete(f"/api/memories/{m.id}?agent={agent}&purge_history=true")
        assert gone.status_code == 200, gone.text
        assert (await c.get(f'/api/memory-history?memory_id={m.id}')).json() == []
    await engine.dispose()
