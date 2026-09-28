"""Spec language v1: parsing, defaults, bounds, canonical form, registry, describe."""
import json
from datetime import date
from decimal import Decimal

import pytest

from engine import (
    BacktestResult,
    Caveat,
    Event,
    Exclusion,
    SeriesPoint,
    SpecError,
    StrategyResult,
    canonical_json,
    describe,
    describe_primitives,
    experiment_id,
    parse_spec,
    spec_hash,
    validate,
)
from engine.primitives import REGISTRY
from engine.spec import Fixed, Rank, When


def minimal(**over):
    spec = {
        "name": "t",
        "period": {"start": "2016-10-01", "end": "2026-09-01"},
        "contributions": {"amount": 1000, "every": "month"},
        "strategies": [{"id": "qqq", "allocate": {"fixed": {"QQQ": 1}}}],
    }
    spec.update(over)
    return spec


def errors_of(raw):
    v = validate(raw)
    assert not v.ok and v.spec is None
    return [e.to_dict() for e in v.errors]


def messages(raw):
    return " | ".join(f"{e['path']}: {e['message']}" for e in errors_of(raw))


# ---- defaults and assumed -------------------------------------------------

def test_defaults_filled_and_recorded_in_assumed():
    v = validate(minimal())
    assert v.ok, v.errors
    s = v.spec
    assert s.base_currency == "CAD"
    assert (s.execution.decide, s.execution.fill, s.execution.whole_shares) == (
        "prior_close", "close", False)
    assert (s.dividends.mode, s.dividends.withholding_pct) == ("reinvest", Decimal(0))
    assert (s.costs.commission, s.costs.slippage_bps, s.costs.fx_bps) == (0, 0, 0)
    assert s.risk_free == 0 and s.ties == "alphabetical"
    assert s.strategies[0].holdings.mode == "keep"
    assert s.strategies[0].label == "qqq"
    assert s.benchmark == "qqq"
    assert s.contributions.on.kind == "first_trading_day"

    assumed = {a.path: a.value for a in v.assumed}
    assert assumed == {
        "base_currency": "CAD",
        "contributions.on": "first_trading_day",
        "execution.decide": "prior_close",
        "execution.fill": "close",
        "execution.whole_shares": False,
        "dividends.mode": "reinvest",
        "dividends.withholding_pct": "0",
        "costs.commission": "0",
        "costs.slippage_bps": "0",
        "costs.fx_bps": "0",
        "risk_free": "0",
        "ties": "alphabetical",
        "strategies[0].label": "qqq",
        "strategies[0].holdings": "keep",
        "benchmark": "qqq",
    }
    assert v.to_dict()["assumed"][0] == {"path": "base_currency", "value": "CAD"}


def test_explicit_values_are_not_assumed():
    v = validate(minimal(base_currency="USD", costs={"commission": 1, "slippage_bps": 5,
                                                     "fx_bps": 25}))
    paths = {a.path for a in v.assumed}
    assert "base_currency" not in paths
    assert not any(p.startswith("costs.") for p in paths)


def test_nested_defaults_when_else_lump_on_rebalance_to():
    v = validate(minimal(
        contributions=None,
        lump_sum={"amount": 5000},
        strategies=[{"id": "a", "allocate": {"when": {
            "symbol": "SPY", "signal": {"price_vs_sma": {"n": 200}}, "op": ">",
            "threshold": 0, "then": {"fixed": {"SPY": 1}}}},
            "holdings": {"rebalance": {"every": "quarter"}}}]))
    assert v.ok, v.errors
    assumed = {a.path: a.value for a in v.assumed}
    assert assumed["lump_sum.on"] == "2016-10-01"
    assert assumed["strategies[0].allocate.when.else"] == "cash"
    assert assumed["strategies[0].holdings.rebalance.to"] == "allocation"
    alloc = v.spec.strategies[0].allocate
    assert isinstance(alloc, When) and alloc.else_ is None
    assert v.spec.lump_sum.on == date(2016, 10, 1)


def test_normalized_spec_revalidates_with_nothing_assumed():
    v = validate(minimal())
    again = validate(v.spec.to_dict())
    assert again.ok and again.assumed == ()
    assert spec_hash(again.spec) == spec_hash(v.spec)


def test_errors_are_path_message_list_and_collected():
    errs = errors_of({"name": "", "period": {"start": "2020-13-01", "end": "x"},
                      "strategies": [], "bogus": 1})
    assert all(set(e) == {"path", "message"} for e in errs)
    paths = {e["path"] for e in errs}
    assert {"name", "period.start", "period.end", "strategies", "bogus"} <= paths
    assert any("contributions or lump_sum" in e["message"] for e in errs)


def test_parse_spec_raises_spec_error_carrying_the_list():
    with pytest.raises(SpecError) as exc:
        parse_spec(minimal(base_currency="EUR"))
    assert exc.value.errors[0].path == "base_currency"
    assert parse_spec(minimal()).name == "t"


def test_not_an_object():
    assert errors_of([1, 2]) == [{"path": "", "message": "a spec must be a JSON object"}]


# ---- bounds ---------------------------------------------------------------

def _syms(n):
    return [f"S{i:02d}" for i in range(n)]


def test_bound_distinct_symbols():
    ok = minimal(strategies=[{"id": "r", "allocate": {"rank": {
        "universe": _syms(25), "signal": {"sma": {"n": 5}}, "pick": {"top": 1}}}}])
    assert validate(ok).ok
    # 25 in the universe plus a 26th elsewhere: the bound is across strategies
    bad = minimal(strategies=ok["strategies"] + [
        {"id": "f", "allocate": {"fixed": {"ZZZ": 1}}}])
    assert "26 distinct symbols" in messages(bad)


def test_bound_strategies():
    strat = [{"id": f"s{i}", "allocate": {"fixed": {"SPY": 1}}} for i in range(9)]
    assert "at most 8" in messages(minimal(strategies=strat))
    assert validate(minimal(strategies=strat[:8])).ok


def test_bound_period_thirty_years():
    assert validate(minimal(period={"start": "1996-01-01", "end": "2026-01-01"})).ok
    assert "30 years" in messages(minimal(period={"start": "1996-01-01",
                                                  "end": "2026-01-02"}))
    assert "after start" in messages(minimal(period={"start": "2020-01-01",
                                                     "end": "2020-01-01"}))


def test_bound_daily_contributions_five_years():
    daily = {"amount": 10, "every": "day"}
    assert validate(minimal(period={"start": "2020-01-01", "end": "2025-01-01"},
                            contributions=daily)).ok
    assert "5 years" in messages(minimal(period={"start": "2020-01-01",
                                                 "end": "2025-01-02"},
                                         contributions=daily))


def test_bound_daily_rebalance_five_years():
    s = minimal(strategies=[{"id": "a", "allocate": {"fixed": {"SPY": 1}},
                             "holdings": {"rebalance": {"every": "day"}}}])
    assert "5 years" in messages(s)


@pytest.mark.parametrize("signal,ok", [
    ({"sma": {"n": 400}}, True),
    ({"sma": {"n": 401}}, False),
    ({"trailing_return": {"lookback": "19mo"}}, True),   # 399 trading days
    ({"trailing_return": {"lookback": "2y"}}, False),    # 504 trading days
    ({"volatility": {"n": 401}}, False),
    ({"drawdown_from_high": {"n": 400}}, True),
])
def test_bound_lookback(signal, ok):
    s = minimal(strategies=[{"id": "r", "allocate": {"rank": {
        "universe": ["SPY", "QQQ"], "signal": signal, "pick": {"top": 1}}}}])
    if ok:
        assert validate(s).ok
    else:
        assert "400 trading days" in messages(s)


@pytest.mark.parametrize("amount,ok", [
    (1, True), (10**9, True), ("0.01", True),
    (0, False), (-5, False), (10**9 + 1, False), ("1.001", False), (True, False),
    ("nan", False), (float("inf"), False),
])
def test_bound_amounts(amount, ok):
    s = minimal(contributions={"amount": amount, "every": "month"})
    assert validate(s).ok is ok
    s = minimal(contributions=None, lump_sum={"amount": amount})
    assert validate(s).ok is ok


def test_weights_must_sum_to_one_and_be_positive():
    assert "sum to 1" in messages(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"SPY": 0.6, "QQQ": 0.3}}}]))
    assert "greater than 0" in messages(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"SPY": 1, "QQQ": 0}}}]))
    assert validate(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"SPY": 0.6, "XIU.TO": "0.4"}}}])).ok


def test_symbol_rules():
    assert "not tradable" in messages(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"CAD=X": 1}}}]))
    assert "symbol" in messages(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"spy": 1}}}]))
    assert "symbol" in messages(minimal(strategies=[
        {"id": "a", "allocate": {"fixed": {"SPY;DROP": 1}}}]))


def test_strategy_ids_unique_and_benchmark_known():
    two = [{"id": "a", "allocate": {"fixed": {"SPY": 1}}},
           {"id": "a", "allocate": {"fixed": {"QQQ": 1}}}]
    assert "duplicate" in messages(minimal(strategies=two))
    assert "benchmark" in messages(minimal(benchmark="nope"))
    assert "id" in messages(minimal(strategies=[{"id": "Bad Id!",
                                                 "allocate": {"fixed": {"SPY": 1}}}]))


def test_rank_pick_bounds():
    s = minimal(strategies=[{"id": "r", "allocate": {"rank": {
        "universe": ["SPY", "QQQ"], "signal": {"sma": {"n": 5}}, "pick": {"top": 3}}}}])
    assert "pick" in messages(s)


def test_every_day_rejects_an_anchor():
    s = minimal(period={"start": "2024-01-01", "end": "2025-01-01"},
                contributions={"amount": 5, "every": "day", "on": "last_trading_day"})
    assert "every trading day" in messages(s)


def test_lump_sum_date_inside_period():
    s = minimal(contributions=None, lump_sum={"amount": 5, "on": "2030-01-01"})
    assert "inside the period" in messages(s)


# ---- the registry is the authority -----------------------------------------

def test_unknown_allocator_names_the_registry():
    msg = messages(minimal(strategies=[{"id": "a", "allocate": {"momentum": {}}}]))
    assert "unknown allocator 'momentum'" in msg
    assert "fixed, rank, when" in msg and "describe_primitives" in msg


def test_unknown_signal_and_holdings_name_the_registry():
    msg = messages(minimal(strategies=[{"id": "a", "allocate": {"rank": {
        "universe": ["SPY"], "signal": {"rsi": {"n": 14}}, "pick": {"top": 1}}},
        "holdings": "hodl"}]))
    assert ("unknown signal 'rsi'; the registry has: drawdown_from_high, price_vs_sma, "
            "sma, trailing_return, volatility") in msg
    assert "unknown holdings 'hodl'; the registry has: keep, rebalance, rotate" in msg


def test_unknown_fields_are_errors():
    msg = messages(minimal(contribution={"amount": 1}))
    assert "unknown field 'contribution'" in msg
    msg = messages(minimal(strategies=[{"id": "a", "allocate": {"rank": {
        "universe": ["SPY"], "signal": {"sma": {"n": 5, "m": 1}}, "pick": {"top": 1}}}}]))
    assert "unknown field 'm'" in msg


def test_describe_primitives_is_generated_from_the_registry():
    g = describe_primitives()
    listed = {p["name"] for fam in g["families"].values() for p in fam}
    assert listed == {p.name for p in REGISTRY}
    for fam in g["families"].values():
        for p in fam:
            assert {"name", "family", "doc", "params", "template", "example"} <= set(p)
    canonical_json(g)  # serializable, no floats


def test_every_primitive_is_exercised_by_a_valid_example_spec():
    used = set()
    for ex in describe_primitives()["examples"]:
        v = validate(ex)
        assert v.ok, (ex["name"], v.errors)
        used |= v.spec.primitives_used()
        describe(v.spec)
    assert used == {p.name for p in REGISTRY}


def test_every_primitive_example_snippet_appears_in_an_example_spec():
    blob = [canonical_json(ex) for ex in describe_primitives()["examples"]]
    for p in REGISTRY:
        snippet = canonical_json(p.example)[1:-1]  # the snippet's members, braces off
        assert any(snippet in b for b in blob), p.name


# ---- canonical form and ids -------------------------------------------------

def _reverse_keys(o):
    if isinstance(o, dict):
        return {k: _reverse_keys(o[k]) for k in reversed(list(o))}
    if isinstance(o, list):
        return [_reverse_keys(x) for x in o]
    return o


def test_key_order_and_number_spelling_do_not_change_the_hash():
    a = minimal(costs={"slippage_bps": 5, "fx_bps": 25})
    b = _reverse_keys(minimal(costs={"fx_bps": "25.0", "slippage_bps": 5.0}))
    b["contributions"]["amount"] = "1000.00"
    ha, hb = spec_hash(parse_spec(a)), spec_hash(parse_spec(b))
    assert ha == hb and len(ha) == 64


def test_universe_order_does_not_change_the_hash():
    def rank(u):
        return minimal(strategies=[{"id": "r", "allocate": {"rank": {
            "universe": u, "signal": {"sma": {"n": 5}}, "pick": {"top": 1}}}}])
    assert spec_hash(parse_spec(rank(["SPY", "QQQ"]))) == \
        spec_hash(parse_spec(rank(["QQQ", "SPY"])))


def test_semantic_change_changes_the_hash():
    assert spec_hash(parse_spec(minimal())) != \
        spec_hash(parse_spec(minimal(base_currency="USD")))


def test_canonical_json_shape():
    assert canonical_json({"b": Decimal("1.50"), "a": date(2020, 1, 2),
                           "c": [Decimal("1E+3"), Decimal("-0.0")]}) == \
        '{"a":"2020-01-02","b":"1.5","c":["1000","0"]}'
    with pytest.raises(TypeError):
        canonical_json({"x": 0.1})
    s = canonical_json(parse_spec(minimal()))
    assert s == canonical_json(json.loads(s))
    assert '"amount":"1000"' in s


def test_experiment_id():
    spec = parse_spec(minimal())
    eid = experiment_id(spec, "a" * 64, "1.0.0")
    assert len(eid) == 32 and int(eid, 16) >= 0
    assert eid == experiment_id(parse_spec(_reverse_keys(minimal())), "a" * 64, "1.0.0")
    assert eid != experiment_id(spec, "b" * 64, "1.0.0")
    assert eid != experiment_id(spec, "a" * 64, "1.0.1")


def test_spec_helpers_for_the_engine():
    spec = parse_spec(minimal(strategies=[
        {"id": "w", "allocate": {"when": {
            "symbol": "^VIX", "signal": {"sma": {"n": 10}}, "op": "<", "threshold": 30,
            "then": {"rank": {"universe": ["SPY", "QQQ"],
                              "signal": {"trailing_return": {"lookback": "3mo"}},
                              "pick": {"top": 1}}},
            "else": {"fixed": {"TLT": 1}}}}}]))
    assert spec.symbols() == ("QQQ", "SPY", "TLT", "^VIX")
    assert spec.max_window() == 63
    alloc = spec.strategies[0].allocate
    assert isinstance(alloc.then, Rank) and isinstance(alloc.else_, Fixed)
    assert alloc.then.universe == ("QQQ", "SPY") and alloc.then.signal.window == 63


def test_when_depth_is_bounded():
    inner = {"fixed": {"SPY": 1}}
    for _ in range(4):
        inner = {"when": {"symbol": "SPY", "signal": {"sma": {"n": 5}}, "op": ">",
                          "threshold": 1, "then": inner}}
    assert "nested" in messages(minimal(strategies=[{"id": "a", "allocate": inner}]))


# ---- describe(): golden text for structurally different specs -----------------

LUMP_60_40 = {
    "name": "60-40 lump",
    "period": {"start": "2015-01-02", "end": "2025-01-02"},
    "lump_sum": {"amount": 50000},
    "strategies": [
        {"id": "sixty-forty", "allocate": {"fixed": {"SPY": 0.6, "XIU.TO": 0.4}},
         "holdings": {"rebalance": {"every": "quarter"}}},
        {"id": "all-spy", "label": "All SPY", "allocate": {"fixed": {"SPY": 1}}},
    ],
    "benchmark": "all-spy",
}
LUMP_60_40_TEXT = """\
Backtest "60-40 lump" from 2015-01-02 to 2025-01-02, valued in CAD.
Invest a lump sum of 50,000 CAD on the first trading day on or after 2015-01-02.
Each decision uses only closes before its event day; trades fill at that day's close plus slippage, in fractional shares.
Dividends are reinvested at the ex-date close.
Costs: 0 CAD commission per trade, 0 bps slippage on every fill and 0 bps on every currency conversion.
Strategy "sixty-forty": allocate to 60% SPY and 40% XIU.TO; holdings are rebalanced to the allocation on the first trading day of every quarter.
Strategy "All SPY" (all-spy): allocate to 100% SPY; existing holdings are kept, so only new money follows the allocation.
Benchmark: "All SPY".
Sharpe ratios use a 0% annual risk-free rate."""

TREND_FILTER = {
    "name": "spy-above-200",
    "period": {"start": "2020-01-01", "end": "2025-01-01"},
    "base_currency": "USD",
    "contributions": {"amount": 500, "every": "week", "on": {"day_n": 3}},
    "dividends": {"mode": "cash", "withholding_pct": 15},
    "costs": {"commission": 1, "slippage_bps": 5},
    "risk_free": 0.03,
    "strategies": [{"id": "trend", "allocate": {"when": {
        "symbol": "SPY", "signal": {"price_vs_sma": {"n": 200}}, "op": ">",
        "threshold": 0, "then": {"fixed": {"SPY": 1}}}},
        "holdings": "rotate"}],
}
TREND_FILTER_TEXT = """\
Backtest "spy-above-200" from 2020-01-01 to 2025-01-01, valued in USD.
Contribute 500 USD every week on the first trading day on or after Wednesday.
Each decision uses only closes before its event day; trades fill at that day's close plus slippage, in fractional shares.
Dividends, less 15% withholding, are held as cash.
Costs: 1 USD commission per trade, 5 bps slippage on every fill and 0 bps on every currency conversion.
Strategy "trend": allocate to 100% SPY while SPY's price vs its 200-day simple moving average is above 0%, otherwise hold cash; at every event all holdings are sold into the allocation.
Benchmark: "trend".
Sharpe ratios use a 3% annual risk-free rate."""

LOW_VOL_KEEP = {
    "name": "low-vol-pair",
    "period": {"start": "2016-10-01", "end": "2026-09-01"},
    "contributions": {"amount": 1000.5, "every": "month", "on": "last_trading_day"},
    "execution": {"whole_shares": True},
    "costs": {"slippage_bps": 5, "fx_bps": 25},
    "strategies": [{"id": "low-vol", "allocate": {"rank": {
        "universe": ["XIU.TO", "SPY", "QQQ", "TLT"],
        "signal": {"volatility": {"n": 60}}, "pick": {"bottom": 2}}},
        "holdings": "keep"}],
}
LOW_VOL_KEEP_TEXT = """\
Backtest "low-vol-pair" from 2016-10-01 to 2026-09-01, valued in CAD.
Contribute 1,000.50 CAD every month on the last trading day of the month.
Each decision uses only closes before its event day; trades fill at that day's close plus slippage, in whole shares, leaving the remainder as cash.
Dividends are reinvested at the ex-date close.
Costs: 0 CAD commission per trade, 5 bps slippage on every fill and 25 bps on every currency conversion.
Strategy "low-vol": allocate equally to the 2 symbols with the lowest 60-day annualized volatility among QQQ, SPY, TLT and XIU.TO; existing holdings are kept, so only new money follows the allocation.
Ties in any ranking break alphabetically by symbol.
Benchmark: "low-vol".
Sharpe ratios use a 0% annual risk-free rate."""

WINNER = {
    "name": "qqq-dca-vs-1m-winner",
    "period": {"start": "2016-10-01", "end": "2026-09-01"},
    "contributions": {"amount": 1000, "every": "month", "on": "first_trading_day"},
    "costs": {"commission": 0, "slippage_bps": 5, "fx_bps": 25},
    "strategies": [
        {"id": "qqq", "allocate": {"fixed": {"QQQ": 1}}},
        {"id": "winner", "allocate": {"rank": {
            "universe": ["QQQ", "SPY", "XIU.TO", "NVDA"],
            "signal": {"trailing_return": {"lookback": "1mo"}}, "pick": {"top": 1}}},
         "holdings": "keep"},
    ],
    "benchmark": "qqq",
}
WINNER_TEXT = """\
Backtest "qqq-dca-vs-1m-winner" from 2016-10-01 to 2026-09-01, valued in CAD.
Contribute 1,000 CAD every month on the first trading day of the month.
Each decision uses only closes before its event day; trades fill at that day's close plus slippage, in fractional shares.
Dividends are reinvested at the ex-date close.
Costs: 0 CAD commission per trade, 5 bps slippage on every fill and 25 bps on every currency conversion.
Strategy "qqq": allocate to 100% QQQ; existing holdings are kept, so only new money follows the allocation.
Strategy "winner": allocate equally to the 1 symbol with the highest trailing 1-month return among NVDA, QQQ, SPY and XIU.TO; existing holdings are kept, so only new money follows the allocation.
Ties in any ranking break alphabetically by symbol.
Benchmark: "qqq".
Sharpe ratios use a 0% annual risk-free rate."""


@pytest.mark.parametrize("raw,text", [
    (LUMP_60_40, LUMP_60_40_TEXT),
    (TREND_FILTER, TREND_FILTER_TEXT),
    (LOW_VOL_KEEP, LOW_VOL_KEEP_TEXT),
    (WINNER, WINNER_TEXT),
])
def test_describe_golden(raw, text):
    v = validate(raw)
    assert v.ok, v.errors
    assert describe(v.spec) == text
    # the description is a function of the canonical spec alone
    assert describe(parse_spec(_reverse_keys(v.spec.to_dict()))) == text


def test_describe_nested_when_with_allocator_else_and_rebalance_weights():
    v = validate(minimal(
        contributions={"amount": 100, "every": "quarter", "on": {"day_n": 15}},
        strategies=[{"id": "dd", "label": "Dip buyer", "allocate": {"when": {
            "symbol": "QQQ", "signal": {"drawdown_from_high": {"n": 252}}, "op": "<=",
            "threshold": "-0.2", "then": {"fixed": {"QQQ": 1}},
            "else": {"when": {"symbol": "SPY", "signal": {"sma": {"n": 50}}, "op": ">=",
                              "threshold": 400, "then": {"fixed": {"SPY": 1}},
                              "else": {"fixed": {"TLT": 1}}}}}},
            "holdings": {"rebalance": {"every": "year",
                                       "to": {"SPY": 0.5, "QQQ": 0.25, "TLT": 0.25}}}}]))
    assert v.ok, v.errors
    lines = describe(v.spec).splitlines()
    assert lines[1] == ("Contribute 100 CAD every quarter on the first trading day on "
                        "or after day 15 of the quarter.")
    assert lines[5] == (
        'Strategy "Dip buyer" (dd): allocate to 100% QQQ while QQQ\'s drawdown from its '
        "252-day high is at or below -20%, otherwise allocate to 100% SPY while SPY's "
        "50-day simple moving average is at or above 400, otherwise allocate to 100% TLT; "
        "holdings are rebalanced to 25% QQQ, 50% SPY and 25% TLT on the first trading "
        "day of every year.")


def test_describe_daily_contributions():
    v = validate(minimal(period={"start": "2024-01-01", "end": "2025-01-01"},
                         contributions={"amount": 10, "every": "day"},
                         lump_sum={"amount": 1000, "on": "2024-06-03"}))
    lines = describe(v.spec).splitlines()
    assert lines[1] == "Contribute 10 CAD every trading day."
    assert lines[2] == ("Invest a lump sum of 1,000 CAD on the first trading day on or "
                        "after 2024-06-03.")


# ---- the result shape T4/T5 fill ----------------------------------------------

def test_result_shape_serializes_canonically():
    v = validate(minimal())
    r = BacktestResult(
        experiment_id="0" * 32, engine_version="1.0.0", dataset_sha="d" * 64,
        spec=v.spec.to_dict(), description=describe(v.spec), assumed=v.assumed,
        caveats=(Caveat("taxes", "Taxes are ignored."),),
        exclusions=(Exclusion("qqq", date(2017, 1, 3), "NVDA", "insufficient history"),),
        strategies=(StrategyResult(
            strategy_id="qqq", label="qqq",
            series=(SeriesPoint(date(2016, 10, 3), Decimal("1000.00"), Decimal("1000.00")),),
            events=(Event(date(2016, 10, 3), "buy", "QQQ",
                          {"shares": Decimal("8.12345678"), "price": Decimal("123.1")}),),
            metrics={"xirr": None, "final_value": Decimal("1000.00")}),))
    d = r.to_dict()
    assert d["strategies"][0]["series"][0] == {"day": "2016-10-03", "value": "1000",
                                               "contributed": "1000"}
    assert d["strategies"][0]["events"][0]["detail"]["shares"] == "8.12345678"
    assert d["assumed"][0] == {"path": "base_currency", "value": "CAD"}
    text = canonical_json(r)
    assert '"shares":"8.12345678"' in text and '"xirr":null' in text
    assert canonical_json(r) == text
    with pytest.raises(ValueError):
        Event(date(2020, 1, 1), "teleport")
