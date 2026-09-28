"""Event dates: the base market's trading calendar and the money-in schedule.

Events fall on the base currency's market (TSX = XIU.TO's bars for CAD, US =
SPY's for USD). Every schedule is clipped to [period.start, period.end]: a
period that starts mid-month gets its first deposit on its first trading
day, and `last_trading_day` of a month cut short by period.end is the last
trading day on or before end.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .data import EngineError

CALENDAR_SYMBOL = {"CAD": "XIU.TO", "USD": "SPY"}


def _tradable(sym):
    return "=" not in sym and not sym.startswith("^")


def base_calendar(spec, dataset):
    """(days in the period, source) — source names what the calendar came from."""
    start, end = spec.period.start, spec.period.end
    sym = CALENDAR_SYMBOL[spec.base_currency]
    if dataset.has(sym):
        days, source = dataset.days(sym), sym
    else:
        universe = [s for s in spec.symbols() if _tradable(s) and dataset.has(s)]
        days = sorted({d for s in universe for d in dataset.days(s)})
        source = f"union of {', '.join(universe)} ({sym} not in the dataset)"
    days = tuple(d for d in days if start <= d <= end)
    if not days:
        raise EngineError(f"no trading days between {start} and {end} on the "
                          f"{spec.base_currency} calendar ({source})")
    return days, source


def group_key(day, every):
    if every == "day":
        return day
    if every == "week":
        return day.isocalendar()[:2]
    if every == "month":
        return (day.year, day.month)
    if every == "quarter":
        return (day.year, (day.month - 1) // 3)
    return day.year


def _groups(days, every):
    out = {}
    for d in days:
        out.setdefault(group_key(d, every), []).append(d)
    return out


def _group_anchor(key, every, n):
    """The calendar date `day_n` points at inside a group."""
    if every == "week":
        year, week = key
        return date.fromisocalendar(year, week, n)
    if every == "month":
        return date(key[0], key[1], n)
    if every == "quarter":
        return date(key[0], key[1] * 3 + 1, n)
    return date(key, 1, n)


def first_on_or_after(days, target):
    for d in days:
        if d >= target:
            return d
    return None


@dataclass(frozen=True)
class MoneyIn:
    day: date
    amount: Decimal
    source: str  # contribution | lump_sum


def contribution_days(contrib, days):
    """One date per `every` period: first/last trading day or day_n."""
    if contrib.every == "day":
        return list(days)
    out = []
    for key, group in _groups(days, contrib.every).items():
        if contrib.on.kind == "first_trading_day":
            out.append(group[0])
        elif contrib.on.kind == "last_trading_day":
            out.append(group[-1])
        else:
            # First trading day on/after calendar day N; if the period has none
            # left in this group it rolls into the next trading day.
            d = first_on_or_after(days, _group_anchor(key, contrib.every, contrib.on.n))
            if d is not None:
                out.append(d)
    return out


def money_in(spec, days):
    """Every deposit, in (day, source) order; several may share a day."""
    out = []
    if spec.contributions:
        c = spec.contributions
        out += [MoneyIn(d, c.amount, "contribution") for d in contribution_days(c, days)]
    if spec.lump_sum:
        d = first_on_or_after(days, spec.lump_sum.on)
        if d is None:
            raise EngineError(f"lump_sum.on {spec.lump_sum.on} has no trading day "
                              "on or after it inside the period")
        out.append(MoneyIn(d, spec.lump_sum.amount, "lump_sum"))
    return sorted(out, key=lambda m: (m.day, m.source))


def period_starts(days, every):
    """First trading day of each `every` period (rebalance dates)."""
    return frozenset(g[0] for g in _groups(days, every).values())
