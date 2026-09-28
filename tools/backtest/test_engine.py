"""Engine core: calendar, schedule, signals, allocators, holdings, fills.

Every expected number below is worked by hand in the comment beside it.
Rounding: cash to cents, shares to 8 dp, both half-even.
"""
import random
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest

from engine import canonical_json, parse_spec
from engine import calendar as cal
from engine import signals
from engine.data import DataIntegrityError, Dataset, EngineError, check_total_return
from engine.metrics import event_metrics
from engine.simulate import simulate

D = date.fromisoformat
d = Decimal


def bars(currency, rows, adj=None):
    """rows: (day, close[, dividend[, split]]). adj_close is built with Yahoo's
    convention (ex-date ratio = close / (prev_close - dividend)) unless given,
    so the total-return cross-check passes by construction."""
    out, prev_close, prev_adj = [], None, None
    for i, row in enumerate(rows):
        day, close = row[0], d(str(row[1]))
        div = d(str(row[2])) if len(row) > 2 else d(0)
        split = d(str(row[3])) if len(row) > 3 else None
        if adj is not None:
            a = d(str(adj[i]))
        elif prev_close is None:
            a = close
        else:
            a = prev_adj * close / (prev_close - div)
        out.append((day, close, a, div, split))
        prev_close, prev_adj = close, a
    return {"currency": currency, "bars": out}


PRE = (D("2023-12-29"), 1)  # a close before every test period; never traded


def mkspec(strategies, start, end, base="USD", **over):
    raw = {"name": "t", "period": {"start": start, "end": end}, "base_currency": base,
           "strategies": strategies}
    raw.update(over)
    return parse_spec(raw)


def run(spec, data, i=0):
    return simulate(spec, Dataset.from_dict(data)).strategies[i].result


def of(result, kind):
    return [e for e in result.events if e.kind == kind]


def value_on(result, day):
    return next(p.value for p in result.series if p.day == D(day))


# ---- schedule ---------------------------------------------------------------

def test_schedule_first_last_and_day_n_are_clipped_to_the_period():
    # base-calendar days, already clipped to the period (as base_calendar returns)
    days = [D(x) for x in ("2024-01-15", "2024-01-16", "2024-01-31", "2024-02-01",
                           "2024-02-05", "2024-02-29", "2024-03-01")]
    def spec(on):
        return mkspec([{"id": "a", "allocate": {"fixed": {"SPY": 1}}}],
                      "2024-01-10", "2024-03-02",
                      contributions={"amount": 1, "every": "month", "on": on})

    # first: the first in-period day of each month (Jan starts mid-month).
    assert [m.day for m in cal.money_in(spec("first_trading_day"), days)] == \
        [D("2024-01-15"), D("2024-02-01"), D("2024-03-01")]
    # last: March is cut at 03-02, so its last in-period day is 03-01.
    assert [m.day for m in cal.money_in(spec("last_trading_day"), days)] == \
        [D("2024-01-31"), D("2024-02-29"), D("2024-03-01")]
    # day_n 2: Jan 2 is before start -> first day on/after it is 01-15;
    # Feb 2 (no bar) -> 02-05; Mar 2 has no trading day on/after it inside the
    # period -> no March deposit.
    assert [m.day for m in cal.money_in(spec({"day_n": 2}), days)] == \
        [D("2024-01-15"), D("2024-02-05")]


def test_weekly_day_n_and_lump_sum_share_a_day():
    days = [D("2024-01-08"), D("2024-01-10"), D("2024-01-16"), D("2024-01-17")]
    spec = mkspec([{"id": "a", "allocate": {"fixed": {"SPY": 1}}}], "2024-01-08",
                  "2024-01-19", contributions={"amount": 5, "every": "week",
                                               "on": {"day_n": 3}},
                  lump_sum={"amount": 100, "on": "2024-01-09"})
    # Week 2 Wednesday = 01-10 (has a bar); week 3 Wednesday = 01-17.
    # Lump sum on 01-09 (no bar) -> 01-10, the same day as a contribution.
    got = [(m.day, m.amount, m.source) for m in cal.money_in(spec, days)]
    assert got == [(D("2024-01-10"), d(5), "contribution"),
                   (D("2024-01-10"), d(100), "lump_sum"),
                   (D("2024-01-17"), d(5), "contribution")]
    assert cal.period_starts(days, "week") == {D("2024-01-08"), D("2024-01-16")}


# ---- signals ----------------------------------------------------------------

def _past(closes):
    rows = [(D("2024-01-01") + timedelta(days=i), c) for i, c in enumerate(closes)]
    ds = Dataset.from_dict({"X": bars("USD", rows)})
    return ds.before("X", D("2030-01-01"))


def _sig(name, **params):
    return mkspec([{"id": "a", "allocate": {"rank": {
        "universe": ["X"], "signal": {name: params}, "pick": {"top": 1}}}}],
        "2024-01-01", "2024-02-01", lump_sum={"amount": 1}).strategies[0].allocate.signal


def test_signal_values_and_history_requirements():
    p = _past([10, 12, 9, 15])
    # trailing_return 2d: last / close 2 bars earlier - 1 = 15/12 - 1 = 0.25; needs 3 bars
    assert signals.compute(_sig("trailing_return", lookback="2d"), p) == d("0.25")
    assert signals.compute(_sig("trailing_return", lookback="4d"), p) is None
    # sma 4 = (10+12+9+15)/4 = 11.5
    assert signals.compute(_sig("sma", n=4), p) == d("11.5")
    # price_vs_sma 2 = 15 / ((9+15)/2) - 1 = 15/12 - 1 = 0.25
    assert signals.compute(_sig("price_vs_sma", n=2), p) == d("0.25")
    # drawdown_from_high 3 on [10, 12, 9] = 9 / max(10,12,9) - 1 = -0.25
    assert signals.compute(_sig("drawdown_from_high", n=3), _past([10, 12, 9])) == d("-0.25")
    # volatility 2: returns 12/10-1=0.2, 9/12-1=-0.25; mean -0.025;
    # sample var = (0.225^2 + 0.225^2)/1 = 0.10125; sqrt*sqrt(252)
    vol = signals.compute(_sig("volatility", n=2), _past([10, 12, 9]))
    assert vol == (d("0.10125").sqrt() * d(252).sqrt())
    assert signals.compute(_sig("volatility", n=3), _past([10, 12, 9])) is None


# ---- goldens ----------------------------------------------------------------

def test_three_month_fixed_dca_with_commission_and_slippage():
    # PRE: a bar before the period, so SPY is listed when the first decision is
    # made (a symbol with no bar before an event is not buyable at it).
    data = {"SPY": bars("USD", [PRE, (D("2024-01-02"), 50), (D("2024-01-03"), 52),
                                (D("2024-02-01"), 40), (D("2024-02-02"), 41),
                                (D("2024-03-01"), 80), (D("2024-03-04"), 79),
                                (D("2024-03-28"), 100)])}
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-01", "2024-03-31",
                  contributions={"amount": 100, "every": "month"},
                  costs={"commission": 1, "slippage_bps": 10})
    r = run(spec, data)
    buys = of(r, "buy")
    assert [b.day for b in buys] == [D("2024-01-02"), D("2024-02-01"), D("2024-03-01")]
    # net = 100 - 1 commission = 99 each month; price = close * 1.001
    # Jan: 99 / 50.05 = 1.978021978.. -> 1.97802198
    # Feb: 99 / 40.04 = 2.472527472.. -> 2.47252747
    # Mar: 99 / 80.08 = 1.236263736.. -> 1.23626374
    assert [b.detail["shares"] for b in buys] == [d("1.97802198"), d("2.47252747"),
                                                   d("1.23626374")]
    assert buys[0].detail["price"] == d("50.05")
    assert buys[0].detail["cost_base"] == d(100)
    # slippage Jan = 1.97802198 * 50 * 0.001 = 0.098901099 -> 0.10
    assert buys[0].detail["slippage"] == d("0.10")
    # Jan 3: 1.97802198 * 52 = 102.85714296 -> 102.86
    assert value_on(r, "2024-01-03") == d("102.86")
    # Mar 28: (1.97802198 + 2.47252747 + 1.23626374) * 100 = 5.68681319 * 100
    #       = 568.681319 -> 568.68
    assert value_on(r, "2024-03-28") == d("568.68")
    assert r.series[-1].contributed == d(300)
    assert [p.day for p in r.series] == [D(x) for x in (
        "2024-01-02", "2024-01-03", "2024-02-01", "2024-02-02", "2024-03-01",
        "2024-03-04", "2024-03-28")]


DIV_ROWS = [PRE, (D("2024-01-02"), 100), (D("2024-01-03"), 102),
            (D("2024-01-04"), 101, "0.5"), (D("2024-01-05"), 103)]


def test_dividend_reinvested_at_ex_date_close_after_withholding():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05", lump_sum={"amount": 1000},
                  dividends={"mode": "reinvest", "withholding_pct": 15})
    r = run(spec, {"SPY": bars("USD", DIV_ROWS)})
    # 1000 / 100 = 10 shares on 01-02.
    (div,) = of(r, "dividend")
    # ex-date 01-04: gross 10 * 0.5 = 5.00; withheld 15% = 0.75; net 4.25
    assert (div.day, div.detail["gross"], div.detail["withheld"], div.detail["net"]) == \
        (D("2024-01-04"), d("5.00"), d("0.75"), d("4.25"))
    reinvest = of(r, "buy")[1]
    # 4.25 / 101 = 0.042079207.. -> 0.04207921 at the ex-date close
    assert (reinvest.day, reinvest.detail["shares"], reinvest.detail["source"]) == \
        (D("2024-01-04"), d("0.04207921"), "dividend")
    # 01-05: 10.04207921 * 103 = 1034.33415863 -> 1034.33 (never adj_close)
    assert value_on(r, "2024-01-05") == d("1034.33")


def test_reinvest_is_a_drip_free_of_commission_and_slippage():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05", lump_sum={"amount": 1000},
                  dividends={"mode": "reinvest", "withholding_pct": 15},
                  costs={"commission": 5, "slippage_bps": 50})
    r = run(spec, {"SPY": bars("USD", DIV_ROWS)})
    # lump: (1000 - 5) / (100 * 1.005) = 995 / 100.5 = 9.900497512.. -> 9.90049751
    # dividend: gross 9.90049751 * 0.5 = 4.950248755 -> 4.95; withheld 15% =
    # 0.7425 -> 0.74 (half-even); net 4.21 -- below the 5 commission, still reinvested
    # DRIP at the plain close: 4.21 / 101 = 0.041683168.. -> 0.04168317
    (div,) = of(r, "dividend")
    assert (div.detail["gross"], div.detail["withheld"], div.detail["net"]) == \
        (d("4.95"), d("0.74"), d("4.21"))
    drip = of(r, "buy")[1]
    assert (drip.detail["shares"], drip.detail["price"], drip.detail["commission"],
            drip.detail["slippage"], drip.detail["source"]) == \
        (d("0.04168317"), d(101), d(0), d(0), "dividend")
    assert of(r, "cash") == []
    # 01-05: (9.90049751 + 0.04168317) * 103 = 9.94218068 * 103 = 1024.0446.. -> 1024.04
    assert value_on(r, "2024-01-05") == d("1024.04")


FX_FLAT = [(D(x), "1.25") for x in ("2023-12-29", "2024-01-02", "2024-01-03",
                                    "2024-01-04", "2024-01-05")]


def test_usd_dividend_in_cad_account_reinvests_without_fx():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05", base="CAD", lump_sum={"amount": 1250},
                  costs={"fx_bps": 100})
    r = run(spec, {"SPY": bars("USD", DIV_ROWS), "CAD=X": bars("CAD", FX_FLAT)})
    # lump: fee 12.50, 1237.50 CAD / 1.25 = 990 USD / 100 = 9.9 shares
    # dividend 9.9 * 0.5 = 4.95 USD stays in USD: 4.95 / 101 = 0.049009900.. -> 0.04900990
    assert len(of(r, "fx")) == 1  # only the lump's conversion
    assert of(r, "buy")[1].detail["shares"] == d("0.04900990")
    # 01-05: 9.9490099 * 103 USD * 1.25 = 1280.935.. -> 1280.94 CAD
    assert value_on(r, "2024-01-05") == d("1280.94")
    # A foreign DRIP day has no fx event (no rate logged): metrics must not need one.
    m = event_metrics(r.events, "CAD")
    assert (m["slippage"], m["fx_paid"]) == (d("0.00"), d("12.50"))


def test_whole_share_drip_converts_only_the_leftover():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05", base="CAD", lump_sum={"amount": 31250},
                  costs={"fx_bps": 100}, execution={"whole_shares": True})
    r = run(spec, {"SPY": bars("USD", DIV_ROWS), "CAD=X": bars("CAD", FX_FLAT)})
    # lump: fee 312.50, 30937.50 / 1.25 = 24750 USD / 100 -> 247 whole shares;
    # spent 31250 * 24700/24750 = 31186.87 CAD, 63.13 CAD left as cash
    # dividend 247 * 0.5 = 123.50 USD -> 1 share at 101, 22.50 USD left:
    # 22.50 * 1.25 = 28.125 -> 28.12 CAD, fee 1% = 0.28, 27.84 CAD to cash
    drip = of(r, "buy")[1]
    assert (drip.detail["shares"], drip.detail["source"]) == (d(1), "dividend")
    leftover = of(r, "fx")[1]
    assert (leftover.detail["amount"], leftover.detail["converted"],
            leftover.detail["fee"]) == (d("22.50"), d("28.12"), d("0.28"))
    assert of(r, "cash")[-1].detail == {"reason": "dividend_remainder", "amount": d("27.84")}
    # 01-05: 248 * 103 * 1.25 + 63.13 + 27.84 = 31930 + 90.97 = 32020.97
    assert value_on(r, "2024-01-05") == d("32020.97")


def test_dividend_cash_mode_and_no_dividend_for_shares_bought_on_the_ex_date():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05", lump_sum={"amount": 1000},
                  dividends={"mode": "cash", "withholding_pct": 15})
    r = run(spec, {"SPY": bars("USD", DIV_ROWS)})
    # 10 shares * 103 + 4.25 cash = 1034.25
    assert value_on(r, "2024-01-05") == d("1034.25")
    assert len(of(r, "buy")) == 1
    late = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-05",
                  lump_sum={"amount": 1010, "on": "2024-01-04"})
    r = run(late, {"SPY": bars("USD", DIV_ROWS)})
    # Bought at the ex-date close (1010 / 101 = 10 shares): not a holder entering it.
    assert of(r, "dividend") == []
    assert value_on(r, "2024-01-05") == d("1030.00")


def test_cad_money_buys_usd_symbol_by_dividing_by_cad_per_usd():
    data = {"SPY": bars("USD", [PRE, (D("2024-01-02"), 100), (D("2024-01-03"), 110)]),
            "CAD=X": bars("CAD", [(D("2024-01-02"), "1.25"), (D("2024-01-03"), "1.30")])}
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-03", base="CAD", lump_sum={"amount": 1000},
                  costs={"fx_bps": 100})
    sim = simulate(spec, Dataset.from_dict(data))
    r = sim.strategies[0].result
    # CAD calendar: XIU.TO is absent, so the union of the universe's days, recorded.
    assert sim.calendar_source.startswith("union of SPY")
    assert sim.assumed[0].path == "calendar"
    (fx,) = of(r, "fx")
    # fee 1% of 1000 CAD = 10.00; 990 CAD / 1.25 CAD-per-USD = 792.00 USD
    assert (fx.detail["from"], fx.detail["to"], fx.detail["fee"], fx.detail["amount"],
            fx.detail["converted"]) == ("CAD", "USD", d("10.00"), d("990.00"), d("792.00"))
    # 792 / 100 = 7.92 shares (the reversed rate would give 12.375)
    assert of(r, "buy")[0].detail["shares"] == d("7.92")
    # valued in CAD: 7.92 * 110 USD * 1.30 = 1132.56 CAD
    assert value_on(r, "2024-01-03") == d("1132.56")


def test_usd_money_buys_cad_symbol_by_multiplying_by_cad_per_usd():
    data = {"XIU.TO": bars("CAD", [PRE, (D("2024-01-02"), 25), (D("2024-01-03"), 26)]),
            "CAD=X": bars("CAD", [(D("2024-01-02"), "1.25"), (D("2024-01-03"), "1.30")])}
    spec = mkspec([{"id": "xiu", "allocate": {"fixed": {"XIU.TO": 1}}}],
                  "2024-01-02", "2024-01-03", base="USD", lump_sum={"amount": 1000},
                  costs={"fx_bps": 100})
    r = run(spec, data)
    # fee 10.00 USD; 990 USD * 1.25 = 1237.50 CAD; / 25 = 49.5 shares
    assert of(r, "fx")[0].detail["converted"] == d("1237.50")
    assert of(r, "buy")[0].detail["shares"] == d("49.5")
    # valued in USD: 49.5 * 26 CAD / 1.30 = 990.00 USD
    assert value_on(r, "2024-01-03") == d("990.00")


def test_sale_of_usd_symbol_converts_proceeds_into_cad():
    data = {"SPY": bars("USD", [(D("2024-01-02"), 100), (D("2024-01-03"), 101),
                                (D("2024-01-04"), 99), (D("2024-01-05"), 110)]),
            "CAD=X": bars("CAD", [(D("2024-01-02"), 1), (D("2024-01-03"), 1),
                                  (D("2024-01-04"), 1), (D("2024-01-05"), "1.25")])}
    trend = {"when": {"symbol": "SPY", "signal": {"trailing_return": {"lookback": "1d"}},
                      "op": ">", "threshold": 0, "then": {"fixed": {"SPY": 1}}}}
    spec = mkspec([{"id": "t", "allocate": trend, "holdings": "rotate"}],
                  "2024-01-04", "2024-01-05", base="CAD",
                  contributions={"amount": 990, "every": "day"}, costs={"fx_bps": 100})
    r = run(spec, data)
    # 01-04: 101/100-1 > 0 -> buy: fee 9.90, 980.10 CAD / 1 = 980.10 USD / 99 = 9.9 shares
    assert of(r, "buy")[0].detail["shares"] == d("9.9")
    # 01-05: 99/101-1 < 0 -> sell all at 110: 1089.00 USD * 1.25 = 1361.25 CAD,
    # fee 1% = 13.61 (13.6125 half-even), proceeds 1347.64; cash 1347.64 + 990 = 2337.64
    (sell,) = of(r, "sell")
    assert (sell.detail["amount"], sell.detail["proceeds_base"]) == \
        (d("1089.00"), d("1347.64"))
    assert of(r, "fx")[1].detail["converted"] == d("1361.25")
    assert value_on(r, "2024-01-05") == d("2337.64")


RANK_DATA = {
    # 01-02 and 01-03 are lookback bars before the period.
    "AAA": bars("USD", [(D("2024-01-02"), 10), (D("2024-01-03"), 11), (D("2024-01-04"), 11),
                        (D("2024-01-05"), 11), (D("2024-01-08"), 12)]),
    "BBB": bars("USD", [(D("2024-01-02"), 20), (D("2024-01-03"), 21), (D("2024-01-04"), 23),
                        (D("2024-01-05"), 23), (D("2024-01-08"), 23)]),
}


def _rank_spec(holdings):
    return mkspec([{"id": "w", "allocate": {"rank": {
        "universe": ["AAA", "BBB"], "signal": {"trailing_return": {"lookback": "1d"}},
        "pick": {"top": 1}}}, "holdings": holdings}],
        "2024-01-04", "2024-01-08", contributions={"amount": 100, "every": "day"})


def test_rank_top1_keep_routes_only_new_money():
    r = run(_rank_spec("keep"), RANK_DATA)
    # 01-04 sees 01-03: AAA 11/10-1=0.10 > BBB 21/20-1=0.05 -> AAA: 100/11 = 9.09090909
    # 01-05 sees 01-04: AAA 11/11-1=0 < BBB 23/21-1=0.095 -> BBB: 100/23 = 4.34782609
    # 01-08 sees 01-05: both 0 -> tie breaks alphabetically -> AAA: 100/12 = 8.33333333
    assert [(b.symbol, b.detail["shares"]) for b in of(r, "buy")] == [
        ("AAA", d("9.09090909")), ("BBB", d("4.34782609")), ("AAA", d("8.33333333"))]
    assert of(r, "sell") == []
    # (9.09090909 + 8.33333333) * 12 + 4.34782609 * 23
    # = 209.09090904 + 100.00000007 = 309.09090911 -> 309.09
    assert value_on(r, "2024-01-08") == d("309.09")


def test_rank_top1_rotate_sells_into_the_new_pick():
    r = run(_rank_spec("rotate"), RANK_DATA)
    # 01-05: sell 9.09090909 AAA at 11 = 99.99999999 -> 100.00; + 100 new = 200
    #        buy BBB 200/23 = 8.69565217
    # 01-08: sell BBB 8.69565217 * 23 = 199.99999991 -> 200.00; + 100 = 300
    #        buy AAA 300/12 = 25
    assert [(s.symbol, s.detail["amount"]) for s in of(r, "sell")] == [
        ("AAA", d("100.00")), ("BBB", d("200.00"))]
    assert [(b.symbol, b.detail["shares"]) for b in of(r, "buy")] == [
        ("AAA", d("9.09090909")), ("BBB", d("8.69565217")), ("AAA", d("25"))]
    assert value_on(r, "2024-01-08") == d("300.00")


TREND_DATA = {"SPY": bars("USD", [(D("2024-01-02"), 100), (D("2024-01-03"), 101),
                                  (D("2024-01-04"), 99), (D("2024-01-05"), 98),
                                  (D("2024-01-08"), 100)])}
TREND = {"when": {"symbol": "SPY", "signal": {"trailing_return": {"lookback": "1d"}},
                  "op": ">", "threshold": 0, "then": {"fixed": {"SPY": 1}}, "else": "cash"}}


@pytest.mark.parametrize("holdings,final", [
    # rotate: 01-04 buy 100/99 = 1.01010101; 01-05 (98/99-1 < 0) sell at 98 =
    # 98.98989898 -> 98.99, cash 198.99; 01-08 still off -> 298.99
    ("rotate", d("298.99")),
    # keep: 1.01010101 * 100 = 101.010101 -> 101.01, plus 200 idle cash = 301.01
    ("keep", d("301.01")),
])
def test_when_filter_holds_cash(holdings, final):
    spec = mkspec([{"id": "t", "allocate": TREND, "holdings": holdings}],
                  "2024-01-04", "2024-01-08", contributions={"amount": 100, "every": "day"})
    r = run(spec, TREND_DATA)
    assert of(r, "buy")[0].detail["shares"] == d("1.01010101")
    assert {e.detail["reason"] for e in of(r, "cash")} == {"when_else"}
    assert value_on(r, "2024-01-08") == final


def test_rebalance_trades_back_to_weights():
    data = {"AAA": bars("USD", [PRE, (D("2024-01-02"), 10), (D("2024-01-03"), 10),
                                (D("2024-02-01"), 20), (D("2024-02-02"), 20)]),
            "BBB": bars("USD", [PRE, (D("2024-01-02"), 20), (D("2024-01-03"), 20),
                                (D("2024-02-01"), 20), (D("2024-02-02"), 20)])}
    spec = mkspec([{"id": "r", "allocate": {"fixed": {"AAA": "0.5", "BBB": "0.5"}},
                    "holdings": {"rebalance": {"every": "month"}}}],
                  "2024-01-02", "2024-02-29", lump_sum={"amount": 1000})
    r = run(spec, data)
    # 01-02: 500/10 = 50 AAA, 500/20 = 25 BBB.
    # 02-01: AAA 50*20 = 1000, BBB 25*20 = 500, V = 1500, target 750 each:
    #   sell AAA 50 * 250/1000 = 12.5 -> 250.00; buy BBB 250/20 = 12.5
    assert [e.day for e in of(r, "rebalance")] == [D("2024-01-02"), D("2024-02-01")]
    assert [(s.symbol, s.detail["shares"]) for s in of(r, "sell")] == [("AAA", d("12.5"))]
    assert [(b.symbol, b.detail["shares"]) for b in of(r, "buy")][-1] == ("BBB", d("12.5"))
    assert value_on(r, "2024-02-02") == d("1500.00")


def test_us_holiday_on_a_tsx_trading_day_fills_at_next_us_bar():
    # 2025-01-20 is MLK day: TSX open, NYSE closed.
    xiu = bars("CAD", [(D("2025-01-17"), 30), (D("2025-01-20"), 30),
                       (D("2025-01-21"), 30), (D("2025-01-22"), 30)])
    spy = bars("USD", [(D("2025-01-17"), 90), (D("2025-01-21"), 100),
                       (D("2025-01-22"), 105)])
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2025-01-18", "2025-01-31", base="CAD", lump_sum={"amount": 1000})
    fx = bars("CAD", [(D("2025-01-17"), "1.40"), (D("2025-01-20"), "1.40"),
                      (D("2025-01-21"), "1.25"), (D("2025-01-22"), "1.25")])
    sim = simulate(spec, Dataset.from_dict({"XIU.TO": xiu, "SPY": spy, "CAD=X": fx}))
    r = sim.strategies[0].result
    assert sim.calendar_source == "XIU.TO"
    # Money arrives on the TSX day; SPY fills at its next bar with that day's FX:
    # 1000 / 1.25 = 800 USD / 100 = 8 shares.
    assert of(r, "contribution")[0].day == D("2025-01-20")
    buy = of(r, "buy")[0]
    assert (buy.day, buy.detail["shares"], buy.detail["decided"]) == \
        (D("2025-01-21"), d("8"), D("2025-01-20"))
    assert value_on(r, "2025-01-20") == d(1000)  # reserved cash
    assert value_on(r, "2025-01-22") == d("1050.00")  # 8 * 105 * 1.25

    # No CAD=X bar on the fill day: the nearest prior one is used, and logged.
    fx_gap = bars("CAD", [(D("2025-01-17"), "1.40"), (D("2025-01-20"), "1.40"),
                          (D("2025-01-22"), "1.25")])
    r = simulate(spec, Dataset.from_dict({"XIU.TO": xiu, "SPY": spy, "CAD=X": fx_gap})
                 ).strategies[0].result
    (conv,) = of(r, "fx")
    # 1000 / 1.40 = 714.2857.. -> 714.29 USD; / 100 = 7.1429 shares
    assert (conv.detail["fx_fallback_day"], conv.detail["converted"]) == \
        (D("2025-01-20"), d("714.29"))
    assert of(r, "buy")[0].detail["shares"] == d("7.1429")


def test_symbol_listed_mid_period_is_excluded_then_eligible():
    data = {"AAA": RANK_DATA["AAA"],
            "NEW": bars("USD", [(D("2024-01-04"), 5), (D("2024-01-05"), 10),
                                (D("2024-01-08"), 10)])}
    spec = mkspec([{"id": "w", "allocate": {"rank": {
        "universe": ["AAA", "NEW"], "signal": {"trailing_return": {"lookback": "1d"}},
        "pick": {"top": 1}}}}], "2024-01-04", "2024-01-08",
        contributions={"amount": 100, "every": "day"})
    sim = simulate(spec, Dataset.from_dict(data))
    r = sim.strategies[0].result
    # trailing 1d return needs 2 bars before the event: NEW has 0 on 01-04, 1 on 01-05.
    assert [(e.day, e.symbol, e.detail["have_bars"]) for e in of(r, "exclusion")] == [
        (D("2024-01-04"), "NEW", 0), (D("2024-01-05"), "NEW", 1)]
    assert [(x.day, x.symbol, x.reason) for x in sim.exclusions] == [
        (D("2024-01-04"), "NEW", "insufficient_history"),
        (D("2024-01-05"), "NEW", "insufficient_history")]
    # 01-08 sees NEW 10/5-1 = 1.0 > AAA 11/11-1 = 0 -> NEW.
    assert [(b.day, b.symbol) for b in of(r, "buy")] == [
        (D("2024-01-04"), "AAA"), (D("2024-01-05"), "AAA"), (D("2024-01-08"), "NEW")]


def test_empty_universe_holds_cash_and_says_so():
    spec = mkspec([{"id": "w", "allocate": {"rank": {
        "universe": ["AAA"], "signal": {"trailing_return": {"lookback": "3d"}},
        "pick": {"top": 1}}}}], "2024-01-04", "2024-01-05", lump_sum={"amount": 100})
    r = run(spec, {"AAA": RANK_DATA["AAA"]})
    assert [(e.kind, e.detail.get("reason")) for e in r.events] == [
        ("contribution", None), ("exclusion", "insufficient_history"),
        ("cash", "empty_universe")]
    assert value_on(r, "2024-01-04") == d(100)


def test_split_is_logged_and_share_count_unchanged():
    # Closes are split-adjusted at the source: a 2:1 split shows no price jump.
    data = {"SPY": bars("USD", [PRE, (D("2024-01-02"), 50), (D("2024-01-03"), 51, 0, 2),
                                (D("2024-01-04"), 52)])}
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-04", lump_sum={"amount": 500})
    r = run(spec, data)
    (split,) = of(r, "split")
    assert (split.day, split.detail["ratio"], split.detail["shares"]) == \
        (D("2024-01-03"), d(2), d(10))
    assert value_on(r, "2024-01-04") == d("520.00")  # still 10 shares * 52


def test_whole_shares_leave_the_remainder_as_cash():
    data = {"SPY": bars("USD", [PRE, (D("2024-01-02"), 30), (D("2024-01-03"), 40)])}
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-03", lump_sum={"amount": 100},
                  execution={"whole_shares": True})
    r = run(spec, data)
    # floor(100/30) = 3 shares = 90; 10 left as cash; 01-03: 3*40 + 10 = 130
    assert of(r, "buy")[0].detail["shares"] == d(3)
    assert value_on(r, "2024-01-03") == d("130.00")


# ---- integrity ----------------------------------------------------------------

def test_total_return_cross_check_raises_on_a_dividend_adj_close_does_not_show():
    rows = [(D("2024-01-02"), 100), (D("2024-01-03"), 100, 1), (D("2024-01-04"), 100)]
    # adj_close flat although a 1% dividend was paid: rebuilt TR 100/99 = 1.0101
    data = {"SPY": bars("USD", rows, adj=[100, 100, 100])}
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-04", lump_sum={"amount": 100})
    with pytest.raises(DataIntegrityError) as exc:
        simulate(spec, Dataset.from_dict(data))
    assert (exc.value.symbol, exc.value.day) == ("SPY", D("2024-01-03"))
    # 0.05% off stays inside the 0.1% tolerance.
    ok = Dataset.from_dict({"SPY": bars("USD", rows, adj=[100, "101.0606", "101.0606"])})
    check_total_return(ok, "SPY", D("2024-01-02"), D("2024-01-04"))


def test_cross_check_covers_when_only_symbols():
    good = bars("USD", [PRE, (D("2024-01-02"), 100), (D("2024-01-03"), 100),
                        (D("2024-01-04"), 100)])
    rows = [PRE, (D("2024-01-02"), 100), (D("2024-01-03"), 100, 1), (D("2024-01-04"), 100)]
    # QQQ only gates the trade, but its total return is still checked:
    # a 1% dividend that adj_close does not show fails the run on its ex-date.
    qqq = bars("USD", rows, adj=[1, 100, 100, 100])
    trend = {"when": {"symbol": "QQQ", "signal": {"sma": {"n": 1}}, "op": ">",
                      "threshold": 0, "then": {"fixed": {"SPY": 1}}}}
    spec = mkspec([{"id": "t", "allocate": trend}], "2024-01-02", "2024-01-04",
                  lump_sum={"amount": 100})
    with pytest.raises(DataIntegrityError) as exc:
        simulate(spec, Dataset.from_dict({"SPY": good, "QQQ": qqq}))
    assert (exc.value.symbol, exc.value.day) == ("QQQ", D("2024-01-03"))


def test_missing_inputs_fail_clearly():
    spec = mkspec([{"id": "spy", "allocate": {"fixed": {"SPY": 1}}}],
                  "2024-01-02", "2024-01-03", base="CAD", lump_sum={"amount": 100})
    spy = bars("USD", [(D("2024-01-02"), 30), (D("2024-01-03"), 40)])
    with pytest.raises(EngineError, match="CAD=X"):
        simulate(spec, Dataset.from_dict({"SPY": spy}))
    with pytest.raises(EngineError, match="missing from the dataset: SPY"):
        simulate(spec, Dataset.from_dict({"QQQ": spy}))
    with pytest.raises(DataIntegrityError, match="currency unknown"):
        simulate(spec, Dataset.from_dict({"SPY": dict(spy, currency=None)}))


def test_past_view_cannot_reach_today():
    ds = Dataset.from_dict({"X": bars("USD", [(D("2024-01-02"), 1), (D("2024-01-03"), 2)])})
    p = ds.before("X", D("2024-01-03"))
    assert len(p) == 1 and p[-1].close == 1 and p.closes(2) is None
    with pytest.raises(IndexError):
        p[1]
    assert [b.day for b in p[0:5]] == [D("2024-01-02")]


# ---- lookahead property -------------------------------------------------------

def _market(seed, perturb_from=None):
    """~60 business days, 3 USD + 1 CAD symbol + CAD=X, dividends included.
    Bars on or after `perturb_from` get arbitrary new closes and dividends;
    adj_close is rebuilt forward, so earlier bars are untouched."""
    rng = random.Random(seed)
    mut = random.Random(seed * 7919 + 1)
    days, day = [], D("2023-10-02")
    while len(days) < 90:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    data = {}
    for sym, ccy, px in (("AAA", "USD", 50), ("BBB", "USD", 80), ("SPY", "USD", 400),
                         ("XIU.TO", "CAD", 30), ("CAD=X", "CAD", "1.35")):
        rows, price = [], d(str(px))
        for i, dy in enumerate(days):
            price = (price * (1 + d(rng.randint(-300, 300)) / 10000)).quantize(d("0.0001"))
            div = d("0.2") if sym != "CAD=X" and i % 20 == 7 else d(0)
            close = price
            if perturb_from is not None and dy >= perturb_from:
                close = (price * d(mut.randint(50, 150)) / 100).quantize(d("0.0001"))
                div = d("0.3") if sym != "CAD=X" and mut.random() < 0.1 else d(0)
            # Keep holidays different per market: skip a few days per symbol.
            if sym == "SPY" and i in (15, 40) or sym == "XIU.TO" and i in (22, 55):
                continue
            rows.append((dy, close, div))
        data[sym] = bars(ccy, rows)
    return data


LOOKAHEAD_SPEC = {
    "name": "lookahead",
    "period": {"start": "2023-11-01", "end": "2024-02-01"},
    "base_currency": "CAD",
    "contributions": {"amount": 100, "every": "week", "on": {"day_n": 2}},
    "lump_sum": {"amount": 1000},
    "costs": {"commission": 1, "slippage_bps": 5, "fx_bps": 25},
    "strategies": [
        {"id": "rank", "allocate": {"rank": {
            "universe": ["AAA", "BBB", "XIU.TO"],
            "signal": {"trailing_return": {"lookback": "5d"}}, "pick": {"top": 2}}},
         "holdings": "rotate"},
        {"id": "trend", "allocate": {"when": {
            "symbol": "SPY", "signal": {"price_vs_sma": {"n": 10}}, "op": ">",
            "threshold": 0, "then": {"fixed": {"SPY": "0.5", "XIU.TO": "0.5"}},
            "else": {"rank": {"universe": ["AAA", "BBB"],
                              "signal": {"volatility": {"n": 5}}, "pick": {"bottom": 1}}}}}},
        {"id": "reb", "allocate": {"fixed": {"AAA": "0.3", "XIU.TO": "0.7"}},
         "holdings": {"rebalance": {"every": "month"}}},
        {"id": "dd", "allocate": {"when": {
            "symbol": "CAD=X", "signal": {"drawdown_from_high": {"n": 8}}, "op": "<",
            "threshold": "-0.01", "then": {"fixed": {"BBB": 1}}}},
         "holdings": "rotate"},
    ],
}


def _before(sim, t):
    out = []
    for s in sim.strategies:
        r = s.result
        out.append(canonical_json({"events": [e for e in r.events if e.day < t],
                                   "series": [p for p in r.series if p.day < t]}))
    return out


def _recording(monkeypatch):
    """Capture every allocator decision with the day it was made on."""
    import engine.allocate as alloc_mod
    seen, real = [], alloc_mod.evaluate

    def spy(alloc, past):
        dec = real(alloc, past)
        seen.append((past.__self__.today, dec))
        return dec
    monkeypatch.setattr(alloc_mod, "evaluate", spy)
    return seen


def _decisions_upto(decisions, t):
    return canonical_json([(day, dec.weights, dec.cash, dec.cash_reason, dec.exclusions)
                           for day, dec in decisions if day <= t])


def test_lookahead_perturbing_every_bar_from_t_changes_nothing_before_t(monkeypatch):
    # Two checks per t: (1) every event and value dated before t is
    # byte-identical; (2) every decision made on or before t is identical,
    # which also catches a decision peeking at t's own close (a leak the
    # events-before-t check cannot see, since t's fills legitimately use it).
    spec = parse_spec(LOOKAHEAD_SPEC)
    seen = _recording(monkeypatch)
    base = simulate(spec, Dataset.from_dict(_market(1)))
    base_decisions = list(seen)
    assert all(len(s.result.events) > 20 for s in base.strategies)
    event_days = sorted({day for day, _ in base_decisions})
    days = [p.day for p in base.strategies[0].result.series]
    ts = random.Random(42).sample(days[1:], 8) + random.Random(7).sample(event_days[1:], 8)
    for t in ts:
        seen.clear()
        mutated = simulate(spec, Dataset.from_dict(_market(1, perturb_from=t)))
        assert _before(mutated, t) == _before(base, t), t
        assert _decisions_upto(seen, t) == _decisions_upto(base_decisions, t), t
        # ...and the perturbation is real: something from t on differs.
        assert canonical_json([s.result for s in mutated.strategies]) != \
            canonical_json([s.result for s in base.strategies])


def test_deterministic_output():
    spec = parse_spec(LOOKAHEAD_SPEC)
    a = simulate(spec, Dataset.from_dict(_market(3)))
    b = simulate(spec, Dataset.from_dict(_market(3)))
    assert canonical_json([s.result for s in a.strategies]) == \
        canonical_json([s.result for s in b.strategies])


# ---- performance ----------------------------------------------------------------

def test_quarter_scale_worst_case_stays_fast():
    # A quarter of the declared worst case (25 symbols, 8 strategies, 400-bar
    # lookbacks, monthly money, 7.5 of 30 years). Full scale measured 8.2 s
    # against the executor's 300 s ceiling; the bound here only catches a
    # complexity regression (e.g. a signal rescanning all history), not noise.
    rng = random.Random(5)
    start = D("2015-01-02")
    days, day = [], start - timedelta(days=600)
    while day <= D("2022-07-01"):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    syms = [f"S{i:02d}" for i in range(24)] + ["SPY"]
    data = {}
    for s in syms:
        p, rows = d(50), []
        for i, dy in enumerate(days):
            p = (p * (1 + d(rng.randint(-200, 210)) / 10000)).quantize(d("0.0001"))
            rows.append((dy, p, d("0.2") if i % 63 == 5 else d(0)))
        data[s] = bars("USD", rows)
    sigs = [{"trailing_return": {"lookback": "400d"}}, {"volatility": {"n": 400}},
            {"sma": {"n": 400}}, {"drawdown_from_high": {"n": 400}}]
    holds = ["keep", "rotate", {"rebalance": {"every": "month"}}, "rotate"]
    spec = mkspec([{"id": f"s{i}", "allocate": {"rank": {
        "universe": syms, "signal": sigs[i % 4], "pick": {"top": 5}}},
        "holdings": holds[i % 4]} for i in range(8)],
        "2015-01-02", "2022-07-01", contributions={"amount": 1000, "every": "month"},
        costs={"commission": 1, "slippage_bps": 5})
    ds = Dataset.from_dict(data)
    t0 = time.perf_counter()
    sim = simulate(spec, ds)
    assert time.perf_counter() - t0 < 30
    assert all(len(s.result.series) > 1800 for s in sim.strategies)
