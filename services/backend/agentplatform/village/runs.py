"""Starting, watching and stopping the village's platform runs (plan §5.1, §10.2).

Planner half. U5 adds the reflection half as new functions in this file
(`launch_reflection` builds on `launch_run`, which is role-agnostic).

The one rule, taken from rapid_chat/dreams: the `village_runs` row is the fence
key, so it is COMMITTED before `materialize_run` makes a Run, and a failed
materialize marks the row failed instead of leaving a fence-less run or a
phantom active one.
"""
import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from agentplatform.db import ACTIVE_STATES, Run, RunState, VillageRun, VillageSession, utcnow
from agentplatform.village import contract as c

log = logging.getLogger(__name__)

TRIGGER = "village"
BACKOFF_S = (60, 120)          # before restart 1, then every later restart
MIN_GAP_S = 120                # at most about one launch per 2 minutes per persona
MAX_RESTARTS = 20              # per persona per session; early exits are expected noise
LIVE_ROW_STATES = ("launching", "running")
MIN_TIMEOUT_S, MAX_TIMEOUT_S = 60, 7200
CANCEL_ERROR = "cancelled by village"


def requested_by(session_id: str, persona: str) -> str:
    return f"village:{session_id}:{persona}"


async def launch_run(sf, producer, *, session_id: str, persona: str, role: str, agent: str,
                     prompt: str, attempt: int = 1, slot: str | None = None,
                     timeout_seconds: int | None = None,
                     memory_query: str | None = None) -> VillageRun:
    """Row first (committed), then the Run; on failure the row ends `failed`
    with the reason and is returned (the caller decides about a retry).
    `timeout_seconds` is the per-run deadline (materialize_run's keyword)."""
    from agentplatform import materialize
    if role not in c.RUN_ROLES:
        raise ValueError(f"unknown village run role {role!r}")
    run_id = uuid.uuid4().hex
    async with sf() as s:
        row = VillageRun(session_id=session_id, persona=persona, role=role, run_id=run_id,
                         slot=slot, attempt=attempt, state="launching")
        s.add(row)
        await s.commit()
        row_id = row.id
    spec = {"run_id": run_id, "agent": agent, "prompt": prompt, "trigger": TRIGGER,
            "requested_by": requested_by(session_id, persona), "initiated_by": "admin"}
    if memory_query:
        spec["memory_query"] = memory_query[:300]
    try:
        await materialize.materialize_run(sf, producer, spec, timeout_seconds=timeout_seconds)
    except Exception as exc:
        log.warning("village %s run for %s failed to launch", role, persona, exc_info=True)
        async with sf() as s:
            row = await s.get(VillageRun, row_id)
            row.state, row.reason, row.finished_at = "failed", f"launch: {exc}"[:256], utcnow()
            await s.commit()
            s.expunge(row)
            return row
    async with sf() as s:
        row = await s.get(VillageRun, row_id)
        s.expunge(row)
        return row


async def launch_planner(sf, producer, *, session_id: str, persona: str, prompt: str,
                         attempt: int = 1, agent: str = "village-planner",
                         timeout_seconds: int | None = None) -> VillageRun:
    return await launch_run(sf, producer, session_id=session_id, persona=persona,
                            role=c.PLANNER, agent=agent, prompt=prompt, attempt=attempt,
                            timeout_seconds=timeout_seconds)


def clamp_timeout(seconds: float) -> int:
    """materialize_run accepts 60..7200."""
    return int(max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, seconds)))


async def launch_reflection(sf, producer, *, session_id: str, persona: str, slot: str,
                            timeout_s: int, prompt: str, memory_query: str) -> str:
    """The persona's own run (§6 Launch): row (role=reflection, slot) committed
    first, then materialize_run with the per-run timeout. Raises when the launch
    failed (the row is already marked failed); returns the run id."""
    row = await launch_run(sf, producer, session_id=session_id, persona=persona,
                           role=c.REFLECTION, agent=persona, prompt=prompt, slot=slot,
                           timeout_seconds=clamp_timeout(timeout_s), memory_query=memory_query)
    if row.state == "failed":
        raise RuntimeError(row.reason or "launch failed")
    return row.run_id


def _row_state(run: Run | None) -> tuple[str, str | None]:
    """VillageRun.state (and a reason) for the platform Run behind it."""
    if run is None:
        return "failed", "run row missing"
    st = run.state
    if st == RunState.QUEUED:
        return "launching", None
    if st in (RunState.DISPATCHED, RunState.RUNNING):
        return "running", None
    if st == RunState.SUCCEEDED:
        return "done", "run ended itself before the session closed"
    if st == RunState.KILLED:
        return "cancelled", (run.error or "killed")[:256]
    return "failed", (run.error or str(st.value if hasattr(st, "value") else st) or "ended")[:256]


async def reconcile(sf, session_id: str, role: str | None = None) -> list[VillageRun]:
    """Copy platform Run state onto still-open `village_runs` rows."""
    async with sf() as s:
        q = select(VillageRun).where(VillageRun.session_id == session_id,
                                     VillageRun.state.in_(LIVE_ROW_STATES))
        if role:
            q = q.where(VillageRun.role == role)
        rows = list((await s.execute(q)).scalars())
        runs = {r.id: r for r in (await s.execute(
            select(Run).where(Run.id.in_([r.run_id for r in rows])))).scalars()} if rows else {}
        for row in rows:
            state, reason = _row_state(runs.get(row.run_id))
            if state != row.state:
                row.state = state
                if state not in LIVE_ROW_STATES:
                    row.reason, row.finished_at = reason, utcnow()
        await s.commit()
        for row in rows:
            s.expunge(row)
        return rows


class CancelFailed(RuntimeError):
    """A cancel could not be confirmed (the publish failed); the run may be alive."""


async def cancel_run_confirmed(sf, producer, run_id: str, *, attempts: int = 3,
                               delay: float = 0.5) -> None:
    """`cancel_turn_run` returns a string when the cancel publish failed; that is
    a failure, retried, and raised if it never goes through."""
    from agentplatform.rapid_chat import cancel_turn_run
    err = ""
    for i in range(attempts):
        async with sf() as s:
            err = await cancel_turn_run(s, producer, run_id, error=CANCEL_ERROR)
        if not err:
            return
        if i + 1 < attempts:
            await asyncio.sleep(delay)
    raise CancelFailed(f"run {run_id}:{err}")


async def cancel_runs(sf, producer, session_id: str, role: str | None = None,
                      reason: str = "session closed") -> int:
    """Cancel every open run of the session (queued ones are killed in the
    database, dispatched ones by the cancel publish; rapid_chat's helper). A
    row is only marked cancelled once its cancel is confirmed; any that could
    not be raise CancelFailed after the rest were tried."""
    n, failed = 0, []
    async with sf() as s:
        q = select(VillageRun).where(VillageRun.session_id == session_id,
                                     VillageRun.state.in_(LIVE_ROW_STATES))
        if role:
            q = q.where(VillageRun.role == role)
        rows = list((await s.execute(q)).scalars())
    for row in rows:
        try:
            await cancel_run_confirmed(sf, producer, row.run_id)
        except CancelFailed as exc:
            failed.append(str(exc))
            continue
        async with sf() as s:
            cur = await s.get(VillageRun, row.id)
            cur.state, cur.reason, cur.finished_at = "cancelled", reason[:256], utcnow()
            await s.commit()
        n += 1
    if failed:
        raise CancelFailed("; ".join(failed))
    return n


async def sweep_stale_runs(sf, producer, *, reason: str = "swept after session close") -> int:
    """Boot sweep: runs of sessions that are no longer live/closing but whose
    village_runs row is non-terminal or whose platform run is still active (a
    crash between close and cancel). Cancels them, confirmed."""
    n = 0
    async with sf() as s:
        ended = select(VillageSession.id).where(VillageSession.state.not_in(("live", "closing", "scheduled")))
        rows = list((await s.execute(
            select(VillageRun, Run.state).outerjoin(Run, Run.id == VillageRun.run_id)
            .where(VillageRun.session_id.in_(ended)))).all())
    for row, run_state in rows:
        if row.state not in LIVE_ROW_STATES and run_state not in ACTIVE_STATES:
            continue
        if run_state in ACTIVE_STATES:
            try:
                await cancel_run_confirmed(sf, producer, row.run_id)
            except CancelFailed:
                log.error("village sweep: could not cancel run %s", row.run_id)
                continue
        async with sf() as s:
            cur = await s.get(VillageRun, row.id)
            if cur.state in LIVE_ROW_STATES:
                cur.state, cur.reason, cur.finished_at = "cancelled", reason[:256], utcnow()
                await s.commit()
        n += 1
    return n


async def latest_planner_rows(sf, session_id: str) -> dict[str, VillageRun]:
    """persona -> its newest planner row this session."""
    async with sf() as s:
        rows = (await s.execute(select(VillageRun).where(
            VillageRun.session_id == session_id, VillageRun.role == c.PLANNER)
            .order_by(VillageRun.attempt))).scalars()
        out = {r.persona: r for r in rows}
        for r in out.values():
            s.expunge(r)
        return out


Emit = Callable[[str, str, str, dict], Any]      # (event kind, persona, text, data)


class PlannerSupervisor:
    """One planner run per persona for the session; bounded restarts (§5.1).

    `tick()` is called by the engine's slow loop (seconds, never the hands'
    tick). It never raises into the caller for one persona's trouble."""

    def __init__(self, sf, producer, *, session_id: str, personas: list[str],
                 prompt_for: Callable[[str, int], Awaitable[str]], rules: dict | None = None,
                 emit: Emit | None = None, hub=None, clock: Callable[[], float] = time.monotonic,
                 backoff: tuple[float, ...] = BACKOFF_S, timeout_seconds: int | None = None,
                 max_restarts: int = MAX_RESTARTS, min_gap_s: float = MIN_GAP_S) -> None:
        self.sf, self.producer = sf, producer
        self.session_id, self.personas = session_id, list(personas)
        self.prompt_for = prompt_for
        self.rules = c.merged_rules(rules)
        self.emit, self.hub, self.clock, self.backoff = emit, hub, clock, backoff
        self.timeout_seconds = timeout_seconds
        self.max_restarts, self.min_gap_s = max_restarts, min_gap_s
        self._last_launch: dict[str, float] = {}
        self.live = False
        self.exhausted: set[str] = set()
        self._due: dict[str, float] = {}

    def _say(self, kind: str, persona: str, text: str, **data: Any) -> None:
        if self.emit is not None:
            self.emit(kind, persona, text, data)

    async def _launch(self, persona: str, attempt: int) -> VillageRun:
        prompt = await self.prompt_for(persona, attempt)
        row = await launch_planner(self.sf, self.producer, session_id=self.session_id,
                                   persona=persona, prompt=prompt, attempt=attempt,
                                   agent=self.rules["planner_agent"],
                                   timeout_seconds=self.timeout_seconds)
        self._last_launch[persona] = self.clock()
        failed = row.state == "failed"
        if self.hub:
            if attempt > 1:
                self.hub.reset(persona)
            self.hub.set_run(persona, row.run_id, active=not failed)
        self._say("planner_status", persona, f"{persona} is waking up" if not failed else
                  f"{persona} can't think right now", status="waking" if not failed else "failed",
                  attempt=attempt)
        return row

    async def open(self) -> list[VillageRun]:
        """Session open: all launches start now; the hands never wait for them."""
        self.live = True
        return [await self._launch(p, 1) for p in self.personas]

    async def close(self, reason: str = "session closed") -> int:
        self.live = False
        if self.hub:
            for p in self.personas:
                self.hub.set_run(p, None, active=False)
        return await cancel_runs(self.sf, self.producer, self.session_id, c.PLANNER, reason)

    async def tick(self) -> None:
        if not self.live:
            return
        await reconcile(self.sf, self.session_id, c.PLANNER)
        rows = await latest_planner_rows(self.sf, self.session_id)
        for persona in self.personas:
            if persona in self.exhausted:
                continue
            row = rows.get(persona)
            if row is None:
                await self._launch(persona, 1)
                continue
            if row.state in LIVE_ROW_STATES:
                self._due.pop(persona, None)
                continue
            await self._ended(persona, row)

    async def _ended(self, persona: str, row: VillageRun) -> None:
        now = self.clock()
        if persona not in self._due:
            if row.attempt - 1 >= self.max_restarts:
                self.exhausted.add(persona)
                if self.hub:
                    self.hub.set_run(persona, None, active=False)
                self._say("planner_status", persona, f"{persona} is running on habits",
                          status="habits", attempt=row.attempt)
                return
            wait = self.backoff[min(row.attempt - 1, len(self.backoff) - 1)]
            due = max(now + wait, self._last_launch.get(persona, now) + self.min_gap_s)
            self._due[persona] = due
            wait = due - now
            if self.hub:
                self.hub.set_run(persona, None, active=False)
            self._say("planner_status", persona,
                      f"{persona} can't think right now: restarting", status="restarting",
                      attempt=row.attempt, in_s=wait, exit_reason=row.reason)
            return
        if now >= self._due[persona]:
            del self._due[persona]
            await self._launch(persona, row.attempt + 1)

    def statuses(self) -> dict[str, str]:
        """persona -> habits | restarting | running, for the card."""
        return {p: ("habits" if p in self.exhausted else "restarting" if p in self._due else "running")
                for p in self.personas}
