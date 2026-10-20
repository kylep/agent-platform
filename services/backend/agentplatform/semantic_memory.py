"""Versioned rebuildable pgvector index, bounded reconciliation and shared hybrid policy.

No inference occurs in a transaction holding authority/definition locks. Indexing
is a durable reconciliation of canonical rows, including direct memory-tool writes.
Exact pgvector search filters namespace, generation and live revisions before LIMIT.
"""
import asyncio
import hashlib
import json
import math
import httpx
from sqlalchemy import case, func, literal, or_, select, text
from agentplatform.db import Memory
from agentplatform.persona_memory import chunk_ranges, source_hash
from agentplatform.memory_relevance import query_terms, lexical_start, coverage

MODEL = 'embeddinggemma:300m-qat-q8_0'
ARTIFACT = 'e84a7acc23943b7a589852cf6da122f0b925631b7884f297a001303dff54ffe6'
PREPROCESS = 'gemma-search-v1-utf8-1024'
GENERATION = ARTIFACT + ':' + PREPROCESS
DIMENSIONS = 768


def bounded(value, size=1024):
    # At most 1024 UTF-8 bytes plus a fixed ASCII task prefix. Even byte-level
    # tokenization fits the 2048-token model context. Never truncate in Ollama.
    return value.encode()[:size].decode('utf-8', errors='ignore')


def chunks(memory):
    return [memory.content[start:end] for start, end in chunk_ranges(memory.content)] or ['']


async def embed(settings, inputs):
    if not 1 <= len(inputs) <= 16:
        raise ValueError('embedding batch size')
    async with httpx.AsyncClient(timeout=settings.memory_embedding_timeout_seconds,
                                 trust_env=False) as client:
        tags = (await client.get(settings.memory_embedding_url + '/api/tags'))
        tags.raise_for_status()
        if not any(m.get('name') == MODEL and m.get('digest', '').removeprefix('sha256:') == ARTIFACT
                   for m in tags.json().get('models', [])):
            raise ValueError('embedding model identity mismatch')
        response = await client.post(settings.memory_embedding_url + '/api/embed', json={
            'model': MODEL, 'input': inputs, 'truncate': False, 'keep_alive': -1,
            'options': {'num_gpu': 0, 'num_thread': 1}})
        response.raise_for_status()
        vectors = response.json()['embeddings']
    if len(vectors) != len(inputs) or any(len(v) != DIMENSIONS or
            any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('invalid embedding dimensions/values')
    return vectors


async def migrate(engine):
    """Explicit opt-in migration; never required by disabled init_db/SQLite.

    Uses the platform migration advisory lock. Extension/index owned by the
    configured platform migration role, with no grants to the memory Tool role.
    """
    from agentplatform.db import INIT_DB_LOCK_KEY
    async with engine.begin() as c:
        if c.dialect.name != 'postgresql':
            raise RuntimeError('semantic memory requires PostgreSQL with pgvector')
        await c.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': INIT_DB_LOCK_KEY})
        await c.execute(text('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public'))
        await c.execute(text('''CREATE TABLE IF NOT EXISTS public.memory_vectors_v1 (
            memory_id varchar(32) NOT NULL, agent varchar(128) NOT NULL,
            source_hash varchar(64) NOT NULL, content_hash varchar(64) NOT NULL,
            source_updated_at timestamptz NOT NULL, generation varchar(128) NOT NULL,
            chunk integer NOT NULL, dimensions integer NOT NULL CHECK (dimensions=768),
            embedding public.vector(768) NOT NULL,
            PRIMARY KEY(memory_id, generation, chunk))'''))
        await c.execute(text('CREATE INDEX IF NOT EXISTS memory_vectors_v1_owner ON public.memory_vectors_v1(agent,generation)'))
        await c.execute(text('''CREATE TABLE IF NOT EXISTS public.memory_embedding_failures_v1 (
            memory_id varchar(32) NOT NULL, generation varchar(128) NOT NULL,
            source_updated_at timestamptz NOT NULL, attempts integer NOT NULL,
            retry_at timestamptz NOT NULL, PRIMARY KEY(memory_id,generation))'''))


async def ready(session):
    if session.bind.dialect.name != 'postgresql' or not (await session.execute(text(
            "SELECT to_regclass('public.memory_vectors_v1')"))).scalar():
        raise RuntimeError('semantic memory migration/pgvector not provisioned')


async def require_storage(engine):
    async with engine.connect() as c:
        if c.dialect.name != 'postgresql':
            raise RuntimeError('semantic memory requires PostgreSQL/pgvector migration')
        if not (await c.execute(text("SELECT to_regclass('public.memory_vectors_v1')"))).scalar():
            raise RuntimeError('semantic memory migration/pgvector not provisioned')


async def reconcile(sf, settings, batch=4):
    """Restart-safe, bounded incremental backfill; source changes win over inference.

    Anti-join the current source revisions. Deletion excludes rows immediately
    through retrieval's join; cleanup here is independent of model availability.
    """
    async with sf() as s:
        await ready(s)
        await s.execute(text('DELETE FROM public.memory_vectors_v1 v WHERE NOT EXISTS '
                            '(SELECT 1 FROM tool_memory.memories m WHERE m.id=v.memory_id)'))
        rows = (await s.execute(select(Memory).where(Memory.content != '', Memory.agent.in_(settings.memory_semantic_agents.split(',')),
            ~Memory.id.in_(select(text('memory_id')).select_from(text('public.memory_vectors_v1'))
                .where(text('generation=:generation AND source_updated_at=tool_memory.memories.updated_at '
                            "AND content_hash=encode(sha256(convert_to(tool_memory.memories.content,'UTF8')),'hex')"))))
            .where(~Memory.id.in_(select(text('memory_id')).select_from(text('public.memory_embedding_failures_v1'))
                .where(text('generation=:generation AND source_updated_at=tool_memory.memories.updated_at AND retry_at>now()'))))
            .params(generation=GENERATION).order_by(Memory.updated_at, Memory.id).limit(batch))).scalars().all()
        await s.commit()
    indexed = 0
    failures = 0
    for memory in rows:
        revision = source_hash(memory)
        try:
            index_settings = settings.model_copy(update={
                'memory_embedding_timeout_seconds': settings.memory_index_embedding_timeout_seconds})
            vectors = await embed(index_settings, ['title: ' + bounded(memory.key or 'none', 128) + ' | text: ' + t
                                             for t in chunks(memory)])
        except (httpx.HTTPError, TimeoutError, ValueError):
            # A bad record/outage must not starve the rest of the durable scan.
            async with sf() as s:
                await s.execute(text('''INSERT INTO public.memory_embedding_failures_v1
                    (memory_id,generation,source_updated_at,attempts,retry_at)
                    VALUES (:id,:g,:updated,1,now()+interval '5 minutes')
                    ON CONFLICT (memory_id,generation) DO UPDATE SET
                        source_updated_at=EXCLUDED.source_updated_at,
                        attempts=memory_embedding_failures_v1.attempts+1,
                        retry_at=now()+interval '5 minutes' '''),
                    {'id': memory.id, 'g': GENERATION, 'updated': memory.updated_at})
                await s.commit()
            failures += 1
            continue
        async with sf() as s:
            current = await s.get(Memory, memory.id, with_for_update=True)
            if not current or source_hash(current) != revision:
                continue
            await s.execute(text('DELETE FROM public.memory_vectors_v1 WHERE memory_id=:id AND generation=:g'),
                            {'id': memory.id, 'g': GENERATION})
            for i, vector in enumerate(vectors):
                await s.execute(text('''INSERT INTO public.memory_vectors_v1
                    (memory_id,agent,source_hash,content_hash,source_updated_at,generation,chunk,dimensions,embedding)
                    VALUES (:id,:agent,:hash,:content_hash,:updated,:g,:chunk,768,CAST(:vector AS public.vector))'''),
                    {'id': memory.id, 'agent': memory.agent, 'hash': revision,
                     'content_hash': hashlib.sha256(memory.content.encode()).hexdigest(),
                     'updated': memory.updated_at, 'g': GENERATION, 'chunk': i,
                     'vector': json.dumps(vector)})
            await s.execute(text('DELETE FROM public.memory_embedding_failures_v1 WHERE memory_id=:id AND generation=:g'),
                            {'id': memory.id, 'g': GENERATION})
            await s.commit()
            indexed += 1
    if failures:
        raise RuntimeError(f'embedding reconciliation degraded: {failures} records deferred')
    return indexed


async def prepare(sf, settings, agent, query, team_id=None, project_id=None):
    """Return ranked revision references, never a privileged cached source payload.

    RRF k=60 over IDF-weighted lexical coverage and cosine ranks (20 each).
    Exact full key first. Startup mandatory pins are applied by capture later.
    """
    status = {'policy': 'hybrid-rrf60-idf-v2', 'preprocessing': PREPROCESS,
              'model': MODEL, 'artifact': ARTIFACT, 'generation': GENERATION,
              'state': 'lexical', 'reason': 'disabled',
              'relevance': {'state': 'unassessed', 'reason': 'no_validated_abstention_policy'}}
    original = query or ''
    query = bounded(original)
    status['query'] = {'input_utf8_bytes': len(original.encode('utf-8')),
                       'used_utf8_bytes': len(query.encode('utf-8')),
                       'truncated': original != query}
    if not query.strip():
        return [], {**status, 'reason': 'empty_query'}
    conditions = [Memory.agent == agent, Memory.content != '']
    if team_id:
        conditions.append(Memory.team_id == team_id)
    if project_id:
        conditions.append(Memory.project_id == project_id)
    async with sf() as s:
        terms = query_terms(query)
        matches = [or_(func.lower(Memory.content).contains(term, autoescape=True),
                       func.lower(Memory.key).contains(term, autoescape=True)) for term in terms]
        stats = (await s.execute(select(func.count(Memory.id),
            *(func.sum(case((match, 1), else_=0)) for match in matches)).where(*conditions))).one()
        weights = [math.log(1 + (stats[0] + 1) / ((df or 0) + 1)) for df in stats[1:]]
        relevance = sum((weight * case((match, 1), else_=0)
                         for weight, match in zip(weights, matches)), literal(0.0))
        lexical = (await s.execute(select(Memory).where(*conditions,
            or_(Memory.key == query, *matches))
            .order_by(func.coalesce(Memory.key == query, False).desc(), relevance.desc(),
                      Memory.updated_at.desc(), Memory.id.desc()).limit(20))).scalars().all()
    semantic, hits = [], []
    if settings.memory_semantic_enabled and agent in settings.memory_semantic_agents.split(','):
        try:
            async with asyncio.timeout(settings.memory_embedding_timeout_seconds):
                vector = (await embed(settings, ['task: search result | query: ' + query]))[0]
                async with sf() as s:
                    await ready(s)
                    scope = ''
                    params = {'agent': agent, 'g': GENERATION, 'v': json.dumps(vector)}
                    for field, value in [('team_id', team_id), ('project_id', project_id)]:
                        if value:
                            scope += f' AND m.{field}=:{field}'
                            params[field] = value
                    hits = (await s.execute(text('''SELECT memory_id, source_hash, chunk, distance FROM (
                        SELECT DISTINCT ON (v.memory_id) v.memory_id, v.source_hash, v.chunk,
                        v.embedding <=> CAST(:v AS public.vector) AS distance
                        FROM public.memory_vectors_v1 v JOIN tool_memory.memories m ON m.id=v.memory_id
                        WHERE v.agent=:agent AND m.agent=:agent AND m.content<>'' AND v.generation=:g
                          AND v.source_updated_at=m.updated_at
                          AND v.content_hash=encode(sha256(convert_to(m.content,'UTF8')),'hex')'''
                        + scope + ' ORDER BY v.memory_id,distance,v.chunk) matched '
                        'ORDER BY distance,memory_id LIMIT 20'), params)).all()
                    semantic = [(r.memory_id, r.source_hash) for r in hits]
            status.update(state='hybrid', reason='')
        except (httpx.HTTPError, TimeoutError, ValueError, RuntimeError):
            status.update(state='degraded', reason='embedding_unavailable_or_incompatible')
    scores, hashes = {}, {}
    for ranking in [[(m.id, source_hash(m)) for m in lexical], semantic]:
        for rank, (mid, revision) in enumerate(ranking, 1):
            scores[mid] = scores.get(mid, 0) + 1 / (60 + rank)
            hashes[mid] = revision
    exact = {m.id for m in lexical if m.key == query}
    hints = {m.id: {'match_start': lexical_start(m.content, terms, weights),
                    'match_terms': terms, 'match_weights': weights} for m in lexical}
    for m in lexical:
        if m.id in exact or not coverage(m.content, terms, weights):
            hints[m.id] = {'match_key': True}
    lexical_rows = {m.id: m for m in lexical}
    for hit in hits:
        if hit.memory_id not in exact:
            m = lexical_rows.get(hit.memory_id)
            if m is not None and source_hash(m) == hit.source_hash:
                spans = list(chunk_ranges(m.content))
                if 0 <= hit.chunk < len(spans):
                    sem_start = min(max(0, spans[hit.chunk][0] - 160), max(0, len(m.content) - 2000))
                    lex_start = hints[m.id].get('match_start', 0)
                    # Keep stronger literal evidence, including matches beyond
                    # the bounded embedding index; otherwise use the winning
                    # semantic passage for paraphrases with no literal overlap.
                    if coverage(m.content[lex_start:lex_start+2000], terms, weights) > coverage(
                            m.content[sem_start:sem_start+2000], terms, weights) + 1e-9:
                        continue
            hints[hit.memory_id] = {'match_chunk': hit.chunk,
                                  'match_terms': terms, 'match_weights': weights}
    ranks = {mid: {'lexical_rank': next((i for i, m in enumerate(lexical, 1) if m.id == mid), None),
                   'semantic_rank': next((i for i, pair in enumerate(semantic, 1) if pair[0] == mid), None),
                   'rrf_score': scores[mid]} for mid in scores}
    status.update(lexical_candidates=len(lexical), semantic_candidates=len(semantic))
    return [{'id': mid, 'source_hash': hashes[mid], **hints.get(mid, {}), **ranks[mid]} for mid in sorted(scores,
        key=lambda mid: (mid not in exact, -scores[mid], mid))[:20]], status


async def resolve(session, agent, references):
    rows = (await session.execute(select(Memory).where(Memory.agent == agent,
        Memory.id.in_([r['id'] for r in references])))).scalars().all()
    live = {m.id: m for m in rows}
    return [live[r['id']] for r in references if r['id'] in live
            and source_hash(live[r['id']]) == r['source_hash']]


async def main():
    import sys
    from agentplatform.config import Settings
    from agentplatform.db import make_engine, make_session_factory
    settings = Settings()
    engine = make_engine(settings.db_url)
    try:
        if '--migrate' in sys.argv:
            await migrate(engine)
        elif settings.memory_semantic_enabled:
            print(json.dumps({'indexed': await reconcile(make_session_factory(engine), settings)}))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
