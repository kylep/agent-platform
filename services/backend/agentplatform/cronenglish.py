"""Plain-English descriptions of 5-field cron expressions.

There is exactly ONE renderer and it lives here, on the server. The web UI used
to describe crons in the browser (cronstrue), which put the sentence an operator
reads in a different codebase — a different *language* — from the schedule that
actually fires: two implementations free to disagree about what `0 9 * * 1-5`
means, with nothing to catch it. Every English sentence the UI shows about a
cron now comes from `/api/cron/preview`, which is this module beside the
scheduler's own `next_fire`. The description and the fire times are computed by
the same code that will do the firing.

The wording favours the shapes the schedule builder emits (every N minutes,
hourly at M, daily/weekly/monthly at HH:MM) and degrades to a plainer,
field-by-field reading for hand-written expressions rather than refusing them.
Times are 24-hour: the builder's inputs are, and 09:00 cannot be misread the
way "9:00" can.
"""
from __future__ import annotations

# Cron's weekday numbering starts at Sunday, and accepts 7 for Sunday too.
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

DAY_ALIASES = {name[:3].upper(): i for i, name in enumerate(DAY_NAMES)}
MONTH_ALIASES = {name[:3].upper(): i + 1 for i, name in enumerate(MONTH_NAMES)}

# The `@`-shorthands croniter accepts, expanded to the 5-field form so one code
# path describes them. Stored crons are 5-field, but a row can predate that.
ALIASES = {
    "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

# A term is a tuple whose first element names its shape:
#   ("all",)                  *
#   ("value", n)              5
#   ("range", a, b)           1-5
#   ("step", a, b, n)         */15, 3-5/2, 3/2
#   ("nth", weekday, n)       1#2  — the 2nd Monday
#   ("lastdow", weekday)      5L   — the last Friday
#   ("lastdom",)              L    — the last day of the month
Term = tuple


def expand_alias(expr: str) -> str:
    return ALIASES.get(expr.strip().lower(), expr.strip())


def _num(token: str, lo: int, hi: int, aliases: dict[str, int]) -> int:
    token = token.strip()
    if token.upper() in aliases:
        value = aliases[token.upper()]
    else:
        try:
            value = int(token)
        except ValueError:
            raise ValueError(f"{token!r} is not a number") from None
    if not lo <= value <= hi:
        raise ValueError(f"{value} is outside {lo}-{hi}")
    return value


def _parse_term(part: str, lo: int, hi: int, aliases: dict[str, int], dow: bool) -> Term:
    part = part.strip()
    if not part:
        raise ValueError("empty field element")
    if "/" in part:
        base, _, step_text = part.partition("/")
        try:
            step = int(step_text)
        except ValueError:
            raise ValueError(f"{step_text!r} is not a step") from None
        if step < 1:
            raise ValueError("step must be at least 1")
        if base.strip() == "*":
            return ("step", lo, hi, step)
        if "-" in base:
            a, _, b = base.partition("-")
            return ("step", _num(a, lo, hi, aliases), _num(b, lo, hi, aliases), step)
        return ("step", _num(base, lo, hi, aliases), hi, step)
    if part == "*":
        return ("all",)
    if dow and "#" in part:
        day, _, nth = part.partition("#")
        return ("nth", _num(day, lo, hi, aliases) % 7, int(nth))
    if part.upper().endswith("L"):
        head = part[:-1]
        if dow and head:
            return ("lastdow", _num(head, lo, hi, aliases) % 7)
        if not dow and not head:
            return ("lastdom",)
        raise ValueError(f"{part!r} is not supported")
    if "-" in part[1:]:      # [1:] so a negative-looking token still errors as a number
        a, _, b = part.partition("-")
        return ("range", _num(a, lo, hi, aliases), _num(b, lo, hi, aliases))
    return ("value", _num(part, lo, hi, aliases))


def _parse_field(raw: str, lo: int, hi: int, aliases: dict[str, int] | None = None,
                 dow: bool = False) -> list[Term]:
    terms = [_parse_term(p, lo, hi, aliases or {}, dow) for p in raw.split(",")]
    if dow:
        # 7 means Sunday, same as 0 — normalize so "0,7" isn't "Sunday and Sunday".
        terms = [("value", t[1] % 7) if t[0] == "value" else t for t in terms]
    return terms


# --- reading terms back out ---------------------------------------------------

def _is_all(terms: list[Term]) -> bool:
    """`*` — and `*/1`, which means the same thing and should read that way."""
    if terms == [("all",)]:
        return True
    return len(terms) == 1 and terms[0][0] == "step" and terms[0][3] == 1


def _full_step(terms: list[Term], lo: int, hi: int) -> int | None:
    """The N of a bare `*/N` over the whole field, or None."""
    if len(terms) == 1 and terms[0][0] == "step" and terms[0][3] > 1 \
            and (terms[0][1], terms[0][2]) == (lo, hi):
        return terms[0][3]
    return None


def _values(terms: list[Term], limit: int = 60) -> list[int] | None:
    """Every value the field matches, when it is plain enough to enumerate.
    None for steps and weekday specials — those get described, not listed."""
    out: list[int] = []
    for t in terms:
        if t[0] == "value":
            out.append(t[1])
        elif t[0] == "range" and t[1] <= t[2]:
            out.extend(range(t[1], t[2] + 1))
        else:
            return None
        if len(out) > limit:
            return None
    return sorted(set(out))


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else _ORDINAL_SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def _unit_phrase(terms: list[Term], unit: str, lo: int, hi: int, fmt=str) -> str:
    """"hour 09", "hours 09 and 17", "every 2nd hour", "hours 03 through 05".
    `lo`/`hi` are the field's own bounds, so a step that spans the whole field
    is "every 2nd hour" while a narrowed one keeps the window it was given."""
    if len(terms) == 1 and terms[0][0] == "step":
        _, a, b, n = terms[0]
        every = f"every {_ordinal(n)} {unit}"
        return every if (a, b) == (lo, hi) else \
            f"{every} from {fmt(a)} through {fmt(b)}"
    parts, plural = [], len(terms) > 1
    for t in terms:
        if t[0] == "value":
            parts.append(fmt(t[1]))
        elif t[0] == "range":
            parts.append(f"{fmt(t[1])} through {fmt(t[2])}")
            plural = True
        elif t[0] == "step":
            parts.append(f"{fmt(t[1])} through {fmt(t[2])} every {_ordinal(t[3])}")
            plural = True
        else:                                   # pragma: no cover - dow-only shapes
            parts.append(str(t))
    return f"{unit}{'s' if plural else ''} {_join(parts)}"


# --- the clauses --------------------------------------------------------------

def _time_clause(minutes: list[Term], hours: list[Term]) -> str:
    hh = lambda h: f"{h:02d}"                                   # noqa: E731
    mm = lambda m: f"{m:02d}"                                   # noqa: E731
    min_all, hour_all = _is_all(minutes), _is_all(hours)
    min_step, hour_step = _full_step(minutes, 0, 59), _full_step(hours, 0, 23)
    min_values, hour_values = _values(minutes), _values(hours)

    hour_phrase = _unit_phrase(hours, "hour", 0, 23, hh)
    minute_phrase = _unit_phrase(minutes, "minute", 0, 59, mm)

    if min_all:
        return "Every minute" if hour_all else f"Every minute of {hour_phrase}"
    if min_step is not None:
        return (f"Every {min_step} minutes" if hour_all
                else f"Every {min_step} minutes of {hour_phrase}")
    if min_values is not None and hour_all:
        if min_values == [0]:
            return "Every hour, on the hour"
        return f"At {minute_phrase} past every hour"
    # A short cross-product of minutes and hours is far more readable as clock
    # times than as two lists the reader has to multiply out.
    if min_values is not None and hour_values is not None and \
            len(min_values) * len(hour_values) <= 6:
        return "At " + _join([f"{h:02d}:{m:02d}" for h in hour_values for m in min_values])
    if hour_step is not None:
        return f"At {minute_phrase} past every {_ordinal(hour_step)} hour"
    return f"At {minute_phrase} past {hour_phrase}"


def _dom_clause(terms: list[Term]) -> str:
    if _is_all(terms):
        return ""
    if terms == [("lastdom",)]:
        return ", on the last day of the month"
    if len(terms) == 1 and terms[0][0] == "step" and terms[0][3] > 1:
        return f", on every {_ordinal(terms[0][3])} day of the month"
    return f", on {_unit_phrase(terms, 'day', 1, 31)} of the month"


def _month_clause(terms: list[Term]) -> str:
    if _is_all(terms):
        return ""
    name = lambda m: MONTH_NAMES[m - 1]                          # noqa: E731
    if len(terms) == 1 and terms[0][0] == "step" and terms[0][3] > 1:
        return f", every {_ordinal(terms[0][3])} month"
    if len(terms) == 1 and terms[0][0] == "range":
        return f", from {name(terms[0][1])} through {name(terms[0][2])}"
    values = _values(terms)
    if values is not None:
        return ", in " + _join([name(v) for v in values])
    return ", in " + _unit_phrase(terms, "month", 1, 12, name)   # pragma: no cover


def _dow_clause(terms: list[Term]) -> str:
    if _is_all(terms):
        return ""
    name = lambda d: DAY_NAMES[d % 7]                            # noqa: E731
    parts = []
    for t in terms:
        if t[0] == "value":
            parts.append(name(t[1]))
        elif t[0] == "range":
            parts.append(f"{name(t[1])} through {name(t[2])}")
        elif t[0] == "step":
            parts.append(f"every {_ordinal(t[3])} day from {name(t[1])} through {name(t[2])}")
        elif t[0] == "nth":
            parts.append(f"the {_ordinal(t[2])} {name(t[1])} of the month")
        elif t[0] == "lastdow":
            parts.append(f"the last {name(t[1])} of the month")
    # "Monday through Friday" is a span and reads as one; "the 2nd Monday" is a
    # thing the run lands *on*; a set of separate days is a list of exceptions
    # and wants "only on".
    kind = terms[0][0] if len(terms) == 1 else "list"
    prefix = {"range": ", ", "step": ", ", "nth": ", on ", "lastdow": ", on "}
    return prefix.get(kind, ", only on ") + _join(parts)


def describe(expr: str) -> str:
    """A plain-English sentence for a 5-field cron. Raises ValueError with the
    reason when the expression cannot be read as one."""
    expr = expand_alias(expr or "")
    if not expr:
        raise ValueError("a cron expression is required")
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected 5 fields, got {len(fields)}")
    minute, hour, dom, month, dow = fields
    minutes = _parse_field(minute, 0, 59)
    hours = _parse_field(hour, 0, 23)
    doms = _parse_field(dom, 1, 31)
    months = _parse_field(month, 1, 12, MONTH_ALIASES)
    dows = _parse_field(dow, 0, 7, DAY_ALIASES, dow=True)
    return (_time_clause(minutes, hours) + _dom_clause(doms)
            + _month_clause(months) + _dow_clause(dows))
