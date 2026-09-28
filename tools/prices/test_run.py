"""Tests with no network and no database: yfinance is replaced by a fake
module and psycopg.connect by an in-memory store that understands exactly the
statements run.py issues (CI runs these against the baked deps)."""
import io
import json
import sys
import types

import pandas as pd
import pytest

import run
from run import BACKFILL_RANGE, clean_symbol, frame_to_rows, new_events, plan_targets


def _frame():
    idx = pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06"])
    return pd.DataFrame({
        "Open": [100.0, 102.0, 101.5],
        "High": [103.0, 104.0, 102.0],
        "Low": [99.0, 101.0, 100.0],
        "Close": [102.123456, 103.0, 101.0],
        "Adj Close": [102.123456, 103.0, 101.0],
        "Volume": [1_000_000.0, 1_200_000.0, float("nan")],
        "Dividends": [0.0, 0.0, 0.0],
        "Stock Splits": [0.0, 0.0, 0.0],
    }, index=idx)


def _bars(days, dividends=None, splits=None):
    """A minimal auto_adjust=False frame: Close = 100, Adj Close = 90 (so the
    dividend-adjustment factor is 0.9), with optional action rows."""
    n = len(days)
    dividends = dividends or {}
    splits = splits or {}
    return pd.DataFrame({
        "Open": [100.0] * n, "High": [110.0] * n, "Low": [80.0] * n,
        "Close": [100.0] * n, "Adj Close": [90.0] * n,
        "Volume": [3_000_000_000.0] * n,
        "Dividends": [dividends.get(d, 0.0) for d in days],
        "Stock Splits": [splits.get(d, 0.0) for d in days],
    }, index=pd.to_datetime(days))


# ---- symbols ---------------------------------------------------------------

def test_symbols_follow_yahoo_conventions():
    assert clean_symbol(" xiu.to ") == "XIU.TO"
    assert clean_symbol("brk-b") == "BRK-B"
    assert clean_symbol("^gspc") == "^GSPC"
    assert clean_symbol("cad=x") == "CAD=X"
    for bad in ["", "TOO-LONG-TICKER", "DROP TABLE", "A;B", "A B", 'A"B',
                "A'B", "CAD=X;"]:
        with pytest.raises(ValueError):
            clean_symbol(bad)


# ---- frame → rows (price basis) ---------------------------------------------

def test_frame_to_rows_shape_and_rounding():
    rows = frame_to_rows("QQQ", _frame())
    assert [r["day"] for r in rows] == ["2026-08-04", "2026-08-05", "2026-08-06"]
    assert rows[0]["symbol"] == "QQQ"
    assert rows[0]["close"] == 102.1235            # rounded to 4dp
    assert rows[0]["volume"] == 1_000_000           # volume is an int
    assert rows[2]["volume"] is None                # NaN volume → None, bar kept


def test_frame_to_rows_drops_bars_with_no_close():
    df = _frame()
    df.loc[df.index[1], "Close"] = float("nan")
    days = [r["day"] for r in frame_to_rows("QQQ", df)]
    assert days == ["2026-08-04", "2026-08-06"]


def test_basis_mapping_with_split_and_dividend_rows():
    days = ["2024-06-07", "2024-06-10", "2024-06-11"]
    df = _bars(days, dividends={"2024-06-11": 0.01},
               splits={"2024-06-10": 10.0})
    rows = {r["day"]: r for r in frame_to_rows("NVDA", df)}
    r = rows["2024-06-07"]
    assert r["close_split_adj"] == 100.0            # Yahoo Close
    assert r["adj_close"] == 90.0                   # Yahoo Adj Close
    assert r["close"] == 90.0                       # display = fully adjusted
    # OHLC scaled onto the same fully adjusted basis as `close`.
    assert (r["open"], r["high"], r["low"]) == (90.0, 99.0, 72.0)
    assert r["volume"] == 3_000_000_000             # needs BIGINT
    assert r["dividend"] == 0.0
    assert r["split_ratio"] is None                 # 0 → NULL
    assert rows["2024-06-10"]["split_ratio"] == 10.0
    assert rows["2024-06-11"]["dividend"] == 0.01


def test_nan_adj_close_falls_back_to_close():
    # FX pairs carry no dividend adjustment; a missing Adj Close is not a
    # missing bar.
    df = _bars(["2026-08-04", "2026-08-05", "2026-08-06"])
    df.loc[df.index[1], "Adj Close"] = float("nan")
    df.loc[df.index[2], "Adj Close"] = float("nan")
    df.loc[df.index[2], "Close"] = float("nan")
    rows = {r["day"]: r for r in frame_to_rows("CAD=X", df)}
    assert sorted(rows) == ["2026-08-04", "2026-08-05"]   # both missing → dropped
    r = rows["2026-08-05"]
    assert r["adj_close"] == r["close"] == r["close_split_adj"] == 100.0
    assert (r["open"], r["high"], r["low"]) == (100.0, 110.0, 80.0)


def test_frame_without_action_columns_still_maps():
    # FX series sometimes come back without Dividends / Stock Splits.
    df = _bars(["2026-08-04"]).drop(columns=["Dividends", "Stock Splits"])
    (r,) = frame_to_rows("CAD=X", df)
    assert r["dividend"] == 0.0 and r["split_ratio"] is None


# ---- refetch trigger ---------------------------------------------------------

def _rows(days, **kw):
    return frame_to_rows("NVDA", _bars(days, **kw))


def test_new_events_dividend_not_stored():
    rows = _rows(["2026-08-04", "2026-08-05"], dividends={"2026-08-05": 0.25})
    assert new_events(rows, {"2026-08-04": (0.0, None)}) == ["2026-08-05"]


def test_new_events_stored_dividend_differs_or_null():
    rows = _rows(["2026-08-05"], dividends={"2026-08-05": 0.25})
    assert new_events(rows, {"2026-08-05": (0.2, None)}) == ["2026-08-05"]
    # Pre-migration rows have NULL dividend: an event there is new to us.
    assert new_events(rows, {"2026-08-05": (None, None)}) == ["2026-08-05"]


def test_new_events_split_differs():
    rows = _rows(["2026-08-05"], splits={"2026-08-05": 4.0})
    assert new_events(rows, {"2026-08-05": (0.0, None)}) == ["2026-08-05"]


def test_new_events_already_stored_is_not_new():
    rows = _rows(["2026-08-05", "2026-08-06"], dividends={"2026-08-05": 0.25},
                 splits={"2026-08-06": 2.0})
    stored = {"2026-08-05": (0.25, None), "2026-08-06": (0.0, 2.0)}
    assert new_events(rows, stored) == []


def test_new_events_ignores_days_without_actions():
    rows = _rows(["2026-08-04", "2026-08-05"])
    assert new_events(rows, {}) == []


# ---- planning ---------------------------------------------------------------

def test_plan_targets_backfills_symbols_never_loaded():
    known = {"QQQ": "ok", "SPY": "ok", "TSLA": "pending"}
    plan = dict(plan_targets(None, known, "5d"))
    assert plan == {"QQQ": "5d", "SPY": "5d", "TSLA": BACKFILL_RANGE}


def test_plan_targets_explicit_symbols_may_be_untracked():
    plan = dict(plan_targets(["nvda", "qqq"], {"QQQ": "ok"}, "1y"))
    assert plan == {"NVDA": BACKFILL_RANGE, "QQQ": "1y"}


def test_plan_targets_empty_when_nothing_tracked():
    assert plan_targets(None, {}, "5d") == []


def test_plan_targets_accepts_10y_and_it_beats_the_backfill():
    # A backtest asking for 10y of a never-loaded symbol must get 10y, not the
    # shorter default backfill.
    plan = dict(plan_targets(["SPY", "QQQ"], {"QQQ": "ok"}, "10y"))
    assert plan == {"SPY": "10y", "QQQ": "10y"}
    assert dict(plan_targets(["SPY"], {}, "max")) == {"SPY": "max"}


def test_plan_targets_rejects_unknown_range():
    with pytest.raises(ValueError):
        plan_targets(["SPY"], {}, "20y")


# ---- end-to-end through main() with fakes -------------------------------------

REQUIRED = {("bars", c) for c in (
    "symbol", "day", "open", "high", "low", "close", "volume",
    "close_split_adj", "adj_close", "dividend", "split_ratio")} | {
    ("symbols", c) for c in (
        "symbol", "label", "kind", "status", "error", "last_synced_at",
        "added_at", "currency")}


class FakeDB:
    def __init__(self, columns=REQUIRED):
        self.columns = set(columns)
        self.symbols: dict[str, dict] = {}
        self.bars: dict[tuple[str, str], dict] = {}


class FakeCursor:
    def __init__(self, db):
        self.db, self._res = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        db, s = self.db, " ".join(sql.split())
        if "information_schema.columns" in s:
            self._res = sorted(db.columns)
        elif s.startswith("SELECT symbol, status FROM symbols"):
            self._res = [(k, v["status"]) for k, v in sorted(db.symbols.items())]
        elif s.startswith("SELECT day, dividend, split_ratio FROM bars"):
            sym, days = params
            self._res = [(d, db.bars[(sym, d)]["dividend"],
                          db.bars[(sym, d)]["split_ratio"])
                         for d in days if (sym, d) in db.bars]
        elif s.startswith("SELECT 1 FROM bars"):
            sym, before = params
            self._res = [(1,)] if any(
                k[0] == sym and k[1] < before for k in db.bars) else []
        elif s.startswith("INSERT INTO symbols"):
            sym, kind, now = params
            db.symbols.setdefault(sym, {"kind": kind, "status": "pending",
                                        "currency": None, "added_at": now})
        elif s.startswith("UPDATE symbols"):
            status, error, when, currency, sym = params
            if sym in db.symbols:
                row = db.symbols[sym]
                row.update(status=status, error=error, last_synced_at=when)
                if currency is not None:
                    row["currency"] = currency
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def executemany(self, sql, seq):
        assert " ".join(sql.split()).startswith("INSERT INTO bars")
        for p in seq:
            self.db.bars[(p["symbol"], p["day"])] = dict(p)

    def fetchall(self):
        return list(self._res)

    def fetchone(self):
        return self._res[0] if self._res else None


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class FakeYahoo:
    """Stands in for the yfinance module; frames[(symbol, period)]."""

    def __init__(self, frames, currency=None):
        self.frames, self.currency, self.calls = frames, currency or {}, []

    def Ticker(self, symbol):  # noqa: N802 — mirrors yfinance's API
        yahoo = self

        class T:
            @property
            def history_metadata(self):
                cur = yahoo.currency.get(symbol)
                return {"currency": cur} if cur else {}

            def history(self, period, auto_adjust, actions):
                assert auto_adjust is False and actions is True
                yahoo.calls.append((symbol, period))
                return yahoo.frames.get((symbol, period), pd.DataFrame())
        return T()


@pytest.fixture
def env(monkeypatch):
    for k, v in {"APP_DB_HOST": "h", "APP_DB_USER": "u",
                 "APP_DB_PASSWORD": "p", "APP_DB_NAME": "n"}.items():
        monkeypatch.setenv(k, v)

    def install(db, yahoo):
        fake_psycopg = types.SimpleNamespace(connect=lambda **kw: FakeConn(db))
        monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
        monkeypatch.setitem(sys.modules, "yfinance", yahoo)
    return install


def _call(monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(args)))
    code = run.main()
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def _seed(db, symbol, days, kind="watch", **kw):
    db.symbols[symbol] = {"kind": kind, "status": "ok", "currency": None}
    for r in frame_to_rows(symbol, _bars(days, **kw)):
        db.bars[(symbol, r["day"])] = r


RECENT = ["2026-08-04", "2026-08-05", "2026-08-06"]


def test_new_dividend_refetches_full_history_once(env, monkeypatch, capsys):
    db = FakeDB()
    _seed(db, "NVDA", ["2026-07-01", "2026-07-02"])
    recent = _bars(RECENT, dividends={"2026-08-05": 0.01})
    full = pd.concat([_bars(["2026-07-01", "2026-07-02"]), recent])
    yahoo = FakeYahoo({("NVDA", "5d"): recent, ("NVDA", "max"): full})
    env(db, yahoo)

    code, out, _ = _call(monkeypatch, capsys, {"symbols": ["NVDA"], "range": "5d"})
    assert code == 0
    assert yahoo.calls == [("NVDA", "5d"), ("NVDA", "max")]
    assert out["refetched"] == ["NVDA"]
    assert out["written"] == 5
    assert db.bars[("NVDA", "2026-08-05")]["dividend"] == 0.01

    # Second identical call: the dividend is stored now, so no refetch.
    yahoo.calls.clear()
    code, out, _ = _call(monkeypatch, capsys, {"symbols": ["NVDA"], "range": "5d"})
    assert code == 0
    assert yahoo.calls == [("NVDA", "5d")]
    assert out["refetched"] == []


def test_new_event_without_older_rows_does_not_refetch(env, monkeypatch, capsys):
    # Every stored row sits inside the fetched window, so the upsert alone
    # moves them all onto the new basis.
    db = FakeDB()
    _seed(db, "NVDA", ["2026-08-04"])
    yahoo = FakeYahoo({("NVDA", "5d"): _bars(RECENT, splits={"2026-08-05": 10.0})})
    env(db, yahoo)
    code, out, _ = _call(monkeypatch, capsys, {"symbols": ["NVDA"], "range": "5d"})
    assert code == 0 and yahoo.calls == [("NVDA", "5d")]
    assert out["refetched"] == []


def test_max_range_never_refetches(env, monkeypatch, capsys):
    db = FakeDB()
    _seed(db, "NVDA", ["2026-07-01"])
    yahoo = FakeYahoo({("NVDA", "max"): _bars(RECENT, dividends={"2026-08-05": 1.0})})
    env(db, yahoo)
    code, out, _ = _call(monkeypatch, capsys, {"symbols": ["NVDA"], "range": "max"})
    assert code == 0 and yahoo.calls == [("NVDA", "max")]
    assert out["refetched"] == []


def test_currency_recorded_and_never_guessed(env, monkeypatch, capsys):
    db = FakeDB()
    yahoo = FakeYahoo({("XIU.TO", "5y"): _bars(RECENT), ("ZZZ", "5y"): _bars(RECENT)},
                      currency={"XIU.TO": "CAD"})
    env(db, yahoo)
    code, _, _ = _call(monkeypatch, capsys, {"symbols": ["XIU.TO", "ZZZ"]})
    assert code == 0
    assert db.symbols["XIU.TO"]["currency"] == "CAD"
    assert db.symbols["ZZZ"]["currency"] is None


def test_currency_absent_keeps_the_known_value(env, monkeypatch, capsys):
    db = FakeDB()
    _seed(db, "SPY", RECENT)
    db.symbols["SPY"]["currency"] = "USD"
    env(db, FakeYahoo({("SPY", "5d"): _bars(RECENT)}))
    _call(monkeypatch, capsys, {"symbols": ["SPY"]})
    assert db.symbols["SPY"]["currency"] == "USD"


def test_kind_rules(env, monkeypatch, capsys):
    db = FakeDB()
    _seed(db, "^GSPC", RECENT, kind="index")
    frames = {(s, p): _bars(RECENT) for s in ("^GSPC", "CAD=X", "SPY")
              for p in ("5d", "5y")}
    env(db, FakeYahoo(frames))
    # New symbol, no kind given → research.
    _call(monkeypatch, capsys, {"symbols": ["SPY"]})
    assert db.symbols["SPY"]["kind"] == "research"
    # New symbol with an explicit kind.
    _call(monkeypatch, capsys, {"symbols": ["CAD=X"], "kind": "fx"})
    assert db.symbols["CAD=X"]["kind"] == "fx"
    # Existing rows keep their kind — an index is never demoted.
    _call(monkeypatch, capsys, {"symbols": ["^GSPC"], "kind": "research"})
    assert db.symbols["^GSPC"]["kind"] == "index"


def test_unknown_kind_fails(env, monkeypatch, capsys):
    env(FakeDB(), FakeYahoo({}))
    code, _, err = _call(monkeypatch, capsys, {"symbols": ["SPY"], "kind": "stock"})
    assert code == 2 and "kind" in err


@pytest.mark.parametrize("kind", ["index", "watch"])
def test_app_minted_kinds_are_refused(env, monkeypatch, capsys, kind):
    # index comes from the app's seed, watch from /watchlist — never from here.
    db = FakeDB()
    yahoo = FakeYahoo({("SPY", "5y"): _bars(RECENT)})
    env(db, yahoo)
    code, out, err = _call(monkeypatch, capsys, {"symbols": ["SPY"], "kind": kind})
    assert code == 2 and out is None and "kind" in err
    assert len(err.strip().splitlines()) == 1
    assert "SPY" not in db.symbols and yahoo.calls == []


def test_invalid_ticker_creates_no_symbol_row(env, monkeypatch, capsys):
    db = FakeDB()
    env(db, FakeYahoo({}))
    code, _, err = _call(monkeypatch, capsys, {"symbols": ["NOPE"]})
    assert code == 1 and "NOPE" in err
    assert "NOPE" not in db.symbols


def test_missing_columns_fail_clearly(env, monkeypatch, capsys):
    db = FakeDB(columns=REQUIRED - {("bars", "close_split_adj"),
                                    ("symbols", "currency")})
    yahoo = FakeYahoo({("SPY", "5y"): _bars(RECENT)})
    env(db, yahoo)
    code, out, err = _call(monkeypatch, capsys, {"symbols": ["SPY"]})
    assert code == 2 and out is None
    assert "bars.close_split_adj" in err and "symbols.currency" in err
    assert yahoo.calls == []                       # failed before fetching


def test_output_is_counts_only(env, monkeypatch, capsys):
    db = FakeDB()
    env(db, FakeYahoo({("SPY", "5y"): _bars(RECENT)}))
    _, out, _ = _call(monkeypatch, capsys, {"symbols": ["SPY"]})
    assert set(out) == {"written", "symbols", "errors", "refetched"}
    assert out["symbols"] == [{"symbol": "SPY", "rows": 3,
                               "first_day": "2026-08-04",
                               "last_day": "2026-08-06"}]
