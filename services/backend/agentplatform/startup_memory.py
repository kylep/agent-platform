"""Immutable detached-pilot capture and run-scoped protocol. No network I/O."""
from fastapi import HTTPException
from agentplatform.db import ACTIVE_STATES, RunStartupContext
from agentplatform.persona_memory import POLICY, PREFIXES, canonical, digest, select_records, candidates

MEMORY = 'mcp__platform__memory'


def eligible(run, frozen, current, settings):
    if (run.execution_location != 'cluster' or run.conversation_id or run.parent_run_id
            or run.external_observation_id or run.trigger_message_id
            or run.trigger not in ('manual', 'schedule', 'task') or frozen.result_topic):
        return False, 'unsupported_trigger'
    if run.agent not in {x.strip() for x in settings.startup_memory_pilot_agents.split(',')}:
        return False, 'not_enabled'
    if (frozen.agent_type != 'persona' or current.agent_type != 'persona'
            or MEMORY not in frozen.platform_tools or MEMORY not in (current.platform_tools or [])):
        return False, 'persona_or_grant_missing'
    return True, 'detached_pilot'


async def capture(session, run, frozen, current, settings, prepared=None):
    enabled, reason = eligible(run, frozen, current, settings)
    rows = await candidates(session, run.agent) if enabled else []
    retrieval = {'state': 'lexical', 'reason': 'disabled'}
    if enabled and prepared:
        from agentplatform.semantic_memory import resolve
        references, retrieval = prepared
        relevant = await resolve(session, run.agent, references)
        if len(relevant) != len(references):
            retrieval = {**retrieval, 'state': 'degraded', 'reason': 'source_changed'}
            relevant = []
        pins = [m for m in rows if (m.key or '').startswith(PREFIXES)]
        rows = list({m.id: m for m in [*pins, *relevant, *rows]}.values())
    records, sources = select_records(rows, references=prepared[0] if enabled and prepared else ())
    payload = {'memories': records}
    provenance = {'sources': sources, 'selection_policy': POLICY, 'retrieval': retrieval}
    row = RunStartupContext(run_id=run.id, enabled=enabled, reason=reason,
        protocol=1, policy=POLICY, payload=payload, provenance=provenance,
        digest=digest(payload))
    if enabled and len(canonical({'protocol': 1, 'state': 'enabled', 'digest': row.digest,
        'payload': payload, 'provenance': provenance, 'policy': POLICY, 'reason': reason}).encode()) > 64 * 1024:
        raise ValueError('startup memory envelope exceeds 64 KiB')
    run.startup_memory_enabled = enabled
    session.add(row)


async def envelope(session, run, current, support):
    row = await session.get(RunStartupContext, run.id)
    if not run.startup_memory_enabled:
        if row and row.enabled:
            raise HTTPException(409, 'startup memory state mismatch')
        return {'state': 'disabled'}
    frozen = run.definition_snapshot or {}
    if (run.state not in ACTIVE_STATES or not current or not current.enabled
            or current.agent_type != 'persona' or frozen.get('agent_type') != 'persona'
            or MEMORY not in (current.platform_tools or [])
            or MEMORY not in frozen.get('platform_tools', [])):
        raise HTTPException(403, 'startup memory authority unavailable')
    if support != '1':
        raise HTTPException(409, 'startup memory protocol 1 required')
    if (not row or not row.enabled or row.protocol != 1 or row.digest != digest(row.payload)
            or not isinstance(row.payload, dict) or not isinstance(row.payload.get('memories'), list)):
        raise HTTPException(409, 'invalid captured startup memory')
    if len(canonical({'payload': row.payload, 'provenance': row.provenance}).encode()) > 64 * 1024:
        raise HTTPException(409, 'oversized captured startup memory')
    return {'protocol': 1, 'state': 'enabled', 'digest': row.digest, 'payload': row.payload}
