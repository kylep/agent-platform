#!/usr/bin/env python3
"""Synthetic local-runtime benchmark/quality check. No hosted model calls or DB writes.

Run inside the trusted API pod (or a test container with the configured service).
Output contains only timing, model identity, dimensions and synthetic quality ranks.
"""
import asyncio
import json
import statistics
import time

from agentplatform.config import Settings
from agentplatform.semantic_memory import ARTIFACT, MODEL, embed


async def main():
    settings = Settings()
    docs = [
        'Database changes must preserve existing rows and use additive schema migrations.',
        'Use checked-in Kubernetes resources to declare infrastructure and reconcile it.',
        'Keep weekly running training easy and increase distance gradually.',
        'A guitar has six strings and can accompany a song.',
    ]
    queries = [
        ('How should I safely evolve a populated database?', 0),
        ('How should a new cluster service be provisioned reproducibly?', 1),
        ('How can I build endurance for a race?', 2),
        ('What instrument can accompany singing?', 3),
    ]
    started = time.perf_counter()
    vectors = await embed(settings, ['title: none | text: ' + d for d in docs])
    batch_seconds = time.perf_counter() - started
    ranks, latencies = [], []
    for query, expected in queries:
        started = time.perf_counter()
        value = (await embed(settings, ['task: search result | query: ' + query]))[0]
        latencies.append(time.perf_counter() - started)
        scores = [sum(x*y for x,y in zip(value,v)) for v in vectors]
        ordered = sorted(range(len(scores)), key=lambda i: -scores[i])
        ranks.append(ordered.index(expected)+1)
    for i in range(16):
        started = time.perf_counter()
        await embed(settings, ['task: search result | query: ' + queries[i % 4][0]])
        latencies.append(time.perf_counter()-started)
    result = {'model': MODEL, 'artifact': ARTIFACT, 'dimensions': len(vectors[0]),
        'batch_documents': len(docs), 'batch_seconds': round(batch_seconds,4),
        'warm_p50_seconds': round(statistics.median(latencies),4),
        'warm_p95_seconds': round(sorted(latencies)[18],4), 'expected_ranks': ranks}
    result['accepted'] = all(r == 1 for r in ranks) and result['warm_p95_seconds'] <= 3.0
    print(json.dumps(result))
    if not result['accepted']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
