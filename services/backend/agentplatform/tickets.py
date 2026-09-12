"""Tickets (docs/design/20) is the platform's work tracker: a ticket is a row
with a state AND a Relay thread. This module is its pure layer — the state
machine, prefix derivation, key references in text, and every sentence the
platform writes into a room about a ticket — with no I/O.

It is separate from the store for the same reason `relay.py` is: the API, the
`tickets` tool, the board and the tests must agree on what a move is legal, what
a key looks like and what the thread was told, and a rule that lives in one
place cannot drift between them. The strings here are read by humans in the web
UI and, unrendered, in the Discord mirror, so they are golden-tested."""
import re
from datetime import datetime, timedelta, timezone

from .db import TicketState
from .relay import AGENT_PREFIX, USER_PREFIX, strip_room_mentions
# Fenced code is quoted text, never a reference — an agent explaining `OPS-12`
# in a snippet must not link work to it. Relay already decided what counts as a
# fence (including the unclosed one an LLM truncated mid-block); borrowing its
# pattern keeps "what is code" one answer across the platform. Inline backticks
# are deliberately NOT stripped here: unlike a mention, naming a ticket in prose
# costs nothing, and people habitually write keys in backticks.
from .relay import _FENCE

# Board order, left to right — the columns the UI renders and the order the
# stats are reported in, so it is a tuple and not the enum's iteration order by
# accident.
STATES = tuple(s.value for s in TicketState)
# Closed work: reaching one of these sets `closed_at`, and leaving one is a
# reopen, not a move.
CLOSED_STATES = frozenset({TicketState.DONE.value, TicketState.CANCELLED.value})
PREFIX_MIN, PREFIX_MAX = 2, 6
# A key is `PREFIX-n`, the prefix being PREFIX_MIN..PREFIX_MAX characters.
# Case-sensitive: `ops-12` in prose is the word, not the ticket. Only the first
# character is required to be a letter, because a prefix may carry a trailing
# digit — that is how `derive_prefix` breaks a collision.
KEY_RE = re.compile(r"([A-Z][A-Z0-9]{1,5})-(\d+)")
_REF_RE = re.compile(r"\b" + KEY_RE.pattern + r"\b")
_FENCE_RE = re.compile(_FENCE, re.DOTALL)
_WORDS_RE = re.compile(r"[A-Za-z]+")
# Same shape as `relay_router.BUDGET_PREFIX`: the store finds "did we already
# say this in this room this hour?" with a LIKE on the prefix, so the variable
# part has to come last.
BUDGET_PREFIX = "⏸️ paused: an agent has used its hourly ticket-creation budget"
_WHITESPACE_RE = re.compile(r"\s+")
# Caps for the fields the platform quotes back into a room. A title is a
# headline and a reason is a sentence; anything past these is not information,
# it is an agent (or a person) filling the thread. The numbers are generous
# enough that no honest ticket is ever cut.
TITLE_LIMIT, REASON_LIMIT, LABEL_LIMIT = 120, 200, 64


def _line(text, limit: int) -> str:
    """One line of somebody else's text, safe to interpolate into a sentence the
    platform writes in its own voice.

    A ticket's title, its reason and its assignee reach here from an agent or a
    connector — the system row and the card body are the platform saying "news
    moved OPS-12 → blocked: <their words>". Unsanitised, a newline in those
    words ends the platform's sentence and starts an attacker's, in the room's
    most trusted voice; and because the thread is replayed into every later
    summons' context window and mirrored raw to Discord, it is said again to
    every agent that touches the ticket afterwards. So: all whitespace collapses
    to single spaces (there is no second line to hijack), room mentions lose
    their `@` exactly as agent-authored text does at post time (a title cannot
    page the room), and the result is cut to `limit` with a visible `…` so a
    truncation is never mistaken for the whole of what was said."""
    one = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
    one = strip_room_mentions(one)
    return one if len(one) <= limit else one[:limit - 1].rstrip() + "…"


def budget_body(limit: int) -> str:
    return f"{BUDGET_PREFIX} ({limit}/hour); try again later"


def state_label(state: str) -> str:
    """The state as a human says it: `in progress`, not `in_progress`. The
    underscore is a database value; nothing a person reads should carry it."""
    return (state or "").replace("_", " ")


def can_move(from_state: str, to_state: str) -> bool:
    """Whether a ticket may go from one state to another. The board is
    deliberately permissive — work really does go from review back to blocked —
    so any known state reaches any other, with two exceptions: a move to where
    the ticket already is says nothing, and closed work reopens before it moves
    again. Coming out of `done` straight into `in_progress` would leave
    `closed_at` set and the ticket both finished and in flight; `open` is the
    one door back, and that transition is the `reopened` event."""
    if from_state not in STATES or to_state not in STATES or from_state == to_state:
        return False
    if from_state in CLOSED_STATES:
        return to_state == TicketState.OPEN.value
    return True


def derive_prefix(name: str, taken: set[str]) -> str:
    """A channel's default ticket prefix, derived from its name and made unique
    against `taken`. Initials of the words first (`health-monitor` → `HM`) since
    that is what a person would abbreviate to; a single-word name has no
    initials to speak of, so it takes the name's own first letters instead
    (`ops` → `OPS`, `general` → `GEN`). Collisions get a counter appended,
    trimming the letters so the prefix stays within its six characters.

    Deterministic: the same name and the same `taken` always give the same
    answer, so a seed migration and a channel created later agree."""
    words = _WORDS_RE.findall(name or "")
    if not words:
        raise ValueError("a ticket prefix needs at least one letter in the name")
    initials = "".join(w[0] for w in words).upper()
    base = initials if len(initials) >= PREFIX_MIN else "".join(words).upper()[:3]
    # A one-letter name has nothing left to pad with but itself.
    while len(base) < PREFIX_MIN:
        base += base[-1]
    base = base[:PREFIX_MAX]
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = str(n)
        # At least one letter always survives the counter: a prefix that was all
        # digits would be a key no `KEY_RE` could ever match, so the channel
        # would own tickets nobody could refer to. Running out of counters is
        # absurd in practice (five digits of them) and a caller that somehow
        # does deserves an error, not an unusable prefix.
        keep = PREFIX_MAX - len(suffix)
        if keep < 1:
            raise ValueError(f"no unique ticket prefix left for {name!r}")
        candidate = base[:keep] + suffix
        if candidate not in taken:
            return candidate
        n += 1


def find_ticket_refs(body: str, prefixes: set[str]) -> list[str]:
    """Ticket keys mentioned in a message, first-seen order, no duplicates, only
    for prefixes that exist. This is how a comment saying "fixes OPS-12" links
    itself to the ticket, so an unknown prefix must not produce a dangling
    reference — `XYZ-1` in someone's pasted log is text."""
    out: list[str] = []
    for m in _REF_RE.finditer(_FENCE_RE.sub(" ", body or "")):
        if m.group(1) not in prefixes:
            continue
        key = m.group(0)
        if key not in out:
            out.append(key)
    return out


def participant_label(participant: str) -> str:
    """A participant as a room says it: `agent:news` is just `news` to the
    people reading the thread. A connector identity keeps its namespace — a bare
    Discord snowflake names nobody."""
    p = participant or ""
    for prefix in (AGENT_PREFIX, USER_PREFIX):
        if p.startswith(prefix):
            return p[len(prefix):]
    return p


def card_for(ticket, *, url_base: str) -> dict:
    """The `card` payload on the ticket's event message. Kept small and flat:
    the Relay client renders it directly, and the Discord bridge falls back to
    the body below when it cannot."""
    return {"type": "ticket", "key": ticket.key,
            "title": _line(ticket.title, TITLE_LIMIT),
            "state": str(ticket.state), "priority": str(ticket.priority),
            "assignee": ticket.assignee,
            "url": f"{(url_base or '').rstrip('/')}/tickets/{ticket.key}"}


def card_body(ticket) -> str:
    """The card message's plain text. It exists because the Discord mirror (and
    any other bridge) gets the body and not the card, so the sentence has to
    carry the ticket on its own."""
    return (f"🎫 {ticket.key} · {_line(ticket.title, TITLE_LIMIT)} — opened by "
            f"{_line(participant_label(ticket.reporter), LABEL_LIMIT)}")


def event_row_text(event, actor_label: str, key: str) -> str:
    """The system row posted in the thread for one ticket event. `event` is
    duck-typed (.kind/.from_value/.to_value/.reason) and the key is passed in
    because the ORM row holds a ticket_id, not a key — the caller has the ticket
    in hand anyway, having just changed it.

    A reason, when there is one, is appended the same way for every kind: the
    thread's value is that the *why* is right there with the *what*."""
    kind = getattr(event, "kind", "")
    if kind == "created":
        text = f"{actor_label} opened {key}"
    elif kind == "moved":
        text = (f"{actor_label} moved {key} → "
                f"{_line(state_label(event.to_value), LABEL_LIMIT)}")
    elif kind == "assigned":
        text = (f"{actor_label} assigned {key} to "
                f"{_line(participant_label(event.to_value), LABEL_LIMIT)}"
                if event.to_value else f"{actor_label} unassigned {key}")
    elif kind == "edited":
        text = f"{actor_label} edited {key}"
    elif kind == "commented":
        text = f"{actor_label} commented on {key}"
    elif kind == "reopened":
        text = f"{actor_label} reopened {key}"
    else:
        # Not a fallback: an unknown kind would put a sentence in the room that
        # describes nothing, and the kinds are a closed set the store controls.
        raise ValueError(f"unknown ticket event kind: {kind!r}")
    reason = _line(getattr(event, "reason", None), REASON_LIMIT)
    return f"{text}: {reason}" if reason else text


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def is_stale(ticket, now: datetime, days: int) -> bool:
    """Work in flight that nobody has touched for `days`. Only `in_progress`
    counts: `blocked` is a wait someone declared, and an untouched `open` ticket
    is a backlog, which is what a backlog is for."""
    if str(ticket.state) != TicketState.IN_PROGRESS.value:
        return False
    last = ticket.last_activity_at
    if last is None:
        return False
    # sqlite hands back naive datetimes and postgres does not, and a caller
    # reaching for `utcnow()` passes a naive `now`. Either mismatch is a
    # TypeError that would take down a whole board pass over one row, so both
    # operands are normalised rather than one.
    return (_aware(now) - _aware(last)) >= timedelta(days=days)
