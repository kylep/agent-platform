"""Session clock and quota gates (plan §4).

Nothing here ever calls a model, a probe or `quota_service.refresh`: the gates
read `quota_store.latest` only (the planner runs themselves keep it fresh).

`decide_start` and `mid_gate` are pure so the gate table is testable without a
database; `SessionClock.poll` is the loop body (every few seconds in prod).
It journals the closing sequence as `system` commands so replay matches.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from agentplatform.db import AgentDef, utcnow
from agentplatform.village.journal import Journal
from agentplatform.village.store import VillageStore, aware

log = logging.getLogger("village.clock")

LATE_START_GRACE = timedelta(minutes=10)     # engine down at noon (§3.1)
MID_GATE_EVERY = timedelta(minutes=1)
STALE_OR_MISSING_LENGTH = 30                 # [A15]


# --- quota views -------------------------------------------------------------

@dataclass
class QuotaView:
    five_hour: int | None        # rounded percent used
    seven_day: int | None
    observed_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {"five_hour": self.five_hour, "seven_day": self.seven_day,
                "observed_at": self.observed_at.isoformat() if self.observed_at else None}


def pct(fraction: float | None) -> int | None:
    """0.0-1.0 -> rounded whole percent (half up; 0.855 is 86, 0.29 is 29)."""
    return None if fraction is None else math.floor(fraction * 100 + 0.5)


def view_of(row) -> QuotaView | None:
    if row is None:
        return None
    return QuotaView(pct(row.five_hour_utilization), pct(row.seven_day_utilization),
                     aware(row.observed_at))


def is_old(view: QuotaView | None, now: datetime, stale_min: int) -> bool:
    return view is None or view.observed_at is None or \
        aware(now) - view.observed_at > timedelta(minutes=stale_min)


Reader = Callable[[str], Awaitable[QuotaView | None]]


def snapshot_reader(session_factory) -> Reader:
    """The only quota access the clock has: the stored snapshot, never a refresh."""
    from agentplatform import quota_store

    async def read(provider: str) -> QuotaView | None:
        async with session_factory() as s:
            return view_of(await quota_store.latest(s, provider))
    return read


def codex_in_use_reader(session_factory, store: VillageStore) -> Callable[[], Awaitable[bool]]:
    """True when the planner agent or any player's persona runs on codex."""
    async def in_use() -> bool:
        rules = await store.rules()
        names = [rules["planner_agent"], *rules["players"]]
        async with session_factory() as s:
            rows = (await s.execute(select(AgentDef.runtime).where(AgentDef.name.in_(names)))).scalars()
            return any(r == "codex" for r in rows)
    return in_use


# --- the gates (pure) ----------------------------------------------------------

@dataclass
class Gate:
    state: str                    # "live" | "rest" | "skipped"
    length_min: int
    reason: str | None = None
    quota: dict[str, Any] = field(default_factory=dict)


def decide_start(rules: dict[str, Any], claude: QuotaView | None, codex: QuotaView | None,
                 now: datetime, *, use_codex: bool = False, override: bool = False,
                 requested_length: int | None = None) -> Gate:
    """§4 "At T-0". Percents are rounded; "over" is strict, the 5h skip is `>=`."""
    lengths = rules["lengths"]
    quota = {"claude": claude.to_dict() if claude else None,
             "codex": codex.to_dict() if (use_codex and codex) else None,
             "override": override}
    normal = requested_length or lengths["normal"]
    if override:                                       # honoured only for test sessions (§4)
        return Gate("live", normal, "quota override", quota)
    stale_min = rules["quota_stale_min"]
    old = is_old(claude, now, stale_min)
    c7 = None if old else claude.seven_day
    c5 = None if old else claude.five_hour
    x7 = None
    if use_codex:
        cold = is_old(codex, now, stale_min)
        old = old or cold
        x7 = None if cold else codex.seven_day
    for label, seven in (("this week's AI budget", c7), ("this week's Codex budget", x7)):
        if seven is not None and seven > lengths["rest_above"]:
            return Gate("rest", 0, f"Rest day: {seven}% of {label} is used.", quota)
    if c5 is not None and c5 >= rules["five_hour_skip_at"]:
        return Gate("skipped", 0, "the 5-hour AI limit is nearly used", quota)
    reduced = any(v is not None and v > lengths["reduce_above"] for v in (c7, x7))
    if reduced:
        return Gate("live", min(normal, lengths["reduced"]),
                    f"over {lengths['reduce_above']}% of this week's AI budget is used", quota)
    if old:                                            # [A15] stale or missing: be careful
        return Gate("live", min(normal, STALE_OR_MISSING_LENGTH),
                    "quota snapshot is stale or missing", quota)
    return Gate("live", normal, None, quota)


def mid_gate(rules: dict[str, Any], claude: QuotaView | None, codex: QuotaView | None,
             now: datetime, *, use_codex: bool = False) -> str | None:
    """§4 "Mid-session gate": the end reason to close early with, or None.
    A stale snapshot closes nothing; the planner runs keep refreshing it."""
    stale_min = rules["quota_stale_min"]
    if not is_old(claude, now, stale_min):
        if claude.seven_day is not None and claude.seven_day > rules["lengths"]["rest_above"]:
            return "quota:7d"
        if claude.five_hour is not None and claude.five_hour >= rules["five_hour_close_at"]:
            return "quota:5h"
    if use_codex and not is_old(codex, now, stale_min) and codex.seven_day is not None \
            and codex.seven_day > rules["lengths"]["rest_above"]:
        return "quota:7d"
    return None


# --- time -----------------------------------------------------------------------

def local_day(now: datetime, rules: dict[str, Any]) -> date:
    return aware(now).astimezone(ZoneInfo(rules["session_tz"])).date()


def start_for(day: date, rules: dict[str, Any]) -> datetime:
    """`session_start_local` on `day` in `session_tz` (zoneinfo: DST-correct), as UTC."""
    hh, mm = (int(x) for x in rules["session_start_local"].split(":"))
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(rules["session_tz"])).astimezone(timezone.utc)


CLOSING_STEPS = (("no_big_jobs", "no_big_jobs_min"), ("walk_to_square", "walk_to_square_min"),
                 ("freeze", None))


class SessionClock:
    """Opens sessions, applies the gates and journals the closing sequence.

    Hooks (async, optional): `on_open(session_row)` after a session goes live,
    for the engine to load the world and for runs to launch."""

    def __init__(self, store: VillageStore, journal: Journal, bus, *, quota: Reader,
                 codex_in_use: Callable[[], Awaitable[bool]],
                 now: Callable[[], datetime] = utcnow,
                 on_open: Callable[[Any], Awaitable[None]] | None = None):
        self.store, self.journal, self.bus = store, journal, bus
        self.quota, self.codex_in_use, self._now, self.on_open = quota, codex_in_use, now, on_open
        self._last_mid: datetime | None = None

    async def _gate_inputs(self, use_codex: bool):
        claude = await self.quota("claude")
        codex = await self.quota("codex") if use_codex else None
        return claude, codex

    # --- opening ---------------------------------------------------------------
    async def poll(self, now: datetime | None = None) -> None:
        now = aware(now or self._now())
        rules = await self.store.rules()
        live = await self.store.live_session()
        if live is not None:
            await self._tend(live, rules, now)
            return
        for row in await self.store.sessions_in(("scheduled",)):
            if row.kind == "test":                      # created by the admin API, started here
                await self._open(row, rules, now, planned_start=now,
                                 override=bool((row.quota_at_start or {}).get("override")))
                return
        if rules["auto_schedule"]:
            await self._daily(rules, now)

    async def _daily(self, rules: dict[str, Any], now: datetime) -> None:
        day = local_day(now, rules)
        start = start_for(day, rules)
        if now < start or await self.store.daily_for(day.isoformat()) is not None:
            return
        if now > start + LATE_START_GRACE:              # engine down at noon: no catch-up session
            row = await self.store.add_session(day=day.isoformat(), kind="daily", planned_start=start,
                                               planned_end=start, length_min=0, state="skipped",
                                               ended_at=now, end_reason="engine was down at noon",
                                               quota_reason="engine was down at noon")
            self._announce(row, "skipped")
            return
        row = await self.store.add_session(day=day.isoformat(), kind="daily", planned_start=start,
                                           length_min=rules["lengths"]["normal"], state="scheduled")
        await self._open(row, rules, now, planned_start=start)

    async def _open(self, row, rules: dict[str, Any], now: datetime, *, planned_start: datetime,
                    override: bool = False) -> None:
        use_codex = await self.codex_in_use()
        claude, codex = await self._gate_inputs(use_codex)
        gate = decide_start(rules, claude, codex, now, use_codex=use_codex, override=override,
                            requested_length=(row.length_min if row.kind == "test" else None) or None)
        if gate.state != "live":
            row = await self.store.update_session(row.id, state=gate.state, length_min=0,
                                                  planned_start=planned_start, planned_end=planned_start,
                                                  quota_at_start=gate.quota, quota_reason=gate.reason,
                                                  ended_at=now, end_reason=gate.reason)
            self._announce(row, gate.state)
            return
        row = await self.store.update_session(
            row.id, state="live", length_min=gate.length_min, planned_start=planned_start,
            planned_end=planned_start + timedelta(minutes=gate.length_min), quota_at_start=gate.quota,
            quota_reason=gate.reason, started_at=now)
        self._last_mid = now
        self._announce(row, "started")
        if self.on_open:
            await self.on_open(row)

    # --- tending a live session -------------------------------------------------
    async def _tend(self, row, rules: dict[str, Any], now: datetime) -> None:
        end = aware(row.planned_end)
        if self._last_mid is None or now - self._last_mid >= MID_GATE_EVERY:
            self._last_mid = now
            if not await self.journal.system_sent(row.id, "quota_close"):
                use_codex = await self.codex_in_use()
                claude, codex = await self._gate_inputs(use_codex)
                reason = mid_gate(rules, claude, codex, now, use_codex=use_codex)
                if reason:
                    await self.journal.system(row.id, "quota_close", {"reason": reason})
                    end = min(end, now + timedelta(minutes=rules["closing"]["walk_to_square_min"]))
                    row = await self.store.update_session(row.id, planned_end=end, end_reason=reason)
                    self._announce(row, "closing")
        for action, key in CLOSING_STEPS:
            lead = timedelta(minutes=rules["closing"][key]) if key else timedelta(0)
            if now < end - lead:
                break
            if await self.journal.system_sent(row.id, action):
                continue
            if action != "freeze" and row.state != "closing":
                row = await self.store.update_session(row.id, state="closing")
                self._announce(row, "closing")
            await self.journal.system(row.id, action, {})

    def _announce(self, row, what: str) -> None:
        self.bus.session(row.day, {"session_id": row.id, "day": row.day, "kind": row.kind,
                                   "state": row.state, "event": what, "length_min": row.length_min,
                                   "planned_start": row.planned_start.isoformat() if row.planned_start else None,
                                   "planned_end": row.planned_end.isoformat() if row.planned_end else None,
                                   "reason": row.quota_reason or row.end_reason})
