from datetime import timedelta
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from agentplatform.db import UiDesignerPresence,UiDesignerAccess,UiDesignerAccessEntry,Principal,LoginSession,utcnow
from agentplatform.ui_designer import presence,store,access

DOC={'schema':1,'pages':[{'id':'p','children':[{'id':'frame','type':'frame','w':100,'h':100,'children':[{'id':'rect','type':'rect','w':20,'h':20}]}]}]}


async def setup(sf):
    async with sf() as s:
        row=await store.create_document(s,slug='presence',owner='user:owner',doc=DOC);await s.commit()
    return row['id']


def body(identifier,**kw):return {'document_id':identifier,'version':1,'page_id':'p','state':'viewing','node_id':'rect','cursor':{'x':5,'y':7},**kw}


async def test_ttl_cooldown_new_tab_leave_and_no_history(sf):
    identifier=await setup(sf);now=utcnow();who=access.AccessSubject('user:owner')
    async with sf() as s:
        got=await presence.refresh(s,identifier,who,'a'*32,body(identifier),now);await s.commit()
        assert got['sessions'][0]['label']=='owner' and got['sessions'][0]['sequence']==1
        for session in ['a'*32,'b'*32]:
            with pytest.raises(HTTPException) as exc:await presence.refresh(s,identifier,who,session,body(identifier),now+timedelta(milliseconds=499))
            assert exc.value.status_code==429
        signal=await presence.leave(s,identifier,who,'a'*32,now+timedelta(milliseconds=500));await s.commit()
        assert signal['sequence']==2 and not (await presence.roster(s,identifier,who,now+timedelta(seconds=1)))['sessions']
        assert (await presence.event_current(s,identifier,who,'user:owner','a'*32,2,now+timedelta(seconds=1)))['state']=='left'
        with pytest.raises(HTTPException):await presence.refresh(s,identifier,who,'b'*32,body(identifier),now+timedelta(milliseconds=999))
        await presence.refresh(s,identifier,who,'b'*32,body(identifier),now+timedelta(seconds=1));await s.commit()
        assert len((await presence.roster(s,identifier,who,now+timedelta(seconds=45)))['sessions'])==1
        assert (await presence.roster(s,identifier,who,now+timedelta(seconds=46)))['sessions']==[]
        assert await presence.purge_expired(s,now+timedelta(seconds=48))==2
        assert (await store.get_document(s,'presence'))['version']==1


async def test_cap_existing_refresh_and_stale_events(sf):
    identifier=await setup(sf);now=utcnow()
    async with sf() as s:
        for i in range(32):await presence.refresh(s,identifier,access.AccessSubject('user:u'+str(i)),f'{i:032x}',body(identifier),now)
        await s.commit()
        with pytest.raises(HTTPException) as exc:await presence.refresh(s,identifier,access.AccessSubject('user:extra'),'f'*32,body(identifier),now)
        assert exc.value.status_code==429
        who=access.AccessSubject('user:u0');await presence.refresh(s,identifier,who,'0'*32,body(identifier),now+timedelta(seconds=1));await s.commit()
        assert await presence.event_current(s,identifier,who,who.participant,'0'*32,1,now+timedelta(seconds=1)) is None
        assert (await presence.event_current(s,identifier,who,who.participant,'0'*32,2,now+timedelta(seconds=1)))['sequence']==2


async def test_actor_acl_revocation_and_write_downgrade(sf):
    identifier=await setup(sf);now=utcnow();who=access.AccessSubject('user:editor');owner=access.AccessSubject('user:owner')
    async with sf() as s:
        s.add(UiDesignerAccess(document_id=identifier,visibility='restricted',readers=[],editors=['user:editor']))
        entry=UiDesignerAccessEntry(document_id=identifier,principal='user:editor',role='editor');s.add(entry);await s.commit()
        await presence.refresh(s,identifier,who,'a'*32,body(identifier,state='editing'),now);await s.commit()
        await s.delete(entry);s.add(UiDesignerAccessEntry(document_id=identifier,principal='user:editor',role='reader'));await s.commit()
        assert (await presence.roster(s,identifier,owner,now))['sessions'][0]['state']=='viewing'
        await s.delete((await s.execute(select(UiDesignerAccessEntry).where(UiDesignerAccessEntry.document_id==identifier))).scalar_one());await s.commit()
        assert (await presence.roster(s,identifier,owner,now))['sessions']==[]
        with pytest.raises(HTTPException):await presence.refresh(s,identifier,who,'a'*32,body(identifier),now+timedelta(seconds=1))


async def test_admin_credential_current_role_and_revocation(sf):
    identifier=await setup(sf);now=utcnow();admin=access.AccessSubject('user:admin-person',is_admin=True);owner=access.AccessSubject('user:owner')
    async with sf() as s:
        s.add(UiDesignerAccess(document_id=identifier,visibility='restricted',readers=[],editors=[]))
        principal=Principal(name='admin-person',role='admin');s.add(principal);await s.flush()
        login=LoginSession(id_hash='a'*64,principal_id=principal.id,expires_at=now+timedelta(days=1));s.add(login);await s.commit()
        await presence.refresh(s,identifier,admin,'b'*32,body(identifier,state='editing'),now,credential={'kind':'session','id':login.id_hash});await s.commit()
        assert (await presence.roster(s,identifier,owner,now))['sessions'][0]['state']=='editing'
        principal.role='reader';s.add(UiDesignerAccessEntry(document_id=identifier,principal=admin.participant,role='editor'));await s.commit()
        assert (await presence.roster(s,identifier,owner,now))['sessions'][0]['state']=='viewing'
        login.revoked_at=now;await s.commit()
        assert (await presence.roster(s,identifier,owner,now))['sessions']==[]


@pytest.mark.parametrize('patch',[{'document_id':'f'*32},{'version':False},{'page_id':'other'},{'node_id':'other'},{'cursor':{'x':float('nan'),'y':0}},{'cursor':{'x':True,'y':2}},{'participant':'user:owner'},{'state':'admin'}])
async def test_closed_bounded_payload(sf,patch):
    identifier=await setup(sf)
    async with sf() as s:
        with pytest.raises(HTTPException):await presence.refresh(s,identifier,access.AccessSubject('user:owner'),'b'*32,body(identifier,**patch))
