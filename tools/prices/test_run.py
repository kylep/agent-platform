"""Pure-function tests (no network, no database — CI runs these against the
baked deps)."""
import pandas as pd
import pytest

from run import BACKFILL_RANGE, clean_symbol, frame_to_rows, plan_targets


def _frame():
    idx = pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06"])
    return pd.DataFrame({
        "Open": [100.0, 102.0, 101.5],
        "High": [103.0, 104.0, 102.0],
        "Low": [99.0, 101.0, 100.0],
        "Close": [102.123456, 103.0, 101.0],
        "Volume": [1_000_000.0, 1_200_000.0, float("nan")],
    }, index=idx)


def test_symbols_follow_yahoo_conventions():
    assert clean_symbol(" xiu.to ") == "XIU.TO"
    assert clean_symbol("brk-b") == "BRK-B"
    assert clean_symbol("^gspc") == "^GSPC"
    for bad in ["", "TOO-LONG-TICKER", "DROP TABLE", "A;B"]:
        with pytest.raises(ValueError):
            clean_symbol(bad)


def test_frame_to_rows_shape_and_rounding():
    rows = frame_to_rows("QQQ", _frame())
    assert [r[1] for r in rows] == ["2026-08-04", "2026-08-05", "2026-08-06"]
    assert rows[0][0] == "QQQ"
    assert rows[0][5] == 102.1235          # close rounded to 4dp
    assert rows[0][6] == 1_000_000         # volume is an int
    assert rows[2][6] is None              # NaN volume → None, bar still kept


def test_frame_to_rows_drops_bars_with_no_close():
    df = _frame()
    df.loc[df.index[1], "Close"] = float("nan")
    days = [r[1] for r in frame_to_rows("QQQ", df)]
    assert days == ["2026-08-04", "2026-08-06"]


def test_plan_targets_backfills_symbols_never_loaded():
    known = {"QQQ": "ok", "SPY": "ok", "TSLA": "pending"}
    plan = dict(plan_targets(None, known, "5d"))
    assert plan == {"QQQ": "5d", "SPY": "5d", "TSLA": BACKFILL_RANGE}


def test_plan_targets_explicit_symbols_may_be_untracked():
    plan = dict(plan_targets(["nvda", "qqq"], {"QQQ": "ok"}, "1y"))
    assert plan == {"NVDA": BACKFILL_RANGE, "QQQ": "1y"}


def test_plan_targets_empty_when_nothing_tracked():
    assert plan_targets(None, {}, "5d") == []
