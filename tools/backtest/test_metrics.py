"""Metrics, caveats, the entry point and the golden output.

Expected numbers are stated with the flows that produce them, so each can be
re-checked by hand or in a spreadsheet (Excel/Sheets `XIRR` uses the same
365-day year as `metrics.xirr`).

Golden files: `golden/*.json` hold the canonical JSON of two fixture runs.
After an intended semantic change, bump `engine/version.py` and regenerate:

    BACKTEST_GOLDEN_REGENERATE=1 ../../.venv-tools/bin/python -m pytest -q test_metrics.py

The regenerating run skips (never passes silently); review `git diff golden/`.
"""
import hashlib
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from engine import ENGINE_VERSION, canonical_json, experiment_id, parse_spec, run_backtest
from engine import caveats as cv
from engine import metrics as M
from engine.data import Dataset
from engine.result import Event, SeriesPoint, StrategyResult
from engine.simulate import StrategyRun

D = date.fromisoformat
d = Decimal
GOLDEN = Path(__file__).parent / "golden"
REGENERATE = os.environ.get("BACKTEST_GOLDEN_REGENERATE") == "1"


def series(*rows):
    """rows: (day, value, contributed)."""
    return tuple(SeriesPoint(D(a), d(str(v)), d(str(c))) for a, v, c in rows)


# ---- XIRR -------------------------------------------------------------------

def test_xirr_one_year_exact():
    # -1000 on 2023-01-01, +1100 on 2024-01-01: 365 days -> exactly 10%.
    rate, why = M.xirr([(D("2023-01-01"), d(-1000)), (D("2024-01-01"), d(1100))])
    assert (rate, why) == (d("0.10000000"), None)


def test_xirr_matches_spreadsheet_value():
    # Flows (Excel: =XIRR({-1000,-1000,2300}, {2024-01-01,2024-07-01,2025-01-01})):
    #   2024-01-01  -1000
    #   2024-07-01  -1000   (182 days = 0.498630 y)
    #   2025-01-01  +2300   (366 days = 1.002740 y)
    # Root of sum(c / (1+r)^(days/365)) = 0 is r = 0.2021415 (20.21%).
    rate, why = M.xirr([(D("2024-01-01"), d(-1000)), (D("2024-07-01"), d(-1000)),
                        (D("2025-01-01"), d(2300))])
    assert why is None
    assert rate == d("0.20214150")


def test_xirr_from_the_series_uses_contribution_deltas_and_final_value():
    s = series(("2024-01-01", 1000, 1000), ("2024-04-01", 1050, 1000),
               ("2024-07-01", 2080, 2000), ("2025-01-01", 2300, 2000))
    assert M.flows(s) == [(D("2024-01-01"), d(-1000)), (D("2024-07-01"), d(-1000)),
                          (D("2025-01-01"), d(2300))]
    # A deposit on the last day nets against the final value.
    s2 = s[:-1] + series(("2025-01-01", 3300, 3000))
    assert M.flows(s2)[-1] == (D("2025-01-01"), d(2300))
    assert M.xirr(M.flows(s))[0] == d("0.20214150")


def test_xirr_refuses_rather_than_guess():
    # No sign change: every flow is money in.
    assert M.xirr([(D("2024-01-01"), d(-1)), (D("2024-02-01"), d(-1))]) == \
        (None, "the cash flows never change sign")
    # All flows on one day: the rate does not enter the equation.
    assert M.xirr([(D("2024-01-01"), d(-1)), (D("2024-01-01"), d(2))]) == \
        (None, "all cash flows fall on one day")
    assert M.xirr([]) == (None, "no money was contributed")


def test_xirr_falls_back_to_bisection_when_newton_diverges(monkeypatch):
    monkeypatch.setattr(M, "_newton", lambda flows: None)
    rate, why = M.xirr([(D("2024-01-01"), d(-1000)), (D("2024-07-01"), d(-1000)),
                        (D("2025-01-01"), d(2300))])
    assert (rate, why) == (d("0.20214150"), None)


# ---- TWR, drawdown, volatility, Sharpe ----------------------------------------

def test_twr_neutralizes_contributions():
    # +10% on day 2; a 1000 deposit on day 3 with no market move (2100 =
    # 1100 + 1000); -10% on day 4. TWR = 1.1 * 1.0 * 0.9 - 1 = -0.01 even
    # though the account holds 1890 on 2000 contributed (XIRR would see that).
    s = series(("2024-01-02", 1000, 1000), ("2024-01-03", 1100, 1000),
               ("2024-01-04", 2100, 2000), ("2024-01-05", 1890, 2000))
    assert M.daily_returns(s) == [(D("2024-01-03"), d("0.1")), (D("2024-01-04"), d(0)),
                                  (D("2024-01-05"), d("-0.1"))]
    m = M.series_metrics(s, d(0))
    assert m["twr"] == d("-0.01")


def test_twr_annualized_over_365_25_day_years_from_first_flow():
    # Nothing before the first deposit counts: days with value 0 are skipped.
    # 2020-01-01 -> 2022-01-01 is 731 days; growth 1.21:
    # 1.21 ** (365.25 / 731) - 1 = 0.0999283 (not exactly 10%: 731 != 2 * 365.25).
    s = series(("2019-12-31", 0, 0), ("2020-01-01", 100, 100), ("2021-01-01", 110, 100),
               ("2022-01-01", 121, 100))
    m = M.series_metrics(s, d(0))
    assert m["twr"] == d("0.21")
    assert m["twr_annualized"] == d("0.09992829")


def test_drawdown_with_recovery():
    # TWR index 1, 1.2 (peak 01-03), 0.9 (trough 01-04, depth 0.25), 1.0,
    # 1.25 (first close back at or above 1.2 -> recovery 01-08).
    s = series(("2024-01-02", 100, 100), ("2024-01-03", 120, 100), ("2024-01-04", 90, 100),
               ("2024-01-05", 100, 100), ("2024-01-08", 125, 100))
    assert M.series_metrics(s, d(0))["max_drawdown"] == {
        "depth": d("0.25"), "peak": D("2024-01-03"), "trough": D("2024-01-04"),
        "recovery": D("2024-01-08")}


def test_drawdown_without_recovery_and_contributions_do_not_hide_it():
    # A deposit on 01-04 lifts the value (90 -> 190) but not the index.
    s = series(("2024-01-02", 100, 100), ("2024-01-03", 120, 100), ("2024-01-04", 190, 200),
               ("2024-01-05", 200, 200))
    dd = M.series_metrics(s, d(0))["max_drawdown"]
    # Index: 1, 1.2, 1.2 * (190-100)/120 = 0.9, 0.9 * 200/190 = 0.947368...
    assert dd == {"depth": d("0.25"), "peak": D("2024-01-03"), "trough": D("2024-01-04"),
                  "recovery": None}


def test_no_drawdown_is_depth_zero():
    s = series(("2024-01-02", 100, 100), ("2024-01-03", 101, 100))
    assert M.series_metrics(s, d(0))["max_drawdown"] == {
        "depth": d(0), "peak": None, "trough": None, "recovery": None}


def test_volatility_and_sharpe_with_nonzero_risk_free():
    # Daily returns 0.01, -0.01, 0.02, 0: mean 0.005, sample std
    # sqrt(0.0005 / 3) = 0.01290994. vol = std * sqrt(252) = 0.20493902.
    # Sharpe with rf 2.52%/yr: (0.005 - 0.0252/252) / std * sqrt(252) = 6.02520705.
    s = series(("2024-01-02", 100, 100), ("2024-01-03", 101, 100),
               ("2024-01-04", "99.99", 100), ("2024-01-05", "101.9898", 100),
               ("2024-01-08", "101.9898", 100))
    m = M.series_metrics(s, d("0.0252"))
    assert m["volatility"] == d("0.20493902")
    assert m["sharpe"] == d("6.02520705")
    assert M.series_metrics(s, d(0))["sharpe"] == d("6.14817046")  # 0.005/std*sqrt(252)


def test_flat_series_has_no_sharpe():
    s = series(("2024-01-02", 100, 100), ("2024-01-03", 100, 100), ("2024-01-04", 100, 100))
    m = M.series_metrics(s, d(0))
    assert (m["volatility"], m["sharpe"]) == (d(0), None)


def test_total_loss():
    # Everything lost: money-weighted and time-weighted are both -100%, the
    # drawdown is total and never recovers.
    s = series(("2024-01-02", 100, 100), ("2024-06-03", 40, 100), ("2025-01-02", 0, 100))
    assert M.xirr(M.flows(s)) == (d(-1), None)
    m = M.series_metrics(s, d(0))
    assert (m["twr"], m["twr_annualized"]) == (d(-1), d(-1))
    assert m["max_drawdown"] == {"depth": d(1), "peak": D("2024-01-02"),
                                 "trough": D("2025-01-02"), "recovery": None}


# ---- event aggregates ---------------------------------------------------------

def test_event_aggregates_convert_foreign_slippage_to_base():
    ev = (
        Event(D("2024-01-02"), "contribution", None, {"amount": d(1000)}),
        Event(D("2024-01-02"), "fx", None, {"from": "CAD", "to": "USD", "rate": d("1.35"),
                                            "amount": d("997.50"), "converted": d("738.89"),
                                            "fee": d("2.50")}),
        Event(D("2024-01-02"), "buy", "AAA", {"shares": d(7), "price": d("100.05"),
                                              "amount": d("700.35"), "currency": "USD",
                                              "cost_base": d("1000"), "commission": d(1),
                                              "slippage": d("0.35"), "source": "allocation"}),
        Event(D("2024-01-02"), "buy", "XIU.TO", {"shares": d(1), "price": d("30"),
                                                 "amount": d("30"), "currency": "CAD",
                                                 "cost_base": d("31"), "commission": d(1),
                                                 "slippage": d("0.10"), "source": "allocation"}),
        Event(D("2024-02-01"), "dividend", "AAA", {"net_base": d("4.05")}),
        Event(D("2024-03-01"), "sell", "XIU.TO", {"shares": d(1), "amount": d("40"),
                                                  "currency": "CAD",
                                                  "proceeds_base": d("39"),
                                                  "commission": d(1), "slippage": d("0.02"),
                                                  "source": "rotate"}),
        Event(D("2024-03-01"), "buy", "BBB", {"shares": d(1), "amount": d("39"),
                                              "currency": "CAD", "cost_base": d("39"),
                                              "commission": d(1), "slippage": d(0),
                                              "decided": D("2024-02-29"),
                                              "source": "rotate"}),
    )
    agg = M.event_metrics(ev, "CAD")
    # slippage: 0.35 USD * 1.35 = 0.4725 -> 0.47 CAD, + 0.10 + 0.02 + 0 = 0.59
    assert agg == {"trades": 4, "commissions": d(4), "slippage": d("0.59"),
                   "costs": d("4.59"), "fx_paid": d("2.50"), "dividends": d("4.05"),
                   "sold": d(40)}
    # The pick timeline: what each day bought and what was held after it.
    # The 03-01 rotate sold XIU.TO and bought BBB; AAA is still held.
    assert M.pick_timeline(ev) == [
        {"day": D("2024-01-02"), "bought": ["AAA", "XIU.TO"], "held": ["AAA", "XIU.TO"]},
        {"day": D("2024-03-01"), "bought": ["BBB"], "held": ["AAA", "BBB"]}]


def test_pick_timeline_logs_changes_only_and_ignores_dividend_buys():
    def buy(day, sym, source="allocation"):
        return Event(D(day), "buy", sym, {"shares": d(1), "source": source})
    ev = (buy("2024-01-02", "SPY"), buy("2024-02-01", "SPY"), buy("2024-02-15", "SPY", "dividend"),
          buy("2024-03-01", "SPY"), buy("2024-04-01", "AAA"), buy("2024-05-01", "AAA"))
    assert M.pick_timeline(ev) == [
        {"day": D("2024-01-02"), "bought": ["SPY"], "held": ["SPY"]},
        {"day": D("2024-04-01"), "bought": ["AAA"], "held": ["AAA", "SPY"]}]


def test_turnover_is_annual_sales_over_average_value():
    # 40 sold over a 366-day span with an average value of 100:
    # 40 / 100 / (366 / 365.25) = 0.39918033
    s = series(("2024-01-01", 100, 100), ("2025-01-01", 100, 100))
    assert M.turnover(d(40), s) == d("0.39918033")
    assert M.turnover(d(0), s) == d(0)


# ---- caveats ------------------------------------------------------------------

def _spec(strategies, **over):
    raw = {"name": "t", "period": {"start": "2024-01-02", "end": "2024-03-01"},
           "base_currency": "USD", "lump_sum": {"amount": 1000}, "strategies": strategies}
    raw.update(over)
    return parse_spec(raw)


def _run(sid, max_weights=None):
    mw = max_weights or {}
    top = max(mw.items(), key=lambda kv: kv[1]["weight"], default=None)
    return StrategyRun(StrategyResult(sid, sid), None if top is None else
                       {"weight": top[1]["weight"], "symbol": top[0], "day": top[1]["day"]},
                       mw)


def test_hindsight_only_for_individual_stocks():
    ds = Dataset.from_dict({"SPY": {"currency": "USD", "bars": [("2024-01-02", 1, 1, 0, None)]}})
    etf = _spec([{"id": "a", "allocate": {"fixed": {"SPY": 1}}}])
    codes = [c.code for c in cv.build(etf, ds, "ab" * 32, [_run("a")], {})]
    assert codes == ["data", "taxes"]
    stock = _spec([{"id": "a", "allocate": {"rank": {
        "universe": ["SPY", "NVDA", "AAPL"], "signal": {"trailing_return": {"lookback": "1mo"}},
        "pick": {"top": 3}}}}])
    got = cv.build(stock, ds, "ab" * 32, [_run("a")], {})
    hind = [c for c in got if c.code == "hindsight"]
    assert hind[0].text.startswith("universe chosen with today's knowledge; winners picked "
                                   "in hindsight inflate results")
    assert "AAPL, NVDA" in hind[0].text


def test_concentration_on_pick_top_2_or_single_name_over_40pct():
    ds = Dataset.from_dict({"SPY": {"currency": "USD", "bars": [("2024-01-02", 1, 1, 0, None)]}})
    rank1 = {"id": "r", "allocate": {"rank": {
        "universe": ["SPY", "QQQ"], "signal": {"trailing_return": {"lookback": "1mo"}},
        "pick": {"top": 1}}}}
    mix = {"id": "m", "allocate": {"fixed": {"SPY": "0.55", "AAPL": "0.45"}}}
    spy = {"id": "s", "allocate": {"fixed": {"SPY": 1}}}
    spec = _spec([rank1, mix, spy])
    runs = [_run("r", {"QQQ": {"weight": d("0.998"), "day": D("2024-01-02")}}),
            _run("m", {"SPY": {"weight": d("0.55"), "day": D("2024-01-02")},
                       "AAPL": {"weight": d("0.451234"), "day": D("2024-02-01")}}),
            _run("s", {"SPY": {"weight": d(1), "day": D("2024-01-02")}})]
    conc = [c.text for c in cv.build(spec, ds, "ab" * 32, runs, {}) if c.code == "concentration"]
    assert conc == [
        'Strategy "r" picks only the top 1; its largest single-holding weight was 99.8% '
        '(QQQ on 2024-01-02). See its pick timeline beside the returns.',
        'Strategy "m" held a single stock above 40%; its largest single-stock weight was '
        '45.1% (AAPL on 2024-02-01). See its pick timeline beside the returns.',
    ]


def test_taxes_and_data_text():
    ds = Dataset.from_dict({"SPY": {"currency": "USD", "bars": [
        ("2024-01-02", 1, 1, 0, None), ("2024-03-01", 1, 1, 0, None)]}})
    spec = _spec([{"id": "a", "allocate": {"fixed": {"SPY": 1}}}],
                 dividends={"mode": "cash", "withholding_pct": 15})
    got = {c.code: c.text for c in cv.build(spec, ds, "ab" * 32, [_run("a")], {},
                                            fetched=(D("2026-09-01"), D("2026-09-28")))}
    assert got["taxes"] == ("Dividends are reduced by 15% withholding tax; no other tax is "
                            "modelled, and account type (TFSA, RRSP or taxable) is not "
                            "modelled.")
    assert got["data"] == ("Yahoo Finance data through yfinance, unaudited. Dataset sha256 "
                           + "ab" * 32 + ", bars 2024-01-02 to 2024-03-01, fetched "
                           "2026-09-01 to 2026-09-28.")


# ---- the entry point + golden output --------------------------------------------

def _weekdays(start, end, skip=()):
    out, day = [], D(start)
    while day <= D(end):
        if day.weekday() < 5 and day.isoformat() not in skip:
            out.append(day)
        day += timedelta(days=1)
    return out


def _bars(currency, days, base, drift, swing, divs=None, turn=None):
    """Deterministic closes: base + drift per bar (switching to turn[1] from
    bar turn[0]) + a 7-bar zigzag of `swing`; adj_close by Yahoo's
    convention so the total-return check passes."""
    divs = divs or {}
    k, drift2 = turn or (len(days), 0)
    rows, prev_c, prev_a = [], None, None
    for i, day in enumerate(days):
        c = (d(str(base)) + d(str(drift)) * min(i, k) + d(str(drift2)) * max(0, i - k)
             + d(str(swing)) * (i % 7 - 3))
        div = d(str(divs.get(day.isoformat(), 0)))
        a = c if prev_c is None else (prev_a * c / (prev_c - div)).quantize(d("0.000001"))
        rows.append((day.isoformat(), str(c), str(a), str(div), None))
        prev_c, prev_a = c, a
    return {"currency": currency, "bars": rows}


def fixture_dataset():
    us = _weekdays("2023-11-01", "2024-06-28", skip={"2024-01-15", "2024-02-19", "2024-03-29",
                                                     "2024-05-27"})
    ca = _weekdays("2023-11-01", "2024-06-28", skip={"2024-02-19", "2024-03-29",
                                                     "2024-05-20"})
    return {
        "SPY": _bars("USD", us, 470, "0.4", "1.5", {"2024-03-15": "1.60"}),
        # AAA leads until mid-March, then BBB does: the rank's pick changes.
        "AAA": _bars("USD", us, 100, "0.35", 3, turn=(90, "-0.5")),
        "BBB": _bars("USD", us, 50, "-0.05", 2, {"2024-02-09": "0.25"}, turn=(90, "0.4")),
        "XIU.TO": _bars("CAD", ca, 31, "0.02", "0.3", {"2024-03-22": "0.20"}),
        "CAD=X": _bars(None, _weekdays("2023-11-01", "2024-06-28"), "1.35", "0.0002", "0.004"),
    }


def dataset_sha(raw):
    return hashlib.sha256(canonical_json(raw).encode()).hexdigest()


FIXTURE_SPECS = {
    # CAD money, monthly DCA, a hindsight 1-winner rank (keep) vs an ETF benchmark.
    "cad_dca_rank_vs_etf": {
        "name": "golden-cad-dca", "period": {"start": "2024-01-02", "end": "2024-06-28"},
        "base_currency": "CAD",
        "contributions": {"amount": 500, "every": "month", "on": "first_trading_day"},
        "costs": {"commission": 0, "slippage_bps": 5, "fx_bps": 25},
        "strategies": [
            {"id": "xiu", "allocate": {"fixed": {"XIU.TO": 1}}},
            {"id": "winner", "allocate": {"rank": {
                "universe": ["AAA", "BBB", "SPY"],
                "signal": {"trailing_return": {"lookback": "1mo"}}, "pick": {"top": 1}}}},
        ],
        "benchmark": "xiu",
    },
    # USD lump sum, trend filter into cash, monthly rebalance, whole shares,
    # commission, cash dividends with withholding, rf != 0.
    "usd_lump_trend_rebalance": {
        "name": "golden-usd-trend", "period": {"start": "2024-01-02", "end": "2024-06-28"},
        "base_currency": "USD", "lump_sum": {"amount": 10000},
        "execution": {"whole_shares": True},
        "dividends": {"mode": "cash", "withholding_pct": 15},
        "costs": {"commission": "4.95", "slippage_bps": 10, "fx_bps": 0},
        "risk_free": "0.04",
        "strategies": [
            {"id": "trend", "allocate": {"when": {
                "symbol": "SPY", "signal": {"price_vs_sma": {"n": 20}}, "op": ">",
                "threshold": 0, "then": {"fixed": {"SPY": "0.6", "BBB": "0.4"}}}},
             "holdings": {"rebalance": {"every": "month"}}},
            {"id": "sixty-forty", "allocate": {"fixed": {"SPY": "0.6", "BBB": "0.4"}},
             "holdings": "rotate"},
        ],
        "benchmark": "sixty-forty",
    },
}


def test_run_backtest_end_to_end():
    raw = fixture_dataset()
    sha = dataset_sha(raw)
    res = run_backtest(FIXTURE_SPECS["cad_dca_rank_vs_etf"], Dataset.from_dict(raw), sha)
    spec = parse_spec(FIXTURE_SPECS["cad_dca_rank_vs_etf"])
    assert res.engine_version == ENGINE_VERSION == "1.0.0"
    assert res.experiment_id == experiment_id(spec, sha, ENGINE_VERSION)
    assert [a.path for a in res.assumed][-1] == "calendar"
    assert {c.code for c in res.caveats} >= {"data", "taxes", "hindsight", "concentration"}
    m = res.strategies[0].metrics
    assert m["contributed"] == d(3000)
    assert m["profit"] == m["final_value"] - m["contributed"]
    assert m["xirr"] is not None and m["risk_free"] == d(0)
    # Byte-identical on a second run.
    again = run_backtest(FIXTURE_SPECS["cad_dca_rank_vs_etf"], raw, sha)
    assert canonical_json(again) == canonical_json(res)


def test_run_backtest_rejects_an_invalid_spec():
    from engine import SpecError
    with pytest.raises(SpecError):
        run_backtest({"name": "x"}, fixture_dataset(), "0" * 64)


@pytest.mark.parametrize("name", sorted(FIXTURE_SPECS))
def test_golden_output(name):
    raw = fixture_dataset()
    got = canonical_json(run_backtest(FIXTURE_SPECS[name], raw, dataset_sha(raw))) + "\n"
    path = GOLDEN / f"{name}.json"
    if REGENERATE:
        path.write_text(got)
        pytest.skip(f"regenerated {path.name}; review `git diff golden/` and rerun without "
                    "BACKTEST_GOLDEN_REGENERATE")
    assert path.exists(), f"{path} missing: run with BACKTEST_GOLDEN_REGENERATE=1"
    if got != path.read_text():
        pytest.fail(f"engine output for {name} changed: if the change is intended, bump "
                    "ENGINE_VERSION (engine/version.py) and regenerate with "
                    "BACKTEST_GOLDEN_REGENERATE=1", pytrace=False)
