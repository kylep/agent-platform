"""Pure-function tests (no network). The contribution arithmetic is the whole
reason this tool exists, so it is the thing most worth pinning down."""
import pandas as pd
import pytest

from run import (clean_symbol, normalize_weights, series_closes,
                 session_return)


def _frame(closes):
    idx = pd.to_datetime(sorted(closes))
    return pd.DataFrame({"Close": [closes[str(d.date())] for d in idx]}, index=idx)


def test_series_closes_drops_empty_bars():
    df = _frame({"2026-08-05": 100.0, "2026-08-06": 101.0})
    df.loc[df.index[1], "Close"] = float("nan")
    assert series_closes(df) == {"2026-08-05": 100.0}


def test_session_return_uses_the_previous_close():
    closes = {"2026-08-04": 100.0, "2026-08-05": 110.0, "2026-08-06": 99.0}
    assert session_return(closes) == ("2026-08-06", -10.0)
    assert session_return(closes, "2026-08-05") == ("2026-08-05", 10.0)


def test_session_return_needs_two_comparable_bars():
    assert session_return({"2026-08-06": 100.0}) is None
    # The earliest bar has nothing before it to compare against.
    assert session_return({"2026-08-05": 100.0, "2026-08-06": 99.0},
                          "2026-08-05") is None
    # A holding with no bar on the index's session is not a mover.
    assert session_return({"2026-08-05": 100.0, "2026-08-06": 99.0},
                          "2026-08-07") is None


def test_normalize_weights_accepts_fractions_or_percents():
    assert normalize_weights({"NVDA": 0.071, "MSFT": 0.068}) == \
        {"NVDA": 7.1, "MSFT": 6.8}
    assert normalize_weights({"NVDA": 7.1, "MSFT": 6.8}) == \
        {"NVDA": 7.1, "MSFT": 6.8}
    assert normalize_weights({}) == {}


def test_contribution_is_weight_times_return_in_bps():
    # The claim a brief rests on: 7.1% of the fund falling 3.1% moves the
    # index 22 basis points, not 3.1% and not 0.22bp.
    weight, ret = normalize_weights({"NVDA": 0.071})["NVDA"], -3.1
    assert round(weight * ret, 1) == -22.0


def test_clean_symbol_rejects_junk():
    assert clean_symbol(" qqq ") == "QQQ"
    for bad in ["", "A B", "WAY-TOO-LONG-X"]:
        with pytest.raises(ValueError):
            clean_symbol(bad)
