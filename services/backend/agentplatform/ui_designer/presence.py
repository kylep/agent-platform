"""Multi-API-safe, UUID-bound presence. Caller owns commit and event publish."""
from __future__ import annotations
import math
import re
from datetime import timedelta,timezone
from sqlalchemy import delete,func,select
from sqlalchemy.orm import load_only
from fastapi import HTTPException
from agentplatform.db import (ApiKey,LoginSession,Principal,AgentDef,Run,ACTIVE_STATES,UiDesignerDocument,UiDesignerPresence,utcnow)
from agentplatform.tickets import participant_label
from . import access,store
from .schema import index_nodes

TTL=timedelta(seconds=45)
COOLDOWN=timedelta(milliseconds=500)
LEFT_RETENTION=timedelta(seconds=1)
CAP=32
READ_ROLES={'reader','annotator','operator','admin','relay'}
WRITE_ROLES={'operator','admin','relay'}
HEX32=re.compile(r'^[0-9a-f]{32}$')
NODE=re.compile(r'^[a-z0-9][a-z0-9/-]{0,511}$')


def _utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _now(session,now):
    if now is not None:return _utc(now)
    if session.bind.dialect.name=='postgresql':return _utc((await session.execute(select(func.clock_timestamp()))).scalar_one())
    return utcnow()


def _ids(document_id,client_session=None):
    if not isinstance(document_id,str) or not HEX32.fullmatch(document_id):raise HTTPException(422,'document_id must be32 lowercase hex characters')
    if client_session is not None and (not isinstance(client_session,str) or not HEX32.fullmatch(client_session)):raise HTTPException(422,'client_session must be32 lowercase hex characters')


async def _document(session,identifier,who,*,lock=False,write=False):
    _ids(identifier)
    query=select(UiDesignerDocument).options(load_only(UiDesignerDocument.id,UiDesignerDocument.slug,UiDesignerDocument.owner,UiDesignerDocument.version,UiDesignerDocument.deleted_at)).where(UiDesignerDocument.id==identifier,UiDesignerDocument.deleted_at.is_(None))
    if lock and session.bind.dialect.name=='postgresql':query=query.with_for_update()
    row=(await session.execute(query)).scalars().first()
    if row is None:raise HTTPException(404,'design document is unavailable')
    # Re-evaluate policy AFTER acquiring the doc lock: a waiting statement's
    # earlier READ COMMITTED snapshot must not carry a revoked ACL through.
    who=await access.subject_for(session,who.participant,is_admin=who.is_admin)
    clause=access.writable_clause(who) if write else access.readable_clause(who)
    if (await session.execute(select(UiDesignerDocument.id).where(UiDesignerDocument.id==identifier,clause))).first() is None:
        raise HTTPException(404,'design document is unavailable')
    return row


async def _credential_role(session,participant,credential,now):
    """Current DB role proof; never persist an admin boolean or raw token."""
    from agentplatform.relay import participant_of
    if not credential:return None
    kind=credential.get('kind');identifier=credential.get('id')
    if not isinstance(identifier,str) or (kind=='key' and not HEX32.fullmatch(identifier)) or (kind=='session' and not re.fullmatch('[0-9a-f]{64}',identifier)):return None
    if kind=='key':
        key=await session.get(ApiKey,identifier)
        if key is None or key.revoked_at is not None:return None
        expected=participant_of(agent=key.agent) if key.agent else participant_of(principal=key.name)
        if expected!=participant:return None
        if key.agent:
            agent=await session.get(AgentDef,key.agent)
            if not agent or not agent.enabled or 'mcp__platform__ui_designer' not in (agent.platform_tools or []):return None
            if key.role=='admin':return 'relay' # agent identities never gain admin authority
        return key.role
    if kind=='session':
        login=await session.get(LoginSession,identifier)
        if login is None or login.revoked_at is not None or _utc(login.expires_at)<=now:return None
        principal=await session.get(Principal,login.principal_id)
        if principal is None or participant_of(principal=principal.name)!=participant:return None
        return principal.role
    return None


async def _actor(session,row,now,cache):
    key=(row.participant,row.credential_kind,row.credential_id)
    if key in cache:return cache[key]
    credential={'kind':row.credential_kind,'id':row.credential_id} if row.credential_kind else None
    role=await _credential_role(session,row.participant,credential,now) if credential else 'operator'
    if role not in READ_ROLES:cache[key]=None;return None
    who=await access.subject_for(session,row.participant,is_admin=role=='admin' and row.participant.startswith('user:'))
    clause=access.readable_clause(who)
    readable=(await session.execute(select(UiDesignerDocument.id).where(UiDesignerDocument.id==row.document_id,UiDesignerDocument.deleted_at.is_(None),clause))).first()
    if not readable:cache[key]=None;return None
    writable=role in WRITE_ROLES and bool((await session.execute(select(UiDesignerDocument.id).where(UiDesignerDocument.id==row.document_id,access.writable_clause(who)))).first())
    if writable and row.participant.startswith('agent:') and row.credential_kind=='key':
        credential_row=await session.get(ApiKey,row.credential_id)
        run=await session.get(Run,credential_row.run_id) if credential_row and credential_row.run_id else None
        writable=bool(run and run.agent==row.participant[6:] and run.state in ACTIVE_STATES)
    result=(who,writable);cache[key]=result;return result


def _view(row,*,writable=True):
    return {'participant':row.participant,'label':participant_label(row.participant),'client_session':row.client_session,
        'state':'editing' if row.state=='editing' and writable else 'viewing','version':row.version,'page_id':row.page_id,
        'node_id':row.node_id,'cursor':{'x':row.x,'y':row.y} if row.x is not None and row.y is not None else None,
        'sequence':row.sequence,'expires_at':_utc(row.expires_at).isoformat()}


async def roster(session,document_id,who,now=None):
    now=await _now(session,now)
    who=await access.subject_for(session,who.participant,is_admin=who.is_admin)
    await _document(session,document_id,who)
    rows=(await session.execute(select(UiDesignerPresence).where(UiDesignerPresence.document_id==document_id,UiDesignerPresence.expires_at>now,UiDesignerPresence.state!='left').order_by(UiDesignerPresence.participant,UiDesignerPresence.client_session).limit(CAP))).scalars().all()
    cache={};sessions=[]
    for row in rows:
        actor=await _actor(session,row,now,cache)
        if actor:sessions.append(_view(row,writable=actor[1]))
    return {'document_id':document_id,'server_now':now.isoformat(),'sessions':sessions}


async def purge_expired(session,now=None,document_id=None,limit=1000):
    now=await _now(session,now)
    query=select(UiDesignerPresence.document_id,UiDesignerPresence.participant,UiDesignerPresence.client_session).where(UiDesignerPresence.expires_at<=now-LEFT_RETENTION)
    if document_id is not None:query=query.where(UiDesignerPresence.document_id==document_id)
    keys=(await session.execute(query.order_by(UiDesignerPresence.expires_at).limit(limit))).all()
    for doc,participant,client in keys:
        await session.execute(delete(UiDesignerPresence).where(UiDesignerPresence.document_id==doc,UiDesignerPresence.participant==participant,UiDesignerPresence.client_session==client,UiDesignerPresence.expires_at<=now-LEFT_RETENTION).execution_options(synchronize_session='fetch'))
    return len(keys)


async def _cooldown(session,document_id,participant,now):
    last=(await session.execute(select(func.max(UiDesignerPresence.updated_at)).where(UiDesignerPresence.document_id==document_id,UiDesignerPresence.participant==participant))).scalar_one()
    if last is not None and now-_utc(last)<COOLDOWN:raise HTTPException(429,'presence updates are limited to2/sec per participant/document',headers={'Retry-After':'1'})


def _payload(data,document_id):
    required={'document_id','version','page_id','state'}
    if not isinstance(data,dict) or not required<=data.keys() or data.keys()-required-{'node_id','cursor'}:raise HTTPException(422,'presence payload has unknown or missing fields')
    if data['document_id']!=document_id:raise HTTPException(409,'document identity changed; reopen the document')
    if type(data['version']) is not int or data['version']<1 or not isinstance(data['page_id'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}',data['page_id']):raise HTTPException(422,'presence needs a retained version and authored page ID')
    node=data.get('node_id')
    if node is not None and (not isinstance(node,str) or not NODE.fullmatch(node)):raise HTTPException(422,'invalid selected node ID')
    if data['state'] not in ('viewing','editing'):raise HTTPException(422,'state must be viewing or editing')
    cursor=data.get('cursor')
    if cursor is not None and (not isinstance(cursor,dict) or set(cursor)!={'x','y'} or any(isinstance(cursor[k],bool) or not isinstance(cursor[k],(int,float)) or not math.isfinite(cursor[k]) or abs(cursor[k])>100000 for k in ('x','y'))):raise HTTPException(422,'cursor must contain finite world coordinates x/y within100000')


async def refresh(session,document_id,who,client_session,payload,now=None,credential=None):
    _ids(document_id,client_session);_payload(payload,document_id)
    # Lock before counting or touching rows; all API replicas share admission.
    await _document(session,document_id,who,lock=True)
    now=await _now(session,now)
    role=await _credential_role(session,who.participant,credential,now) if credential else 'operator'
    if role not in READ_ROLES:raise HTTPException(403,'presence credential is no longer readable')
    who=await access.subject_for(session,who.participant,is_admin=role=='admin' and who.participant.startswith('user:'))
    doc=await _document(session,document_id,who,write=payload['state']=='editing')
    if payload['state']=='editing' and role not in WRITE_ROLES:raise HTTPException(403,'editing presence requires write authority')
    if payload['state']=='editing' and who.participant.startswith('agent:') and credential:
        keyrow=await session.get(ApiKey,credential['id']) if credential['kind']=='key' else None
        run=await session.get(Run,keyrow.run_id) if keyrow and keyrow.run_id else None
        if not run or run.agent!=who.participant[6:] or run.state not in ACTIVE_STATES:raise HTTPException(403,'editing presence requires an active run')
    if payload['state']=='editing' and payload['version']!=doc.version:raise HTTPException(409,'editing presence requires the current document version')
    await purge_expired(session,now,document_id)
    await _cooldown(session,document_id,who.participant,now)
    key=(document_id,who.participant,client_session);row=await session.get(UiDesignerPresence,key)
    live=row is not None and _utc(row.expires_at)>now and row.state!='left'
    if not live:
        count=(await session.execute(select(func.count()).select_from(UiDesignerPresence).where(UiDesignerPresence.document_id==document_id,UiDesignerPresence.expires_at>now,UiDesignerPresence.state!='left'))).scalar_one()
        if count>=CAP:raise HTTPException(429,'document presence has32 active sessions',headers={'Retry-After':'15'})
    if row is None or row.version!=payload['version'] or row.page_id!=payload['page_id'] or row.node_id!=payload.get('node_id'):
        snapshot=await store.resolve_version(session,doc,payload['version'])
        if snapshot is None:raise HTTPException(404,'presence version is unavailable')
        page=next((p for p in snapshot.get('pages',[]) if p['id']==payload['page_id']),None)
        if page is None:raise HTTPException(422,'presence page is not in that document version')
        if payload.get('node_id'):
            # Expanded instance IDs are not trusted as authored selection IDs.
            nodes=index_nodes({'pages':[page]})
            found=payload['node_id'] in nodes
            if not found and '/' in payload['node_id']:
                from .layout import _Builder
                builder=_Builder(snapshot,(snapshot.get('modes') or [None])[0],measure_text=False)
                pending=[builder.build(n,'',None,i,None,0,'$.presence') for i,n in enumerate(page.get('children',[]))]
                while pending:
                    item=pending.pop()
                    if item.id==payload['node_id']:found=True;break
                    pending.extend(item.kids)
            if not found:raise HTTPException(422,'selected node is not on that page/version')
    if row is None:
        row=UiDesignerPresence(document_id=document_id,participant=who.participant,client_session=client_session,sequence=0)
        session.add(row)
    row.version=payload['version'];row.page_id=payload['page_id'];row.node_id=payload.get('node_id');row.state=payload['state']
    cursor=payload.get('cursor');row.x=cursor['x'] if cursor else None;row.y=cursor['y'] if cursor else None
    row.updated_at=now;row.expires_at=now+TTL;row.sequence+=1
    row.credential_kind=credential['kind'] if credential else None;row.credential_id=credential['id'] if credential else None
    await session.flush()
    return await roster(session,document_id,who,now)


async def leave(session,document_id,who,client_session,now=None):
    _ids(document_id,client_session);await _document(session,document_id,who,lock=True)
    now=await _now(session,now)
    row=await session.get(UiDesignerPresence,(document_id,who.participant,client_session))
    # Leaving releases capacity immediately, even after a pointer update. Keep
    # the timestamp as admission throttle evidence; repeats do not extend it.
    if row is None or row.state=='left':return None
    row.state='left';row.x=row.y=None;row.updated_at=now;row.expires_at=now;row.sequence+=1
    await session.flush()
    return {'participant':who.participant,'client_session':client_session,'sequence':row.sequence}


async def event_current(session,document_id,who,participant,client_session,sequence,now=None):
    now=await _now(session,now);await _document(session,document_id,who)
    row=await session.get(UiDesignerPresence,(document_id,participant,client_session))
    if row is None or row.sequence!=sequence:return None
    actor=await _actor(session,row,now,{})
    if not actor:return None
    if row.state=='left' and now-_utc(row.updated_at)<=LEFT_RETENTION:
        return {'participant':row.participant,'client_session':row.client_session,'sequence':row.sequence,'state':'left'}
    if _utc(row.expires_at)<=now:return None
    return _view(row,writable=actor[1])
