# Frozen answer diagnostic v1 (CA-10)

This fresh synthetic corpus adds answer-level checks to the CA-7 production
candidate, excerpt and immutable detached-capture checks. It is separate from
CA-4/CA-6 fixtures and their observed development/held-out sets. Its author also
wrote the gold labels; this is not an independent human benchmark. Once observed,
use it for regression diagnostics only. Future policy selection needs fresh
untouched controls, not a renamed reuse of this set.

Fifteen cases, 25 answer calls total: current behavior first for each case, then
one intervention at a time on selected cases. Four explicit-query comparisons,
three manually curated summaries, three attribution/scope text comparisons.
The direct case is unchanged for the summary and attribution controls, including
the rendered prompt. Query metadata can differ under explicit-query comparison.
The whole-key control uses a fixed caller-provided literal memory_query separate
from execution instructions; other baseline cases use the execution prompt.
There is no automatic query extraction, new ranking, relevance threshold, pin
reservation, schema for semantic categories, or rewritten key prefix.

Coverage: head/middle/tail verbose tasks; older lessons under 32 receipts;
current project correction, historical question and global default; quoted
third-party diagnostics; near-miss and unrelated questions; whole-key and real
receipt questions; long evidence requiring two distant clauses; seven valid
long pins that can exclude task evidence. Author-labelled evidence groups are
AND within a group and OR across sufficient alternatives. Labels were corrected
in read-only methodology review before any answer calls (including a valid
older default citation and a literal-key control). Draft retrieval-only output
was inspected during tooling development; no labels or candidates were tuned
against answer outputs. Do not describe this as an untouched end-to-end holdout.

## Reproduce

Use the backend Python. Docker owns only a new ephemeral local PostgreSQL/
pgvector container; the tool accepts no production DB URL. Pinning is the same
as `bin/ap-persona-postgres-test`. Embeddings must come from the pinned
EmbeddingGemma artifact/generation in production, or its captured vector cache.

```sh
services/backend/.venv/bin/python bin/ap-memory-answer-eval inputs --out /tmp/answer-eval
services/backend/.venv/bin/python bin/ap-memory-answer-eval prepare \
  --embedding-url http://127.0.0.1:<explicit-Ollama-forward> --out /tmp/answer-eval
# Or --vectors <captured-pinned-vectors.json>, not --embedding-url.

# Authenticate through the explicitly chosen API; admin password is env-only.
# This creates up to 25 billed/subscription answer runs, never a recurring Job.
source exports.sh
services/backend/.venv/bin/python bin/ap-memory-answer-eval answer \
  --prepared /tmp/answer-eval/prepared.json \
  --api-url http://127.0.0.1:<explicit-platform-forward> --out /tmp/answer-eval/answers
services/backend/.venv/bin/python bin/ap-memory-answer-eval score \
  --prepared /tmp/answer-eval/prepared.json \
  --results /tmp/answer-eval/answers/answer-ledger.json --out /tmp/answer-eval/rescore
```

Keep the same fixture/tooling files after `inputs`: a differing frozen manifest
requires a new output directory before execution. `frozen.json` versions fixture,
procedure/source hashes, policy, embedding generation, prompts/model and budget.
Gold labels, spans, rationales, review text and outputs are excluded from answer
prompts and retrieval corpus. The current production selector/capture and the
runner's actual `_memory_block` function render each packet locally. Namespace,
canonical slices, budget bounds and capture digests are checked. Foreign-topic
fixtures cannot enter a packet. Full native startup authority is covered by the
platform regression suite and CA-8 live checks, not recreated by this benchmark.

The tool-free answer worker uses explicit Sonnet 5.5, no tools/skills/Secrets,
no result topic or external destination, reader role and a 120-second timeout.
All grants are disabled explicitly. Every run is independent. The worker is
removed in cleanup; run/audit records survive. No production memory rows are
created. Deleting a worker is not erasure of its results. Do not point a ledger
at another agent. An uncertain run submission must be reconciled to its durable
ID before resuming, never automatically resubmitted. Rejected/DLQ/timed-out/
interrupted outcomes terminate; a 180-second polling deadline prevents hangs.
Invalid model/tool/usage outcomes are persisted and cannot be bypassed by rerun.

## Budget and predeclared decisions

Hard cap 28 answer submissions, planned 25, one per case/condition, no retries.
Stop thresholds: 180,000 input tokens including cache read/creation and 8,000
output tokens, with 16,000 input/1,000 output tokens reserved before each call.
These are checked between calls; one in-flight call can overshoot. Actual totals
must be reported. Fresh allowed quota below 50% in both windows is required
before answer execution. Any separate quota probe must be counted separately.
No recurring study or waiting period. Local embedding requests are separate CPU
inference, not answer-model calls; vector-replay timings exclude serving time.

A candidate must retain >=90% useful positive answer coverage, introduce zero
unsupported personal claims/scope errors on tested comparisons, preserve
unchanged controls, add no generation calls and increase median input tokens by
no more than 15% before any broader proposal. Small paired subsets, no repeats,
closed answer vocabulary and only three negatives do not establish a general
non-regression guarantee. No ranking/allocation rollout is authorized by this
fixture. Results can prioritize a separately scoped follow-up or justify keeping
current behavior. Manual curation effort is recorded as edits plus explicitly
estimated minutes, not fabricated stopwatch telemetry.

## Score and limits

Report candidate source coverage, delivery of all required evidence, answer
correctness, grounded correctness, unsupported claims, scope errors, missed
facts, and appropriate uncertainty separately. A lucky correct answer without
its supporting delivered evidence is not grounded. Incorrect values/scopes and
invented citations are unsupported. The grader uses exact synthetic values and
a disclosed closed scope vocabulary; it is not a general natural-language judge.
Format failures stay visible, with raw answers retained for disagreement review.
All per-case results, actual usage/model identities, latency and paired controls
must remain visible. One stochastic answer per condition cannot separate model
variation from an intervention; do not infer speed improvements from these runs.

For incidence, reuse private OPS-40 and startup capture telemetry. Query truncation
with a missing observation is unknown, not false. Pin occupancy is descriptive:
it cannot establish whether unlogged excluded evidence was relevant. Report
counts/denominators and unknowns; no broad new production counters are necessary
for the bounded question answered here.

## Separate counted Messages protocol

The original CLI phase stopped at its predeclared input stop after six valid
answers: 176,389 input tokens including cache. The CLI's approximately 29,000
input tokens per answer dominated the fixture. Its worker was removed and that
phase was not resumed with a larger budget. Its results/cost remain separate.

`bin/ap-memory-answer-messages` is a distinct diagnostic protocol for the same
unchanged frozen labels/packets. It supplies no Tool definitions and bypasses
the CLI harness through the existing platform Claude proxy. Its explicit small
system prefix, requested model, payload hashes and code hash are frozen before
generation. It pre-counts every input via `/v1/messages/count_tokens` and refuses
to generate without a matching count manifest. It does not create a worker or
production memory rows. An explicit localhost-only proxy forward is required;
authentication remains in the proxy and credentials are never exported. Close
that forward after execution.

```sh
services/backend/.venv/bin/python bin/ap-memory-answer-messages count \
  --prepared /tmp/answer-eval/prepared.json \
  --proxy-url http://127.0.0.1:<explicit-Claude-proxy-forward> --out /tmp/messages-eval
source exports.sh
services/backend/.venv/bin/python bin/ap-memory-answer-messages run \
  --prepared /tmp/answer-eval/prepared.json \
  --proxy-url http://127.0.0.1:<explicit-Claude-proxy-forward> \
  --api-url http://127.0.0.1:<explicit-platform-forward> --out /tmp/messages-eval
```

This protocol caps generation at 25 calls, 90,000 input tokens including cache,
and 256 output tokens per request (6,400 total). Pre-counted inputs plus 64-token
per-call headroom must fit before generation. Actual usage above the count
headroom stops the run and cannot be bypassed by resume. No automatic answer
retries. One already submitted request can become uncertain on a network failure;
do not resubmit it. The initial pre-count was 76,414 tokens. Recounting a finalized
budget guard used 25 additional non-generation count requests; both preflights
are disclosed. The frozen core cases/score did not change after CLI answers.

These direct Messages results are not pooled with CLI results and are not an
untouched holdout: six CLI answers existed before the new interface was selected
for efficiency. No ranking, allocation, labels or prompts were tuned against
those answers. Report both procedures and all costs. Do not infer native persona
answer quality from the tool-free diagnostic. Full native startup behavior was
separately exercised by CA-8's isolated live tasks.
