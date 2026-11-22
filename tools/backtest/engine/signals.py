"""Signal functions: exact Decimal values from split-adjusted closes.

Each takes `past` (a data.Past holding only bars strictly before the event
day) and returns a Decimal, or None when the symbol has too little history,
which makes it ineligible for that event. Definitions are the registry's
`doc` strings in primitives.py.
"""
from decimal import Decimal, localcontext

from .spec import DECIMAL_CONTEXT

SQRT_252 = Decimal(252).sqrt(DECIMAL_CONTEXT)


def required_bars(signal):
    """Closes needed before the event day. Return-based signals need one more
    bar than their window: N returns span N + 1 closes."""
    if signal.name in ("trailing_return", "volatility", "rsi"):
        return signal.window + 1
    return signal.window


def _trailing_return(closes):
    return closes[-1] / closes[0] - 1


def _sma(closes):
    return sum(closes, Decimal(0)) / len(closes)


def _price_vs_sma(closes):
    return closes[-1] / _sma(closes) - 1


def _volatility(closes):
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    mean = sum(rets, Decimal(0)) / len(rets)
    var = sum(((r - mean) ** 2 for r in rets), Decimal(0)) / (len(rets) - 1)
    return var.sqrt() * SQRT_252


def _drawdown_from_high(closes):
    return closes[-1] / max(closes) - 1


def _rsi_value(avg_gain, avg_loss):
    if avg_loss == 0:
        return Decimal(100) if avg_gain > 0 else Decimal(50)
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _rsi_simple(closes):
    """Cutler's RSI: plain averages of every change in `closes`."""
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum((c for c in changes if c > 0), Decimal(0))
    loss = sum((-c for c in changes if c < 0), Decimal(0))
    return _rsi_value(gain, loss)


def _rsi_wilder(closes, n):
    """Wilder's RSI over all of `closes`: the first n changes seed the
    averages (simple mean), every later change smooths them in with weight 1/n."""
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(c, Decimal(0)) for c in changes]
    losses = [max(-c, Decimal(0)) for c in changes]
    avg_gain = sum(gains[:n], Decimal(0)) / n
    avg_loss = sum(losses[:n], Decimal(0)) / n
    for gain, loss in zip(gains[n:], losses[n:]):
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
    return _rsi_value(avg_gain, avg_loss)


_FUNCS = {
    "trailing_return": _trailing_return,
    "sma": _sma,
    "price_vs_sma": _price_vs_sma,
    "volatility": _volatility,
    "drawdown_from_high": _drawdown_from_high,
}


def compute(signal, past):
    """The signal's value on `past`, or None if there are too few bars."""
    closes = past.closes(required_bars(signal))
    if closes is None:
        return None
    with localcontext(DECIMAL_CONTEXT):
        if signal.name == "rsi":
            args = dict(signal.params)
            if args["smoothing"] == "wilder":
                return +_rsi_wilder(closes, args["n"])
            return +_rsi_simple(closes)
        return +_FUNCS[signal.name](closes)
