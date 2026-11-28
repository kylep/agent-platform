"""The planner's side of the engine: the `wait` long-poll and the delta it
returns (plan §5.2), edit bookkeeping (think time, refusals fed back), and the
status words the screen shows (§5.1 failure table).

Pure asyncio, no database and no model. The engine feeds it the events each tick
produces (`feed`), and the command port (U4) calls `wait` / `record_edit`. What it
needs to know about the world comes through `WorldView`, which the engine glue
implements over the real World (U1): this module never imports a world.
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from agentplatform.village import contract as c
from agentplatform.village.planner_ops import EditPlan
from agentplatform.village.prompts import village_data_block

# Most urgent first. step_failed / step_blocked bypass `planner_min_s`.
PRIORITY = ("step_failed", "step_blocked", "addressed", "step_done", "nearby", "notice",
            "brief_changed", "runway", "session_state", "heartbeat")
URGENT = frozenset({"step_failed", "step_blocked"})
FULL_LOOK_EVERY = 10          # every 10th return is a full look (bounds drift)
SLOW_THINK_S = 20             # "thinking slowly"
SILENT_WAIT_S = 90            # "can't think right now"
MAX_REMINDERS = 2
NOTICE_KINDS = frozenset({"rain_notice", "rain_started", "no_big_jobs", "walk_to_square"})
SPEECH_KINDS = ("say", "tell")
ADDRESSED_KINDS = frozenset({"say", "tell", "invite", "pull_set"})


class WorldView(Protocol):
    """What the hub reads about a character. Implemented by the engine glue over
    the real world; every method is read-only and never awaits."""

    def queue(self, persona: str) -> list[dict[str, Any]]: ...
    def queue_version(self, persona: str) -> int: ...
    def queue_seconds(self, persona: str) -> float: ...
    def look(self, persona: str) -> dict[str, Any]:
        """Full state: self (position, carrying, queue with states and reasons,
        goals, habits) and everything visible in the view circle."""
    def changes(self, persona: str) -> dict[str, Any]:
        """The same shape, only what changed since the last call for this persona."""
    def is_near(self, persona: str, event: c.Event) -> bool: ...
    def private(self, persona: str) -> dict[str, Any]:
        """{"knowledge": [{"kind","text","source"}], "reminders": [str]}: this
        persona's own rows, each delivered once (reminders once per session)."""


@dataclass
class _Chan:
    persona: str
    seq: int = 0
    returns: int = 0
    pending: set[str] = field(default_factory=set)
    speech: list[tuple[str, str, str]] = field(default_factory=list)   # (source, kind, text)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    brief: tuple[str, str] | None = None                                # (version, text)
    notes: list[str] = field(default_factory=list)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    last_return: float | None = None
    last_wait: float | None = None
    last_seen: float | None = None     # last wait call or edit
    awaiting: tuple[float, str, datetime] | None = None   # (monotonic, reason, utc) of last return
    last_reply: dict[str, Any] | None = None
    run_id: str | None = None
    active: bool = False


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _source(actor: str | None) -> str:
    actor = actor or "unknown"
    return actor if ":" in actor else f"agent:{actor}"


class PlannerHub:
    def __init__(self, view: WorldView, rules: dict[str, Any] | None = None, *,
                 clock: Callable[[], float] = time.monotonic, poll_s: float = 0.25,
                 session_id: str = "") -> None:
        self.view = view
        self.rules = c.merged_rules(rules)
        self.clock = clock
        self.poll_s = poll_s
        self.session_id = session_id
        self.stopped = False
        self._chans: dict[str, _Chan] = {}

    # --- wiring -------------------------------------------------------------

    def chan(self, persona: str) -> _Chan:
        if persona not in self._chans:
            self._chans[persona] = _Chan(persona)
        return self._chans[persona]

    def set_run(self, persona: str, run_id: str | None, active: bool = True) -> None:
        """The supervisor says which run is the planner now (and whether it lives)."""
        ch = self.chan(persona)
        ch.run_id, ch.active = run_id, active

    def reset(self, persona: str) -> None:
        """A fresh run (restart): its first `wait` gets a full look, nothing stale."""
        ch = self.chan(persona)
        ch.returns, ch.last_reply, ch.awaiting, ch.last_return = 0, None, None, None
        ch.pending.clear()
        ch.speech.clear()
        ch.last_wait = ch.last_seen = None

    def brief_changed(self, persona: str, brief: str) -> None:
        from agentplatform.village.prompts import brief_version
        ch = self.chan(persona)
        ch.brief = (brief_version(brief), brief)
        self._wake(ch, "brief_changed")

    def session_state(self, state: str) -> None:
        """closing → a note and a wake for everyone; closed → `stop:true`."""
        if state == "closed":
            self.stopped = True
        for ch in self._chans.values():
            if state == "closing":
                ch.notes.append("the session is closing")
            self._wake(ch, "session_state")
        if state == "closed":
            for ch in self._chans.values():
                ch.wake.set()

    # --- events in ----------------------------------------------------------

    def _wake(self, ch: _Chan, reason: str) -> None:
        ch.pending.add(reason)
        ch.wake.set()

    def feed(self, events: list[c.Event]) -> None:
        """Classify one tick's events into per-persona wake reasons."""
        for ev in events:
            if ev.kind == "session_closed":
                self.session_state("closed")
                continue
            if ev.kind == "session_closing":
                self.session_state("closing")
                continue
            for ch in self._chans.values():
                self._classify(ch, ev)

    def _classify(self, ch: _Chan, ev: c.Event) -> None:
        me = ch.persona
        mine = ev.actor == me
        if mine and ev.kind in ("step_done", "step_failed", "step_blocked"):
            self._wake(ch, ev.kind)
            return
        if mine:
            return
        if ev.kind in NOTICE_KINDS:
            self._wake(ch, "notice")
            return
        addressed = me in (ev.subjects or []) and ev.kind in ADDRESSED_KINDS
        near = addressed or self.view.is_near(me, ev)
        if not near:
            return
        if ev.kind in SPEECH_KINDS and ev.public_text:
            ch.speech.append((_source(ev.actor), ev.kind, ev.public_text))
        self._wake(ch, "addressed" if addressed else "nearby")

    # --- wait ---------------------------------------------------------------

    def _ready_reason(self, ch: _Chan) -> str | None:
        if self.view.queue_seconds(ch.persona) < self.rules["runway_s"]:
            ch.pending.add("runway")
        if not ch.pending:
            return None
        if ch.pending & URGENT or ch.last_return is None:
            return self._top(ch)
        if self.clock() - ch.last_return >= self.rules["planner_min_s"]:
            return self._top(ch)
        return None

    @staticmethod
    def _top(ch: _Chan) -> str:
        return next(r for r in PRIORITY if r in ch.pending)

    async def wait(self, persona: str, cursor: str = "0") -> dict[str, Any]:
        ch = self.chan(persona)
        start = self.clock()
        ch.last_wait = ch.last_seen = start
        ch.awaiting = None                       # a wait with no edit in between: no cycle
        # A retry of a reply the model never saw (tool timeout): same cursor as the
        # last reply it did see → serve that reply again. "0" always means fresh.
        if cursor != "0" and ch.last_reply is not None and cursor.isdigit() and int(cursor) < ch.seq:
            return ch.last_reply
        deadline = start + self.rules["planner_heartbeat_s"]
        while True:
            if self.stopped:
                return self._reply(ch, "session_state")
            if ch.returns == 0:
                return self._reply(ch, "session_state")
            reason = self._ready_reason(ch)
            if reason:
                return self._reply(ch, reason)
            now = self.clock()
            if now >= deadline:
                return self._reply(ch, "heartbeat")
            timeout = min(self.poll_s, deadline - now)
            ch.wake.clear()
            try:
                await asyncio.wait_for(ch.wake.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    def _reply(self, ch: _Chan, reason: str) -> dict[str, Any]:
        me = ch.persona
        full = ch.returns % FULL_LOOK_EVERY == 0
        ch.seq += 1
        out: dict[str, Any] = {"cursor": str(ch.seq), "wake": reason,
                               "reasons": [r for r in PRIORITY if r in ch.pending] or [reason],
                               "stop": self.stopped, "full": full,
                               "queue_version": self.view.queue_version(me)}
        out["look" if full else "changes"] = self.view.look(me) if full else self.view.changes(me)
        if ch.speech:
            out["speech"] = [{"from": s, "kind": k, "text": t} for s, k, t in ch.speech]
        priv = self.view.private(me) or {}
        if priv.get("knowledge"):
            out["knowledge"] = [{"kind": k.get("kind", "told"), "source": k.get("source"),
                                 "text": k["text"]} for k in priv["knowledge"]]
        if priv.get("reminders"):
            out["reminders"] = list(priv["reminders"])[:MAX_REMINDERS]
        if ch.refusals:
            out["refusals"] = ch.refusals
        if ch.notes:
            out["notes"] = ch.notes
        if ch.brief:
            out["brief"] = {"version": ch.brief[0], "text": ch.brief[1]}
        ch.pending.clear()
        ch.speech, ch.refusals, ch.notes, ch.brief = [], [], [], None
        ch.returns += 1
        ch.last_return = self.clock()
        ch.awaiting = (ch.last_return, reason, _now_utc())
        ch.last_reply = out
        return out

    # --- edits --------------------------------------------------------------

    def record_edit(self, persona: str, plan: EditPlan) -> dict[str, Any]:
        """Bookkeeping after an `edit_queue`: refusals ride the next `wait`; the
        returned dict is one `village_planner_cycles` row's fields (metadata only)."""
        ch = self.chan(persona)
        ch.refusals.extend(plan.refused)
        now = ch.last_seen = self.clock()
        cycle = {"session_id": self.session_id, "persona": persona, "run_id": ch.run_id,
                 "wake_reason": None, "wait_returned_at": None, "edit_at": _now_utc(),
                 "think_ms": None, "ops_applied": len(plan.valid),
                 "ops_refused": [{"index": r["index"], "op": r["op"]} for r in plan.refused]}
        if ch.awaiting:
            t0, reason, at = ch.awaiting
            cycle.update(wake_reason=reason, wait_returned_at=at, think_ms=int((now - t0) * 1000))
            ch.awaiting = None
        return cycle

    # --- screen status -------------------------------------------------------

    def status(self, persona: str) -> str:
        """ok | thinking | thinking_slowly | cant_think (§5.1 failure table)."""
        ch = self.chan(persona)
        now = self.clock()
        if not ch.active:
            return "ok"
        if ch.awaiting:
            waited = now - ch.awaiting[0]
            if waited > SILENT_WAIT_S:
                return "cant_think"
            return "thinking_slowly" if waited > SLOW_THINK_S else "thinking"
        if ch.last_seen is not None and now - ch.last_seen > SILENT_WAIT_S:
            return "cant_think"
        return "ok"


def render(reply: dict[str, Any]) -> str:
    """The text the tool hands the model: the whole reply, once, in one block."""
    return village_data_block(reply)


async def write_cycle(sf, cycle: dict[str, Any]) -> None:
    """Persist one cycle row (the command port calls this after `record_edit`)."""
    from agentplatform.db import VillagePlannerCycle
    row = {k: v for k, v in cycle.items() if v is not None or k in ("ops_refused",)}
    row.setdefault("wait_returned_at", _now_utc())
    async with sf() as s:
        s.add(VillagePlannerCycle(**row))
        await s.commit()
