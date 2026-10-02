# App data performance gate (A12), 2026-10-02

Design 39, "Batch writes" → "Release 1a performance gate": load 10⁶ bars and
10⁶ results, measure the stockmarket, TCMS and backtest read paths, a 140k
backtest write and one TCMS materialization, and set timeouts from the numbers.

**Verdict.** The gate passes after four fixes in `appdata`. Every named
interactive read has p95 < 300 ms, and the 140k staged commit takes 10 s
(target: < 120 s). Before the fixes, it failed on four paths: the 140k commit
(148 s), newest-first reads (300–500 ms), whole-collection scans (they went
quadratic and ran out of the 60 s budget at 611k rows), and the Apps list (12 s).

## How it was measured

`scripts/appdata_perf.py` creates three Apps through `appdata/lifecycle.py`
(`create` → `draft` → `publish`) and loads them with batch jobs
(`open_staging_set`, `stage` in 5,000-record calls, then `commit_staging_set`
per 100k records):

- **stockmarket:**
  - `bars`: symbol (string, `ix_text1`), day (date, `ix_time1`), OHLC numbers
    and volume. It is immutable, with `unique(symbol, day)`.
  - 400 symbols × 2,500 weekdays = 10⁶ records, loaded day-major as daily
    ingestion writes them. That spreads one symbol over the heap, which is the
    slow layout for a chart.
- **tcms:**
  - `results`: run (`ix_text1`), ref (`ix_text2`), status enum, duration and
    finished_at (`ix_time1`). It is immutable, with `unique(run, ref)`.
  - 2,000 runs × 500 results = 10⁶ records over a year: 90% passed, 6%
    failed, 3% skipped, 1% error.
- **news:** 10k items with a title and a 40–80-word summary.

Reads go through `published_view`, the function the API routes call. Each
timing includes the App lookup and `load_app`, but not HTTP or the broker.
Each read ran 50 times with random parameters after 3 warm-ups.

```sh
DOCKER_HOST=unix://$HOME/.rd/docker.sock docker run -d --name ap-a12-perf-pg \
  --memory=2g --cpus=2 --shm-size=1g -e POSTGRES_PASSWORD=perf -e POSTGRES_DB=perf \
  -p 127.0.0.1:55432:5432 postgres:16-alpine -c shared_buffers=512MB \
  -c effective_cache_size=1GB -c work_mem=4MB -c maintenance_work_mem=128MB \
  -c max_wal_size=2GB
cd services/backend && PYTHONPATH=. .venv/bin/python ../../scripts/appdata_perf.py \
  --pg-url postgresql+asyncpg://postgres:perf@127.0.0.1:55432/perf --explain --out perf.json
```

Pass `--scale 0.005 --runs 5` for a smoke run of about 30 s, or `--skip-load`
to reuse loaded Apps.

**Machine:**
- **Host:** Apple M2 (8 cores, 16 GB).
- **Postgres:** 16.15 in Rancher Desktop's VM (2 vCPU, 3.8 GiB), with the
  container capped at 2 GiB and 2 CPUs and 512 MB `shared_buffers`.
- **Client:** the backend ran on the macOS host through the VM's NAT port
  forward, about 0.3–0.5 ms per round trip. That is slower than pod-to-pod on
  the NUC, so per-record write paths are pessimistic here.
- **Pai's NUC differs:** its Postgres is the Bitnami chart with a 2 GiB memory
  limit, the default 128 MB `shared_buffers` and a **2 GiB PVC** (see
  "Storage").

## Results

The baseline is `feat/r1a` at 6a2c9eb. "After" is this branch. Both runs used
the same machine, the same volumes and the default scan budget (2M rows /
60 s). Only the row and write quotas were raised, so the load could exceed
the default 250k records per App.

| Path | Baseline p50 / p95 | After p50 / p95 | Target |
|---|---|---|---|
| **a** chart: one symbol, 12 months (`within_last 12m`, `anchor max(day)`), 2 pages, 261 rows | 15.9 / 49.3 ms | 14.7 / 29.1 ms | < 300 ms ✅ |
| a′ chart, first page only | 7.6 / 8.8 ms | 8.0 / 12.8 ms | ✅ |
| **b** watchlist: latest bar of 20 symbols (20 sequential reads) | 164.5 / 226.0 ms | 60.9 / 79.3 ms | ✅ |
| b′ the same 20 reads concurrently | 189.3 / 223.7 ms | 163.9 / 185.6 ms | ✅ |
| **c** one run's results with status = failed | 4.1 / 4.9 ms | 5.4 / 8.1 ms | ✅ |
| c2 newest failures over all runs (status eq, sort finished_at desc) | 318.5 / 374.0 ms | 5.6 / 8.4 ms | ✅ (was ❌) |
| c3 one ref's history over all runs (ref is `ix_text2`, not leading) | 104.8 / 166.9 ms | 99.3 / 474.4 ms | ⚠️ cold p95 (not a gate path, see below) |
| c4 rare status (1%), default newest-first order | 401.1 / 501.9 ms | 5.5 / 7.4 ms | ✅ (was ❌) |
| **d** news `contains` on title (10k items) | 15.6 / 21.9 ms | 4.3 / 5.3 ms | ✅ |
| d′ `contains` on summary (`text`) | 37.1 / 39.1 ms | 9.6 / 17.6 ms | ✅ |
| **e** 140k backtest batch job (no `unique`): stage / commit | 3.1 s / **148.2 s** | 3.0 s / **10.4 s** | commit < 120 s ✅ (was ❌) |
| e′ 140k commit into a *new* collection with `unique(run, ref)` | 69.6 s | 22.0 s (under cProfile) | ✅ |
| e2 one backtest series, 2,500 rows over 13 pages | 103.7 / 153.7 ms | 106.3 / 139.4 ms | ✅ |
| **f** `app_data scan`: one week of results (19k rows) | 0.60 s | 0.59 s | ✅ |
| f′ `app_data scan`: all 10⁶ results | **refused** `AD-QUOTA-SCAN-TIME` at 611k rows / 60 s (10k rows/s, slowing) | 29.5 s (33.9k rows/s) | under the 60 s budget ✅ |
| **g** 10 concurrent writers × 50 `create` (unique rule) | 80.2 / 133.6 ms, 109 writes/s | 76.4 / 100.3 ms, 124 writes/s | ✅ |
| g′ 10 concurrent writers × 5 `batch` of 500 | 1,247 / 1,447 ms, 3.8k rec/s | 219 / 632 ms, 16.1k rec/s | ✅ |
| h daily bars `upsert` (400 records, one-at-a-time path) | 906 / 1,158 ms | 1,059 / 1,183 ms | ✅ |
| i `apps list` (Kyle), cold / warm | 11.9–13.0 s every call | 7.1 s once an hour, then 10 ms | warm ✅ |
| i′ `apps get` (stockmarket / tcms) | 5.2 / 2.8 s every call | 3–11 ms (reused checks) | ✅ |
| i″ `health` action (always fresh) | 5.2 / 2.8 s | 3.8 / 2.8 s | explicit action; acceptable |
| Load: 2.01M records with batch jobs (commit time) | 4,513 s (75 min, 400–590 rec/s) | 231 s (8–13k rec/s steady) | — |

**Storage** (after, 2.3M records): `app_data_records` takes 2,168 MB: 728 MB
of heap and 1,439 MB of indexes. The database is 2,273 MB.

| Index | Size |
|---|---|
| `t1_t2_time` | 297 MB |
| pkey | 295 MB |
| GIN `doc jsonb_path_ops` | 266 MB |
| `t1_time` | 218 MB |
| `created` | 186 MB |
| `t1_num` | 176 MB |
| `time` | 84 MB |

These sizes were measured on the baseline load (2,302 MB total), which has the
same shape.

## What was too slow, and the fixes

All four fixes are in `services/backend/agentplatform/appdata/` and have tests.
Every app_data test passes on both SQLite and Postgres.

1. **Bulk inserts** (`records._insert_many`, used by `batch` and by batch-job
   commits for `insert` mode):
   - **Before:** each record cost a `unique` SELECT and an INSERT round trip,
     about 1.7 ms per record (148 s for 140k).
   - **Now:** a 1,000-record chunk costs one query per `unique` rule and per
     ref field, then one executemany.
   - The checks and their order per record are unchanged. A record refused on
     any check claims none of its unique keys. A missing value still compares
     equal. Quotas are charged as before.
   - Collections with artifact fields keep the one-at-a-time path.
   - The unique lookup adds a plain `IN` per key column beside the row-value
     `IN`. Without it, a new collection with stale statistics was sought on
     `(app_id, collection)` alone, and every lookup filtered every record so
     far. That made the first 100k sets take 40–106 s, and a fresh 140k
     commit 70 s.
2. **Newest-first order kept the index** (`views.order_by`):
   - **Before:** every sort was `NULLS LAST`. Postgres reads a btree backwards
     as `DESC NULLS FIRST`, so every descending sort (including the default
     `created_at desc`) sorted all of its matches.
   - **Now:** a key that is never null (the system fields except `via`, and
     `required` fields, which publish keeps filled) drops the clause. This
     fixed c2, c4 and b.
3. **Keyset paging can seek** (`views._after`):
   - **Before:** the cursor condition was an OR with null branches, which
     Postgres can't use as an index range. Each page re-read every row before
     it, so a 1M-row scan was quadratic.
   - **Now:** a never-null leading key gets a redundant plain range
     (`>=` / `<=` the cursor value), and the null branches go away for
     never-null keys. Scans now stream at a steady ~34k rows/s.
4. **App health off the hot path** (`lifecycle._health`):
   - **Before:** `apps list` and `get` ran the record checks on every call: a
     GROUP BY over `doc->>` per unique rule, a missing count per required
     field, and a `sum(length(doc::text))` recount of every record. That is
     seconds per million-record App.
   - **Now:**
     - The quota part reads the quota row that quotas enforce, so the health
       limits are the enforced limits (250k records and 256 MiB by default),
       not lifecycle's old 100k and 64 MiB.
     - List and detail reuse the record checks for one hour per (approved
       version, authority generation). A publish rechecks at once, and the
       `health` action always checks afresh and refreshes the cache.
     - Engine writes enforce the rules, and publish checks stored records, so
       a violation can only come from a write racing a publish (the A9 repair)
       or a row written around the engine.
   - The cold first list per process per hour still costs ~3–4 s per
     million-record App. A periodic job could warm it later.

Also from review: **`_reindex`** now flushes and expunges every 1,000 records.
SQLAlchemy's autoflush on the next `_scan` chunk already kept the dirty set
bounded. The new test passed before the change and now guards it explicitly.

**The baseline scan-time failure was a real defect, not a quota setting.** The
quadratic keyset paging stopped the scan at 611k rows in 60 s. With fix 3, the
whole 1M-row collection scans in 29.5 s under the unchanged default budget.
The benchmark doesn't raise any scan limit.

## Timeouts (recommended)

- **Interactive tool call** (`app_data` query/get/create/update, `batch`):
  - A 60 s tool-call timeout.
  - A Postgres `statement_timeout` of 10 s on the request session.
  - Why:
    - Every gate read is < 100 ms p95 warm.
    - A 5,000-record `upsert` or `skip_existing` batch still uses the
      one-at-a-time path, measured at about 400 records/s (the 400-bar daily
      upsert took 1.06 s), so ~13 s per full call.
    - A 5,000-record insert takes < 1 s.
    - 60 s gives that upsert 4× headroom.
- **`batch_job` commit:**
  - A 300 s call timeout, with no statement timeout inside the commit
    transaction.
  - Why:
    - The commit measured 10.4 s for 140k records (~13.5k/s), and 8–13k/s
      steadily.
    - The worst case seen after the fixes was 6.4k/s (new collection, unique
      rule, under a profiler).
    - 300 s covers about 1.9M records at that worst rate, which is above
      stockmarket's whole measured size.
    - Staging calls are 5,000 records each, and the interactive timeout covers
      them (~0.1 s each).
- **Scan:**
  - Keep 60 s per execution, two concurrent scans per App, and the hourly row
    budgets.
  - Lower `app_data_scan_max_rows` from 2M to 1M, or raise
    `app_data_scan_max_seconds` to 120 s.
  - At ~34k rows/s here, 2M rows needs ~59 s, which leaves no headroom on the
    NUC's smaller `shared_buffers`. A scan near the row cap would fail on time
    instead of rows.
  - Kyle's call. This change leaves the config as the design states it.
- **Materialization refresh:** one week of TCMS results scans in 0.6 s. A full
  year of results (1M rows) takes 30 s, so a 10-minute `every:` is
  comfortable.

## Index and side-column findings (not changed here)

- **The NUC's Postgres PVC (2 GiB) can't hold the migrated volumes.**
  - The design's quotas (stockmarket ~2M and TCMS ~1.5M records) come to about
    2.3 GB of `app_data_records`, at ~660 bytes per record all-in on these
    shapes.
  - Add WAL, the other platform tables and backups' headroom on top.
  - **Raise the PVC (≥ 10 GiB) before M2/M5.** Also give Postgres more than
    the default 128 MB `shared_buffers`. These numbers used 512 MB.
- **The GIN `jsonb_path_ops` index is unused.**
  - No query the engine builds uses containment.
  - It costs 266 MB per 2.3M records (12% of the table) plus write time.
  - The measured paths don't need it. Eq on a non-indexed field (c, c2, c4)
    is fast through the side-column indexes now that the order can use them.
  - Emitting `doc @> {f: v}` for eq would invite bad plans: Postgres's fixed
    containment estimate (~0.1%) would pick a bitmap of 60k rows for
    status = failed over the 1 ms backward index scan.
  - Recommendation: drop the GIN index in R1b unless a containment path is
    added deliberately.
- **Half-used composite indexes:**
  - `t1_num` (176 MB) indexes every record, though neither App here fills
    `ix_num1`. `t1_t2_time` also indexes records with no `ix_text2`.
  - Partial indexes (`WHERE ix_num1 IS NOT NULL`, `WHERE ix_text2 IS NOT
    NULL`) would cut them to what is used. Postgres proves the predicate from
    the `=` / range conditions the engine emits.
- **c3, one ref across runs:** this is not a gate path. The TCMS inventory's
  analytics are windowed over the last 20–30 runs and materialized.
  - `ref` sits in `ix_text2` behind `ix_text1` (run), so no index leads with
    it. The read walks the time index backwards and filters ~25k rows for 50
    matches: 17 ms warm, 474 ms p95 with a cold cache.
  - If TCMS gets a per-case history page, either filter it to recent runs
    (`run in [...]`), or add a fifth fixed index
    `(app_id, collection, ix_text2, ix_time1)` (~200 MB per 2.3M records).
- **The watchlist (b)** is 20 reads at ~3 ms each. A grouped "latest per
  symbol" view (R3) would make it one query, but it's not needed for the
  target.
