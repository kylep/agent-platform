"""Local persona authority. No credential in this module is a legacy API key.

The central route fence is deliberately finite: adding an API route cannot
accidentally give a laptop persona management authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, or_, and_

from agentplatform.apikeys import hash_token
from agentplatform.authority import expired
from agentplatform.db import (ACTIVE_STATES, AgentDef, ChatIdentity, Conversation,
    LocalPersonaCredential, LocalPersonaEnrollment, LocalPersonaSession,
    RelayParticipant, Run, RunState, utcnow)

ENROLLMENT_PREFIX = 'lpe_'
BEARER_PREFIX = 'lps_'
RENEWAL_PREFIX = 'lpr_'
SESSION_HOURS = 12
TOKEN_MINUTES = 15
DRAIN_MINUTES = 60


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


# Each expression is a full match, method-specific, and paired with a grant.
# Resource ACLs and action/role checks still execute inside the target API.
ROUTES = (
    ('GET', r'/api/whoami', None),
    ('POST', r'/api/memory-hybrid', 'memory'),
    ('GET', r'/api/runs(?:/[a-f0-9]{32})?', 'runs_read'),
    ('GET', r'/api/tags', 'runs_read'),
    ('GET', r'/api/agents(?:/[a-z0-9-]+(?:/versions(?:/[0-9]+)?)?)?', None),
    ('POST', r'/api/agents', 'agents_edit'),
    ('PUT', r'/api/agents/[a-z0-9-]+', ('agents_edit', 'agents_grant')),
    ('DELETE', r'/api/agents/[a-z0-9-]+', 'agents_edit'),
    ('POST', r'/api/app-data/agent/apps/(?:schema|list|create|get|propose|proposal|draft|notes|validate|preview|publish|rollback|retire|authority|health)', 'apps'),
    ('POST', r'/api/app-data/agent/records/(?:describe|query|get|history|version|create|update|delete|transaction|delete_preview|request_refresh|staging/(?:open|stage|commit|abandon))', 'app_data'),
    ('POST', r'/api/runs/[a-f0-9]{32}/annotate', 'runs_write'),
    ('GET', r'/api/metrics/(?:agents|tools|overview)', 'metrics'),
    ('GET', r'/api/health/kafka', 'metrics'),
    ('POST', r'/api/health/incidents', 'health_incident'),
    ('GET', r'/api/apps/[a-z0-9-]+/query/[a-zA-Z0-9_/-]+', 'query_app'),
    ('GET', r'/api/agent-self(?:/models)?', 'agent_self'),
    ('PATCH', r'/api/agent-self', 'agent_self'),
    ('PUT', r'/api/agent-self/avatar', 'agent_self'),
    ('GET', r'/api/relay/(?:channels|search)', 'relay'),
    ('GET', r'/api/relay/channels/[a-f0-9]{32}(?:/messages)?', 'relay'),
    ('POST', r'/api/relay/channels/[a-f0-9]{32}/messages', 'relay'),
    ('POST', r'/api/relay/(?:dm|messages/[a-f0-9]{32}/reactions)', 'relay'),
    ('GET', r'/api/tickets(?:/[A-Z0-9-]+)?', 'tickets'),
    ('POST', r'/api/tickets(?:/[A-Z0-9-]+/(?:move|assign|comments))?', 'tickets'),
    ('PATCH', r'/api/tickets/[A-Z0-9-]+', 'tickets'),
    ('GET', r'/api/wiki/(?:wanted|pages(?:/[a-z0-9/-]+)?(?:/history)?)', 'wiki'),
    ('POST', r'/api/wiki/(?:pages|promote|pages/[a-z0-9/-]+/(?:move|restore|append))', 'wiki'),
    ('PUT', r'/api/wiki/pages/[a-z0-9/-]+', 'wiki'),
    ('DELETE', r'/api/wiki/pages/[a-z0-9/-]+', 'wiki'),
    ('GET', r'/api/external-chat/(?:identities|endpoints|messages|scan|deliveries/[a-f0-9]{32})', 'discord'),
    ('POST', r'/api/external-chat/(?:send|scan/ack)', 'discord'),
    ('GET', r'/api/artifacts(?:/[a-f0-9]{32}(?:/content)?)?', 'artifacts'),
    ('POST', r'/api/artifacts', 'artifacts'),
    ('DELETE', r'/api/artifacts/[a-f0-9]{32}', 'artifacts'),
    ('GET', r'/api/artifacts/models', 'image_gen'),
    ('POST', r'/api/artifacts/generate', 'image_gen'),
    ('GET', r'/api/quota', 'get_quota_usage'),
    ('POST', r'/api/quota/refresh', 'get_quota_usage'),
    ('GET', r'/api/quota/ok', 'quota_ok'),
    # These APIs have their own exhaustive verb/grant policies.
    ('GET', r'/api/tasks(?:/[a-f0-9]{32}(?:/events)?|/models)?', 'tasks'),
    ('POST', r'/api/tasks(?:/[a-f0-9]{32}/cancel)?', 'tasks'),
    ('PATCH', r'/api/tasks/[a-f0-9]{32}', 'tasks'),
)
# Reuse the authoritative registry for definition and App verbs, but snapshot
# exact existing path/method pairs (populated below after route inspection).


def route_allowed(method, path, tools, *, delegated_mint=False):
    if delegated_mint and method == 'POST' and path == '/api/tool-calls':
        return True
    return any(method == m and re.fullmatch(pattern, path) and
               (grant is None or any(f'mcp__platform__{g}' in tools for g in (grant if isinstance(grant, tuple) else (grant,))))
               for m, pattern, grant in ROUTES)


async def access_snapshot(s, agent):
    from agentplatform.external_chat import ExternalAccess
    identities = list((await s.execute(select(ChatIdentity).where(
        ChatIdentity.owner_agent == agent))).scalars())
    owned = {i.id: i.ownership_generation for i in identities
             if i.status not in ('deleted', 'deleting')}
    accesses = list((await s.execute(select(ExternalAccess).where(
        ExternalAccess.identity_id.in_(owned)))).scalars()) if owned else []
    # Lease timestamps are deliberately absent: lease refresh/lapse is benign.
    endpoints = {json.dumps([a.identity_id, a.endpoint_id], separators=(',', ':')):
                 [a.can_read, a.can_history, a.can_send] for a in accesses}
    explicit = select(RelayParticipant.channel_id).where(
        RelayParticipant.participant == f'agent:{agent}')
    rooms = list((await s.execute(select(Conversation.id).where(
        or_(Conversation.id.in_(explicit), and_(Conversation.kind == 'channel', Conversation.open.is_(True))),
        Conversation.home == 'relay'))).scalars())
    return {'owned': owned, 'endpoints': endpoints, 'rooms': sorted(rooms)}


def lost_access(before, after):
    if not before or not set(before['rooms']) <= set(after['rooms']):
        return True
    if any(after['owned'].get(k) != v for k, v in before['owned'].items()):
        return True
    return any(any(old and not new for old, new in zip(values, after['endpoints'].get(k, [False]*3)))
               for k, values in before['endpoints'].items())


async def enrollment_live(s, row, *, upload=False):
    enrollment = await s.get(LocalPersonaEnrollment, row.enrollment_id)
    return (enrollment is not None and enrollment.revoked_at is None and
            not expired(enrollment.expires_at) and row.revoked_at is None and
            (not expired(row.expires_at) or
             (upload and row.state == 'draining' and not expired(row.drain_until))))


async def grants_now(s, row, *, refresh=False):
    """Domain authority, never upload authority. Caller commits state changes."""
    agent = await s.get(AgentDef, row.agent)
    if not agent or not agent.enabled or agent.agent_type != 'persona' or agent.system_source:
        await drain(s, row)
        raise HTTPException(403, 'persona access ended; captured history may still sync')
    current = await access_snapshot(s, row.agent)
    if lost_access(row.access_snapshot, current):
        await drain(s, row)
        raise HTTPException(403, 'persona access changed; start a fresh conversation')
    if row.state != 'active':
        raise HTTPException(409, 'persona domain authority is suspended; use ap-persona resume')
    if (agent.authorization_generation or 0) != row.authorization_generation:
        if not refresh:
            raise HTTPException(409, 'persona context changed; synchronize to reauthorize')
        row.authorization_generation = agent.authorization_generation or 0
        row.context_stale = True
        row.access_snapshot = current
        run = await s.get(Run, row.run_id)
        run.authorization_generation = row.authorization_generation
    row.access_snapshot = current
    return sorted(set(row.frozen_tools) & set(agent.platform_tools or []))


async def run_authorized(s, run, tool=None):
    row = await s.get(LocalPersonaSession, run.local_persona_session_id)
    if (not row or row.run_id != run.id or row.writer_epoch != run.local_persona_epoch
            or run.state not in ACTIVE_STATES or not await enrollment_live(s, row)):
        return False
    try:
        tools = await grants_now(s, row)
    except HTTPException:
        return False
    return tool is None or f'mcp__platform__{tool}' in tools


async def drain(s, row, state='draining'):
    row.state = state
    row.drain_until = utcnow() + timedelta(minutes=DRAIN_MINUTES)
    run = await s.get(Run, row.run_id)
    if run and run.state in ACTIVE_STATES:
        run.state = RunState.INTERRUPTED
        run.finished_at = utcnow()


async def new_run(s, row, enrollment):
    run = Run(agent=row.agent, runtime=row.harness, trigger='local_persona',
              requested_by=f'device:{enrollment.device_id}'[:128], initiated_by=enrollment.initiated_by,
              conversation_id=row.conversation_id, prompt='', state=RunState.RUNNING,
              execution_location='laptop', local_persona_session_id=row.id,
              local_persona_epoch=row.writer_epoch, started_at=utcnow(),
              authorization_generation=row.authorization_generation)
    s.add(run)
    await s.flush()
    row.run_id = run.id


async def mint(s, row):
    token = BEARER_PREFIX + secrets.token_urlsafe(32)
    deadline = row.drain_until if row.state == 'draining' else row.expires_at
    expiry = min(deadline.replace(tzinfo=utcnow().tzinfo),
                 utcnow() + timedelta(minutes=TOKEN_MINUTES))
    s.add(LocalPersonaCredential(token_hash=hash_token(token), session_id=row.id,
                                writer_epoch=row.writer_epoch, expires_at=expiry))
    return {'token': token, 'expires_at': expiry.isoformat(), 'session_id': row.id,
            'writer_epoch': row.writer_epoch, 'agent': row.agent, 'harness': row.harness,
            'conversation_id': row.conversation_id}


async def resolve(s, token, *, upload=False, refresh=False, status=False):
    credential = await s.get(LocalPersonaCredential, hash_token(token))
    if not credential or expired(credential.expires_at):
        raise HTTPException(401, 'persona credential expired')
    row = await s.get(LocalPersonaSession, credential.session_id, with_for_update=True)
    if (not row or row.writer_epoch != credential.writer_epoch or
            not await enrollment_live(s, row, upload=upload)):
        raise HTTPException(401, 'persona credential revoked or superseded')
    if upload:
        if not status and (row.state == 'finalized' or (row.state == 'draining' and expired(row.drain_until))):
            raise HTTPException(409, 'recording finalized; use ap-persona sync --recover')
        tools = []
    else:
        tools = await grants_now(s, row, refresh=refresh)
    row.last_seen_at = utcnow()
    return row, tools


async def authenticate(request, token, *, delegated_mint=False):
    from agentplatform.agentspec import platform_token_role
    if not request.app.state.settings.local_persona_enabled:
        raise HTTPException(403, 'local persona domain access is disabled')
    async with request.app.state.session_factory() as s:
        try:
            row, tools = await resolve(s, token)
            if not route_allowed(request.method, request.url.path, tools, delegated_mint=delegated_mint):
                raise HTTPException(403, 'route is not available to local personas')
            run = await s.get(Run, row.run_id)
            if not run or not await run_authorized(s, run):
                raise HTTPException(403, 'no active persona execution')
            request.state.auth_kind = 'local_persona'
            request.state.api_key_agent = row.agent
            request.state.api_key_run_id = row.run_id
            request.state.local_persona_session_id = row.id
            request.state.frozen_tools = tools
            enrollment = await s.get(LocalPersonaEnrollment, row.enrollment_id)
            request.state.initiated_by = enrollment.initiated_by
            return f'agent:{row.agent}', platform_token_role(tools) or 'tools'
        finally:
            # Access-loss transitions must persist even when denying a request.
            await s.commit()
