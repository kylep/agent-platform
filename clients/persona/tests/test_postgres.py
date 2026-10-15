"""Real PostgreSQL memory tool + migration. Run bin/ap-persona-postgres-test."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get('AP_PERSONA_TEST_POSTGRES_URL'),
                                reason='use bin/ap-persona-postgres-test for isolated PostgreSQL')


@pytest.mark.asyncio
async def test_postgres_memory_fencing_provenance_revert_and_migration(tmp_path):
    import httpx
    from sqlalchemy import select, text
    from agentplatform.db import (AgentDef, LocalPersonaSession, LocalPersonaMutation, Memory,
        Run, Conversation, RelayParticipant, init_db, make_engine, make_session_factory)
    from agentplatform.appprovisioner import ToolProvisioner
    from agentplatform.config import Settings
    from agentplatform.events import FakeProducer
    from agentplatform.secrets import InMemorySecretStore
    from agentplatform.api.app import create_app
    from agentplatform.agents import AgentStore
    root = Path(__file__).resolve().parents[3]
    url = os.environ['AP_PERSONA_TEST_POSTGRES_URL']
    engine = make_engine(url)
    await init_db(engine)
    sf = make_session_factory(engine)
    async with sf() as s:
        s.add(AgentDef(name='local-test', agent_type='persona', platform_tools=['mcp__platform__memory']))
        s.add(Conversation(id='scope-room', connector='web', agent='local-test', kind='group'))
        s.add(RelayParticipant(channel_id='scope-room', participant='agent:local-test'))
        s.add(Run(id='legacy', agent='local-test', trigger='test', requested_by='test', prompt=''))
        await s.commit()
    # Recreate the exact additive migration situation: live runs predate the
    # discriminant. Old rows must not disappear from cluster capacity queries.
    async with engine.begin() as c:
        await c.execute(text('ALTER TABLE runs DROP COLUMN execution_location'))
    await init_db(engine)
    async with sf() as s:
        assert (await s.get(Run, 'legacy')).execution_location == 'cluster'
    settings = Settings(local_persona_enabled=True, db_url=url,
        checkout_root=str(tmp_path), skills_root=str(tmp_path/'skills'),
        secrets_root=str(root/'secrets'), tools_root=str(root/'tools'),
        apps_root=str(root/'apps'), reports_root=str(root/'reports'))
    secrets_store = InMemorySecretStore()
    store = AgentStore(sf)
    await store.reload()
    provisioner = ToolProvisioner(None, engine, secrets_store, settings)
    await provisioner._ensure_database('memory')
    app = create_app(settings, sf, FakeProducer(), secret_store=secrets_store, agent_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        await c.post('/api/setup', json={'password':'local-test-password'})
        await c.post('/api/login', json={'password':'local-test-password'})
        e = (await c.post('/api/local-persona/enrollments', json={'agent':'local-test',
             'device_id':'synthetic', 'device_label':'Test'})).json()
        async with httpx.AsyncClient(transport=c._transport, base_url='http://test') as machine:
            r = await machine.post('/api/local-persona/sessions', json={'harness':'codex',
                'launch_nonce':uuid.uuid4().hex}, headers={'Authorization':'Bearer '+e['token']})
            assert r.status_code == 200, r.text
            session = r.json()
            headers = {'Authorization':'Bearer '+session['token']}
            path = '/api/local-persona/sessions/'+session['session_id']
            async with sf() as s:
                run_id = (await s.get(LocalPersonaSession, session['session_id'])).run_id
            tool_env = {k:v for k,v in os.environ.items() if not k.startswith(('AP_','PERSONA_'))}
            tool_env.update(TOOL_CALLER_AGENT='local-test', TOOL_RUN_ID=run_id,
                            TOOL_DB_URL=(await secrets_store.get('tool-memory-db'))['TOOL_DB_URL'])
            def memory(args):
                return subprocess.run([sys.executable, str(root/'tools/memory/run.py')],
                    input=json.dumps(args), text=True, capture_output=True, env=tool_env, timeout=5)
            saved = await asyncio.to_thread(memory, {'action':'save', 'key':'identity.test', 'content':'synthetic identity'})
            assert saved.returncode == 0, saved.stderr
            memory_id = json.loads(saved.stdout)['id']
            async with sf() as s:
                audit = (await s.execute(select(LocalPersonaMutation))).scalar_one()
                assert audit.session_id == session['session_id'] and audit.target_id == memory_id
                assert audit.before is None and audit.after['content'] == 'synthetic identity'
            # Permission loss between broker authorization and execution is
            # denied by SQL, before any hook/API has drained the session.
            async with sf() as s:
                await s.delete(await s.get(RelayParticipant, ('scope-room','agent:local-test')))
                await s.commit()
            denied = await asyncio.to_thread(memory, {'action':'read'})
            assert denied.returncode != 0 and 'authority' in denied.stderr
            async with sf() as s:
                s.add(RelayParticipant(channel_id='scope-room', participant='agent:local-test'))
                agent = await s.get(AgentDef,'local-test')
                agent.platform_tools = []
                await s.commit()
            denied = await asyncio.to_thread(memory, {'action':'read'})
            assert denied.returncode != 0 and 'authority' in denied.stderr
            async with sf() as s:
                (await s.get(AgentDef,'local-test')).platform_tools = ['mcp__platform__memory']
                await s.commit()
            # Execution time, not only the broker's earlier grant check.
            await machine.post(path+'/close', headers=headers)
            denied = await asyncio.to_thread(memory, {'action':'save', 'key':'identity.test', 'content':'must not write'})
            assert denied.returncode != 0 and 'authority' in denied.stderr
            reverted = await c.post(path+'/revert')
            assert reverted.status_code == 200, reverted.text
            assert reverted.json()['reverted'] == 1
            async with sf() as s:
                assert await s.get(Memory, memory_id) is None
    await engine.dispose()
