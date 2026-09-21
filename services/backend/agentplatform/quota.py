"""Reading Anthropic's unified usage headers (docs/design/22): the one place a
header string becomes a number, a datetime or None.

Everything here is pure — no session, no producer, no clock of its own — because
the same parse has to serve three callers that must never disagree: the proxy's
report, the deliberate refresh, and whatever reads the row back. A value that
reached the row unparsed would be a string from someone else's server sitting in
a prompt, and `raw` is where those actually live: kept verbatim for the headers
this file does not understand yet, never rendered.

The forms are deliberately wide. Anthropic's utilization was not observable
from this machine before implementation, so both a fraction (`0.22`) and a
percent (`22`, `22.5`) parse, and a reset is epoch seconds or ISO-8601; live
verification records which one actually arrives."""
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger("quota")

# Every usage header shares this prefix; the five below are the ones with a
# typed column. The others (overage, grace, slow) reach `raw` and stop there.
HEADER_PREFIX = "anthropic-ratelimit-unified-"
H_5H_UTILIZATION = HEADER_PREFIX + "5h-utilization"
H_5H_RESET = HEADER_PREFIX + "5h-reset"
H_7D_UTILIZATION = HEADER_PREFIX + "7d-utilization"
H_7D_RESET = HEADER_PREFIX + "7d-reset"
H_STATUS = HEADER_PREFIX + "status"
KNOWN_HEADERS = (H_5H_UTILIZATION, H_5H_RESET, H_7D_UTILIZATION, H_7D_RESET,
                 H_STATUS)

# `status` is the one free-text value that reaches a prompt, so it is a token or
# it is nothing: lower-case, short, and no character that could open markup.
STATUS_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")
# `raw` is written to the singleton row on EVERY observation, so its size is a
# cost the platform pays per API call, and its content is chosen by whoever
# answered the request. A response carrying ten thousand prefixed headers would
# otherwise rewrite most of a megabyte into postgres each time. The known
# headers are kept first, so the cap can only ever drop values nothing reads.
RAW_MAX_HEADERS = 64
RAW_MAX_VALUE = 256
# The one `raw` key that is not a header: the HTTP status the reporter saw.
# Namespaced away from the `anthropic-ratelimit-unified-` prefix so it can
# never collide with something Anthropic sends.
RAW_HTTP_STATUS = "http_status"
# How far ahead of our own clock a reported `observed_at` may sit before it is
# treated as wrong rather than as news. Two machines' clocks disagree by
# milliseconds; anything past a minute is a bug or a lie, and an observation
# dated far in the future is the one input that can wedge the snapshot for
# good — it can never be superseded, so every later real observation is
# silently dropped. Both ends of that bound live here: what is accepted on the
# way in, and what `quota_store` refuses to let block a write.
FUTURE_SKEW = timedelta(seconds=60)
# Where the tool stops reporting and starts advising.
ADVISORY_THRESHOLD = 0.90


@dataclass(frozen=True)
class Observation:
    """One set of header values seen on one response. The snapshot row is the
    latest of these; the `quota.events` topic is the ones that moved."""
    five_hour_utilization: float | None
    five_hour_resets_at: datetime | None
    seven_day_utilization: float | None
    seven_day_resets_at: datetime | None
    status: str | None
    raw: dict[str, str]
    observed_at: datetime
    source: str


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def parse_utilization(value: str | None) -> float | None:
    """A fraction or a percent, as a fraction.

    The rule is `> 1 means percent`, which puts the only ambiguous input on the
    fraction side: `1.0` is a window that is exactly full, while `100` is the
    same window written as a percentage. Everything outside 0..1 after that is
    clamped rather than dropped — a bar cannot draw 150%, and a negative
    utilization is a server bug, not a reason to lose the other window."""
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number > 1:
        number /= 100
    return min(max(number, 0.0), 1.0)


def parse_reset(value: str | None) -> datetime | None:
    """Epoch seconds or ISO-8601, as a tz-aware UTC datetime. A timestamp with
    no offset is read as UTC: the alternative is a naive datetime escaping into
    a subtraction somewhere, which is a crash, not a wrong number."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        epoch = float(text)
    except (TypeError, ValueError):
        epoch = None
    if epoch is not None:
        # An epoch at or before 1970 is a sentinel or a bug — `0` and `-1` are
        # both common ways of saying "no value" — never a window that resets.
        if epoch <= 0 or not math.isfinite(epoch):
            return None
        try:
            return datetime.fromtimestamp(epoch, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def parse_observed_at(value, now: datetime) -> datetime:
    """When a REPORTER says it saw something, bounded by our own clock.

    Always returns a usable time: an absent or unparsable value means "about
    now", because a report whose timestamp is garbage is still a report. A
    timestamp from the future is the dangerous case rather than the useless
    one — it outranks every subsequent observation forever — so it is clamped
    to now instead of trusted or rejected."""
    parsed = parse_reset(value)
    if parsed is None:
        return _aware(now)
    return _aware(now) if parsed > _aware(now) + FUTURE_SKEW else parsed


def parse_status(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    return token if STATUS_PATTERN.match(token) else None


def parse_observation(headers, observed_at: datetime, source: str,
                      http_status=None) -> Observation | None:
    """Header map → `Observation`, or None when the response said nothing about
    usage. nginx lower-cases what it forwards, but the match is case-insensitive
    anyway so a hand-made body or a different hop cannot silently miss.

    `http_status` is the status the reporter saw alongside those headers. It is
    diagnostic only — nothing reads it, and no typed column holds it — but
    knowing that a 0.99 reading came off a 429 rather than a 200 is the
    difference between reading the snapshot and guessing at it, so it is kept
    in `raw` as a string like everything else there."""
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    if not any(h in lowered for h in KNOWN_HEADERS):
        return None
    raw = _raw_of(lowered)
    if http_status is not None:
        # After the cap, so a flood of unknown headers cannot displace it.
        raw[RAW_HTTP_STATUS] = str(http_status)[:RAW_MAX_VALUE]
    return Observation(
        five_hour_utilization=parse_utilization(lowered.get(H_5H_UTILIZATION)),
        five_hour_resets_at=parse_reset(lowered.get(H_5H_RESET)),
        seven_day_utilization=parse_utilization(lowered.get(H_7D_UTILIZATION)),
        seven_day_resets_at=parse_reset(lowered.get(H_7D_RESET)),
        status=parse_status(lowered.get(H_STATUS)),
        raw=raw,
        observed_at=_aware(observed_at), source=source)


def parse_codex_usage(payload, observed_at: datetime, source: str) -> Observation | None:
    """Codex's usage document → the same provider-neutral observation.

    Window names are derived from their duration. Codex accounts do not all
    expose both windows, and primary/secondary ordering is not a stable label.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rate_limit"), dict):
        return None
    rate = payload["rate_limit"]
    values = {}
    raw = {}
    for name in ("primary_window", "secondary_window"):
        window = rate.get(name)
        if not isinstance(window, dict):
            continue
        try:
            seconds = int(window.get("limit_window_seconds"))
        except (TypeError, ValueError):
            continue
        key = "five" if seconds == 5 * 3600 else "seven" if seconds == 7 * 86400 else None
        if key is None:
            continue
        values[key] = (parse_utilization(window.get("used_percent")),
                       parse_reset(window.get("reset_at")))
        raw[f"{name}_seconds"] = str(seconds)
    if not values:
        return None
    allowed = rate.get("allowed")
    status = "allowed" if allowed is True else "limited" if allowed is False else None
    if isinstance(payload.get("plan_type"), str):
        raw["plan_type"] = payload["plan_type"][:RAW_MAX_VALUE]
    if allowed is not None:
        raw["allowed"] = str(bool(allowed)).lower()
    five = values.get("five", (None, None))
    seven = values.get("seven", (None, None))
    return Observation(five[0], five[1], seven[0], seven[1], status, raw,
                       _aware(observed_at), source)


def _raw_of(lowered: dict) -> dict[str, str]:
    """Every `anthropic-ratelimit-unified-*` header, verbatim but bounded. The
    five the platform reads come first so a flood of unknown headers can only
    displace headers nothing looks at; the rest are sorted so the same response
    always produces the same `raw` and a re-observation is not a spurious diff."""
    prefixed = [k for k in lowered if k.startswith(HEADER_PREFIX)]
    known = [k for k in KNOWN_HEADERS if k in lowered]
    ordered = known + sorted(set(prefixed) - set(known))
    return {k: str(lowered[k])[:RAW_MAX_VALUE] for k in ordered[:RAW_MAX_HEADERS]}


def is_stale(snapshot, now: datetime) -> bool:
    """Whether what the platform holds can still be believed. A window that has
    reset since the observation makes the whole snapshot a description of a
    window that no longer exists, so the earliest non-null reset decides. A row
    with no reset at all has nothing to expire against — it is thin, not
    stale, and the UI shows it as what it is."""
    if snapshot is None:
        return True
    resets = [r for r in (snapshot.five_hour_resets_at, snapshot.seven_day_resets_at)
              if r is not None]
    if not resets:
        return False
    return _aware(now) > min(_aware(r) for r in resets)


def changed(prev, obs: Observation) -> bool:
    """Whether this observation is worth an event. Only the four numbers count:
    `status` flapping or a repeated response would otherwise turn the topic
    into a log of every request the platform made, which is the thing the
    design chose a singleton row to avoid."""
    if prev is None:
        return True
    def same(a, b) -> bool:
        if a is None or b is None:
            return a is None and b is None
        return _aware(a) == _aware(b) if isinstance(a, datetime) else a == b
    return not (same(prev.five_hour_utilization, obs.five_hour_utilization)
                and same(prev.five_hour_resets_at, obs.five_hour_resets_at)
                and same(prev.seven_day_utilization, obs.seven_day_utilization)
                and same(prev.seven_day_resets_at, obs.seven_day_resets_at))


def humanize_delta(delta: timedelta) -> str:
    """`3h 54m`, `3d 8h`, `2s`. Two units is what a person or a model reads at a
    glance; a third only ever adds precision nobody acts on. A negative delta
    (a reset already passed, a skewed clock) reads as zero rather than as a
    minus sign in the middle of a sentence."""
    total = int(max(delta.total_seconds(), 0))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    units = ((days, "d"), (hours, "h"), (minutes, "m"), (seconds, "s"))
    lead = next((i for i, (value, _) in enumerate(units) if value), None)
    if lead is None:
        return "0s"
    parts = [f"{units[lead][0]}{units[lead][1]}"]
    if lead + 1 < len(units) and units[lead + 1][0]:
        parts.append(f"{units[lead + 1][0]}{units[lead + 1][1]}")
    return " ".join(parts)


def _percent(utilization: float) -> int:
    """Half-UP, not Python's half-to-even: 22.5% reading as 22% is the kind of
    quiet off-by-one nobody ever finds, and a usage number should round toward
    the bad news."""
    return math.floor(utilization * 100 + 0.5)


def _window_line(label: str, utilization: float | None, resets_at: datetime | None,
                 now: datetime) -> str:
    if utilization is None:
        return f"{label} window: unknown."
    used = f"{label} window: {_percent(utilization)}% used"
    if resets_at is None:
        return used + "."
    reset = _aware(resets_at)
    return (f"{used}, resets in {humanize_delta(reset - _aware(now))} "
            f"({reset.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC).")


def render_text(snapshot, now: datetime) -> str:
    """The tool's answer. Built entirely from parsed numbers plus `status`,
    which `parse_status` has already reduced to a token — no header string
    reaches a model through here."""
    if snapshot is None:
        return "No usage observation yet."
    lines = [_window_line("5-hour", snapshot.five_hour_utilization,
                          snapshot.five_hour_resets_at, now),
             _window_line("7-day", snapshot.seven_day_utilization,
                          snapshot.seven_day_resets_at, now)]
    tail = []
    if snapshot.observed_at is not None:
        age = humanize_delta(_aware(now) - _aware(snapshot.observed_at))
        tail.append(f"Observed {age} ago ({snapshot.source}).")
    if snapshot.status:
        tail.append(f"Status: {snapshot.status}.")
    if tail:
        lines.append(" ".join(tail))
    if any(u is not None and u > ADVISORY_THRESHOLD
           for u in (snapshot.five_hour_utilization, snapshot.seven_day_utilization)):
        lines.append("Usage is above 90%: defer heavy work until the window resets.")
    return "\n".join(lines)
