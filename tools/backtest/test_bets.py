"""Wilder RSI, short positions and the daily-bet strategy (OPS-29).

Every expected number is worked by hand in the comment beside it.
"""
import importlib.util
import sys
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from engine import primitives as P
from engine import run_backtest, signals, validate
from engine.data import Dataset, EngineError
from engine.simulate import simulate
from test_engine import _past, _sig, bars, mkspec

D = date.fromisoformat
d = Decimal

STOCK_BETS_ENGINE = Path(__file__).parent.parent / "stock_bets" / "engine.py"


def stock_bets_engine():
    """tools/stock_bets/engine.py under its own name (this package is also
    called `engine`)."""
    spec = importlib.util.spec_from_file_location("stock_bets_engine", STOCK_BETS_ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- Wilder RSI ---------------------------------------------------------------

# The classic 14-period worked example (Wilder's method as charting packages
# and the StockCharts RSI spreadsheet use it): closes, then the RSI each day
# from the 15th close on. The published column is rounded to 2 dp from a
# spreadsheet that rounds its running averages, so it can sit up to 0.1 away.
CLASSIC = ("44.34 44.09 44.15 43.61 44.33 44.83 45.10 45.42 45.84 46.08 45.89 46.03 "
           "45.61 46.28 46.28 46.00 46.03 46.41 46.22 45.64 46.21 46.25 45.71 46.45 "
           "45.78 45.35 44.03 44.18 44.22 44.57 43.42 42.66 43.13").split()
CLASSIC_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38,
               54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_wilder_rsi_matches_the_classic_reference_series():
    closes = [d(c) for c in CLASSIC]
    got = [float(signals._rsi_wilder(closes[:i], 14)) for i in range(15, len(closes) + 1)]
    assert len(got) == len(CLASSIC_RSI)
    assert got == pytest.approx(CLASSIC_RSI, abs=0.1)


def test_wilder_rsi_first_two_values_by_exact_arithmetic():
    closes = [Fraction(c) for c in CLASSIC]
    # First value: the 14 changes sum to gains 3.34 and losses 1.40, so
    # RSI = 100 - 100 / (1 + 3.34 / 1.40).
    first = 100 - 100 / (1 + Fraction("3.34") / Fraction("1.40"))
    assert float(signals._rsi_wilder([d(c) for c in CLASSIC[:15]], 14)) == pytest.approx(
        float(first), abs=1e-9)
    # Second value: the next change is -0.28, so with the first averages
    # g = 3.34/14 and l = 1.40/14: g' = g * 13/14, l' = (l * 13 + 0.28) / 14.
    g, loss = Fraction("3.34") / 14, Fraction("1.40") / 14
    g2, l2 = g * 13 / 14, (loss * 13 + Fraction("0.28")) / 14
    second = 100 - 100 / (1 + g2 / l2)
    assert closes[15] - closes[14] == Fraction("-0.28")
    assert float(signals._rsi_wilder([d(c) for c in CLASSIC[:16]], 14)) == pytest.approx(
        float(second), abs=1e-9)


def test_rsi_signal_defaults_to_wilder_with_warmup_history():
    sig = _sig("rsi", n=14)
    assert dict(sig.params) == {"n": 14, "smoothing": "wilder"}
    assert sig.window == 70 and signals.required_bars(sig) == 71
    assert signals.compute(sig, _past([100 + i % 3 for i in range(70)])) is None
    assert signals.compute(sig, _past([100 + i % 3 for i in range(71)])) is not None
    simple = _sig("rsi", n=14, smoothing="simple")
    assert simple.window == 14 and signals.required_bars(simple) == 15


def test_rsi_is_100_without_losses_and_50_when_flat():
    assert signals._rsi_wilder([d(x) for x in (10, 11, 12, 13)], 3) == 100
    assert signals._rsi_wilder([d(x) for x in (10, 10, 10, 10)], 3) == 50


# ---- strict validation --------------------------------------------------------

def raw_spec(bet, **over):
    spec = {"name": "t", "period": {"start": "2024-01-02", "end": "2024-01-17"},
            "base_currency": "USD", "lump_sum": {"amount": 10000},
            "strategies": [{"id": "b", "bet": bet}]}
    spec.update(over)
    return spec


def errors(raw):
    v = validate(raw)
    return {e.path: e.message for e in v.errors}


RSI70 = {"signal": {"rsi": {"n": 2, "smoothing": "simple"}}, "op": ">", "threshold": 70}


@pytest.mark.parametrize("bet, path, text", [
    ({"universe": ["TSLA"], "side": "up"}, "strategies[0].bet.side", "one of: long, short"),
    ({"universe": ["TSLA", "AAPL"], "side": "long"}, "strategies[0].bet.pick",
     "is required for a universe of several"),
    ({"universe": ["TSLA"], "side": "long",
      "pick": {"top": {"sma": {"n": 3}}}}, "strategies[0].bet.pick", "not allowed for one"),
    ({"universe": ["TSLA"], "side": "short", "stop_pct": 5}, "strategies[0].bet.hold",
     "need hold: open"),
    ({"universe": ["TSLA"], "side": "short", "hold": "open"}, "strategies[0].bet.hold",
     "at least one of exit"),
    ({"universe": ["TSLA"], "side": "short", "enter": {**RSI70, "op": "above"}},
     "strategies[0].bet.enter.op", "must be one of"),
    ({"universe": ["TSLA"], "side": "short", "enter": {"signal": {"rsi": {"n": 100}},
                                                       "op": ">", "threshold": 70}},
     "strategies[0].bet.enter.signal.rsi.n", "Wilder smoothing needs 5 x n"),
    ({"universe": ["TSLA"], "side": "short", "enter": {"signal": {"rsi": {
        "n": 14, "smoothing": "ema"}}, "op": ">", "threshold": 70}},
     "strategies[0].bet.enter.signal.rsi.smoothing", "one of: wilder, simple"),
    ({"universe": ["^GSPC"], "side": "long"}, "strategies[0].bet.universe[0]",
     "not tradable"),
    ({"universe": ["TSLA"], "side": "long", "stake": 20000}, "lump_sum.amount",
     "at least every bet's stake"),
    ({"universe": ["TSLA"], "side": "long", "stop_pct": 0, "hold": "open"},
     "strategies[0].bet.stop_pct", "greater than 0"),
    ({"universe": ["TSLA"], "side": "long", "colour": "red"}, "strategies[0].bet.colour",
     "unknown field"),
])
def test_bet_specs_are_validated_strictly(bet, path, text):
    assert text in errors(raw_spec(bet)).get(path, "")


def test_bet_spec_level_rules():
    ok = {"universe": ["TSLA"], "side": "long"}
    assert "contributions" in errors(raw_spec(ok, contributions={"amount": 5, "every": "month"}))
    assert "costs" in errors(raw_spec(ok, costs={"slippage_bps": 5}))
    both = raw_spec(ok)
    both["strategies"][0]["allocate"] = {"fixed": {"TSLA": 1}}
    assert "not both" in errors(both)["strategies[0]"]
    assert validate(raw_spec(ok)).ok


def test_bet_spec_canonical_form_and_assumptions():
    v = validate(raw_spec({"universe": "stock_bets", "side": "long",
                           "pick": {"top": {"trailing_return": {"lookback": "5d"}}}}))
    assert v.ok
    bet = v.spec.to_dict()["strategies"][0]["bet"]
    assert bet["universe"] == sorted(P.STOCK_BETS_SYMBOLS)
    assert (bet["hold"], bet["stake"], bet["cost_pct"]) == ("1d", "1000", "0.05")
    assert {"strategies[0].bet.hold", "strategies[0].bet.stake",
            "strategies[0].bet.cost_pct"} <= {a.path for a in v.assumed}
    assert "SPY" in v.spec.symbols()  # the comparison series is always loaded


def test_stock_bets_constants_have_not_drifted():
    sb = stock_bets_engine()
    assert P.STOCK_BETS_SYMBOLS == tuple(sorted(sb.SYMBOLS))
    assert P.STOCK_BETS_COST_PCT == sb.COST_PCT
    assert P.STOCK_BETS_STAKE == sb.STAKE


# ---- shorts: covering, P&L, stops ---------------------------------------------

DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
        "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15",
        "2024-01-16", "2024-01-17"]


def dataset(**closes):
    return Dataset.from_dict({
        sym: bars("USD", [(D(day), c) for day, c in zip(DAYS, series)])
        for sym, series in closes.items()})


TSLA = [100, 102, 104, 106, 110, 100, 95, 96, 97, 98, 101, 104]
SPY = [400, 401, 402, 400, 404, 402, 408, 408, 410, 400, 404, 396]

SHORT_RSI = {
    "universe": ["TSLA"], "side": "short", "enter": RSI70, "hold": "open",
    "exit": {"signal": {"rsi": {"n": 2, "smoothing": "simple"}}, "op": "<", "threshold": 50},
    "stop_pct": 5}


def run_bet(bet, **closes):
    spec = mkspec([{"id": "b", "bet": bet}], DAYS[0], DAYS[-1], lump_sum={"amount": 10000})
    return spec, simulate(spec, dataset(**closes)).strategies[0].result


def test_short_is_opened_covered_and_stopped_with_the_right_pnl():
    _, res = run_bet(SHORT_RSI, TSLA=TSLA, SPY=SPY)
    trades = [(e.day.isoformat(), e.kind, e.detail["price"], e.detail["source"])
              for e in res.events if e.kind in ("short", "cover")]
    # RSI(2) of closes before 01-05 (100, 102, 104) is 100 > 70: short at 01-05's
    # close 106. Before 01-10 the last two changes are +4 and -10: RSI =
    # 100 - 100 / (1 + 4/10) = 28.57 < 50, so it is covered at 01-10's close, 95.
    # Re-entered 01-15 at 98 (RSI of 95, 96, 97 = 100), the day after the stop
    # check: 01-16's 101 is a 3.06% loss, 01-17's 104 a 6.12% loss > 5%: stopped.
    assert trades == [("2024-01-05", "short", 106, "bet"), ("2024-01-10", "cover", 95, "exit"),
                      ("2024-01-15", "short", 98, "bet"), ("2024-01-17", "cover", 104, "stop")]
    first, second = [e.detail for e in res.events if e.kind == "bet"]
    # Short gross = (entry - exit) / entry: (106 - 95) / 106 = 10.3774%, less 0.05%.
    assert (first["gross_pct"], first["net_pct"], first["pnl"]) == (
        d("10.3774"), d("10.3274"), d("103.27"))
    # (98 - 104) / 98 = -6.1224%; net -6.1724%; on 1,000 = -61.72.
    assert (second["gross_pct"], second["net_pct"], second["pnl"], second["reason"]) == (
        d("-6.1224"), d("-6.1724"), d("-61.72"), "stop")
    # SPY over the same days: 400 -> 408 is +2.0%; 400 -> 396 is -1.0%.
    assert (first["spy_pct"], second["spy_pct"]) == (d("2.0000"), d("-1.0000"))
    assert [e.detail["slippage"] for e in res.events if e.kind == "cover"] == [d("0.50")] * 2
    assert res.series[-1].value == d("10000") + d("103.27") - d("61.72")


def test_short_is_marked_to_market_while_open_and_loses_when_the_price_rises():
    _, res = run_bet({"universe": ["TSLA"], "side": "short", "enter": RSI70, "hold": "open",
                      "target_pct": 50}, TSLA=TSLA, SPY=SPY)
    by_day = {p.day.isoformat(): p.value for p in res.series}
    # Short from 106: at 110 the position is down (106 - 110) / 106 = -3.7736%
    # of the 1,000 stake = -37.74 (cents, half-even); at 100 it is up 5.6604% = 56.60.
    assert by_day["2024-01-05"] == d("10000")
    assert by_day["2024-01-08"] == d("10000") + d("-37.74")
    assert by_day["2024-01-09"] == d("10000") + d("56.60")
    # Never closed (no stop, the 50% target is not reached): marked at the last close.
    assert not [e for e in res.events if e.kind == "cover"]
    # 1,000 * (106 - 104) / 106 = 18.87.
    assert res.series[-1].value == d("10000") + d("18.87")


def test_a_long_bet_gains_where_a_short_loses_and_uses_the_same_rules():
    long_ = {"universe": ["TSLA"], "side": "long", "enter": RSI70, "hold": "open",
             "target_pct": 3}
    _, res = run_bet(long_, TSLA=TSLA, SPY=SPY)
    # Long from 106 (01-05); 110 on 01-08 is +3.77% >= 3%: target, closed at 110.
    bet = [e.detail for e in res.events if e.kind == "bet"][0]
    assert (bet["reason"], bet["entry"], bet["exit"]) == ("target", 106, 110)
    assert bet["gross_pct"] == d("3.7736")
    assert [e.kind for e in res.events if e.kind in ("buy", "sell")][:2] == ["buy", "sell"]


def test_after_a_stop_the_next_bet_waits_for_the_next_day():
    bet = {"universe": ["TSLA"], "side": "long", "hold": "open", "stop_pct": 1}
    closes = [100, 100, 100, 100, 98, 99, 99, 99, 99, 99, 99, 99]
    _, res = run_bet(bet, TSLA=closes, SPY=SPY)
    opened = [(e.day.isoformat(), e.kind) for e in res.events if e.kind in ("buy", "sell")]
    # Opens on 01-02 (no signal needed); 01-03 and 01-04 stay at 100; the 98
    # on 01-08 is a stop. No re-entry that same close; the next bet is 01-09.
    assert opened[:3] == [("2024-01-02", "buy"), ("2024-01-08", "sell"), ("2024-01-09", "buy")]


# ---- crossings ----------------------------------------------------------------

def test_a_crossing_fires_once_where_a_plain_comparison_fires_every_day():
    closes = [100, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109]

    def bets(op):
        enter = {**RSI70, "op": op}
        _, res = run_bet({"universe": ["TSLA"], "side": "long", "enter": enter},
                         TSLA=closes, SPY=SPY)
        return [e.day.isoformat() for e in res.events if e.kind == "bet"]

    # RSI(2) from the closes before 01-05 (100, 99, 100: +-1) is 50; before 01-08
    # (99, 100, 101) it is 100. Only 01-08 crosses 70 from below: bought at that
    # close, settled at the next, 01-09.
    assert bets("crosses_above") == ["2024-01-09"]
    assert len(bets(">")) > 1


def test_a_cross_needs_the_extra_day_of_history():
    spec = mkspec([{"id": "b", "bet": {"universe": ["TSLA"], "side": "long", "enter": {
        **RSI70, "op": "crosses_above"}}}], DAYS[0], DAYS[-1], lump_sum={"amount": 1000})
    assert spec.max_window() == 2 + 1
    plain = mkspec([{"id": "b", "bet": {"universe": ["TSLA"], "side": "long",
                                        "enter": RSI70}}], DAYS[0], DAYS[-1],
                   lump_sum={"amount": 1000})
    assert plain.max_window() == 2


# ---- the daily bet, as the stock-bets pet makes it -----------------------------

def test_daily_bets_score_exactly_as_stock_bets_does():
    sb = stock_bets_engine()
    a = [100, 101, 103, 102, 104, 107, 105, 106, 108, 107, 109, 111]
    b = [50, 49, 48, 50, 53, 52, 54, 53, 51, 52, 54, 53]
    bet = {"universe": ["AAA", "BBB"], "side": "short",
           "pick": {"top": {"trailing_return": {"lookback": "1d"}}}}
    spec = mkspec([{"id": "fade", "bet": bet}], DAYS[0], DAYS[-1], lump_sum={"amount": 1000})
    ds = Dataset.from_dict({
        sym: bars("USD", [(D(day), c) for day, c in zip(DAYS, series)])
        for sym, series in (("AAA", a), ("BBB", b), ("SPY", SPY))})
    res = simulate(spec, ds).strategies[0].result
    settled = [e for e in res.events if e.kind == "bet"]
    assert len(settled) >= 8
    close = {s: dict(zip(DAYS, c)) for s, c in (("AAA", a), ("BBB", b), ("SPY", SPY))}
    for e in settled:
        entry_day, exit_day = e.detail["entry_day"].isoformat(), e.day.isoformat()
        px_in, px_out = close[e.symbol][entry_day], close[e.symbol][exit_day]
        score = sb.score("short", {"kind": "close"}, px_in,
                         {"open": px_out, "high": px_out, "low": px_out, "close": px_out},
                         close["SPY"][entry_day], close["SPY"][exit_day])
        assert (e.detail["gross_pct"], e.detail["net_pct"], e.detail["pnl"]) == (
            score.gross_pct, score.net_pct, score.pnl)
        assert e.detail["spy_pct"] == score.spy_pct
    # The pick is the previous day's biggest 1-day mover: 01-03 looks at the
    # 01-02 closes only (no return yet), so the first bet is 01-04.
    assert settled[0].detail["entry_day"] == D("2024-01-04")
    assert settled[0].symbol == "AAA"  # 1d returns before 01-04: A +1.00%, B -2.00%


def test_bet_metrics_report_the_stock_bets_scoreboard_numbers():
    ds = dataset(TSLA=TSLA, SPY=SPY)
    res = run_backtest({"name": "t", "period": {"start": DAYS[0], "end": DAYS[-1]},
                        "base_currency": "USD", "lump_sum": {"amount": 10000},
                        "strategies": [{"id": "b", "bet": SHORT_RSI}]}, ds, "0" * 64)
    m = res.strategies[0].metrics["bets"]
    # Nets 10.3274 and -6.1724: win rate 1/2, avg 2.0775. Direction (gross > 0):
    # 1/2. SPY less the 0.05% cost: +1.95 and -1.05, so the edges are
    # 8.3774 and -5.1224, averaging 1.6275; SPY's average is 0.45.
    assert (m["settled"], m["open_at_end"], m["wins"], m["win_rate"]) == (
        2, 0, 1, d("0.5"))
    assert (m["direction_rate"], m["avg_net_pct"]) == (d("0.5"), d("2.0775"))
    assert (m["spy_avg_net_pct"], m["edge_vs_spy_pct"]) == (d("0.45"), d("1.6275"))
    assert (m["pnl"], m["exit_reasons"]) == (d("41.55"), {"exit": 1, "stop": 1})
    assert "bets" in {c.code for c in res.caveats}
    assert "go short on" in res.description and "crosses" not in res.description
    assert res.strategies[0].metrics["trades"] == 4


def test_the_stock_bets_universe_runs_on_the_full_symbol_list():
    closes = {s: [100 + (i * (k + 1)) % 7 + i for i in range(len(DAYS))]
              for k, s in enumerate(P.STOCK_BETS_SYMBOLS)}
    raw = {"name": "t", "period": {"start": DAYS[0], "end": DAYS[-1]},
           "base_currency": "USD", "lump_sum": {"amount": 1000},
           "strategies": [{"id": "fade", "bet": {
               "universe": "stock_bets", "side": "short",
               "pick": {"top": {"trailing_return": {"lookback": "1d"}}}}}]}
    res = run_backtest(raw, dataset(**closes), "0" * 64)
    syms = {e.symbol for e in res.strategies[0].events if e.kind == "bet"}
    assert syms and syms <= set(P.STOCK_BETS_SYMBOLS)
    assert res.strategies[0].metrics["bets"]["settled"] >= 8


def test_bets_refuse_symbols_outside_the_base_currency():
    ds = Dataset.from_dict({
        "SHOP.TO": bars("CAD", [(D(day), 100 + i) for i, day in enumerate(DAYS)]),
        "SPY": bars("USD", [(D(day), c) for day, c in zip(DAYS, SPY)])})
    spec = mkspec([{"id": "b", "bet": {"universe": ["SHOP.TO"], "side": "long"}}],
                  DAYS[0], DAYS[-1], lump_sum={"amount": 1000})
    with pytest.raises(EngineError, match="base currency"):
        simulate(spec, ds)


def test_the_tool_reply_carries_the_bet_scoreboard():
    import run
    res = run_backtest({"name": "t", "period": {"start": DAYS[0], "end": DAYS[-1]},
                        "base_currency": "USD", "lump_sum": {"amount": 10000},
                        "strategies": [{"id": "b", "bet": SHORT_RSI}]},
                       dataset(TSLA=TSLA, SPY=SPY), "0" * 64)
    out = run.summary(res, stored=True)
    assert out["bets"] == {"b": {
        "settled": 2, "win_rate": "0.5", "direction_rate": "0.5", "avg_net_pct": "2.0775",
        "spy_avg_net_pct": "0.45", "edge_vs_spy_pct": "1.6275"}}
    assert len(run.render(out).encode()) <= run.SUMMARY_CAP
