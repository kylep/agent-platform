"""backtest tool: run a declarative backtest spec on the stockmarket app's
price archive and store the result in the app's schema (app_stockmarket).

The engine (engine/, pure standard library) does the finance; this file is
the I/O around it: read coverage and bars from `bars`/`symbols`, pin the
exact rows a run priced against (canonical JSON, sha256, gzip), write the
experiment into the five `backtest_*` tables in ONE transaction, then
publish a small `backtest.completed` notice to `app.stockmarket.backtest`.
Results live in Postgres, not Kafka: a worst-case result is ~20 MB
(design 35, "Why these choices"), far over a Kafka message; the notice only
tells the app which experiment to render.

The app owns every table here (DDL at its startup). This tool creates
nothing, writes only the `backtest_*` tables (never `bars`/`symbols`), and
fails clearly when they are missing. Everything in a spec (name, labels,
symbols) is untrusted: it reaches SQL only as parameters, and the Kafka key
is the engine's hex experiment id.

Executor contract: JSON args on stdin, JSON result on stdout, non-zero exit
+ one stderr line on failure. Env: APP_DB_* (secret app-stockmarket-db),
AP_KAFKA_BOOTSTRAP (infra.kafka), TOOL_CALLER_AGENT / TOOL_RUN_ID.
"""
import asyncio
import gzip
import hashlib
import json
import math
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import engine
from engine.calendar import CALENDAR_SYMBOL
from engine.data import FX_SYMBOL, EngineError

SCHEMA = "app_stockmarket"
TOPIC = "app.stockmarket.backtest"
EVENT_TYPE = "backtest.completed"
ACTIONS = ("describe_primitives", "validate", "run", "rerun")
EXPERIMENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
URL = "/apps/stockmarket/#/backtests/{}"   # hash route: StaticFiles has no SPA fallback
SUMMARY_CAP = 4096
NOTICE_CAP = 8192
CAVEAT_CHARS = 160
# tools/prices takes at most this many symbols per call.
PRICES_MAX_SYMBOLS = 25
# tools/prices ranges, shortest first, with the calendar days each reaches.
PRICES_RANGES = (("5d", 6), ("1mo", 28), ("6mo", 180), ("1y", 365), ("5y", 5 * 365),
                 ("10y", 10 * 365), ("max", None))
# A symbol whose last bar is this many days before the period's end (or
# today) is reported as stale.
STALE_DAYS = 5
RESULT_TABLES = ("backtest_datasets", "backtest_experiments", "backtest_results",
                 "backtest_series", "backtest_events")
REQUIRED_COLUMNS = {
    "bars": ("symbol", "day", "close_split_adj", "adj_close", "dividend", "split_ratio"),
    "symbols": ("symbol", "currency", "last_synced_at"),
    "backtest_datasets": ("sha", "rows_gz", "symbols", "day_from", "day_to", "created_at"),
    "backtest_experiments": ("id", "name", "spec", "description", "assumed", "caveats",
                             "exclusions", "dataset_sha", "engine_version", "caller",
                             "run_id", "created_at"),
    "backtest_results": ("experiment_id", "strategy_id", "label", "metrics"),
    "backtest_series": ("experiment_id", "strategy_id", "day", "value", "contributed"),
    "backtest_events": ("experiment_id", "strategy_id", "day", "kind", "symbol", "detail"),
}
HEADLINE = ("final_value", "contributed", "xirr", "twr_annualized", "max_drawdown_depth")
SUMMARY_METRICS = HEADLINE + ("volatility", "sharpe", "trades", "costs")


class ToolError(Exception):
    """A failure the model can act on; `code` is the exit status."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def _today():
    return datetime.now(timezone.utc).date()


def _now():
    return datetime.now(timezone.utc)


# ---- database -----------------------------------------------------------------------

def connect():
    """Connect as the app's role, from the secret's components (APP_DB_URL
    carries SQLAlchemy's `+asyncpg` suffix). Same as tools/prices."""
    import psycopg
    missing = [k for k in ("APP_DB_HOST", "APP_DB_USER", "APP_DB_PASSWORD", "APP_DB_NAME")
               if not os.environ.get(k)]
    if missing:
        raise ToolError(f"missing {', '.join(missing)}: the app-stockmarket-db secret is not "
                        "bound; is the stockmarket app provisioned?", 2)
    return psycopg.connect(
        host=os.environ["APP_DB_HOST"], port=int(os.environ.get("APP_DB_PORT", "5432")),
        user=os.environ["APP_DB_USER"], password=os.environ["APP_DB_PASSWORD"],
        dbname=os.environ["APP_DB_NAME"], connect_timeout=10,
        options=f"-c search_path={SCHEMA}")


def check_schema(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = %s", (SCHEMA,))
        have = {(r[0], r[1]) for r in cur.fetchall()}
    tables = {t for t, _ in have}
    missing = [t for t in REQUIRED_COLUMNS if t not in tables] or \
        [f"{t}.{c}" for t, cols in REQUIRED_COLUMNS.items() for c in cols
         if (t, c) not in have]
    if missing:
        raise ToolError(f"{SCHEMA} is missing {', '.join(missing)}: the stockmarket app "
                        "creates these at startup; deploy the current app version first", 2)


def _dec(v):
    """A stored float as the Decimal its shortest repr spells (prices rounds
    to 4-6 dp, so this is the value that was written)."""
    return None if v is None else Decimal(repr(float(v)))


# ---- coverage ---------------------------------------------------------------------------

def _range_back_to(day, today):
    """The shortest `prices` range whose fetch reaches back to `day`."""
    days = (today - day).days
    for name, reach in PRICES_RANGES:
        if reach is None or days <= reach:
            return name
    return "max"


def _wider(a, b):
    names = [n for n, _ in PRICES_RANGES]
    return a if b is None or names.index(a) >= names.index(b) else b


class Coverage:
    """What the archive holds for one spec: the rows to pin and what is missing."""

    def __init__(self, spec, info, bars, today):
        self.info = info
        self.load, self.traded = self.plan(spec, {s: i["currency"] for s, i in info.items()})
        start, end = spec.period.start, spec.period.end
        # N returns span N + 1 closes (engine.signals.required_bars), and a
        # fixed allocation still decides on the close before the first event.
        self.need = spec.max_window() + 1
        needed_from = start - timedelta(days=math.ceil(self.need * 1.5) + 10)
        history_range = _range_back_to(needed_from, today)
        self.rows, problems = {}, {}   # problems: sym -> [range, blocking, notes]

        def problem(sym, rng, blocking, note):
            p = problems.setdefault(sym, [None, False, []])
            p[0] = _wider(rng, p[0])
            p[1] = p[1] or blocking
            p[2].append(note)

        for sym in self.load:
            rows = bars.get(sym, [])
            before = [r for r in rows if r[0] < start.isoformat()]
            kept = before[max(0, len(before) - self.need):] + \
                [r for r in rows if r[0] >= start.isoformat()]
            if not kept:
                problem(sym, history_range, True, "no bars for the period")
                continue
            if len(before) < self.need:
                # Not blocking: a symbol listed after the start can never
                # have more, and the engine marks it ineligible until it does.
                problem(sym, history_range, False,
                        f"{len(before)} of {self.need} bars before {start}; if already "
                        "loaded with this range it has no earlier history")
            if any(r[1] is None or r[2] is None for r in kept):
                problem(sym, "max", True, "bars stored before the split/dividend columns "
                                          "existed")
            last = date.fromisoformat(kept[-1][0])
            if (min(end, today) - last).days > STALE_DAYS:
                problem(sym, _range_back_to(last, today), False, f"bars end {last}")
            if sym in self.traded and info.get(sym, {}).get("currency") is None:
                problem(sym, "5d", True, "currency unknown")
            self.rows[sym] = kept

        self.blocking = any(p[1] for p in problems.values())
        self.notes = [f"{sym}: {'; '.join(p[2])}" for sym, p in sorted(problems.items())]
        groups = {}
        for sym, (rng, _, _) in sorted(problems.items()):
            groups.setdefault((rng, "fx" if sym == FX_SYMBOL else "research"), []).append(sym)
        order = [n for n, _ in PRICES_RANGES]
        self.missing = [
            {"symbols": syms[i:i + PRICES_MAX_SYMBOLS], "range": rng, "kind": kind}
            for (rng, kind), syms in sorted(groups.items(),
                                             key=lambda g: (order.index(g[0][0]), g[0][1]))
            for i in range(0, len(syms), PRICES_MAX_SYMBOLS)]

    @staticmethod
    def plan(spec, currencies):
        """(symbols to load, symbols traded). The base calendar symbol is
        always loaded, even when not traded, so the calendar never silently
        falls back to a union of the traded symbols' days. CAD=X joins when
        a traded currency differs from the base or is not yet known."""
        traded = {s for s in spec.symbols() if "=" not in s and not s.startswith("^")}
        load = set(spec.symbols()) | {CALENDAR_SYMBOL[spec.base_currency]}
        if any(currencies.get(s) != spec.base_currency for s in traded):
            load.add(FX_SYMBOL)
        return sorted(load), traded


def gather(conn, spec, today):
    candidates = sorted(set(spec.symbols()) | set(CALENDAR_SYMBOL.values()) | {FX_SYMBOL})
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, currency, last_synced_at FROM symbols "
                    "WHERE symbol = ANY(%s)", (candidates,))
        info = {r[0]: {"currency": r[1], "last_synced_at": r[2]} for r in cur.fetchall()}
    load, _ = Coverage.plan(spec, {s: i["currency"] for s, i in info.items()})
    need = spec.max_window() + 1
    # Generous calendar margin for `need` trading days; trimmed exactly in Coverage.
    lo = spec.period.start - timedelta(days=need * 2 + 30)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol, day, close_split_adj, adj_close, dividend, split_ratio "
                    "FROM bars WHERE symbol = ANY(%s) AND day >= %s AND day <= %s "
                    "ORDER BY symbol, day",
                    (load, lo.isoformat(), spec.period.end.isoformat()))
        bars = {}
        for r in cur.fetchall():
            bars.setdefault(r[0], []).append(tuple(r[1:]))
    return Coverage(spec, info, bars, today)


# ---- the pin -------------------------------------------------------------------------------

def pin(cov):
    """(canonical JSON, sha256, gzip bytes, dataset for the engine, fetched).

    The fetch span is part of the pinned content, so the data caveat (which
    prints both the sha and the fetch dates) is a function of the sha."""
    synced = sorted(i["last_synced_at"].date().isoformat()
                    for s, i in cov.info.items() if s in cov.rows and i["last_synced_at"])
    fetched = [synced[0], synced[-1]] if synced else None
    symbols = {sym: {"currency": cov.info.get(sym, {}).get("currency"),
                     "bars": [[r[0]] + [_dec(v) for v in r[1:]] for r in rows]}
               for sym, rows in cov.rows.items()}
    canonical = engine.canonical_json({"fetched": fetched, "symbols": symbols})
    return canonical, *unpin(gzip.compress(canonical.encode(), compresslevel=9, mtime=0))


def unpin(gz, expect_sha=None):
    """(sha, gz, dataset, fetched) from stored gzip bytes, checking the sha.
    run and rerun both feed the engine from here, so they share one path."""
    raw = gzip.decompress(gz)
    sha = hashlib.sha256(raw).hexdigest()
    if expect_sha is not None and sha != expect_sha:
        raise ToolError(f"pinned dataset {expect_sha} is corrupt (content hashes to {sha})")
    doc = json.loads(raw)
    return sha, gz, doc["symbols"], tuple(doc["fetched"]) if doc["fetched"] else None


# ---- storing + notifying ------------------------------------------------------------------

def _j(obj):
    return engine.canonical_json(obj)


def store(conn, result, gz, dataset, caller, run_id, now):
    """Insert-if-absent, all in the caller's one transaction. Returns True
    when the experiment is new; a repeat run writes nothing."""
    days = [b[0] for s in dataset.values() for b in s["bars"]]
    with conn.cursor() as cur:
        cur.execute("INSERT INTO backtest_datasets (sha, rows_gz, symbols, day_from, day_to, "
                    "created_at) VALUES (%s, %s, %s::json, %s, %s, %s) "
                    "ON CONFLICT (sha) DO NOTHING",
                    (result.dataset_sha, gz, _j(sorted(dataset)), min(days), max(days), now))
        cur.execute("INSERT INTO backtest_experiments (id, name, spec, description, assumed, "
                    "caveats, exclusions, dataset_sha, engine_version, caller, run_id, "
                    "created_at) VALUES (%s, %s, %s::json, %s, %s::json, %s::json, %s::json, "
                    "%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id",
                    (result.experiment_id, result.spec["name"], _j(result.spec),
                     result.description, _j(result.assumed), _j(result.caveats),
                     _j(result.exclusions), result.dataset_sha, result.engine_version,
                     caller, run_id, now))
        if cur.fetchone() is None:
            return False
        eid = result.experiment_id
        # executemany pipelines in psycopg 3 (one round trip per batch, not
        # per row), which is what keeps ~120k event rows inside the timeout.
        cur.executemany("INSERT INTO backtest_results (experiment_id, strategy_id, label, "
                        "metrics) VALUES (%s, %s, %s, %s::json)",
                        [(eid, s.strategy_id, s.label, _j(s.metrics))
                         for s in result.strategies])
        cur.executemany("INSERT INTO backtest_series (experiment_id, strategy_id, day, value, "
                        "contributed) VALUES (%s, %s, %s, %s, %s)",
                        [(eid, s.strategy_id, p.day.isoformat(), float(p.value),
                          float(p.contributed))
                         for s in result.strategies for p in s.series])
        cur.executemany("INSERT INTO backtest_events (experiment_id, strategy_id, day, kind, "
                        "symbol, detail) VALUES (%s, %s, %s, %s, %s, %s::json)",
                        [(eid, s.strategy_id, e.day.isoformat(), e.kind, e.symbol,
                          _j(e.detail))
                         for s in result.strategies for e in s.events])
    return True


def _headline(metrics, keys):
    m = dict(metrics)
    m["max_drawdown_depth"] = (m.get("max_drawdown") or {}).get("depth")
    return json.loads(engine.canonical_json({k: m.get(k) for k in keys}))


def notice(result):
    return {"experiment_id": result.experiment_id, "name": result.spec["name"],
            "strategies": [{"id": s.strategy_id, "label": s.label,
                            "headline": _headline(s.metrics, HEADLINE)}
                           for s in result.strategies]}


def envelope(result):
    body = json.dumps({"type": EVENT_TYPE, "schema_version": 1, "id": uuid.uuid4().hex,
                       "ts": _now().isoformat(), "key": result.experiment_id,
                       "source": "tool-backtest", "data": notice(result)},
                      sort_keys=True, separators=(",", ":")).encode()
    if len(body) > NOTICE_CAP:  # bounded by the spec's limits; a guard, not a path
        # Deterministic for this experiment, so retrying cannot help.
        raise ToolError(f"experiment {result.experiment_id} is stored but its "
                        f"backtest.completed notice is {len(body)} bytes (cap {NOTICE_CAP}); "
                        "this is a tool bug, retrying will not help")
    return body


async def _send(bootstrap, key, value):
    from aiokafka import AIOKafkaProducer
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await producer.start()
    try:
        await producer.send_and_wait(TOPIC, value, key=key.encode())
    finally:
        await producer.stop()


def publish(bootstrap, key, body):
    asyncio.run(_send(bootstrap, key, body))


# ---- output ------------------------------------------------------------------------------

def _short(text, n):
    return text if len(text) <= n else text[:n - 1] + "…"


def render(out):
    """stdout text. UTF-8 rather than escapes, so a label in any script
    costs its bytes, not six per character."""
    return json.dumps(out, ensure_ascii=False)


def summary(result, stored, **extra):
    """The compact reply (<= 4 KB); the full result lives at `url`. Metrics
    are one row per strategy under shared column names. Trimmed, least
    useful to the model first, until it fits: caveat text, assumed values,
    strategy labels (the description names them), the description's tail."""
    eid = result.experiment_id
    rows = []
    for s in result.strategies:
        h = _headline(s.metrics, SUMMARY_METRICS)
        rows.append([s.strategy_id, s.label, *(h[k] for k in SUMMARY_METRICS)])
    out = {"experiment_id": eid, "name": result.spec["name"], "stored": stored,
           "dataset_sha": result.dataset_sha, "engine_version": result.engine_version,
           "description": result.description,
           "assumed": json.loads(engine.canonical_json(result.assumed)),
           "metrics": {"columns": ["id", "label", *SUMMARY_METRICS], "rows": rows},
           "caveats": [f"{c.code}: {_short(c.text, CAVEAT_CHARS)}" for c in result.caveats],
           "exclusions": len(result.exclusions), "url": URL.format(eid), **extra}

    def over():
        return len(render(out).encode()) - SUMMARY_CAP

    if over() > 0:
        out["caveats"] = sorted({c.code for c in result.caveats})
    if over() > 0:
        out["assumed"] = [a["path"] for a in out["assumed"]]
    if over() > 0:
        out["metrics"]["columns"].remove("label")
        for row in rows:
            del row[1]
    if over() > 0:
        tail = f"… (full text at {out['url']})"
        text = result.description
        out["description"] = text + tail
        while text and over() > 0:
            text = text[:-max(1, over())]   # each char is >= 1 rendered byte
            out["description"] = text + tail
    return out


# ---- actions --------------------------------------------------------------------------------

def _validated(raw):
    v = engine.validate(raw)
    if not v.ok:
        raise ToolError("invalid spec: " + "; ".join(f"{e.path}: {e.message}"
                                                       for e in v.errors))
    return v.spec


def do_validate(conn, args):
    v = engine.validate(args.get("spec"))
    out = {"spec": None, "description": None,
           "assumed": [a.to_dict() for a in v.assumed],
           "errors": [e.to_dict() for e in v.errors], "missing": [], "notes": []}
    if not v.ok:
        return json.loads(engine.canonical_json(out))
    cov = gather(conn, v.spec, _today())
    out.update(spec=v.spec.to_dict(), description=engine.describe(v.spec),
               missing=cov.missing, notes=cov.notes)
    return json.loads(engine.canonical_json(out))


def _execute(conn, raw_spec, sha, gz, dataset, fetched, bootstrap):
    # The raw spec, not the normalized one: `assumed` lists the defaults the
    # caller left out, and only the raw spec still has them left out.
    try:
        result = engine.run_backtest(raw_spec, dataset, sha, fetched)
    except engine.SpecError as e:
        raise ToolError(f"invalid spec: {e}") from None
    except EngineError as e:
        raise ToolError(f"backtest cannot run: {e}") from None
    try:
        stored = store(conn, result, gz, dataset, _caller(), _run_id(), _now())
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise ToolError(f"storing the result failed, nothing was saved: {e}") from None
    # After the commit: the app reads the rows the notice points at. A repeat
    # run republishes, so a lost notice is recovered by running again.
    body = envelope(result)   # outside the try: an oversize notice is not transient
    try:
        publish(bootstrap, result.experiment_id, body)
    except Exception as e:
        raise ToolError(f"experiment {result.experiment_id} is stored but its "
                        f"backtest.completed notice failed ({e}); make the same call again "
                        "to republish it") from None
    return result, stored


def do_run(conn, raw_spec, bootstrap, **extra):
    spec = _validated(raw_spec)
    cov = gather(conn, spec, _today())
    if cov.blocking:
        raise ToolError("data missing, load it with the prices tool first: "
                        + json.dumps(cov.missing)
                        # The executor keeps only stderr's last 2000 characters.
                        + " (" + _short("; ".join(cov.notes), 800) + ")")
    _, sha, gz, dataset, fetched = pin(cov)
    result, stored = _execute(conn, raw_spec, sha, gz, dataset, fetched, bootstrap)
    if cov.missing:
        extra["missing"] = cov.missing
    return summary(result, stored, **extra)


def do_rerun(conn, args, bootstrap):
    eid = args.get("experiment_id")
    if not isinstance(eid, str) or not EXPERIMENT_ID_RE.match(eid):
        raise ToolError("experiment_id must be 32 lowercase hex characters", 2)
    with conn.cursor() as cur:
        cur.execute("SELECT spec, dataset_sha FROM backtest_experiments WHERE id = %s", (eid,))
        row = cur.fetchone()
    if row is None:
        raise ToolError(f"no experiment {eid}")
    stored_spec, old_sha = row
    if isinstance(stored_spec, str):
        stored_spec = json.loads(stored_spec)
    rerun_of = {"experiment_id": eid, "dataset_sha": old_sha}
    if args.get("refresh"):
        return do_run(conn, stored_spec, bootstrap, rerun_of=rerun_of)
    with conn.cursor() as cur:
        cur.execute("SELECT rows_gz FROM backtest_datasets WHERE sha = %s", (old_sha,))
        ds = cur.fetchone()
    if ds is None:
        raise ToolError(f"pinned dataset {old_sha} is gone; rerun with refresh: true")
    sha, gz, dataset, fetched = unpin(bytes(ds[0]), old_sha)
    result, stored = _execute(conn, stored_spec, sha, gz, dataset, fetched, bootstrap)
    # A different id on the same pin means the engine version changed.
    return summary(result, stored, reproduced=result.experiment_id == eid, rerun_of=rerun_of)


def _caller():
    return os.environ.get("TOOL_CALLER_AGENT", "")[:128]


def _run_id():
    rid = os.environ.get("TOOL_RUN_ID", "")
    return rid if RUN_ID_RE.match(rid) else None


def dispatch(args):
    action = args.get("action")
    if action not in ACTIONS:
        raise ToolError(f"action must be one of {', '.join(ACTIONS)}", 2)
    if action == "describe_primitives":
        return json.loads(engine.canonical_json(engine.describe_primitives()))
    bootstrap = None
    if action in ("run", "rerun"):
        bootstrap = os.environ.get("AP_KAFKA_BOOTSTRAP", "").strip()
        if not bootstrap:
            raise ToolError("backtest run needs AP_KAFKA_BOOTSTRAP (infra.kafka: true)", 2)
    conn = connect()
    try:
        check_schema(conn)
        if action == "validate":
            return do_validate(conn, args)
        if action == "run":
            return do_run(conn, args.get("spec"), bootstrap)
        return do_rerun(conn, args, bootstrap)
    finally:
        conn.close()


def main():
    try:
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object", 2)
        out = dispatch(args)
    except ToolError as e:
        print(" ".join(str(e).split()), file=sys.stderr)
        return e.code
    except Exception as e:  # one line for the model, never a traceback
        print(f"backtest failed: {type(e).__name__}: {' '.join(str(e).split())}",
              file=sys.stderr)
        return 1
    print(render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
