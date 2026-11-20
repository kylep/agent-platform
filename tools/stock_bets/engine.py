"""Stock-bets engine: the US session calendar, bet intake and scoring.

Pure functions, standard library only. No platform wiring lives here: the
Tool (step 2 of ENG-16) calls these, and the scoring job (step 3) feeds them
prices from the stockmarket App's daily bars. Nothing here reads a clock,
the network or a store; every input is an argument, so the same inputs give
the same outputs every time.

Rules are the ones on wiki page `stock-bets` ("What a bet is" and
"Scoring"). A change to a rule is a change to this file and its tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

SYMBOLS = frozenset(
    "SPY QQQ IWM DIA XLK XLF XLE XLV XLY XLI XLU XLP SMH TLT GLD USO "
    "AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD".split())
SIDES = ("long", "short")
EXIT_KINDS = ("open", "close", "bracket")
BASES = ("catalyst", "momentum", "reversal", "macro", "default")
CONFIDENCE_MIN, CONFIDENCE_MAX = Decimal("0.50"), Decimal("0.90")
BRACKET_MIN_PCT, BRACKET_MAX_PCT = Decimal("0.2"), Decimal("5")
COST_PCT = Decimal("0.05")
STAKE = Decimal("1000")
THESIS_MAX = 600
SOURCES_MAX = 10
SOURCE_MAX = 300

REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

_PCT = Decimal("0.0001")
_MONEY = Decimal("0.01")
_PRICE = Decimal("0.000001")


# ------------------------------------------------------------------ calendar

def _nth_weekday(year, month, weekday, n):
    """The n-th `weekday` (Mon=0) of a month; n=-1 is the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year):
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - the algorithm's name
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(day):
    """NYSE observance: Saturday moves to Friday, Sunday to Monday."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def holidays(year):
    """NYSE full-day closures in `year`, by rule.

    New Year's Day falling on a Saturday is not moved to the Friday before
    (NYSE rule 7.2), so it adds nothing to either year.
    """
    days = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:
        days.add(_observed(new_year))
    days.add(_nth_weekday(year, 1, 0, 3))            # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))            # Washington's Birthday
    days.add(_easter(year) - timedelta(days=2))      # Good Friday
    days.add(_nth_weekday(year, 5, 0, -1))           # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))       # Juneteenth
    days.add(_observed(date(year, 7, 4)))            # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))            # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))           # Thanksgiving
    days.add(_observed(date(year, 12, 25)))          # Christmas
    return frozenset(d for d in days if d.year == year)


def is_session(day):
    """True when the US market trades on `day`."""
    return day.weekday() < 5 and day not in holidays(day.year)


def early_closes(year):
    """Sessions that close at 13:00 ET: July 3 (Mon-Thu), the day after
    Thanksgiving, and Christmas Eve, each only when it is a session."""
    days = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1), date(year, 12, 24)}
    july3 = date(year, 7, 3)
    if july3.weekday() <= 3:
        days.add(july3)
    return frozenset(d for d in days if is_session(d))


def close_time(day):
    """Local (ET) closing time of a session."""
    if not is_session(day):
        raise ValueError(f"{day} is not a US market session")
    return EARLY_CLOSE if day in early_closes(day.year) else REGULAR_CLOSE


def next_session(day):
    """The first session strictly after `day`."""
    nxt = day + timedelta(days=1)
    while not is_session(nxt):
        nxt += timedelta(days=1)
    return nxt


def _et_offset(when_utc):
    """US Eastern offset for a UTC instant: DST from 02:00 local on the
    second Sunday of March to 02:00 local on the first Sunday of November."""
    year = when_utc.year
    start = datetime.combine(_nth_weekday(year, 3, 6, 2), time(7), timezone.utc)
    end = datetime.combine(_nth_weekday(year, 11, 6, 1), time(6), timezone.utc)
    return timedelta(hours=-4) if start <= when_utc < end else timedelta(hours=-5)


def to_eastern(when_utc):
    """A naive local Eastern datetime for an aware instant."""
    if when_utc.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    when_utc = when_utc.astimezone(timezone.utc)
    return (when_utc + _et_offset(when_utc)).replace(tzinfo=None)


# -------------------------------------------------------------------- intake

class BetRefused(ValueError):
    """A bet the app will not take. `code` is stable for callers."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExitPlan:
    kind: str
    target_pct: Decimal | None = None
    stop_pct: Decimal | None = None


@dataclass(frozen=True)
class Bet:
    session: date
    symbol: str
    side: str
    exit: ExitPlan
    confidence: Decimal
    basis: str
    thesis: str
    sources: tuple
    taken_at: datetime
    late: bool


@dataclass(frozen=True)
class Taken:
    """The outcome of `take_bet`: `accepted` with a bet, or `closed`."""
    status: str
    bet: Bet | None = None
    session: date | None = None


def _decimal(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise BetRefused("bad_" + field, f"{field} must be a number")
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        raise BetRefused("bad_" + field, f"{field} must be a number") from None
    if not out.is_finite():
        raise BetRefused("bad_" + field, f"{field} must be a finite number")
    return out


def parse_exit(raw):
    """Validate an exit plan: {"kind": open|close} or
    {"kind": "bracket", "target_pct"?: n, "stop_pct"?: n} with at least one
    level, each from 0.2 to 5 percent."""
    if not isinstance(raw, dict):
        raise BetRefused("bad_exit", "exit plan must be an object")
    kind = raw.get("kind")
    if kind not in EXIT_KINDS:
        raise BetRefused("bad_exit", f"exit kind must be one of {', '.join(EXIT_KINDS)}")
    extra = set(raw) - {"kind", "target_pct", "stop_pct"}
    if extra:
        raise BetRefused("bad_exit", f"unknown exit field(s): {', '.join(sorted(extra))}")
    levels = {k: raw.get(k) for k in ("target_pct", "stop_pct") if raw.get(k) is not None}
    if kind != "bracket":
        if levels:
            raise BetRefused("bad_exit", f"an {kind} exit takes no target or stop")
        return ExitPlan(kind)
    if not levels:
        raise BetRefused("bad_exit", "a bracket needs a target_pct, a stop_pct, or both")
    parsed = {}
    for key, value in levels.items():
        pct = _decimal(value, "exit")
        if not BRACKET_MIN_PCT <= pct <= BRACKET_MAX_PCT:
            raise BetRefused("bad_exit",
                             f"{key} must be from {BRACKET_MIN_PCT} to {BRACKET_MAX_PCT} percent")
        parsed[key] = pct
    return ExitPlan("bracket", parsed.get("target_pct"), parsed.get("stop_pct"))


def take_bet(payload, now_utc, taken_sessions):
    """Validate a bet submitted at `now_utc` (the app's clock, never the
    caller's). `taken_sessions` holds the session dates that already have a
    bet. A non-session day returns Taken("closed") and is not an error."""
    local = to_eastern(now_utc)
    session = local.date()
    if not is_session(session):
        return Taken("closed", session=session)
    if not isinstance(payload, dict):
        raise BetRefused("bad_bet", "bet must be an object")
    if session in taken_sessions:
        raise BetRefused("duplicate_session", f"a bet for the {session} session already exists")

    symbol = payload.get("symbol")
    if symbol not in SYMBOLS:
        raise BetRefused("bad_symbol", f"symbol {symbol!r} is not on the stock-bets list")
    side = payload.get("side")
    if side not in SIDES:
        raise BetRefused("bad_side", "side must be long or short")
    exit_plan = parse_exit(payload.get("exit"))
    confidence = _decimal(payload.get("confidence"), "confidence")
    if not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
        raise BetRefused("bad_confidence", "confidence must be from 0.50 to 0.90")
    basis = payload.get("basis")
    if basis not in BASES:
        raise BetRefused("bad_basis", f"basis must be one of {', '.join(BASES)}")
    thesis = payload.get("thesis")
    if not isinstance(thesis, str) or not thesis.strip() or len(thesis) > THESIS_MAX:
        raise BetRefused("bad_thesis", f"thesis must be 1 to {THESIS_MAX} characters")
    sources = payload.get("sources") or []
    if (not isinstance(sources, list) or len(sources) > SOURCES_MAX
            or not all(isinstance(s, str) and s.strip() and len(s) <= SOURCE_MAX
                       for s in sources)):
        raise BetRefused("bad_sources",
                         f"sources must be up to {SOURCES_MAX} strings of up to {SOURCE_MAX} characters")

    late = local.time() >= close_time(session)
    return Taken("accepted", Bet(
        session=session, symbol=symbol, side=side, exit=exit_plan,
        confidence=confidence, basis=basis, thesis=thesis.strip(),
        sources=tuple(s.strip() for s in sources),
        taken_at=now_utc.astimezone(timezone.utc), late=late), session=session)


# ------------------------------------------------------------------- scoring

@dataclass(frozen=True)
class Bar:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def of(cls, open, high, low, close):  # noqa: A002 - OHLC names
        return cls(*(_price(v) for v in (open, high, low, close)))


@dataclass(frozen=True)
class Result:
    entry: Decimal
    exit: Decimal
    exit_reason: str      # open | close | target | stop | gap_target | gap_stop
    gross_pct: Decimal
    net_pct: Decimal
    pnl: Decimal
    win: bool
    spy_pct: Decimal


def _price(value):
    out = Decimal(str(value))
    if not out.is_finite() or out <= 0:
        raise ValueError(f"price must be a positive number, got {value!r}")
    return out


def _levels(side, entry, plan):
    """Rule 1, mirrored for a short: (target price, stop price), either None."""
    sign = 1 if side == "long" else -1
    target = stop = None
    if plan.target_pct is not None:
        target = entry * (1 + sign * plan.target_pct / 100)
    if plan.stop_pct is not None:
        stop = entry * (1 - sign * plan.stop_pct / 100)
    return target, stop


def _bracket_exit(side, entry, plan, bar):
    """Rules 2-4. A short's 'past the target' is below it, so compare in the
    side's own direction: `ahead(a, b)` is true when a is at or beyond b in
    the bet's favour."""
    target, stop = _levels(side, entry, plan)
    if side == "long":
        def ahead(a, b):
            return a >= b
        best, worst = bar.high, bar.low
    else:
        def ahead(a, b):
            return a <= b
        best, worst = bar.low, bar.high

    if stop is not None and ahead(stop, bar.open):
        return bar.open, "gap_stop"
    if target is not None and ahead(bar.open, target):
        return bar.open, "gap_target"
    hit_stop = stop is not None and ahead(stop, worst)
    hit_target = target is not None and ahead(best, target)
    if hit_stop:          # rule 3: both touched scores as the stop
        return stop, "stop"
    if hit_target:
        return target, "target"
    return bar.close, "close"


def score(side, exit_plan, entry_close, next_bar, spy_entry_close, spy_next_close):
    """Score one bet from stored prices (rules 1-7).

    `entry_close` is the bet day's official close; `next_bar` is the next
    session's daily bar; the SPY pair is SPY's close on those same two
    sessions. Nothing here knows the symbol, so the same side and plan can be
    scored on every symbol of a session (the random-pick comparison).
    """
    if side not in SIDES:
        raise ValueError("side must be long or short")
    if not isinstance(exit_plan, ExitPlan):
        exit_plan = parse_exit(exit_plan)
    if not isinstance(next_bar, Bar):
        next_bar = Bar.of(**next_bar)
    entry = _price(entry_close)

    if exit_plan.kind == "open":
        exit_price, reason = next_bar.open, "open"
    elif exit_plan.kind == "close":
        exit_price, reason = next_bar.close, "close"
    else:
        exit_price, reason = _bracket_exit(side, entry, exit_plan, next_bar)

    if side == "long":
        gross = (exit_price / entry - 1) * 100
    else:
        gross = (entry - exit_price) / entry * 100
    gross = gross.quantize(_PCT, ROUND_HALF_EVEN)
    net = gross - COST_PCT
    pnl = (STAKE * net / 100).quantize(_MONEY, ROUND_HALF_EVEN)
    spy = ((_price(spy_next_close) / _price(spy_entry_close) - 1) * 100).quantize(
        _PCT, ROUND_HALF_EVEN)
    return Result(entry=entry, exit=exit_price.quantize(_PRICE, ROUND_HALF_EVEN),
                  exit_reason=reason, gross_pct=gross, net_pct=net, pnl=pnl,
                  win=net > 0, spy_pct=spy)
