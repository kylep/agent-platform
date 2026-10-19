# Bounded memory relevance fixture

36 synthetic sources: six older domain notes and thirty newer routine receipt distractors. Eight queries include seven labelled relevance judgments and one unlabelled unrelated question. Grade 3 is the target, 2 a supporting lesson, and 1 related context. No private memories are included.

Comparison on 2026-10-19 against Git `a0226b4`, using real vectors from the pinned CPU embedding model and disposable PostgreSQL/pgvector. RRF k=60 and embedding generation are unchanged. Only lexical tokenization/candidate ordering changes the ranking.

| Policy | nDCG@3 | Recall@3 |
|---|---:|---:|
| baseline_hybrid | 0.923 | 0.952 |
| proposed_hybrid | 1.000 | 1.000 |
| baseline_lexical | 0.537 | 0.714 |
| proposed_lexical | 0.857 | 0.857 |

The explicit receipt query and exact key retain their target at rank 1. The older database lesson moves ahead of receipt churn. The unrelated query has no lexical results; hybrid still returns nearest semantic neighbors. No calibrated similarity cutoff or general judgment claim follows from these eight queries.

Reproduce with `bin/ap-memory-relevance-eval` using the backend Python and either an explicit authorized `--embedding-url` or `--vectors` captured from that model. The command creates and removes its own local PostgreSQL container and prints per-query candidates and rank contributions. It never accepts a production database URL.

Corpus SHA-256: `d6ccc98212284ed89142aa2d52f7ecb4a5b6534cc60e271975acc6d9b5afae70`. Generation: `e84a7acc23943b7a589852cf6da122f0b925631b7884f297a001303dff54ffe6:gemma-search-v1-utf8-1024`.
