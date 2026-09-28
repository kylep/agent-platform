# 35 — Backtest Lab

Status: **approved 2026-09-28**. Kyle's decisions: default currency CAD;
the new-money reading of "put $X into the winner"; no new agent; extend the
stockmarket app. The build plan is
`docs/superpowers/plans/2026-09-28-backtest-lab.md`.

## What this delivers

Kyle asks market "what if" questions in conversation. Examples:

- "$1000/mo into QQQ for 10 years vs the same money into whichever of QQQ,
  SPY, XIU.TO and the Mag 7 had the best trailing month"
- "$50k lump sum in 2015, 60/40 SPY/XIU.TO rebalanced quarterly vs all SPY"
- "only buy SPY while it is above its 200-day average, otherwise hold cash"

The `stockmarket-data` worker turns each question into a **declarative
experiment spec**. A **deterministic engine** runs the spec against a
**pinned price dataset**. The stockmarket app stores the result and shows it
on a **Backtests** page and in a sanitized HTML report. Any persona (for
example Pai) can read results through `query_app`.

The questions above are illustrations. The design is the spec language and
its primitives, not any one question: a question is supported when it
composes from the primitives, and new primitives arrive through reviewed PRs.

The model only ever writes a spec. Every number comes from reviewed code. The
plain-English "what was tested" text is also generated from the spec by code,
so the model cannot misdescribe what ran. The same spec, the same dataset and
the same engine version always give a byte-identical result.

## Starting point (review)

### The stockmarket connector (`apps/stockmarket`, `tools/prices`)

The `prices` tool fetches from yfinance inside the tool-executor. It writes
OHLCV straight into `app_stockmarket.bars` using the app's DB secret, so the
data never passes through a model. New symbols get a 5-year backfill. `.TO`
tickers work. Two agents split the work: the `stockmarket` narrator writes
the brief, and the `stockmarket-data` worker holds `prices` and has no web
access.

The connector has six problems for backtesting:

1. **It stores adjusted prices only, and they get rewritten.** It fetches
   with `auto_adjust=True`, so OHLC is back-adjusted for dividends and
   splits. There is no raw close, no dividend history and no split history,
   and every new adjustment rewrites earlier prices.
2. **A stitching bug that affects today's chart too.** The daily sync
   refetches only the last 5 days (`5d`) and upserts them.
   - After an ex-dividend date, those recent rows are on the new adjustment
     basis while older rows keep the old one. That leaves a small step in the
     series.
   - A split leaves a large step.
   - kytrade has the same bug (`stocks.py:111-118`).
   - The chart's CAGR tile reads across these steps.
3. **No currency.** XIU.TO (CAD) sits next to USD tickers. There is no
   currency column and no FX series.
4. **Not enough history.** New symbols get 5 years. A 10-year test plus a
   lookback window needs more.
5. **No migrations.** Tables come from `create_all` alone. `day` is a
   string, and `volume` is a 32-bit integer.
6. **The read API can't feed a backtest.** `/series` returns closes only and
   downsamples to 400 points.

### kytrade (`multi/apps/kytrade`)

kytrade is clean and typed, with about 105 unit tests and matching CLI and
API commands that return pydantic models. It contains **no backtest code**.
Roadmap Phase 4 (a strategy protocol plus CAGR, max drawdown and Sharpe) was
never built.

- **Worth lifting:** the price-provider interface, ticker normalization
  (`BRK.B`→`BRK-B`, `CTC.A`→`CTC-A.TO`), and the honesty rule "report only
  numbers the commands produced; state data freshness".
- **Worth discarding:** the JSON-blob price store and the incremental
  adjusted-price stitching.
- **Not handled there:** survivorship and hindsight bias, FX, dividend cash
  flows, when a trade fills, and money-weighted vs time-weighted returns.
  This design handles each one explicitly and states each one in every
  report.

## Decision: a toolkit, not agent-written scripts

**Rejected: the agent writes a Python script for each question.**

- The code would differ every run. A bug such as lookahead, an off-by-one
  month or a reversed FX rate would be new each time and hard to spot.
- Reviewing every script costs more than the question is worth.
- Non-dev runners deny Bash by design, so the agent couldn't run a script
  anyway.
- A dev pod for every question would spend a lot of quota.

**Chosen: a deterministic engine that runs a small spec language.**

- Questions compose from a closed set of **primitives**.
- Each primitive is reviewed code with hand-computed golden tests.
- When a question cannot be expressed, the worker does not improvise. It
  says so and files a ticket naming the missing primitive. The engineer agent
  then adds that primitive through a normal PR with tests.

This is how the toolkit gains new tools as needed: through review, never at
run time.

## Architecture

```
Kyle ──Relay DM / Pai hands off──▶ stockmarket-data (worker; no web, no shell)
                        │ mcp__platform__backtest {action, spec}
                        ▼
                 tool-executor: tools/backtest/run.py  (+ tools/backtest/engine/)
                   1. validate + normalize spec, generate its description
                   2. load bars/actions/fx from app_stockmarket (APP_DB_*)
                   3. pin the dataset: canonical rows → sha256
                   4. engine: pure function (stdlib + Decimal)
                   5. write experiment + dataset + results into app_stockmarket (one txn)
                   6. publish backtest.completed (id + headline) ──▶ Kafka app.stockmarket.backtest
                   7. return a compact summary (≤ 4 KB) + experiment id + link
                        │
                        ▼
                 apps/stockmarket (the Backtests view)
                   consume → render /api/reports (type backtest) from the stored rows
                   UI /apps/stockmarket/backtests[/<id>]; agents read via query_app
```

| Piece | Change |
|---|---|
| `tools/prices` | Adds the split-adjusted (not dividend-adjusted) close, dividends, splits, currency, the `CAD=X` FX series, `10y`, a full refetch when a new dividend or split arrives, and a `research` symbol kind. |
| `tools/backtest` (new) | `run.py` plus an `engine/` package beside it. Actions: `validate`, `run`, `rerun`, `describe_primitives`. It declares `infra.kafka: true` and `secrets: [app-stockmarket-db]`. |
| `apps/stockmarket` | New columns, `backtest_*` tables, a consumer for `app.stockmarket.backtest`, the Backtests view, a `backtest` report type, and read endpoints. |
| `stockmarket-data` | Granted `backtest` and `tickets` (it keeps `prices`). A prompt section covers the conversation loop, the pitfalls and the honesty rules. |

### Why these choices

**The engine lives in the tool, written in plain Python.**
- The executor runs `run.py` with the tool's directory as its working
  directory, so `engine/` imports directly.
- Tool code goes live on git sync, so the engine needs no image build.
- It uses only the standard library, with `Decimal` for money. That means no
  pandas or numpy version drift and exact determinism.
- A 10-year daily run over 10 symbols is about 26k rows. That takes well
  under a second, far inside the 300 s ceiling.
- App pods have no egress, but that doesn't matter here: the compute needs
  none.

**Results go to Postgres; Kafka carries the notice.**
- The tool writes the full result and the pinned dataset straight into
  `app_stockmarket` in one transaction, idempotent by experiment id. This is
  the same way `prices` writes bars.
- It then publishes a small `backtest.completed` event (id, name, headline
  metrics). The app consumes that event to render the report, and anything
  else can subscribe to it.
- *Changed during the build (2026-09-28):* the first draft put the whole
  result in the Kafka message. A measured worst case at the spec's bounds is
  14–22 MB of canonical JSON (8 strategies × 25 symbols, 55k–119k events),
  far over Kafka's 1 MB message limit. Postgres, which is backed up by
  pg-backup, is now the system of record. The trade-off: replaying the topic
  no longer rebuilds the tables.

**Pinned datasets live in the app database, not in artifacts.**
- Datasets are keyed by sha256, so identical pins are stored once.
- `rerun` reads the pin back through the same DB secret.
- An October rerun of a March experiment is therefore exact, even after
  Yahoo revises its history.

**The stockmarket app, not a new app** (Kyle's decision). The price archive
is already there, and the tool already binds its secret.

**The `stockmarket-data` worker, not a new agent** (Kyle's decision).
- It already holds `prices` and has no web access.
- A worker without web access has no path for prompt injection into the
  data it writes. Its only untrusted input is numbers from Yahoo.
- Under design 34, Kyle reaches it by Relay DM, and personas can hand work to
  it.
- The narrator, `stockmarket`, stays as it is.

## Data layer (`tools/prices`, `app_stockmarket`)

- **Fetch:** `auto_adjust=False, actions=True`.
- **New columns on `bars`:** `close_split_adj`, `adj_close`, `dividend` and
  `split_ratio`.
  - `close` stays the display series and keeps its current meaning (the
    fully adjusted close), so the chart is unchanged.
  - `volume` is widened to BIGINT.
- **New column on `symbols`:** `currency`, taken from
  `history_metadata["currency"]`.
- **New symbol kinds:**
  - `fx`: `CAD=X`, the number of CAD per USD.
  - `research`: symbols loaded only for backtests. They never appear on the
    chart or the watchlist, and the daily sync still tops them up.
- **Stitching fix:** if a fetched window contains a dividend or split not yet
  stored for that symbol, the tool refetches that symbol's **full** history
  in the same call. Adjusted series are then always coherent, which fixes
  today's chart too.
- **Ranges:** `10y` joins the allowed ranges. The backtest tool's
  missing-data error names the exact `prices` call to make.
- **Migration:** idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` and
  `ALTER COLUMN … TYPE BIGINT` run at app startup, after `create_all`.
- **Price basis (probed 2026-09-28).** With `auto_adjust=False`, Yahoo's
  `Close` is **split-adjusted but not dividend-adjusted**.
  - NVDA closed at 120.888 on 2024-06-07, the day before its 10:1 split,
    rather than about 1,209.
  - Dividends come in the same post-split share units: NVDA paid 0.01 on
    2024-06-11.
  - `CAD=X` closes about 1.37, which is CAD per USD.
  - `history_metadata["currency"]` is present: USD, CAD, CAD.
  - The column is therefore named `close_split_adj`, which says what it is.
  - The engine treats every share as a split-adjusted share, so a split never
    changes a share count. `split_ratio` is kept as an event for the log and
    for the refetch trigger.
- **Total return is computed, then checked.** The engine builds its own
  total-return series from `close_split_adj` and `dividend`. It cross-checks
  that series against Yahoo's `adj_close` over the backtest period, with a
  tolerance of 0.1% cumulative. A mismatch fails the run with the symbol and
  date, rather than producing a quietly wrong number.

## The spec language (v1)

A spec is JSON. Validation fills in every default explicitly. The
canonicalized form (sorted keys, and decimals as strings) is what gets
hashed. Below is one illustration. Any other question is simply a different
combination of the same primitives.

```yaml
name: qqq-dca-vs-1m-winner
period: {start: 2016-10-01, end: 2026-09-01}
base_currency: CAD                       # default
contributions: {amount: 1000, every: month, on: first_trading_day}
execution: {decide: prior_close, fill: close}
dividends: reinvest
costs: {commission: 0, slippage_bps: 5, fx_bps: 25}
strategies:
  - id: qqq
    allocate: {fixed: {QQQ: 1}}
  - id: winner
    allocate:
      rank:
        universe: [QQQ, SPY, XIU.TO, AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA]
        signal: {trailing_return: {lookback: 1mo}}
        pick: {top: 1}
    holdings: keep                       # default: only new money follows the pick
benchmark: qqq
```

### v1 primitives

| Family | Primitives |
|---|---|
| Money in | `contributions {amount, every: day\|week\|month\|quarter\|year, on: first_trading_day\|last_trading_day\|day_n}`; `lump_sum {amount, on}`; the two combine |
| Signals | `trailing_return(lookback)`, `sma(n)`, `price_vs_sma(n)`, `volatility(n)`, `drawdown_from_high(n)` |
| Allocators | `fixed {weights}`; `rank {universe, signal, pick: top N\|bottom N}` (equal weight); `when {signal op threshold, then, else}`, where `else` may be `cash` |
| Holdings | `keep` (new money only; the default), `rotate` (sell all into the new allocation at each event), `rebalance {to: weights, every}` |
| Costs | `commission` per trade, `slippage_bps`, `fx_bps` |
| Dividends | `reinvest` (on the ex-date close) or `cash`; optional `withholding_pct` |

**Extension rule.** A new primitive needs all of the following or it does
not merge:
- a spec model
- a pure function
- a hand-computed golden test
- a `describe_primitives` entry
- a sentence template for the generated description
- an example spec that uses it, returned by `describe_primitives`

## Engine semantics: where correctness lives

Each of these rules is fixed in code, printed in every report, and tested.

- **No lookahead.**
  - A decision on day *t* sees only closes strictly before *t*.
  - The fill happens at *t*'s close plus slippage.
  - Property test: perturbing every price after *t* changes no decision up to
    *t*.
- **Calendars.**
  - Trading days come from each symbol's own bars, so US and TSX holidays
    differ correctly.
  - An event is dated to the first trading day of the period on the base
    currency's market: the TSX calendar (XIU.TO's bars) for CAD, the US
    calendar (SPY's) for USD.
  - Each fill uses that symbol's first bar on or after that date.
- **Currency.**
  - Money arrives in `base_currency`.
  - Buying a symbol in another currency converts at that day's `CAD=X` close,
    minus `fx_bps`.
  - Holdings are valued in base currency every day.
  - Dividends are paid in the symbol's currency. **Reinvest** buys the same
    symbol in that currency at the ex-date close, with no FX, commission or
    slippage, the way a DRIP works. This avoids two things: a phantom double
    FX conversion, and a dividend smaller than the commission quietly
    becoming cash. **Cash** mode converts the dividend to base currency and
    charges `fx_bps`.
- **Shares and money.**
  - Fractional shares by default; `whole_shares: true` leaves leftover cash
    instead.
  - Prices are split-adjusted at the source, so a split changes no share
    count. The split is logged as an event.
  - Accounting uses `Decimal`: shares to 8 decimal places, cash to cents,
    with half-even rounding.
  - Signals only rank symbols, but they are `Decimal` too, so ties are exact.
  - Ties break alphabetically by symbol.
- **Coverage.**
  - A symbol without enough bars for a signal's lookback is *ineligible* for
    that event, and the exclusion is logged.
  - When nothing is eligible, the money is held as cash and that is logged.
  - The engine never skips silently.
  - A period that starts before the dataset's coverage is a validation error
    that names the exact `prices` call to make.
- **Metrics, per strategy.**
  - Final value, total contributed and profit.
  - **Money-weighted return (XIRR).** This is the headline number: what your
    dollars actually earned.
  - Time-weighted return and its annualized rate.
  - Max drawdown: depth, peak, trough and recovery dates.
  - Annualized volatility.
  - Sharpe ratio, using a risk-free rate from the spec that defaults to 0 and
    is always printed.
  - Turnover, trade count, costs, FX paid, dividends received, and the
    largest weight held in a single name.
- **Determinism.**
  - `experiment_id` = sha256 of the canonical spec, the dataset sha and the
    engine version, truncated to 32 hex characters.
  - `ENGINE_VERSION` is bumped by any change to semantics. A golden-output
    test fails if the output changes without that bump.

## Caveats printed in every report

These are generated from the spec and cannot be turned off:

- **Hindsight.** Any explicit list of individual stocks gets this warning:
  "universe chosen with today's knowledge; winners picked in hindsight
  inflate results". There is no point-in-time index membership in v1.
- **Concentration.** Any strategy with `pick.top ≤ 2` or a single-name
  weight above 40% prints its maximum single-name weight and the pick
  timeline next to its returns.
- **Taxes.** Taxes are ignored unless `withholding_pct` is set. Account type
  (TFSA, RRSP or taxable) is not modelled.
- **Data.** Yahoo data through yfinance, unaudited. The report shows the
  dataset sha and the fetch dates.

## Stockmarket app: the Backtests view

- **Topic:** `app.stockmarket.backtest`, declared in `app.yaml`.
- **Tables:**
  - `backtest_experiments`: id, name, spec, description, `dataset_sha`,
    `engine_version`, caller, run id, created time, report id
  - `backtest_datasets`: sha, gzipped canonical rows, symbols, date span
  - `backtest_results`: experiment, strategy, metrics
  - `backtest_series`: experiment, strategy, day, value, contributed
  - `backtest_events`: experiment, strategy, day, kind, symbol, detail
- **UI** at `/apps/stockmarket/backtests`, as a tab beside the existing
  page:
  - **The experiment list.**
  - **The experiment page:**
    - the description
    - the spec, collapsible
    - one stat row per strategy
    - a value vs contributed chart
    - a drawdown chart
    - the pick timeline: a month × symbol grid
    - returns by year
    - caveats and exclusions
    - **Re-run on current data**, which uses the operator key to start a
      `stockmarket-data` run, the same way the watchlist backfill does
  - **A compare view** for 2–4 experiments.
- **Report:** type `backtest`.
  - Settings: `generator: app:stockmarket`, `cadence: adhoc`,
    `retention_days: 3650`.
  - It is written on ingest using `rk-*` HTML and server-rendered line and
    bar charts, and it links to the experiment page.
  - It can be regenerated from stored rows.
- **Read endpoints** for `query_app`, GET only:
  - `backtests?q=&limit=`
  - `backtests/{id}`: description, metrics, caveats and exclusions, under
    8 KB
  - `backtests/{id}/events?strategy=&page=`
  - `backtests/{id}/series?strategy=`, monthly-sampled for agents
  - the endpoints are also listed in `help`

## The conversation (`stockmarket-data`)

The grammar the worker writes against comes from `describe_primitives`. That
output is generated from the engine's primitive registry, so it cannot drift
from the code. There is no separate skill: a hand-written grammar document
would be a second source of truth. The worker's prompt section carries only
the loop, the pitfalls and the honesty rules.

0. **Read the grammar.** Call `describe_primitives` once per conversation.
1. **Validate.** Draft a spec and call `validate`. It returns:
   - the normalized spec
   - the generated description
   - `assumed`: every default the model left out, for example
     `holdings: keep` or `base_currency: CAD`
   - any missing data, with the exact `prices` call needed
2. **Load missing data.** If `validate` reports any, call `prices` exactly
   as it says, with the new symbols as kind `research`.
3. **Run.** Call `run` without asking first; runs are cheap. The reply
   contains:
   - the generated description, verbatim
   - a small metrics table
   - the `assumed` list
   - the link
   - one variant worth trying
4. **Refuse to approximate.** If the question can't be expressed, say so and
   open a ticket with `tickets` naming the missing primitive. Never quietly
   answer a different question.
5. **Report only tool output.** Never state a number that `backtest` or
   `query_app` did not return.

Pai and other personas answer "what did the backtest say?" with
`query_app('stockmarket', 'backtests…')`. They never hold the tool.

## Deferred

- Point-in-time index universes. These need a historical constituents
  source.
- Intraday data, options, shorting, margin and leverage.
- Tax modelling by account type.
- Parameter sweeps: a `sweep:` key that expands into child experiments under
  one parent.
- Moving the price archive into a tool-owned `tool_market` schema. Revisit
  when a third consumer appears.
