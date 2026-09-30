"""Current agent authority, independent of cached manifests and frozen grants."""
from datetime import timezone
from sqlalchemy import select, text
from agentplatform.db import (AgentDef, AgentVersion, ChatIdentity, Conversation,
                              RelayBinding, RelaySession, Run, SchemaMark, utcnow)


def expired(value, now=None):
    if value is None:
        return True
    return value.replace(tzinfo=timezone.utc) <= (now or utcnow())


async def authority_lock(session):
    # One short transaction lock orders agent/account mutations across services.
    if session.bind.dialect.name == 'postgresql':
        await session.execute(text('SELECT pg_advisory_xact_lock(340091)'))


async def current_generation(session, agent):
    await authority_lock(session)
    row = await session.get(AgentDef, agent, with_for_update=True)
    if row is None or not row.enabled:
        return None
    identities = (await session.execute(select(ChatIdentity).where(
        ChatIdentity.owner_agent == agent).with_for_update())).scalars().all()
    for identity in identities:
        if (identity.access_expires_at is not None and expired(identity.access_expires_at)
                and not identity.lease_invalidated):
            row.authorization_generation = (row.authorization_generation or 0) + 1
            identity.lease_invalidated = True
    await session.flush()
    return row.authorization_generation or 0


async def ensure_run_authority(session, run):
    generation = await current_generation(session, run.agent)
    if generation is None or generation != (run.authorization_generation or 0):
        return False
    if run.conversation_id:
        channel = await session.get(Conversation, run.conversation_id)
        if channel and channel.home == 'external':
            from agentplatform.external_chat import can_read_channel
            return await can_read_channel(session, run.agent, channel.id)
    return True


async def credential_secrets(session):
    refs = (await session.execute(select(ChatIdentity.secret_refs))).scalars().all()
    return {'claude-credentials', 'codex-credentials'} | {
        ref['secret'] for identity in refs for ref in (identity or {}).values()
        if isinstance(ref, dict) and isinstance(ref.get('secret'), str)}


def migrate_authority(conn):
    """One-time bridge retirement and ownership conversion. Never rewrite history."""
    from agentplatform.agentdefs import DEF_FIELDS
    from sqlalchemy import func
    for table, columns in ((AgentDef.__table__, {'authorization_generation': 0, 'external_observer': False}),
                           (Run.__table__, {'authorization_generation': 0}),
                           (ChatIdentity.__table__, {'ownership_generation': 0, 'permission_sequence': 0,
                                                   'lease_invalidated': False})):
        for column, value in columns.items():
            conn.execute(table.update().where(table.c[column].is_(None)).values({column: value}))
    mark = 'persona-external-authority-v1'
    if conn.execute(select(SchemaMark).where(SchemaMark.name == mark)).first():
        return
    agents = AgentDef.__table__
    identities = ChatIdentity.__table__
    rows = conn.execute(select(agents)).mappings().all()
    by_name = {row['name']: row for row in rows}
    for identity in conn.execute(select(identities)).mappings().all():
        owners = [r['name'] for r in rows if r['agent_type'] == 'persona'
                  and r['enabled'] and r['discord_identity_id'] == identity['id']]
        owner = 'pai' if identity['id'] == 'discord-default' and 'pai' in by_name else (owners[0] if len(owners) == 1 else None)
        conn.execute(identities.update().where(identities.c.id == identity['id']).values(
            owner_agent=owner, ownership_generation=1, access_expires_at=None))
    changed = set()
    for row in rows:
        tools = [t for t in (row['platform_tools'] or []) if t != 'mcp__platform__discord_chat']
        owned = conn.execute(select(identities.c.id).where(identities.c.owner_agent == row['name'])).scalar()
        if owned and 'mcp__platform__discord' not in tools:
            tools.append('mcp__platform__discord')
        prompt = row['prompt'] or ''
        if row['name'] == 'news':
            prompt = prompt.replace('and posts the rest to Discord.', 'and stores the accepted items in the News app.')
        if row['name'] == 'stockmarket':
            prompt = prompt.replace('report and to Discord,', 'report,')
        if row['name'] == 'pai':
            prompt += '\n\n## Platform communication (design 34)\nUse the discord connector Tool to discover your owned identities and exact endpoints, read permitted history, and send messages. Other workers save platform outputs; use query_app and artifacts to retrieve them. Health intervention tickets arrive internally: inspect their evidence, decide whether/how to notify the human using your memories and preferences, and record your decision in the ticket. Close a handled or deliberately dismissed ticket to stop reminders. External chats in Relay are read-only mirrors; external-turn final answers are sent automatically through your owned account. For an explicit Tool reply to that same turn, pass answer_to with its triggering Relay message ID to prevent duplicates. Never claim a queued send was delivered before its receipt confirms acceptance.\n'
        if (prompt, tools, owned) != (row['prompt'] or '', list(row['platform_tools'] or []),
                                      row['discord_identity_id']):
            changed.add(row['name'])
        conn.execute(agents.update().where(agents.c.name == row['name']).values(
            prompt=prompt, platform_tools=tools, discord_identity_id=owned,
            authorization_generation=(row['authorization_generation'] or 0) + 1))
    # Every generation advances (old sessions lose authority), but only a
    # definition this migration actually rewrote earns a change-log version.
    for row in conn.execute(select(agents).where(agents.c.name.in_(changed))).mappings().all():
        definition = {field: row[field] for field in DEF_FIELDS if row[field] is not None}
        version = conn.execute(select(func.max(AgentVersion.version)).where(AgentVersion.agent == row['name'])).scalar() or 0
        conn.execute(AgentVersion.__table__.insert().values(agent=row['name'], version=version + 1,
            snapshot=definition, changed_by='system', changed_via='persona-authority-migration'))
    conn.execute(RelayBinding.__table__.update().values(status='disabled'))
    # Old resumes cannot carry mixed historical external context into new authority.
    conn.execute(SchemaMark.__table__.insert().values(name=mark))


async def assert_readable_run(session, request, run):
    from fastapi import HTTPException
    caller = getattr(request.state, 'api_key_agent', None)
    if not caller or not run.conversation_id:
        return
    from agentplatform.relay import is_member, participant_of
    from agentplatform.relay_store import enabled_agents, explicit_members
    # One request may filter thousands of tags/artifacts. Resolve the policy
    # once in its transaction, with fresh policy again on the next request.
    policy = session.info.get('run_read_policy')
    if policy is None:
        policy = {'agents': await enabled_agents(session), 'rooms': {}, 'members': {}}
        session.info['run_read_policy'] = policy
    if run.conversation_id not in policy['rooms']:
        policy['rooms'][run.conversation_id] = await session.get(Conversation, run.conversation_id)
        policy['members'][run.conversation_id] = await explicit_members(session, run.conversation_id)
    channel = policy['rooms'][run.conversation_id]
    if channel is None:
        return
    if not is_member(channel, participant_of(agent=caller), policy['agents'],
                     policy['members'][channel.id]):
        raise HTTPException(403, 'Conversation history is outside current agent access')


async def assign_owner(session, identity, owner):
    """Move one account atomically; derived tools are never independent grants."""
    from fastapi import HTTPException
    await authority_lock(session)
    if owner:
        row = await session.get(AgentDef, owner, with_for_update=True)
        if row is None or not row.enabled or row.agent_type != 'persona' or row.system_source:
            raise HTTPException(422, 'Chat accounts require an enabled persona owner')
    if identity.owner_agent == owner:
        return
    prior = identity.owner_agent
    identity.owner_agent = owner
    identity.ownership_generation = (identity.ownership_generation or 0) + 1
    identity.access_expires_at = None
    identity.permission_sequence = 0
    identity.lease_invalidated = False
    await session.flush()
    for name in {prior, owner} - {None}:
        agent = await session.get(AgentDef, name, with_for_update=True)
        if not agent:
            continue
        agent.authorization_generation = (agent.authorization_generation or 0) + 1
        owned = (await session.execute(select(ChatIdentity.id).where(
            ChatIdentity.owner_agent == name, ChatIdentity.status.not_in(['deleted', 'deleting'])))).scalars().all()
        agent.discord_identity_id = owned[0] if owned else None
        tools = [t for t in (agent.platform_tools or [])
                 if t not in ('mcp__platform__discord', 'mcp__platform__discord_chat')]
        if owned:
            tools.append('mcp__platform__discord')
        agent.platform_tools = tools
        from agentplatform.agentdefs import next_version, snapshot_of
        session.add(AgentVersion(agent=name, version=await next_version(session, name),
                                 snapshot=snapshot_of(agent), changed_by='admin', changed_via='chat-owner'))
