"""Pure-function tests (no network). The contribution arithmetic is the whole
reason this tool exists, so it is the thing most worth pinning down."""
import pandas as pd
import pytest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from run import (build, clean_symbol, completed_closes, normalize_weights,
                 series_closes, session_return)


def _at(day, time):
    return datetime.fromisoformat(f"{day}T{time}").replace(tzinfo=ZoneInfo("America/New_York"))


def _metadata(day="2026-10-05", close="16:00"):
    return {"exchangeTimezoneName": "America/New_York",
            "currentTradingPeriod": {"regular": {
                "start": int(_at(day, "09:30").timestamp()),
                "end": int(_at(day, close).timestamp())}}}


@pytest.mark.parametrize("time", ["08:00", "09:35", "15:59", "16:00", "16:14"])
def test_live_or_delayed_bar_is_not_a_completed_session(time):
    closes = {"2026-10-01": 100, "2026-10-02": 110, "2026-10-05": 99}
    result = completed_closes(closes, _metadata(), _at("2026-10-05", time))
    assert session_return(result) == ("2026-10-02", 10.0)


@pytest.mark.parametrize("close,time", [("16:00", "16:15"), ("13:00", "13:15")])
def test_completed_regular_or_early_close_is_included(close, time):
    closes = {"2026-10-02": 100, "2026-10-05": 110, "2026-10-06": 999}
    result = completed_closes(closes, _metadata(close=close), _at("2026-10-05", time))
    assert session_return(result) == ("2026-10-05", 10.0)


@pytest.mark.parametrize("metadata", [{}, {"currentTradingPeriod": {}},
    {"currentTradingPeriod": {"regular": {"start": "invalid", "end": None}}},
    _metadata("2026-10-02")])
def test_unknown_or_stale_session_metadata_excludes_today(metadata):
    closes = {"2026-10-01": 100, "2026-10-02": 110, "2026-10-05": 99}
    assert session_return(completed_closes(closes, metadata, _at("2026-10-05", "18:00"))) == ("2026-10-02", 10.0)


def test_weekend_keeps_last_actual_session():
    assert completed_closes({"2026-10-02": 100}, _metadata("2026-10-02"),
                            _at("2026-10-04", "10:00")) == {"2026-10-02": 100}


def test_cutoff_uses_exchange_date_not_utc_date():
    now = _at("2026-10-05", "21:00").astimezone(ZoneInfo("UTC"))
    assert completed_closes({"2026-10-05": 100}, {}, now) == {}


def test_build_refuses_a_single_completed_bar(monkeypatch):
    ticker = SimpleNamespace(history=lambda **kwargs: _frame({
        "2026-10-02": 100, "2026-10-05": 110}), history_metadata=_metadata())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", SimpleNamespace(Ticker=lambda index: ticker))
    with pytest.raises(LookupError, match="completed-session"):
        build("QQQ", now=_at("2026-10-05", "09:35"))


def test_build_attributes_holdings_on_completed_index_session(monkeypatch):
    ticker = SimpleNamespace(history=lambda **kwargs: _frame({
        "2026-10-01": 100, "2026-10-02": 110, "2026-10-05": 99}),
        history_metadata=_metadata())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", SimpleNamespace(Ticker=lambda index: ticker))
    monkeypatch.setattr("run.read_holdings", lambda ticker: ({"NVDA": 10}, {"NVDA": "Nvidia"}, {}, ""))
    monkeypatch.setattr("run.holding_closes", lambda symbols, index: {
        "NVDA": {"2026-10-01": 100, "2026-10-02": 120, "2026-10-05": 60}})
    result = build("QQQ", now=_at("2026-10-05", "09:35"))
    assert result["day"] == "2026-10-02"
    assert result["return_pct"] == 10
    assert result["holdings"][0]["return_pct"] == 20
    assert result["explained_bps"] == 200


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
