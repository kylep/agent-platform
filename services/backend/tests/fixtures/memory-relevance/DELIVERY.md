# Memory acceptance and delivery diagnostic v1

CA-3, CA-5 and CA-7 exposed one missing assurance: finding a source did not prove
its useful passage survived formatting and residual budgets. Source ranking,
passage preservation, and answer applicability are separate contracts.

`evidence-v1.json` adds independently specified canonical evidence spans to the
unchanged CA-4 and CA-6 source fixtures. Short relevant sources require their
whole text; long sources require the explicit answer clause. The sidecar checks
the original fixture hash. These annotations were added after observing earlier
results: they are diagnostic, not an untouched held-out answer benchmark.

`bin/ap-memory-relevance-eval` now resolves current source revisions, invokes the
production selector, and freezes a real detached startup packet for each ranking.
It checks canonical slicing, record/content limits and required-span containment.
It reports candidate top-three source coverage, explicit top-three annotated
span coverage, and detached packet span coverage separately. Historical ranking
comparisons use the current delivery implementation with each ranking's hints;
they do not replay a historical formatter. No answer-level model grading occurs.

Replay against `88cbe4c`, using the previously captured pinned vectors and
unchanged 37-source/40-query CA-6 corpus:

```sh
services/backend/.venv/bin/python bin/ap-memory-relevance-eval \
  --fixture services/backend/tests/fixtures/memory-relevance/abstention-v1.json \
  --vectors /private/tmp/ap-ca6-vectors.json --baseline-ref 88cbe4c
```

Across its 21 positive development/held-out questions, unchanged hybrid source
coverage and explicit delivered span coverage are both 20/21; detached coverage
is 21/21. Lexical-only coverage is 17/21 for explicit delivery and 19/21 for
detached delivery. The development security-review question misses its source in
the top three; detached pins can recover it. These aggregate diagnostics do not
replace the original frozen CA-6 split or justify tuning on its held-out cases.
Ranking is unchanged by CA-7; these numbers expose, rather than fix, its misses.

The budget matrix in `test_memory_delivery.py` tests actual required-span
boundaries with ASCII/Unicode prefixes, dotted values, URLs with query
punctuation, versions, decimals, sentence/line endings, substring queries, empty
sources, later fitting records and invalid/stale locations. Each value is tested
at 1, 18, one below/exactly/one above its required length, 100 and 2,000 remaining
codepoints. API and real-PostgreSQL detached capture separately verify omission
at 18 and complete delivery at 100. Existing authority, namespace, stale revision,
record-limit and immutable-capture tests still apply.

The remaining quality limitations are explicit: a chunk/structural unit is not a
complete fact; quoted questions, negation and qualifications need applicability
judgment; nearest neighbors may be irrelevant; a verbose prompt can bury the
question beyond the query prefix; pinned records can exhaust startup space.
The query truncation flag makes the prefix loss observable. Changing query
construction, reserving task-evidence space or filtering provenance categories
requires a separately measured coverage tradeoff. Never infer a preference from a
review mentioning its name, or blanket-hide receipts that explicit receipt
questions need.

Close a correctness fix when its stated invariants and API/deployed path pass.
Record retrieval limitations with their measured scope. A new issue should show
a reproduced invariant violation, a measured regression, or a new product
requirement; an unconstrained request for another criticism is not a stable
acceptance gate. Future applicability work needs fresh isolated examples,
including quoted-but-unanswered queries and a question after the byte cutoff.
