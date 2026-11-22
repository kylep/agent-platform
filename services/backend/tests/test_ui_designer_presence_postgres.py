"""Actual cross-API READ COMMITTED admission; disposable AP_TEST_PG_URL only."""
import asyncio
import os
import uuid
from datetime import timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy import select,text,func
from sqlalchemy.ext.asyncio import AsyncSession,async_sessionmaker,create_async_engine
from agentplatform import db
from agentplatform.ui_designer import presence,store,access

pytestmark=pytest.mark.skipif(not os.environ.get('AP_TEST_PG_URL'),reason='requires disposable PostgreSQL AP_TEST_PG_URL')
DOC={'schema':1,'pages':[{'id':'p','children':[{'id':'frame','type':'frame','w':100,'h':100}]}]}


def payload(identifier):return {'document_id':identifier,'version':1,'page_id':'p','state':'viewing','cursor':None}


async def test_cross_api_cap_refresh_cooldown_and_acl_wait_recheck():
    url=os.environ['AP_TEST_PG_URL'];schema='presence_'+uuid.uuid4().hex
    control=create_async_engine(url)
    async with control.begin() as conn:await conn.execute(text(f'CREATE SCHEMA {schema}'))
    engines=[create_async_engine(url,connect_args={'server_settings':{'search_path':schema}}) for _ in range(2)]
    factories=[async_sessionmaker(e,expire_on_commit=False) for e in engines]
    classes=[model for model in db.Base.registry.mappers if model.local_table.name.startswith('ui_designer_')]
    tables=[mapper.local_table for mapper in classes]+[db.Team.__table__,db.TeamAgent.__table__,db.RelayParticipant.__table__]
    try:
        async with engines[0].begin() as conn:await conn.run_sync(lambda sync:db.Base.metadata.create_all(sync,tables=tables))
        now=db.utcnow()
        async with factories[0]() as session:
            view=await store.create_document(session,slug='presence',owner='user:owner',doc=DOC);identifier=view['id']
            for i in range(31):session.add(db.UiDesignerPresence(document_id=identifier,participant=f'user:seed{i}',client_session=f'{i:032x}',state='viewing',version=1,page_id='p',sequence=1,updated_at=now-timedelta(seconds=1),expires_at=now+presence.TTL))
            await session.commit()
        arrived=0;both=asyncio.Event()
        class BarrierSession(AsyncSession):
            async def execute(self,statement,*args,**kwargs):
                nonlocal arrived
                if getattr(statement,'_for_update_arg',None) is not None:
                    arrived+=1
                    if arrived>=2:both.set()
                    await asyncio.wait_for(both.wait(),5)
                return await super().execute(statement,*args,**kwargs)
        racing=[async_sessionmaker(e,class_=BarrierSession,expire_on_commit=False) for e in engines]
        async def join(index):
            async with racing[index]() as session:
                try:
                    await presence.refresh(session,identifier,access.AccessSubject(f'user:new{index}'),str(index+8)*32,payload(identifier),now)
                    await session.commit();return 200
                except HTTPException as error:await session.rollback();return error.status_code
        assert sorted(await asyncio.wait_for(asyncio.gather(join(0),join(1)),10))==[200,429]
        async with factories[0]() as session:
            count=(await session.execute(select(func.count()).select_from(db.UiDesignerPresence).where(db.UiDesignerPresence.expires_at>now))).scalar_one()
            assert count==32
            # Existing tab refresh works even when admission is full.
            await presence.refresh(session,identifier,access.AccessSubject('user:seed0'),'0'*32,payload(identifier),now);await session.commit()
        # Another API process cannot bypass the actor cooldown with a new tab.
        async with factories[1]() as session:
            with pytest.raises(HTTPException) as error:await presence.refresh(session,identifier,access.AccessSubject('user:seed0'),'f'*32,payload(identifier),now+timedelta(milliseconds=99))
            assert error.value.status_code==429;await session.rollback()
            await presence.leave(session,identifier,access.AccessSubject('user:seed0'),'0'*32,now+timedelta(milliseconds=100));await session.commit()
            assert await presence.leave(session,identifier,access.AccessSubject('user:seed0'),'0'*32,now+timedelta(milliseconds=200)) is None
        async with factories[0]() as session:
            with pytest.raises(HTTPException) as error:await presence.refresh(session,identifier,access.AccessSubject('user:seed0'),'f'*32,payload(identifier),now+timedelta(milliseconds=599))
            assert error.value.status_code==429;await session.rollback()
            await presence.refresh(session,identifier,access.AccessSubject('user:seed0'),'f'*32,payload(identifier),now+timedelta(milliseconds=600));await session.commit()
        # Revoke while refresh is waiting for doc lock: policy must be read after wait.
        waiting=asyncio.Event()
        class WaitingSession(AsyncSession):
            async def execute(self,statement,*args,**kwargs):
                if getattr(statement,'_for_update_arg',None) is not None:waiting.set()
                return await super().execute(statement,*args,**kwargs)
        blocked=async_sessionmaker(engines[1],class_=WaitingSession,expire_on_commit=False)
        async with factories[0]() as blocker:
            await blocker.execute(select(db.UiDesignerDocument.id).where(db.UiDesignerDocument.id==identifier).with_for_update())
            blocker.add(db.UiDesignerAccess(document_id=identifier,visibility='restricted',readers=[],editors=[]));await blocker.flush()
            async def revoked_join():
                async with blocked() as session:
                    try:await presence.refresh(session,identifier,access.AccessSubject('user:revoked'),'e'*32,payload(identifier),now+timedelta(seconds=2));return 200
                    except HTTPException as error:return error.status_code
            task=asyncio.create_task(revoked_join());await asyncio.wait_for(waiting.wait(),5)
            await asyncio.sleep(.05);assert not task.done();await blocker.commit()
        assert await asyncio.wait_for(task,5)==404
    finally:
        for engine in engines:await engine.dispose()
        async with control.begin() as conn:await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await control.dispose()
