"""The engine's wiring (plan §1, §5, §6, §7): connects the units inside one
`python -m agentplatform.village.main` process.

- `EngineHandler` is commands.Handler: reads (help/look/recall_notes through
  actions.dispatch, `wait` through the planner hub) and writes (actions.dispatch
  with `_Ports`, which journal through engine.journal, so a write is committed
  before the tool gets its receipt and the tick applies it).
- `Village` owns one session's minds: the PlannerHub (fed every tick), the
  PlannerSupervisor (planner runs, bounded restarts) and the
  ReflectionScheduler (look-back slots), opened and closed by engine hooks and
  driven by a slow loop that never touches the tick.
- `_ReflectionHost` is reflection.ReflectionHost over runs.py and the DB.

`run(engine)` is the main.py component entry: it attaches, resumes a live
session after a restart (no relaunch: the runs it started keep looping) and
serves the :8001 command port.
"""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import replace
from typing import Any

from sqlalchemy import func, select

from agentplatform.db import AgentDef, Run, RunState, VillageNote, VillageRun, utcnow
from agentplatform.village import actions, glue, prompts, runs
from agentplatform.village import contract as c
from agentplatform.village import reflection as R
from agentplatform.village.commands import Caller, CommandPort, Refused, build_app
from agentplatform.village.journal import IdempotencyConflict
from agentplatform.village.planner import PlannerHub, cycle_row, write_cycle
from agentplatform.village.planner_ops import prepare_edit
from agentplatform.village.store import aware

log = logging.getLogger("village.host")

VILLAGE_TOOL = "mcp__platform__village"
SLOW_LOOP_S = 2.0
PLANNER_MARGIN_S = 30 * 60      # planner deadline = session length + 30 min (§5.1)
RECENT_EVENTS = 2000
RECENT_WINDOW_S = 600           # "the last 10 min of its own events" (§6)
BUSY_RUN_STATES = (RunState.DISPATCHED, RunState.RUNNING)
REFLECTION_EVENT = {"running": "reflection_started", "done": "reflection_done",
                    "skipped": "reflection_skipped", "cancelled": "reflection_skipped",
                    "failed": "reflection_failed"}


# --- command port handler --------------------------------------------------------

class _Ports:
    """actions.ActionPorts for one call. Every write goes through the journal
    with the call's request_id and args hash, so a retry finds the first row."""

    def __init__(self, village: "Village", caller: Caller, command: c.Command | None):
        self.v, self.caller, self.command = village, caller, command
        self.saved: c.Command | None = None
        self.duplicate = False

    async def _journal(self, action: str, args: dict[str, Any], side=None) -> c.CommandResult:
        """`side` (see Journal.submit) writes side-table rows atomically with the
        journal row; a duplicate submit writes nothing."""
        cmd, who = self.command, self.caller
        try:
            out = await self.v.engine.journal.submit(
                who.session_id, who.role, action, args, run_id=who.run_id, persona=who.persona,
                request_id=cmd.request_id, digest=cmd.args_hash, side=side)
        except IdempotencyConflict:
            raise Refused(409, "this request_id was already used with different arguments; "
                               "use a new request_id")
        self.saved, self.duplicate = out.command, out.duplicate
        return c.CommandResult("accepted", data={"seq": out.command.seq})

    async def apply_world(self, action: str, args: dict[str, Any]) -> c.CommandResult:
        return await self._journal(action, self.command.args)

    async def look(self) -> dict[str, Any]:
        self.v.need_world()
        return self.v.view.look(self.caller.persona)

    async def queue_edit(self, base_version: int, ops: list[dict[str, Any]]) -> c.CommandResult:
        who = self.caller
        plan = prepare_edit({"request_id": self.command.request_id, "base_version": base_version,
                             "ops": ops}, self.v.view.queue(who.persona))
        hub = self.v.hub if who.role == c.PLANNER else None

        def record():
            cycle = hub.record_edit(who.persona, plan)
            cycle["run_id"] = who.run_id
            return cycle
        if not plan.valid:                    # nothing journaled: nothing to repeat
            if hub is not None:
                await write_cycle(self.v.sf, record())
            return actions.refused("; ".join(r["reason"] for r in plan.refused)[:256],
                                   refusals=plan.refused)
        # The cycle row (and the hub's refusal bookkeeping) happen only for a new
        # command, in the journal row's transaction: a retry records nothing twice.
        side = (lambda s, _row: s.add(cycle_row(record()))) if hub is not None else None
        return await self._journal("edit_queue", {"base_version": base_version, "ops": plan.valid},
                                   side=side)

    async def add_note(self, text: str, links: list[str]) -> str | None:
        """The note and its journal row commit together (one transaction); a
        retry that finds the first row writes no second note."""
        note = VillageNote(agent=self.caller.persona, session_id=self.caller.session_id,
                           text=text, links=list(links))

        def side(s, _row) -> None:
            s.add(note)
        await self._journal("note", self.command.args, side=side)
        return None if self.duplicate else note.id

    async def search_notes(self, q: str, limit: int) -> list[dict[str, Any]]:
        stmt = select(VillageNote).where(VillageNote.agent == self.caller.persona)
        if q:
            stmt = stmt.where(VillageNote.text.ilike(f"%{q}%"))
        async with self.v.sf() as s:
            rows = (await s.execute(stmt.order_by(VillageNote.created_at.desc()).limit(limit))).scalars()
            return [{"id": r.id, "text": r.text, "links": r.links or [],
                     "at": r.created_at.isoformat() if r.created_at else None} for r in rows]

    async def write_tomorrow(self, steps: list[dict[str, Any]], away_note: str | None) -> c.CommandResult:
        secs = glue.estimate_seconds(steps)
        if secs > glue.TOMORROW_MAX_S:
            return actions.refused(f"that is about {int(secs // 60)} minutes of work; "
                                   f"tomorrow's list is at most {glue.TOMORROW_MAX_S // 60}")
        return await self._journal("tomorrow", self.command.args)


class EngineHandler:
    """commands.Handler over the engine."""

    def __init__(self, village: "Village"):
        self.v = village

    def _ctx(self, caller: Caller, ports: _Ports) -> actions.ActionContext:
        return actions.ActionContext(session_id=caller.session_id, persona=caller.persona,
                                     run_id=caller.run_id, role=caller.role, ports=ports,
                                     slot=caller.slot, rules=self.v.rules)

    async def read(self, caller: Caller, action: str, args: dict[str, Any]) -> dict[str, Any]:
        if action == "wait":
            hub = self.v.hub
            if hub is None or hub.session_id != caller.session_id:
                raise Refused(503, "the village is not running this session")
            return await hub.wait(caller.persona, str(args.get("cursor") or "0"))
        result = await actions.dispatch(self._ctx(caller, _Ports(self.v, caller, None)), action, args)
        if result.state == "refused":
            raise Refused(422, result.reason or "refused")
        return result.data

    async def submit(self, caller: Caller, command: c.Command) -> c.Command:
        if caller.session_id != self.v.engine.session_id:
            raise Refused(403, "that session is over")
        ports = _Ports(self.v, caller, command)
        result = await actions.dispatch(self._ctx(caller, ports), command.action,
                                        {**command.args, "request_id": command.request_id})
        if ports.saved is not None:
            return ports.saved
        return replace(command, state="refused" if result.state == "refused" else "applied",
                       reason=result.reason)


# --- reflection host ----------------------------------------------------------------

class _ReflectionHost:
    def __init__(self, village: "Village"):
        self.v = village

    async def build_prompt(self, persona: str, slot: R.Slot) -> str:
        return R.build_prompt(persona, self.v.reflection_brief(persona), final=slot.final,
                              players=self.v.players)

    async def launch(self, persona: str, slot: R.Slot, prompt: str) -> str:
        a = self.v.actor(persona) or {}
        goal = (a.get("goals") or [None])[0]
        return await runs.launch_reflection(
            self.v.sf, self.v.producer, session_id=self.v.session.id, persona=persona,
            slot=slot.name, timeout_s=slot.timeout_s, prompt=prompt,
            memory_query=R.memory_query(persona, goal))

    async def run_state(self, run_id: str) -> str:
        async with self.v.sf() as s:
            run = await s.get(Run, run_id)
        return str(run.state) if run is not None else "missing"

    async def cancel(self, run_id: str, reason: str) -> None:
        from agentplatform.rapid_chat import cancel_turn_run
        async with self.v.sf() as s:
            await cancel_turn_run(s, self.v.producer, run_id, error=runs.CANCEL_ERROR)

    async def has_grant(self, persona: str) -> bool:
        async with self.v.sf() as s:
            row = await s.get(AgentDef, persona)
        return row is not None and VILLAGE_TOOL in (row.platform_tools or [])

    async def free_global_slots(self) -> int:
        async with self.v.sf() as s:
            busy = (await s.execute(select(func.count()).select_from(Run).where(
                Run.state.in_(BUSY_RUN_STATES)))).scalar() or 0
        return self.v.global_concurrency - busy

    async def record(self, persona: str, slot: R.Slot, outcome: str, reason: str | None,
                     run_id: str | None, public_text: str | None) -> None:
        if run_id:
            async with self.v.sf() as s:
                row = (await s.execute(select(VillageRun).where(VillageRun.run_id == run_id))).scalars().first()
                if row is not None:
                    row.state, row.reason = outcome, (reason or None) and reason[:256]
                    if outcome != "running":
                        row.finished_at = utcnow()
                    await s.commit()
        name = persona[:1].upper() + persona[1:]
        text = public_text or {"running": f"{name} is looking back",
                               "done": f"{name} finished a look-back"}.get(outcome)
        self.v.emit(REFLECTION_EVENT[outcome], persona, text or "",
                    {"slot": slot.name, "outcome": outcome, "reason": reason})

    async def has_tomorrow(self, persona: str) -> bool:
        a = self.v.actor(persona)
        return a is not None and "tomorrow" in a

    async def copy_queue_as_tomorrow(self, persona: str) -> None:
        # The world already did this at T-0 (glue._before_freeze), deterministically.
        log.info("village: %s has no tomorrow list after close", persona)


# --- the session's minds -------------------------------------------------------------

class Village:
    def __init__(self, engine, *, global_concurrency: int | None = None, clock=time.monotonic):
        self.engine, self.sf, self.clock = engine, engine.sf, clock
        if global_concurrency is None:
            settings = engine.settings
            if settings is None:
                from agentplatform.config import get_settings
                settings = get_settings()
            global_concurrency = settings.global_concurrency
        self.global_concurrency = global_concurrency
        self.view = glue.EngineView(lambda: engine.world)
        self.port = CommandPort(engine.sf, EngineHandler(self))
        self.rules: dict[str, Any] = c.merged_rules(None)
        self.players: list[str] = list(self.rules["players"])
        self.session = None
        self.hub: PlannerHub | None = None
        self.supervisor: runs.PlannerSupervisor | None = None
        self.scheduler: R.ReflectionScheduler | None = None
        self.recent: deque[c.Event] = deque(maxlen=RECENT_EVENTS)
        self._status: dict[str, str] = {}
        engine.open_hooks.append(self.on_open)
        engine.close_hooks.append(self.on_close)
        engine.tick_hooks.append(self.on_events)

    @property
    def producer(self):
        return self.engine.bus.producer

    def need_world(self):
        if self.engine.world is None:
            raise Refused(503, "the village is not running yet")
        return self.engine.world

    def actor(self, persona: str) -> dict | None:
        w = self.engine.world
        return None if w is None else w.actors.get(persona)

    def emit(self, kind: str, persona: str | None, text: str, data: dict | None = None) -> None:
        """A narrative event from outside the world (runs, look-backs). Not
        journaled: it changes no game state."""
        sid, w = self.engine.session_id, self.engine.world
        if sid is None or w is None:
            return
        self.engine.writer.push_events(sid, [c.Event(
            tick=w.tick, kind=kind, actor=persona, public_text=(text or None) and text[:200],
            data=dict(data or {}))])

    def elapsed_s(self) -> float:
        start = aware(self.session.started_at) if self.session is not None else None
        return 0.0 if start is None else (aware(self.engine._now()) - start).total_seconds()

    # --- hooks ---------------------------------------------------------------------
    async def on_open(self, row) -> None:
        await self._start(row, fresh=True)

    async def resume(self) -> None:
        """After a restart into a live session: rebuild the minds, launch nothing new."""
        if self.engine.live and self.engine.session_row is not None:
            await self._start(self.engine.session_row, fresh=False)

    async def _start(self, row, *, fresh: bool) -> None:
        self.rules = await self.engine.store.rules()
        self.players = list(self.rules["players"])
        self.session, self._status = row, {}
        self.hub = PlannerHub(self.view, self.rules, session_id=row.id, clock=self.clock)
        self.supervisor = runs.PlannerSupervisor(
            self.sf, self.producer, session_id=row.id, personas=self.players,
            prompt_for=self.planner_prompt, rules=self.rules, emit=self.emit, hub=self.hub,
            clock=self.clock, timeout_seconds=runs.clamp_timeout(row.length_min * 60 + PLANNER_MARGIN_S))
        self.scheduler = R.ReflectionScheduler(_ReflectionHost(self), self.rules, row.length_min,
                                               self.players)
        if fresh:
            for p in self.players:
                await self.engine.journal.submit(row.id, "system", "start_tomorrow", {}, persona=p)
            await self.supervisor.open()
            return
        self.supervisor.live = True
        for persona, vrow in (await runs.latest_planner_rows(self.sf, row.id)).items():
            self.hub.set_run(persona, vrow.run_id, active=vrow.state in runs.LIVE_ROW_STATES)
        elapsed = self.elapsed_s()
        self.scheduler.handled |= {(s.persona, s.name) for s in self.scheduler.plan
                                   if s.offset_s <= elapsed}

    async def on_close(self, row) -> None:
        """T-0 (after the close snapshot): stop every mind, launch nothing more."""
        if self.hub is not None:
            self.hub.session_state("closed")
        scheduler, supervisor = self.scheduler, self.supervisor
        self.scheduler = self.supervisor = None
        if scheduler is not None:
            await scheduler.close()
        if supervisor is not None:
            await supervisor.close()
        await runs.cancel_runs(self.sf, self.producer, row.id, reason="session closed")
        self.session = None

    def on_events(self, events: list[c.Event]) -> None:
        hub = self.hub
        if hub is not None:
            hub.feed(events)
            for ev in events:     # the world's own per-op refusals ride the next `wait`
                if ev.kind == "command_refused" and ev.actor in self.players:
                    hub.chan(ev.actor).refusals.append(
                        {"index": -1, "op": ev.data.get("op"), "reason": ev.data.get("reason")})
        self.recent.extend(ev for ev in events if ev.kind != "heartbeat")

    # --- the slow loop -----------------------------------------------------------------
    async def tend(self) -> None:
        if (not self.engine.live or self.supervisor is None or self.session is None
                or self.engine.session_id != self.session.id):
            return
        await self.supervisor.tick()
        if self.scheduler is not None:
            await self.scheduler.poll(self.elapsed_s())
        await self._push_statuses()

    async def _push_statuses(self) -> None:
        if self.supervisor is None or self.hub is None:
            return
        sup = self.supervisor.statuses()
        for p in self.players:
            st = sup.get(p, "running")
            st = self.hub.status(p) if st == "running" else st
            if self._status.get(p) != st:
                self._status[p] = st
                await self.engine.journal.system(self.session.id, "planner_status",
                                                 {"persona": p, "status": st})

    async def loop(self) -> None:
        while not self.engine.stop.is_set():
            try:
                await self.tend()
            except Exception:
                log.exception("village host: slow loop failed")
            await asyncio.sleep(SLOW_LOOP_S)

    async def serve(self) -> None:
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(build_app(self.port), host="0.0.0.0",
                                               port=self.engine.port, log_level="warning",
                                               lifespan="off"))
        await server.serve()
        if not self.engine.stop.is_set():
            raise RuntimeError("village command port stopped")

    # --- prompts ------------------------------------------------------------------------
    async def planner_prompt(self, persona: str, attempt: int) -> str:
        from agentplatform.api.agent_self import PlaySectionError, hide_play_section, play_section
        async with self.sf() as s:
            row = await s.get(AgentDef, persona)
        text = row.prompt if row is not None else ""
        try:
            section, body = play_section(text), hide_play_section(text)
        except PlaySectionError:
            section, body = "", hide_play_section(text)
        a = self.actor(persona) or {}
        brief = prompts.build_brief(body, a.get("goals") or [], section,
                                    prompt_apply=self.rules["prompt_apply"])
        return prompts.planner_prompt(persona, brief, session_id=self.session.id, attempt=attempt)

    def reflection_brief(self, persona: str) -> R.ReflectionBrief:
        w, a = self.engine.world, self.actor(persona) or {}
        cutoff = w.tick - RECENT_WINDOW_S * c.TICK_HZ
        blocks = [f"- {b['op']} ({b['step']}): {b['reason']}" for b in w.block_log
                  if b["actor"] == persona][-10:]
        own = [f"- {e.public_text}" for e in self.recent
               if e.actor == persona and e.tick >= cutoff and e.public_text][-30:]
        peers = [f"- {e.actor}: {e.public_text}" for e in self.recent
                 if e.kind in ("say", "tell") and e.actor != persona and e.public_text][-10:]
        residents = [f"- {r['name']} ({r['id']}): habits {json.dumps(r['habits'])}"
                     for _, r in sorted(w.actors.items())
                     if r.get("owner") == persona and r["kind"] != "character"]
        menu = "\n".join(f"- {k}: {v}" for k, v in actions.ACTION_HELP.items()
                         if c.REFLECTION in c.ACTION_ROLES[k] and k in actions.HANDLERS)
        return R.ReflectionBrief(
            block_list="\n".join(blocks),
            goals_habits=json.dumps({"goals": a.get("goals") or [], "habits": a.get("habits") or [],
                                     "queue": glue._queue(a) if a else []}, ensure_ascii=False),
            recent_events="\n".join(own), residents="\n".join(residents),
            peer_lines="\n".join(peers), tool_menu=menu)


def attach(engine, **kw) -> Village:
    return Village(engine, **kw)


async def run(engine) -> None:
    """main.py component: wire in, resume, serve the port and tend the minds."""
    village = attach(engine)
    await village.resume()
    await asyncio.gather(village.serve(), village.loop())
