"""The engine's command port (plan §7, §10.1): who is calling, may they call
this action, and has this write been seen before. What an action DOES is the
handler's (U5 `actions.py` plus the journal); this module only decides whether
the call reaches it.

Identity. The tool sends `X-Caller-Agent` / `X-Run-Id` from the env the
executor set after the broker verified the run (`TOOL_CALLER_AGENT`,
`TOOL_RUN_ID`). There is no shared secret: a secret in the tool env would be
readable by any same-uid sibling tool. Instead every call is checked against
Postgres: the Run must exist, belong to the named agent, have trigger
`village` and still be active, and it must have a `village_runs` row whose
role permits the action. NetworkPolicy `ap-village-ingress` keeps everything
but tool-executor off this port.

Idempotency. Writes carry a model-supplied `request_id`; a retry with the same
id and the same arguments gets the first receipt, the same id with different
arguments is refused (judgment precedent). Reads take none.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import (ACTIVE_STATES, Run, VillageCommand, VillageConfig, VillageRun,
                              VillageSession)
from agentplatform.village import contract as c

log = logging.getLogger(__name__)

CALLER_HEADER = "X-Caller-Agent"
RUN_HEADER = "X-Run-Id"
TRIGGER = "village"
LIVE_ROW_STATES = ("launching", "running")       # village_runs rows the engine still serves
WRITE_SESSION_STATES = ("live", "closing")       # §10.1: writes need a live or closing session
MAX_BODY_BYTES = 64 * 1024


async def village_run(s, run_id: str | None) -> VillageRun | None:
    """The village_runs row for `run_id` in ANY state: a village run keeps its
    rails for its whole life, even after its session closed or its row was
    marked failed (dreams.active_dream, rapid_chat.active_turn). The API fence
    keys on this; never narrow it to "active row" or "live session"."""
    if not run_id:
        return None
    return (await s.execute(select(VillageRun).where(VillageRun.run_id == run_id))).scalars().first()


async def village_rules(s) -> dict[str, Any]:
    row = await s.get(VillageConfig, 1)
    return c.merged_rules(row.rules if row else None)


class Refused(Exception):
    """A call that does not reach the handler. `status` is the HTTP code."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass(frozen=True)
class Caller:
    """A verified caller. `persona` is the character acted on: always the one
    bound to the caller's village_runs row, never an argument."""
    run_id: str
    agent: str
    persona: str
    role: str
    slot: str | None
    session_id: str
    session_state: str


class Handler(Protocol):
    """What U5 fills. Both are called only for an authorized, schema-valid call."""

    async def read(self, caller: Caller, action: str, args: dict[str, Any]) -> dict[str, Any]:
        """help / look / wait / recall_notes. May block (wait ≤ 30 s)."""
        ...

    async def submit(self, caller: Caller, command: c.Command) -> c.Command:
        """Journal the write (commit before returning) and return it with
        `seq` and `state` set. The tick applies it later. Must let an
        IntegrityError on (run_id, request_id) propagate: the port turns the
        race into the first receipt."""
        ...


class NotReady:
    """Until the engine wires a real handler, every authorized call says so."""

    async def read(self, caller, action, args):
        raise Refused(503, "the village is not running yet")

    async def submit(self, caller, command):
        raise Refused(503, "the village is not running yet")


async def resolve_caller(s, agent: str, run_id: str, action: str) -> Caller:
    """§10.1 identity and role checks, all against Postgres. Every failure is
    a 403 with a reason in plain words."""
    if not agent or not run_id:
        raise Refused(403, "no verified caller")
    run = await s.get(Run, run_id)
    if run is None:
        raise Refused(403, "unknown run")
    if run.agent != agent:
        raise Refused(403, "the caller is not this run's agent")
    if run.trigger != TRIGGER:
        raise Refused(403, "not a village run")
    if run.state not in ACTIVE_STATES:
        raise Refused(403, "this run has finished")
    vrun = await village_run(s, run_id)
    if vrun is None:
        raise Refused(403, "this run is not in the village")
    if vrun.state not in LIVE_ROW_STATES:
        raise Refused(403, f"this village run is {vrun.state}")
    rules = await village_rules(s)
    expected_agent = rules["planner_agent"] if vrun.role == c.PLANNER else vrun.persona
    if vrun.role not in c.RUN_ROLES or run.agent != expected_agent:
        raise Refused(403, "the run's agent does not match its village role")
    roles = c.ACTION_ROLES.get(action)
    if roles is None:
        raise Refused(422, f"unknown action {action!r}; call help")
    if vrun.role not in roles:
        raise Refused(403, f"{action} is not for a {vrun.role} run")
    if action in c.FINAL_SLOT_ONLY and vrun.slot != "final":
        raise Refused(403, f"{action} is only for the final look-back")
    session = await s.get(VillageSession, vrun.session_id)
    if session is None:
        raise Refused(403, "this run's session is gone")
    if action in c.WRITE_ACTIONS and session.state not in WRITE_SESSION_STATES:
        raise Refused(403, f"the session is {session.state}; nothing can change now")
    return Caller(run_id=run.id, agent=agent, persona=vrun.persona, role=vrun.role,
                  slot=vrun.slot, session_id=vrun.session_id, session_state=session.state)


def receipt(row: VillageCommand) -> dict[str, Any]:
    """The answer to a write, rebuilt from its journal row so a retry gets
    exactly the first one."""
    return {"seq": row.seq, "state": row.state, "reason": row.reason,
            "request_id": row.request_id}


async def _prior(s, run_id: str, request_id: str) -> VillageCommand | None:
    return (await s.execute(select(VillageCommand).where(
        VillageCommand.run_id == run_id, VillageCommand.request_id == request_id))).scalars().first()


def _replay_or_refuse(prior: VillageCommand, digest: str) -> dict[str, Any]:
    if prior.args_hash != digest:
        raise Refused(409, "this request_id was already used with different arguments; "
                           "use a new request_id")
    return {**receipt(prior), "repeat": True}


class CommandPort:
    """The port's logic, independent of HTTP so tests drive it directly."""

    def __init__(self, session_factory, handler: Handler | None = None):
        self.sf = session_factory
        self.handler: Handler = handler or NotReady()

    async def call(self, agent: str, run_id: str, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise Refused(422, "body must be an object {action, args}")
        action, args = body.get("action"), body.get("args") or {}
        if not isinstance(action, str) or not isinstance(args, dict):
            raise Refused(422, "body must be an object {action, args}")
        async with self.sf() as s:
            caller = await resolve_caller(s, agent, run_id, action)
        errors = c.check(c.ACTION_ARGS[action], args, "args")
        if errors:
            raise Refused(422, "; ".join(errors[:10]))
        if action in c.READ_ACTIONS:
            return {"ok": True, "data": await self.handler.read(caller, action, args)}
        request_id = args["request_id"]
        digest = c.args_hash(args)
        async with self.sf() as s:
            prior = await _prior(s, run_id, request_id)
        if prior is not None:
            return {"ok": True, "data": _replay_or_refuse(prior, digest)}
        command = c.Command(session_id=caller.session_id, source=caller.role, action=action,
                            args={k: v for k, v in args.items() if k != "request_id"},
                            run_id=caller.run_id, persona=caller.persona,
                            request_id=request_id, args_hash=digest)
        try:
            saved = await self.handler.submit(caller, command)
        except IntegrityError:
            # A concurrent retry journaled first: answer with its receipt.
            async with self.sf() as s:
                prior = await _prior(s, run_id, request_id)
            if prior is None:
                raise
            return {"ok": True, "data": _replay_or_refuse(prior, digest)}
        return {"ok": True, "data": {"seq": saved.seq, "state": saved.state,
                                     "reason": saved.reason, "request_id": request_id}}


def build_app(port: CommandPort):
    """The HTTP face (`POST /cmd` on :8001). main.py (U2) serves it."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/cmd")
    async def cmd(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse({"ok": False, "reason": "request too large"}, status_code=413)
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            return JSONResponse({"ok": False, "reason": "body is not JSON"}, status_code=422)
        try:
            return await port.call(request.headers.get(CALLER_HEADER, ""),
                                   request.headers.get(RUN_HEADER, ""), body)
        except Refused as exc:
            if exc.status == 403:
                log.warning("village command refused: %s (agent=%s run=%s)", exc.reason,
                            request.headers.get(CALLER_HEADER, ""),
                            request.headers.get(RUN_HEADER, ""))
            return JSONResponse({"ok": False, "reason": exc.reason}, status_code=exc.status)

    return app
