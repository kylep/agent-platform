"""Rapid design reads stay on the controller-owned document and frozen grants."""
import pytest
from agentplatform.api.auth import rapid_turn_allows
from agentplatform.api.schemas import RapidChatSinkIn
from agentplatform.db import RapidChat, RapidChatTurn, Run, RunState, UiDesignerDocument, utcnow
from agentplatform.ui_designer import store
from .test_relay_api import _agent_token, _seed

UD='mcp__platform__ui_designer'


async def turn(sf, seed_agent, agent_store, *, kind='ui_designer', granted=True):
    await _seed(seed_agent,agent_store,'pai',platform_tools=[UD] if granted else [])
    async with sf() as s:
        view=await store.create_document(s,slug='sink',owner='user:admin')
        await store.create_document(s,slug='other',owner='user:admin')
        s.add(RapidChat(id='rapid-ui',title='Design',goal='test',created_by='admin',participants=['pai'],
            sink={'kind':kind,'slug':'sink','mode':'replace','document_id':view['id']},areas=[],
            state='running',duration_minutes=60,max_turns=10,turn_timeout_seconds=240,limits={},started_at=utcnow(),turns_total=0))
        s.add(Run(id='a'*32,agent='pai',trigger='rapid_chat',requested_by='rapid-chat:rapid-ui',prompt='x',state=RunState.RUNNING))
        s.add(RapidChatTurn(chat_id='rapid-ui',seq=0,agent='pai',role='author',run_id='a'*32,state='running',prompt='x'))
        await s.commit()
    return await _agent_token(sf,'pai',run_id='a'*32)


def test_exact_paths_and_replace_only():
    sink={'kind':'ui_designer','slug':'sink','document_id':'a'*32}
    for path in ['/api/ui-designer/docs/sink','/api/ui-designer/docs/sink/render','/api/ui-designer/docs/sink/versions/2']:
        assert rapid_turn_allows('GET',path,sink)
        assert not rapid_turn_allows('POST',path,sink)
    for path in ['/api/ui-designer/docs/sink2','/api/ui-designer/docs/other','/api/ui-designer/docs/sink/../other', '/api/ui-designer/docs/sink/edit','/api/ui-designer/events','/api/ui-designer/assets/'+'a'*64,'/api/ui-designer/docs']:
        assert not rapid_turn_allows('GET',path,sink)
    with pytest.raises(ValueError):RapidChatSinkIn(kind='ui_designer',slug='x',mode='append')


async def test_ui_turn_scoped_reads_and_no_writes(token_client,sf,seed_agent,agent_store):
    headers=await turn(sf,seed_agent,agent_store)
    me=(await token_client.get('/api/whoami',headers=headers)).json()
    assert me['tools']==[UD] and me['rapid_ui_slug']=='sink'
    assert (await token_client.get('/api/ui-designer/docs/sink',headers=headers)).status_code==200
    assert (await token_client.get('/api/ui-designer/docs/sink/versions',headers=headers)).status_code==200
    for path in ['/api/ui-designer/docs/other','/api/ui-designer/docs','/api/ui-designer/events','/api/ui-designer/icons','/api/ui-designer/assets/'+'a'*64]:
        assert (await token_client.get(path,headers=headers)).status_code==403,path
    for method,path in [('post','/api/ui-designer/docs/sink/edit'),('delete','/api/ui-designer/docs/sink'),('post','/api/ui-designer/docs/sink/export')]:
        assert (await getattr(token_client,method)(path,headers=headers)).status_code==403
    async with sf() as s:
        from sqlalchemy import select
        row=(await s.execute(select(UiDesignerDocument).where(UiDesignerDocument.slug=='sink'))).scalar_one()
        row.deleted_at=utcnow();await s.flush()
        await store.create_document(s,slug='sink',owner='user:admin');await s.commit()
    assert (await token_client.get('/api/ui-designer/docs/sink',headers=headers)).status_code==403


@pytest.mark.parametrize('kind,granted',[('wiki',True),('ui_designer',False)])
async def test_wiki_sink_and_ungranted_ui_turn_do_not_gain_ui(token_client,sf,seed_agent,agent_store,kind,granted):
    headers=await turn(sf,seed_agent,agent_store,kind=kind,granted=granted)
    me=(await token_client.get('/api/whoami',headers=headers)).json()
    assert UD not in me['tools']
    assert (await token_client.get('/api/ui-designer/docs/sink',headers=headers)).status_code==403


async def test_workload_without_run_jwt_keeps_same_scope(sf,seed_agent,agent_store):
    from types import SimpleNamespace
    from starlette.requests import Request
    from fastapi import HTTPException
    from agentplatform.api.auth import _dream_fence
    await turn(sf,seed_agent,agent_store)
    def request(method,path):
        app=SimpleNamespace(state=SimpleNamespace(session_factory=sf))
        r=Request({'type':'http','method':method,'path':path,'scheme':'http','server':('test',80),'headers':[], 'query_string':b'','app':app})
        r.state.api_key_agent='pai';r.state.api_key_run_id=None;r.state.auth_kind='workload'
        return r
    await _dream_fence(request('GET','/api/ui-designer/docs/sink'))
    for method,path in [('GET','/api/ui-designer/docs/other'),('POST','/api/ui-designer/docs/sink/edit'),('GET','/api/ui-designer/assets/'+'a'*64)]:
        with pytest.raises(HTTPException) as exc:await _dream_fence(request(method,path))
        assert exc.value.status_code==403
    # Ending the chat does not widen a turn that still has an active Run.
    async with sf() as s:
        (await s.get(RapidChat,'rapid-ui')).state='done';await s.commit()
    with pytest.raises(HTTPException):await _dream_fence(request('POST','/api/ui-designer/docs/sink/edit'))
