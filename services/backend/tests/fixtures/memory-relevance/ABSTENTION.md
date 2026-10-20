# CA-6: held-out abstention evaluation (v1)

The versioned `abstention-v1.json` corpus is separate from CA-4's development
fixture: 37 synthetic sources (including 24 newer routine receipts), 16 development
queries and 24 held-out queries with source-level relevance labels and rationale.
The held-out set has 13 positive and 11 negative questions. It includes near
misses, generic overlap, paraphrases, exact keys, explicit receipts, older
lessons, long sources and direct/verbose pairs. Labels and the split were fixed
before running real embeddings. The same author created both splits; this is not
an independent human benchmark or a statement about all persona memories.

The development comparison considers retaining all candidates and cosine gates
0.35, 0.40, 0.45, 0.50, 0.55 and 0.60, retaining whole-key matches. Selection is
restricted to >=90% useful-answer coverage and successful exact/receipt cases;
then minimize negatives with any returned result, maximize recall@3 and nDCG@3.
Development-only top-relative margin diagnostics (0.05–0.20) retain a
nearest neighbor on every negative; relative rank alone cannot abstain.
Only development results select the threshold. The selected 0.45 policy is then
evaluated once on held-out queries. Neither held-out labels nor production
examples were used to retune it after this check.

Using pinned embeddinggemma:300m-qat-q8_0, unchanged generation
`e84a7acc23943b7a589852cf6da122f0b925631b7884f297a001303dff54ffe6:gemma-search-v1-utf8-1024`
and baseline Git `e197641`:

| Held-out measure | Existing hybrid | Development-selected 0.45 gate |
|---|---:|---:|
| Negative questions returning anything | 11/11 | 1/11 |
| Positive questions with useful top-3 evidence | 13/13 | 10/13 |
| Mean recall@3 (positive queries) | 0.962 | 0.731 |
| Mean nDCG@3 (positive queries) | 0.955 | 0.753 |
| Empty / abstained queries | 0/24 | 13/24 |
| Irrelevant fraction of returned top-3 records | 0.806 | 0.083 |

**Decision: reject the gate; retain the existing ranking.** Coverage falls below
90%. These historical coverage figures score source IDs in the top three, not
delivered evidence or correct answers. The CLI now calls this metric
`positive_source_coverage`. The [delivery diagnostic](DELIVERY.md) separately
checks the passages actually delivered. The rejected gate loses useful API-security and writing paraphrases and the
verbose long-note question; a near-miss espresso-machine ownership question still
gets the coffee preference. Exact-key and explicit receipt cases survive. A valid
source hash is identity/provenance, not relevance confidence. A similarity score
is not a calibrated applicability probability. No prompt extraction/rewrite is
introduced. `retrieval.relevance` explicitly reports `state=unassessed` and
`reason=no_validated_abstention_policy`; callers must decide whether a note
actually answers their question and state absence/uncertainty when it does not.
This change does not claim a solved general no-evidence classifier.

During an embedding outage, existing lexical fallback remains available and
unassessed. On this held-out set it gives 11/13 positive coverage, 8/11 negative
questions with results, and four empty queries. Empty scopes return no candidates;
neither empty candidates nor `state=hybrid` proves a fact is absent/present.
Scope, namespace, live-revision and grant checks remain server-side and precede
returning any source. Immutable historical captures retain their original policy.

Repeat the evaluation with disposable local PostgreSQL and real pinned vectors:

```sh
services/backend/.venv/bin/python bin/ap-memory-relevance-eval \
  --fixture services/backend/tests/fixtures/memory-relevance/abstention-v1.json \
  --baseline-ref e197641 --embedding-url http://127.0.0.1:<authorized-forward> \
  --write-vectors /private/tmp/abstention-v1-vectors.json > /private/tmp/abstention-v1-ranks.json
bin/ap-memory-abstention-eval /private/tmp/abstention-v1-ranks.json
```

Use `--vectors` instead of `--embedding-url` to replay the captured vectors. The
ranking report contains individual lexical/semantic/RRF contributions and cosine
similarities; `abstention-v1-results.json` records development selection and the
frozen held-out result. Local evaluation timings exclude embedding inference
because vectors are replayed; deployed latency and outage observations belong to
the ticket acceptance report. Future tuning needs new held-out queries, rather
than repeatedly fitting this now-observed set.
