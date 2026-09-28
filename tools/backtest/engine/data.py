"""The pinned dataset and the one accessor every price read goes through.

`Dataset.before(symbol, t)` is the only way to read a bar: it returns the
bars strictly before `t`. A decision on day t asks for `before(sym, t)`; a
fill, a dividend or a valuation on day d asks for `before(sym, d + 1 day)`,
i.e. what is known at d's close. Nothing else hands out bars, so lookahead
has exactly one place to hide, and the simulator only ever passes it "today"
or "today's close" (see simulate._Market).

Price basis (design 35, "Data layer"): `close` is Yahoo's split-adjusted,
NOT dividend-adjusted Close; `dividend` is in the same split-adjusted share
units; `adj_close` (split + dividend adjusted) is used for nothing but the
total-return cross-check below. `CAD=X` is CAD per USD.
"""
import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from .spec import DECIMAL_CONTEXT

FX_SYMBOL = "CAD=X"
CURRENCIES = ("CAD", "USD")
# Cumulative total-return mismatch vs adj_close that fails a run.
TR_TOLERANCE = Decimal("0.001")
ONE_DAY = timedelta(days=1)


class EngineError(ValueError):
    """The spec cannot run on this dataset (missing symbol, FX series, calendar)."""


class DataIntegrityError(EngineError):
    """The data contradicts itself; the run stops rather than print a wrong number."""

    def __init__(self, symbol, day, message):
        self.symbol, self.day = symbol, day
        where = f"{symbol} on {day.isoformat()}" if day else symbol
        super().__init__(f"{where}: {message}")


@dataclass(frozen=True)
class Bar:
    day: date
    close: Decimal            # close_split_adj: split-adjusted, dividends NOT added back
    adj_close: Decimal | None  # cross-check only; never used for accounting
    dividend: Decimal         # per split-adjusted share, paid to holders entering `day`
    split_ratio: Decimal | None


class Past:
    """Read-only view of a symbol's bars[:stop]; indexing cannot reach past stop."""

    __slots__ = ("_bars", "_stop")

    def __init__(self, bars, stop):
        self._bars, self._stop = bars, stop

    def __len__(self):
        return self._stop

    def __getitem__(self, i):
        if isinstance(i, slice):
            return tuple(self._bars[j] for j in range(*i.indices(self._stop)))
        if i < 0:
            i += self._stop
        if not 0 <= i < self._stop:
            raise IndexError("bar index out of the visible range")
        return self._bars[i]

    def __iter__(self):
        return (self._bars[j] for j in range(self._stop))

    def last(self):
        return self._bars[self._stop - 1] if self._stop else None

    def closes(self, n):
        """The last n closes (oldest first), or None if fewer than n are visible."""
        if n > self._stop:
            return None
        return tuple(self._bars[j].close for j in range(self._stop - n, self._stop))


class Dataset:
    """Per-symbol bars (sorted, unique days) plus currency. Immutable."""

    def __init__(self, series):
        # series: {symbol: (currency | None, tuple[Bar])}
        self._currency = {s: c for s, (c, _) in series.items()}
        self._bars = {s: bars for s, (_, bars) in series.items()}
        self._days = {s: tuple(b.day for b in bars) for s, bars in self._bars.items()}

    @classmethod
    def from_dict(cls, raw):
        """{symbol: {currency, bars: [(day, close_split_adj, adj_close, dividend,
        split_ratio), ...]}}. Days are dates or ISO strings; numbers are Decimal,
        int or str (a float goes through its repr, which is deterministic)."""
        series = {}
        for sym in sorted(raw):
            entry = raw[sym]
            cur = entry.get("currency")
            if cur is not None and cur not in CURRENCIES:
                raise EngineError(f"{sym}: unsupported currency {cur!r}")
            bars = []
            for row in entry["bars"]:
                day, close, adj, div, split = row
                day = day if isinstance(day, date) else date.fromisoformat(day)
                bar = Bar(day, _dec(close, sym, day), None if adj is None else _dec(adj, sym, day),
                          Decimal(0) if div is None else _dec(div, sym, day),
                          None if split is None else _dec(split, sym, day))
                if bar.close <= 0:
                    raise DataIntegrityError(sym, day, "close must be positive")
                if bars and bar.day <= bars[-1].day:
                    raise DataIntegrityError(sym, day, "bars must be sorted with unique days")
                bars.append(bar)
            series[sym] = (cur, tuple(bars))
        return cls(series)

    def symbols(self):
        return tuple(sorted(self._bars))

    def has(self, symbol):
        return symbol in self._bars

    def currency(self, symbol):
        return self._currency[symbol]

    def days(self, symbol):
        """Trading dates only (the calendar), never prices."""
        return self._days[symbol]

    def before(self, symbol, t):
        """THE accessor: the symbol's bars with day < t."""
        return Past(self._bars[symbol], bisect.bisect_left(self._days[symbol], t))


def _dec(v, sym, day):
    if isinstance(v, float):
        v = repr(v)
    try:
        d = Decimal(v)
    except (InvalidOperation, TypeError, ValueError):
        raise DataIntegrityError(sym, day, f"not a number: {v!r}") from None
    if not d.is_finite():
        raise DataIntegrityError(sym, day, f"not a finite number: {v!r}")
    return d


def check_total_return(dataset, symbol, start, end):
    """Rebuild total return from close + dividend and compare to adj_close.

    Uses Yahoo's own adjustment convention (on an ex-date the prior close is
    reduced by the dividend: ratio = close / (prev_close - dividend)) so a
    mismatch means the stored closes or dividends are wrong, not that two
    reinvestment conventions differ. Raises at the first day the cumulative
    ratio is more than TR_TOLERANCE off.
    """
    bars = [b for b in dataset.before(symbol, end + ONE_DAY) if b.day >= start]
    if len(bars) < 2:
        return
    with localcontext(DECIMAL_CONTEXT):
        tr = Decimal(1)
        first = bars[0]
        if first.adj_close is None or first.adj_close <= 0:
            raise DataIntegrityError(symbol, first.day, "adj_close missing for the "
                                                        "total-return cross-check")
        for prev, bar in zip(bars, bars[1:]):
            if bar.adj_close is None or bar.adj_close <= 0:
                raise DataIntegrityError(symbol, bar.day, "adj_close missing for the "
                                                          "total-return cross-check")
            base = prev.close - bar.dividend
            if base <= 0:
                raise DataIntegrityError(symbol, bar.day, "dividend is not below the "
                                                          "prior close")
            tr *= bar.close / base
            expected = bar.adj_close / first.adj_close
            if abs(tr / expected - 1) > TR_TOLERANCE:
                raise DataIntegrityError(
                    symbol, bar.day,
                    f"total return rebuilt from close and dividends ({tr:.6f}) differs "
                    f"from adj_close ({expected:.6f}) by more than {TR_TOLERANCE * 100}%; "
                    "refetch this symbol with range max")
