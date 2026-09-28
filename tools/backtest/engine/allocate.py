"""Allocators: turn a strategy's `allocate` into target weights for one event.

`past(symbol)` is the only data an allocator sees: the simulator binds it
to the decision day, so it returns bars strictly before the event.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from . import signals
from .spec import DECIMAL_CONTEXT, Fixed, Rank, When


@dataclass(frozen=True)
class Decision:
    weights: tuple            # ((symbol, weight), ...) sorted by symbol, weights > 0
    cash: Decimal             # weight held as cash: 1 - sum(weights)
    cash_reason: str | None   # excluded | empty_universe | when_else | when_undecidable
    exclusions: tuple         # ((symbol, reason, detail), ...)


_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def evaluate(alloc, past):
    with localcontext(DECIMAL_CONTEXT):
        return _evaluate(alloc, past)


def _cash(reason, exclusions=()):
    return Decision((), Decimal(1), reason, tuple(exclusions))


def _insufficient(sym, need, have):
    return (sym, "insufficient_history", {"need_bars": need, "have_bars": have})


def _evaluate(alloc, past):
    if isinstance(alloc, Fixed):
        # A symbol not yet listed (no bar before the event) cannot be bought;
        # its weight is held as cash rather than silently spread to the rest.
        weights, excl = [], []
        for sym, w in alloc.weights:
            if len(past(sym)) == 0:
                excl.append(_insufficient(sym, 1, 0))
            else:
                weights.append((sym, w))
        cash = Decimal(1) - sum((w for _, w in weights), Decimal(0))
        return Decision(tuple(weights), cash, "excluded" if excl else None, tuple(excl))

    if isinstance(alloc, Rank):
        need = signals.required_bars(alloc.signal)
        scored, excl = [], []
        for sym in alloc.universe:
            p = past(sym)
            value = signals.compute(alloc.signal, p)
            if value is None:
                excl.append(_insufficient(sym, need, len(p)))
            else:
                scored.append((sym, value))
        if not scored:
            return _cash("empty_universe", excl)
        # Ties break alphabetically: the symbol is the secondary sort key in
        # both directions.
        if alloc.pick == "top":
            scored.sort(key=lambda sv: (-sv[1], sv[0]))
        else:
            scored.sort(key=lambda sv: (sv[1], sv[0]))
        picked = sorted(sym for sym, _ in scored[:alloc.n])
        w = Decimal(1) / len(picked)
        return Decision(tuple((s, w) for s in picked), Decimal(0), None, tuple(excl))

    assert isinstance(alloc, When)
    p = past(alloc.symbol)
    value = signals.compute(alloc.signal, p)
    if value is None:
        return _cash("when_undecidable",
                     [_insufficient(alloc.symbol, signals.required_bars(alloc.signal), len(p))])
    if _OPS[alloc.op](value, alloc.threshold):
        return _evaluate(alloc.then, past)
    if alloc.else_ is None:
        return _cash("when_else")
    return _evaluate(alloc.else_, past)
