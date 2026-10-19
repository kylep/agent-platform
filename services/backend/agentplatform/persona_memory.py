"""Own-namespace startup selection. Labels are not ACLs.

v1 preserves native ordering/budgets, adding ID-descending timestamp ties.
A single statement ranks both pinned (10) and general (20) candidates.
"""
import hashlib
import json
from sqlalchemy import case, func, or_, select
from agentplatform.db import Memory

POLICY = 'persona-memory-v1'
PREFIXES = ('identity', 'preferences', 'continuity')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def source_hash(memory):
    return digest({'id': memory.id, 'key': memory.key, 'content': memory.content,
                   'tags': memory.tags, 'updated_at': memory.updated_at.isoformat()})


async def candidates(session, agent):
    pinned = or_(*(Memory.key.like(p + '%') for p in PREFIXES))
    order = (Memory.updated_at.desc(), Memory.id.desc())
    ranked = select(Memory.id,
        func.row_number().over(order_by=order).label('recent'),
        func.row_number().over(partition_by=case((pinned, 1), else_=0),
                               order_by=order).label('pin')).where(Memory.agent == agent).subquery()
    rows = (await session.execute(select(Memory).join(ranked, Memory.id == ranked.c.id)
        .where(or_(ranked.c.recent <= 20, pinned & (ranked.c.pin <= 10)))
        .order_by(*order))).scalars().all()
    # Stable pinned-first partition, retaining recency and ID ordering.
    return sorted(rows, key=lambda m: not (m.key or '').startswith(PREFIXES))


def select_records(rows):
    seed, provenance, remaining = [], [], 12000
    for m in rows[:20]:
        content = m.content[:min(2000, remaining)]
        if not content:
            break
        seed.append({'id': m.id, 'key': m.key, 'content': content,
                     'updated_at': m.updated_at.isoformat(), 'tags': m.tags})
        provenance.append({'id': m.id, 'updated_at': m.updated_at.isoformat(),
            'source_hash': source_hash(m), 'delivered_hash': digest(seed[-1]),
            'position': len(seed) - 1, 'truncated': len(content) < len(m.content)})
        remaining -= len(content)
    return seed, provenance


async def select_memory(session, agent):
    return select_records(await candidates(session, agent))
