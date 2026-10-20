"""Own-namespace startup selection. Labels are not ACLs.

v2 retains ordering/budgets, skips empty notes and exposes bounded excerpts.
A single statement ranks both pinned (10) and general (20) nonempty candidates.
"""
import hashlib
import json
from sqlalchemy import case, func, or_, select
from agentplatform.db import Memory

POLICY = 'persona-memory-v3-budgeted-excerpts'
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
                               order_by=order).label('pin')).where(Memory.agent == agent, Memory.content != '').subquery()
    rows = (await session.execute(select(Memory).join(ranked, Memory.id == ranked.c.id)
        .where(or_(ranked.c.recent <= 20, pinned & (ranked.c.pin <= 10)))
        .order_by(*order))).scalars().all()
    # Stable pinned-first partition, retaining recency and ID ordering.
    return sorted(rows, key=lambda m: not (m.key or '').startswith(PREFIXES))


def chunk_ranges(content):
    """The existing UTF-8 preprocessing, with codepoint offsets for delivery."""
    raw, start, count = content.encode(), 0, 0
    while raw and count < 16:
        piece = raw[:1024].decode('utf-8', errors='ignore')
        end = start + len(piece)
        yield start, end
        raw, start, count = raw[len(piece.encode()):], end, count + 1


def select_records(rows, references=(), limit=20):
    seed, provenance, remaining = [], [], 12000
    hints = {r['id']: r for r in references}
    for m in rows:
        if remaining <= 0 or len(seed) >= min(20, limit):
            break
        if not m.content:
            continue
        revision, size = source_hash(m), min(2000, remaining)
        hint, start, method = hints.get(m.id, {}), 0, 'prefix'
        required = None
        if hint.get('source_hash') == revision:
            if hint.get('match_key'):
                method = 'key_match'
            elif type(hint.get('match_chunk')) is int:
                ranges = list(chunk_ranges(m.content))
                if 0 <= hint['match_chunk'] < len(ranges):
                    required = ranges[hint['match_chunk']]
                    padding = min(160, max(0, size - (required[1] - required[0])) // 2)
                    start, method = max(0, required[0] - padding), 'semantic_chunk'
            elif type(hint.get('match_start')) is int:
                start, method = max(0, hint['match_start']), 'lexical_match'
                # Old hints have no match location inside their 2,000-char
                # window. They remain usable at full size, never as evidence
                # for an arbitrarily shortened prefix.
                required = (start, min(len(m.content), start + 2000))
            if hint.get('match_terms') and (method == 'lexical_match' or
                    required and required[1] - required[0] > size):
                from agentplatform.memory_relevance import lexical_window
                window = lexical_window(m.content, hint['match_terms'], hint['match_weights'], size)
                if window:
                    start, lo, hi = window
                    required, method = (lo, hi), 'lexical_match'
                elif method == 'lexical_match':
                    required = (0, size + 1)
            if required and required[1] - required[0] > size:
                provenance.append({'id': m.id, 'source_hash': revision,
                    'updated_at': m.updated_at.isoformat(), 'outcome': 'omitted',
                    'reason': 'matching_evidence_exceeds_remaining_budget',
                    'available_codepoints': size, 'method': method})
                continue
        start = min(start, max(0, len(m.content) - size))
        content = m.content[start:start + size]
        excerpt = {'start': start, 'end': start + len(content), 'source_length': len(m.content),
                   'unit': 'unicode_codepoints', 'method': method}
        seed.append({'id': m.id, 'key': m.key, 'content': content,
                     'updated_at': m.updated_at.isoformat(), 'tags': m.tags,
                     'source_hash': revision, 'truncated': len(content) < len(m.content), 'excerpt': excerpt})
        provenance.append({'id': m.id, 'updated_at': m.updated_at.isoformat(),
            'source_hash': revision, 'delivered_hash': digest(seed[-1]),
            'position': len(seed) - 1, 'truncated': len(content) < len(m.content), 'excerpt': excerpt})
        remaining -= len(content)
    return seed, provenance


async def select_memory(session, agent):
    return select_records(await candidates(session, agent))
