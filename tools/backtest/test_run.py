"""The backtest tool with no network and no database: psycopg.connect is
replaced by an in-memory store that understands exactly the statements
run.py issues, and aiokafka by a producer that records what it was sent.
The price fixtures are the engine's own (test_metrics.fixture_dataset)."""
import copy
import gzip
import hashlib
import io
import json
import sys
import types
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import engine
import run
from test_metrics import FIXTURE_SPECS, fixture_dataset

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SYNCED = datetime(2026, 9, 27, 22, 0, tzinfo=timezone.utc)
CAD = FIXTURE_SPECS["cad_dca_rank_vs_etf"]
USD = FIXTURE_SPECS["usd_lump_trend_rebalance"]


# ---- the fakes --------------------------------------------------------------------

class FakeDB:
    """Tables as plain Python. Writes land in `pending` and only reach the
    tables on commit, so a rollback (or a crash before commit) leaves no rows."""

    def __init__(self, data=None, columns=None):
        self.symbols, self.bars = {}, {}
        for sym, entry in (data if data is not None else fixture_dataset()).items():
            self.symbols[sym] = {"currency": entry["currency"], "last_synced_at": SYNCED}
            self.bars[sym] = [(day, float(c), None if a is None else float(a),
                               float(v), None if s is None else float(s))
                              for day, c, a, v, s in entry["bars"]]
        self.tables = {t: [] for t in run.RESULT_TABLES}
        self.columns = columns if columns is not None else {
            (t, c) for t, cols in run.REQUIRED_COLUMNS.items() for c in cols}
        self.log, self.pending, self.commits = [], None, 0

    def connect(self, **kw):
        assert kw["options"] == "-c search_path=app_stockmarket"
        return FakeConn(self)

    def rows(self, table):
        return self.tables[table]


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.closed = False

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        db = self.db
        if db.pending:
            for table, row in db.pending:
                db.tables[table].append(row)
        db.pending = None
        db.commits += 1
        db.log.append("commit")

    def rollback(self):
        self.db.pending = None
        self.db.log.append("rollback")

    def close(self):
        self.closed = True


def _param_json(v):
    assert isinstance(v, str), "JSON columns go over as canonical strings"
    return json.loads(v)


class FakeCursor:
    def __init__(self, db):
        self.db, self._rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def _stage(self, table, row):
        if self.db.pending is None:
            self.db.pending = []
        self.db.pending.append((table, row))

    def _visible(self, table):
        return self.db.tables[table] + [r for t, r in (self.db.pending or []) if t == table]

    def execute(self, sql, params=()):
        db = self.db
        s = " ".join(sql.split())
        db.log.append(s.split(" (")[0][:60])
        if "information_schema.columns" in s:
            assert params == (run.SCHEMA,)
            self._rows = sorted(db.columns)
        elif s.startswith("SELECT symbol, currency, last_synced_at FROM symbols"):
            (syms,) = params
            self._rows = [(x, db.symbols[x]["currency"], db.symbols[x]["last_synced_at"])
                          for x in sorted(syms) if x in db.symbols]
        elif s.startswith("SELECT symbol, day, close_split_adj"):
            syms, lo, hi = params
            self._rows = [(x,) + b for x in sorted(syms) for b in db.bars.get(x, [])
                          if lo <= b[0] <= hi]
        elif s.startswith("INSERT INTO backtest_datasets"):
            sha, gz, syms, lo, hi, created = params
            if not any(r["sha"] == sha for r in self._visible("backtest_datasets")):
                self._stage("backtest_datasets", {"sha": sha, "rows_gz": gz,
                                                  "symbols": _param_json(syms),
                                                  "day_from": lo, "day_to": hi})
            self._rows = []
        elif s.startswith("INSERT INTO backtest_experiments"):
            assert "ON CONFLICT (id) DO NOTHING RETURNING id" in s
            keys = ("id", "name", "spec", "description", "assumed", "caveats", "exclusions",
                    "dataset_sha", "engine_version", "caller", "run_id", "created_at")
            row = dict(zip(keys, params))
            for k in ("spec", "assumed", "caveats", "exclusions"):
                row[k] = _param_json(row[k])
            if any(r["id"] == row["id"] for r in self._visible("backtest_experiments")):
                self._rows = []
            else:
                self._stage("backtest_experiments", row)
                self._rows = [(row["id"],)]
        elif s.startswith("SELECT spec, dataset_sha FROM backtest_experiments"):
            self._rows = [(r["spec"], r["dataset_sha"]) for r in db.tables["backtest_experiments"]
                          if r["id"] == params[0]]
        elif s.startswith("SELECT rows_gz FROM backtest_datasets"):
            self._rows = [(r["rows_gz"],) for r in db.tables["backtest_datasets"]
                          if r["sha"] == params[0]]
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def executemany(self, sql, rows):
        s = " ".join(sql.split())
        table = s.split()[2]
        assert table in ("backtest_results", "backtest_series", "backtest_events"), s
        self.db.log.append(f"executemany {table}")
        for r in rows:
            r = list(r)
            if table in ("backtest_results", "backtest_events"):
                r[-1] = _param_json(r[-1])
            self._stage(table, tuple(r))


class FakeProducer:
    sent = []

    def __init__(self, bootstrap_servers):
        assert bootstrap_servers == "kafka:9092"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send_and_wait(self, topic, value, key=None):
        FakeProducer.sent.append((topic, key, value))
        FakeProducer.db.log.append("publish")


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    install(monkeypatch, fake)
    return fake


def install(monkeypatch, fake):
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = fake.connect
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    aiokafka = types.ModuleType("aiokafka")
    aiokafka.AIOKafkaProducer = FakeProducer
    monkeypatch.setitem(sys.modules, "aiokafka", aiokafka)
    FakeProducer.sent, FakeProducer.db = [], fake
    for k, v in {"APP_DB_HOST": "pg", "APP_DB_USER": "u", "APP_DB_PASSWORD": "p",
                 "APP_DB_NAME": "d", "AP_KAFKA_BOOTSTRAP": "kafka:9092",
                 "TOOL_CALLER_AGENT": "stockmarket-data",
                 "TOOL_RUN_ID": "ab" * 16}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(run, "_today", lambda: TODAY)
    monkeypatch.setattr(run, "_now", lambda: NOW)


def call(monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(args)))
    code = run.main()
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), out, err


def notices():
    """(topic, key, envelope minus its per-publish id and ts)."""
    out = []
    for topic, key, value in FakeProducer.sent:
        env = json.loads(value)
        assert len(env.pop("id")) == 32 and env.pop("ts") == NOW.isoformat()
        out.append((topic, key, env))
    return out


# ---- describe_primitives ----------------------------------------------------------

def test_describe_primitives_needs_no_database(monkeypatch, capsys, db):
    def boom(**kw):
        raise AssertionError("describe_primitives must not connect")
    monkeypatch.setattr(sys.modules["psycopg"], "connect", boom)
    code, out, raw, _ = call(monkeypatch, capsys, {"action": "describe_primitives"})
    assert code == 0
    assert out == json.loads(engine.canonical_json(engine.describe_primitives()))
    assert len(raw.encode()) < 256 * 1024


def test_unknown_action_fails_clearly(monkeypatch, capsys, db):
    code, _, _, err = call(monkeypatch, capsys, {"action": "drop"})
    assert code == 2 and "action" in err and err.count("\n") == 1


# ---- validate ---------------------------------------------------------------------

def test_validate_complete_data(monkeypatch, capsys, db):
    code, out, _, _ = call(monkeypatch, capsys, {"action": "validate", "spec": CAD})
    assert code == 0
    assert out["errors"] == [] and out["missing"] == []
    assert out["spec"] == engine.validate(CAD).spec.to_dict()
    assert out["description"] == engine.describe(engine.validate(CAD).spec)
    assert {"path": "execution.whole_shares", "value": False} in out["assumed"]
    # Read-only: validate never writes or commits.
    assert db.commits == 0 and all(not r for r in db.tables.values())


def test_validate_invalid_spec_returns_errors(monkeypatch, capsys, db):
    code, out, _, _ = call(monkeypatch, capsys, {"action": "validate",
                                                 "spec": {"name": "x"}})
    assert code == 0
    assert out["spec"] is None and out["description"] is None
    assert out["errors"] and all({"path", "message"} <= set(e) for e in out["errors"])
    assert out["missing"] == []


def test_validate_names_the_exact_prices_calls(monkeypatch, capsys):
    data = fixture_dataset()
    del data["BBB"], data["CAD=X"]
    fake = FakeDB(data)
    install(monkeypatch, fake)
    code, out, _, _ = call(monkeypatch, capsys, {"action": "validate", "spec": CAD})
    assert code == 0
    # 2024 start from 2026-09-28 is ~2.8 years back: a 5y fetch covers it.
    assert out["missing"] == [{"symbols": ["CAD=X"], "range": "5y", "kind": "fx"},
                              {"symbols": ["BBB"], "range": "5y", "kind": "research"}]
    assert any("BBB" in n for n in out["notes"])


def test_validate_always_loads_the_calendar_symbol(monkeypatch, capsys):
    # A USD spec that trades only AAA still needs SPY: it is the US calendar.
    spec = {"name": "aaa", "period": {"start": "2024-01-02", "end": "2024-06-28"},
            "base_currency": "USD", "lump_sum": {"amount": 1000},
            "strategies": [{"id": "a", "allocate": {"fixed": {"AAA": 1}}}]}
    data = fixture_dataset()
    del data["SPY"]
    install(monkeypatch, FakeDB(data))
    _, out, _, _ = call(monkeypatch, capsys, {"action": "validate", "spec": spec})
    assert out["missing"] == [{"symbols": ["SPY"], "range": "5y", "kind": "research"}]
    # CAD=X is only asked for when currencies mix.
    assert all("CAD=X" not in m["symbols"] for m in out["missing"])


def test_validate_rows_from_before_the_migration_need_a_full_refetch(monkeypatch, capsys):
    fake = FakeDB()
    day, c, a, v, s = fake.bars["AAA"][-3]
    fake.bars["AAA"][-3] = (day, None, a, v, s)
    install(monkeypatch, fake)
    _, out, _, _ = call(monkeypatch, capsys, {"action": "validate", "spec": CAD})
    assert out["missing"] == [{"symbols": ["AAA"], "range": "max", "kind": "research"}]


def _trim_before(data, sym, start, keep):
    bars = data[sym]["bars"]
    before = [b for b in bars if b[0] < start]
    data[sym]["bars"] = before[len(before) - keep:] + [b for b in bars if b[0] >= start]


def test_coverage_needs_exactly_max_window_plus_one_bars(monkeypatch, capsys):
    # The 1mo rank is a 21-bar trailing return: 22 closes before the start.
    need = engine.validate(CAD).spec.max_window() + 1
    assert need == 22
    for keep, missing in ((need, []), (need - 1, [
            {"symbols": ["AAA"], "range": "5y", "kind": "research"}])):
        data = fixture_dataset()
        _trim_before(data, "AAA", "2024-01-02", keep)
        install(monkeypatch, FakeDB(data))
        _, out, _, _ = call(monkeypatch, capsys, {"action": "validate", "spec": CAD})
        assert out["missing"] == missing


# ---- run ----------------------------------------------------------------------------

def test_run_stores_everything_in_one_transaction_then_publishes(monkeypatch, capsys, db):
    code, out, raw, _ = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 0
    eid = out["experiment_id"]
    assert len(raw.encode()) <= 4096
    assert out["url"] == f"/apps/stockmarket/#/backtests/{eid}"
    # Defaults the spec left out are reported, plus the calendar the engine used.
    assert {"path": "execution.whole_shares", "value": False} in out["assumed"]
    assert out["assumed"][-1] == {"path": "calendar", "value": "XIU.TO"}
    # One commit, after every insert and before the notice.
    writes = [x for x in db.log if x.startswith(("INSERT", "executemany"))]
    assert writes == ["INSERT INTO backtest_datasets", "INSERT INTO backtest_experiments",
                      "executemany backtest_results", "executemany backtest_series",
                      "executemany backtest_events"]
    assert db.log.count("commit") == 1
    assert db.log.index("commit") > db.log.index("executemany backtest_events")
    assert db.log.index("publish") > db.log.index("commit")

    (exp,) = db.rows("backtest_experiments")
    assert exp["id"] == eid and exp["name"] == "golden-cad-dca"
    assert exp["caller"] == "stockmarket-data" and exp["run_id"] == "ab" * 16
    assert exp["engine_version"] == engine.ENGINE_VERSION
    (ds,) = db.rows("backtest_datasets")
    assert ds["sha"] == exp["dataset_sha"] == out["dataset_sha"]
    pinned = gzip.decompress(ds["rows_gz"])
    assert hashlib.sha256(pinned).hexdigest() == ds["sha"]
    # The calendar (XIU.TO for CAD) and FX (USD traded from CAD) are pinned.
    assert ds["symbols"] == ["AAA", "BBB", "CAD=X", "SPY", "XIU.TO"]
    assert json.loads(pinned)["fetched"] == ["2026-09-27", "2026-09-27"]
    assert {r[1] for r in db.rows("backtest_results")} == {"xiu", "winner"}
    assert len(db.rows("backtest_series")) == 2 * len(
        [p for p in db.rows("backtest_series") if p[1] == "xiu"])
    kinds = {r[3] for r in db.rows("backtest_events")}
    assert {"contribution", "buy", "fx", "dividend"} <= kinds

    ((topic, key, env),) = notices()
    assert topic == "app.stockmarket.backtest" and key == eid.encode()
    assert env["type"] == "backtest.completed" and env["key"] == eid
    data = env["data"]
    assert data["experiment_id"] == eid and data["name"] == "golden-cad-dca"
    assert [s["id"] for s in data["strategies"]] == ["xiu", "winner"]
    assert set(data["strategies"][0]["headline"]) == {
        "final_value", "contributed", "xirr", "twr_annualized", "max_drawdown_depth"}
    cols = out["metrics"]["columns"]
    assert cols[:2] == ["id", "label"] and len(out["metrics"]["rows"]) == 2
    xiu = dict(zip(cols, out["metrics"]["rows"][0]))
    assert xiu["id"] == "xiu"
    assert xiu["final_value"] == data["strategies"][0]["headline"]["final_value"]


def test_run_twice_is_a_no_op_but_still_notifies(monkeypatch, capsys, db):
    call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    before = copy.deepcopy(db.tables)
    code, out, _, _ = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 0 and out["stored"] is False
    assert db.tables == before
    # A lost first notice is recovered by running again: the consumer is idempotent.
    first, second = notices()
    assert first == second


def test_run_is_deterministic_across_databases(monkeypatch, capsys):
    stored = []
    for _ in range(2):
        fake = FakeDB()
        install(monkeypatch, fake)
        call(monkeypatch, capsys, {"action": "run", "spec": USD})
        stored.append((fake.tables, notices()))
    assert stored[0] == stored[1]


def test_run_refuses_on_missing_data_and_writes_nothing(monkeypatch, capsys):
    data = fixture_dataset()
    del data["CAD=X"]
    fake = FakeDB(data)
    install(monkeypatch, fake)
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 1
    assert "prices" in err and "CAD=X" in err and err.count("\n") == 1
    assert all(not r for r in fake.tables.values()) and notices() == []


def test_run_invalid_spec_fails_with_the_errors(monkeypatch, capsys, db):
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": {"name": "x"}})
    assert code == 1 and "period" in err and err.count("\n") == 1


def test_run_needs_kafka_before_it_writes(monkeypatch, capsys, db):
    monkeypatch.delenv("AP_KAFKA_BOOTSTRAP")
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 2 and "AP_KAFKA_BOOTSTRAP" in err
    assert all(not r for r in db.tables.values())


def test_missing_tables_fail_clearly(monkeypatch, capsys):
    fake = FakeDB(columns={(t, c) for t, cols in run.REQUIRED_COLUMNS.items()
                           for c in cols if not t.startswith("backtest_")})
    install(monkeypatch, fake)
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 2 and "backtest_experiments" in err and "deploy" in err


def test_a_failed_insert_rolls_back_and_publishes_nothing(monkeypatch, capsys, db):
    def broken(self, sql, rows):
        raise RuntimeError("disk full")
    monkeypatch.setattr(FakeCursor, "executemany", broken)
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 1 and "disk full" in err
    assert "rollback" in db.log and all(not r for r in db.tables.values())
    assert notices() == []


def test_a_failed_notice_keeps_the_rows_and_says_how_to_recover(monkeypatch, capsys, db):
    async def down(self, topic, value, key=None):
        raise ConnectionError("broker unreachable")
    monkeypatch.setattr(FakeProducer, "send_and_wait", down)
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 1 and "is stored" in err and "same call again" in err
    assert "broker unreachable" in err
    assert len(db.rows("backtest_experiments")) == 1


def test_an_oversize_notice_is_not_reported_as_retryable(monkeypatch, capsys, db):
    monkeypatch.setattr(run, "NOTICE_CAP", 10)
    code, _, _, err = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    assert code == 1 and "is stored" in err and "cap 10" in err
    assert "retrying will not help" in err and "same call again" not in err
    assert len(db.rows("backtest_experiments")) == 1 and notices() == []


# ---- rerun -------------------------------------------------------------------------

def test_rerun_reproduces_the_id_from_the_pinned_dataset(monkeypatch, capsys, db):
    _, first, _, _ = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    # Yahoo revises history after the fact: the pin must not notice.
    db.bars["AAA"] = [(d, c * 1.5, a * 1.5, v, s) for d, c, a, v, s in db.bars["AAA"]]
    code, out, _, _ = call(monkeypatch, capsys, {"action": "rerun",
                                                 "experiment_id": first["experiment_id"]})
    assert code == 0
    assert out["experiment_id"] == first["experiment_id"] and out["reproduced"] is True
    assert out["metrics"] == first["metrics"]
    assert len(notices()) == 2


def test_rerun_with_refresh_runs_the_stored_spec_on_fresh_data(monkeypatch, capsys, db):
    _, first, _, _ = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    db.bars["BBB"] = [(d, c * 1.1, a * 1.1, v * 1.1, s) for d, c, a, v, s in db.bars["BBB"]]
    code, out, _, _ = call(monkeypatch, capsys, {"action": "rerun", "refresh": True,
                                                 "experiment_id": first["experiment_id"]})
    assert code == 0
    assert out["experiment_id"] != first["experiment_id"]
    assert out["rerun_of"] == {"experiment_id": first["experiment_id"],
                               "dataset_sha": first["dataset_sha"]}
    assert out["dataset_sha"] != first["dataset_sha"]
    assert len(db.rows("backtest_experiments")) == 2


@pytest.mark.parametrize("eid", ["nope", "A" * 32, "0" * 31, None])
def test_rerun_rejects_a_malformed_id(monkeypatch, capsys, db, eid):
    code, _, _, err = call(monkeypatch, capsys, {"action": "rerun", "experiment_id": eid})
    assert code == 2 and "experiment_id" in err


def test_rerun_when_the_pinned_dataset_is_gone(monkeypatch, capsys, db):
    _, first, _, _ = call(monkeypatch, capsys, {"action": "run", "spec": CAD})
    db.tables["backtest_datasets"].clear()
    code, _, _, err = call(monkeypatch, capsys, {"action": "rerun",
                                                 "experiment_id": first["experiment_id"]})
    assert code == 1 and err.count("\n") == 1
    assert first["dataset_sha"] in err and "refresh: true" in err
    assert len(notices()) == 1   # only the original run's


def test_rerun_unknown_id(monkeypatch, capsys, db):
    code, _, _, err = call(monkeypatch, capsys, {"action": "rerun", "experiment_id": "0" * 32})
    assert code == 1 and "no experiment" in err


# ---- size bounds at the spec's limits ----------------------------------------------

def _worst_case_result():
    """8 strategies x 25 symbols, max-length name and labels (non-ASCII, so
    JSON escaping inflates them), every metric present and long."""
    syms = [f"S{i:02d}.TO" for i in range(23)] + ["SPY", "XIU.TO"]
    strategies = []
    for i in range(8):
        alloc = {"rank": {"universe": syms, "signal": {"trailing_return": {"lookback": "400d"}},
                          "pick": {"top": 5}}}
        if i % 2:
            alloc = {"when": {"symbol": "SPY", "signal": {"volatility": {"n": 400}}, "op": "<",
                              "threshold": "0.5", "then": alloc, "else": {"fixed": {"XIU.TO": 1}}}}
        strategies.append({"id": f"strategy-{i}-" + "x" * 20, "label": "é" * 60,
                           "allocate": alloc,
                           "holdings": {"rebalance": {"every": "month"}}})
    spec = engine.validate({"name": "ü" * 80, "period": {"start": "1996-01-02",
                                                         "end": "2025-12-31"},
                            "contributions": {"amount": "123456.78", "every": "month"},
                            "lump_sum": {"amount": 999999999},
                            "costs": {"commission": "9.99", "slippage_bps": 5, "fx_bps": 25},
                            "dividends": {"mode": "reinvest", "withholding_pct": 15},
                            "strategies": strategies})
    assert spec.ok, spec.errors
    big = Decimal("-123456789012.12")
    metrics = {k: big for k in ("final_value", "contributed", "profit", "costs",
                                "commissions", "slippage", "fx_paid", "dividends")}
    metrics.update({k: Decimal("-0.12345678") for k in (
        "xirr", "twr", "twr_annualized", "volatility", "sharpe", "turnover", "risk_free")})
    metrics.update(trades=123456, max_drawdown={"depth": Decimal("0.99999999"),
                                                "peak": date(2000, 1, 1),
                                                "trough": date(2002, 1, 1), "recovery": None},
                   pick_timeline=[{"day": date(2000, 1, 1), "bought": syms, "held": syms}] * 50)
    caveats = tuple(engine.Caveat(c, "t" * 400) for c in ["data", "taxes", "hindsight"]
                    + ["concentration", "xirr"] * 8)
    return engine.BacktestResult(
        experiment_id="f" * 32, engine_version=engine.ENGINE_VERSION, dataset_sha="e" * 64,
        spec=spec.spec.to_dict(), description=engine.describe(spec.spec),
        assumed=spec.assumed + (engine.Assumption("calendar", "x" * 200),), caveats=caveats,
        exclusions=(engine.Exclusion("s", date(2000, 1, 1), "S01.TO", "r"),) * 5000,
        strategies=tuple(engine.StrategyResult(s.id, s.label, metrics=metrics)
                         for s in spec.spec.strategies))


def test_worst_case_notice_and_summary_stay_small():
    res = _worst_case_result()
    assert len(run.envelope(res)) <= 8 * 1024
    text = run.render(run.summary(res, stored=True))
    assert len(text.encode()) <= 4096
    out = json.loads(text)
    assert out["experiment_id"] == "f" * 32 and out["exclusions"] == 5000
    assert len(out["metrics"]["rows"]) == 8 and out["url"].endswith("f" * 32)
    assert out["description"].endswith(f"(full text at {out['url']})")
