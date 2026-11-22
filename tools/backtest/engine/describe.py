"""Plain-English description of a spec, from templates only.

Primitive sentences come from the registry (`Primitive.template`); the
few spec-level sentences are the TEMPLATES below. This module only formats
values into them, so what the report says ran is exactly what the spec says.
"""
from decimal import Decimal

from . import primitives as P
from .spec import LOOKBACK_RE, Fixed, Rank, decimal_str

TEMPLATES = {
    "header": 'Backtest "{name}" from {start} to {end}, valued in {currency}.',
    "execution": ("Each decision uses only closes before its event day; trades fill "
                  "at that day's close plus slippage, {shares}."),
    "fractional": "in fractional shares",
    "whole": "in whole shares, leaving the remainder as cash",
    "withholding": ", less {pct} withholding,",
    "strategy": "Strategy {label}{id}: {allocation}; {holdings}.",
    "bet_strategy": "Strategy {label}{id}: {bet}.",
    "side_long": "go long on",
    "side_short": "go short on (paper only: no borrow fee, margin or dividends owed)",
    "stock_bets": "the stock-bets symbols",
    "pick": ", picking the one with the {direction} {signal}",
    "enter": ", only when {condition}",
    "hold_1d": "each position is closed at the next trading day's close",
    "hold_open": "a position is held until {closers}, whichever comes first, and "
                 "closed at that day's close",
    "stop": "it has lost {pct}%",
    "target": "it has gained {pct}%",
    "ties": "Ties in any ranking break alphabetically by symbol.",
    "benchmark": "Benchmark: {label}.",
    "risk_free": "Sharpe ratios use a {rate} annual risk-free rate.",
    "cash": "hold cash",
    "allocation": "the allocation",
}

CONTRIBUTION_SCHEDULE = {
    "day": "every trading day",
    "first_trading_day": "every {unit} on the first trading day of the {unit}",
    "last_trading_day": "every {unit} on the last trading day of the {unit}",
    "day_n": "every {unit} on the first trading day on or after day {n} of the {unit}",
    "weekday": "every week on the first trading day on or after {weekday}",
}
REBALANCE_SCHEDULE = {
    "day": "on every trading day",
    "period": "on the first trading day of every {unit}",
}
WEEKDAYS = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday"}
LOOKBACK_WORDS = {"d": "trading-day", "w": "week", "mo": "month", "y": "year"}
OP_WORDS = {">": "above", ">=": "at or above", "<": "below", "<=": "at or below",
            "crosses_above": "crosses above", "crosses_below": "crosses below"}
DIRECTION = {"top": "highest", "bottom": "lowest"}


def _money(d):
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.quantize(Decimal('0.01')):,f}"


def _pct(fraction):
    return decimal_str(fraction * 100) + "%"


def _and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _or(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def _quote(text):
    return f'"{text}"'


def _weights(weights):
    return _and(f"{_pct(w)} {sym}" for sym, w in weights)


def _signal(sig):
    prim = P.BY_NAME[sig.name]
    args = dict(sig.params)
    if "lookback" in args:
        num, unit = LOOKBACK_RE.match(args["lookback"]).groups()
        args["lookback"] = f"{num}-{LOOKBACK_WORDS[unit]}"
    return prim.template.format(**args)


def _allocation(alloc):
    if alloc is None:
        return TEMPLATES["cash"]
    if isinstance(alloc, Fixed):
        return P.BY_NAME["fixed"].template.format(weights=_weights(alloc.weights))
    if isinstance(alloc, Rank):
        count = f"{alloc.n} symbol" + ("" if alloc.n == 1 else "s")
        return P.BY_NAME["rank"].template.format(
            count=count, direction=DIRECTION[alloc.pick], signal=_signal(alloc.signal),
            universe=_and(alloc.universe))
    return P.BY_NAME["when"].template.format(**{
        "then": _allocation(alloc.then), "symbol": alloc.symbol,
        "signal": _signal(alloc.signal), "op": OP_WORDS[alloc.op],
        "threshold": _threshold(alloc.signal, alloc.threshold),
        "else": _allocation(alloc.else_)})


def _threshold(sig, value):
    unit = P.BY_NAME[sig.name].unit
    return _pct(value) if unit == "fraction" else decimal_str(value)


def _condition(cond, owner="its"):
    return (f"{owner} {_signal(cond.signal)} {OP_WORDS[cond.op]} "
            f"{_threshold(cond.signal, cond.threshold)}")


def _bet(bet, currency):
    universe = TEMPLATES["stock_bets"] if bet.universe == P.STOCK_BETS_SYMBOLS \
        else _and(bet.universe)
    pick = "" if bet.pick is None else TEMPLATES["pick"].format(
        direction=DIRECTION[bet.pick], signal=_signal(bet.pick_signal))
    enter = "" if bet.enter is None else TEMPLATES["enter"].format(
        condition=_condition(bet.enter))
    if bet.hold == "1d":
        hold = TEMPLATES["hold_1d"]
    else:
        closers = []
        if bet.exit:
            closers.append(_condition(bet.exit, "the held symbol's"))
        if bet.stop_pct is not None:
            closers.append(TEMPLATES["stop"].format(pct=decimal_str(bet.stop_pct)))
        if bet.target_pct is not None:
            closers.append(TEMPLATES["target"].format(pct=decimal_str(bet.target_pct)))
        hold = TEMPLATES["hold_open"].format(closers=_or(closers))
    return P.BY_NAME["bet"].template.format(
        side=TEMPLATES["side_" + bet.side], universe=universe, pick=pick, enter=enter,
        hold=hold, stake=_money(bet.stake), currency=currency,
        cost_pct=decimal_str(bet.cost_pct))


def _holdings(h):
    prim = P.BY_NAME[h.mode]
    if h.mode != "rebalance":
        return prim.template
    schedule = REBALANCE_SCHEDULE["day" if h.every == "day" else "period"]
    to = TEMPLATES["allocation"] if h.to is None else _weights(h.to)
    return prim.template.format(to=to, schedule=schedule.format(unit=h.every))


def _contribution_schedule(c):
    if c.every == "day":
        return CONTRIBUTION_SCHEDULE["day"]
    if c.on.kind == "day_n" and c.every == "week":
        return CONTRIBUTION_SCHEDULE["weekday"].format(weekday=WEEKDAYS[c.on.n])
    return CONTRIBUTION_SCHEDULE[c.on.kind].format(unit=c.every, n=c.on.n)


def describe(spec):
    cur = spec.base_currency
    lines = [TEMPLATES["header"].format(name=spec.name, start=spec.period.start,
                                        end=spec.period.end, currency=cur)]
    if spec.contributions:
        c = spec.contributions
        lines.append(P.BY_NAME["contributions"].template.format(
            amount=_money(c.amount), currency=cur, schedule=_contribution_schedule(c)))
    if spec.lump_sum:
        lines.append(P.BY_NAME["lump_sum"].template.format(
            amount=_money(spec.lump_sum.amount), currency=cur, on=spec.lump_sum.on))
    lines.append(TEMPLATES["execution"].format(
        shares=TEMPLATES["whole" if spec.execution.whole_shares else "fractional"]))
    d = spec.dividends
    withholding = "" if d.withholding_pct == 0 else \
        TEMPLATES["withholding"].format(pct=decimal_str(d.withholding_pct) + "%")
    lines.append(P.BY_NAME[d.mode].template.format(withholding=withholding))
    k = spec.costs
    lines.append(P.BY_NAME["costs"].template.format(
        commission=_money(k.commission), currency=cur,
        slippage_bps=decimal_str(k.slippage_bps), fx_bps=decimal_str(k.fx_bps)))
    labels = {}
    for s in spec.strategies:
        labels[s.id] = s.label
        ident = {"label": _quote(s.label), "id": "" if s.label == s.id else f" ({s.id})"}
        if s.bet:
            lines.append(TEMPLATES["bet_strategy"].format(bet=_bet(s.bet, cur), **ident))
        else:
            lines.append(TEMPLATES["strategy"].format(
                allocation=_allocation(s.allocate), holdings=_holdings(s.holdings), **ident))
    if "rank" in spec.primitives_used():
        lines.append(TEMPLATES["ties"])
    lines.append(TEMPLATES["benchmark"].format(label=_quote(labels[spec.benchmark])))
    lines.append(TEMPLATES["risk_free"].format(rate=_pct(spec.risk_free)))
    return "\n".join(lines)
