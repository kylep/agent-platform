"""The village spectator API (GEN-11, plan 2026-11-27-village-game §9, §10.5, §11).

Read-only over the engine's `village_*` tables. The engine is the single writer
of world state, so nothing here writes a world table; the admin controls
(start a test session, stop) go through the engine's command interface, which
is stubbed behind `app.state.village_command_sender` until it lands.

Every serializer is an allowlist. `village_notes`, `village_knowledge`,
planner briefs, `wait` deltas and "how I play" sections are never selected,
and snapshot state is filtered by key allowlist plus a recursive private-name
deny list, so a field the engine adds later cannot leak by accident.

The live stream follows the durable `village_events` rows (Postgres is the
source of truth, plan §11) and re-emits the latest snapshot's positions as
`frame` events. The 2 Hz `village.frames` Kafka topic is not consumed yet:
the topic specs belong to the engine unit, and a late joiner needs the pg
snapshot first either way."""
import asyncio
import importlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api import relay as relay_api
from agentplatform.api.auth import require_role, still_signed_in, stream_cookie
from agentplatform.db import (VillageConfig, VillageEvent, VillagePlannerCycle,
                              VillageRun, VillageSession, VillageSnapshot, utcnow)
from agentplatform.village import contract

router = APIRouter(tags=["village"])

ADMIN = require_role()  # no roles listed: admin only (the console's Gate)

EVENT_PAGE = 200
STREAM_POLL_S = 1.0
FRAME_EVERY_S = 2.0

# Snapshot state keys a spectator may see, and names that are private wherever
# they appear in the tree.
SNAPSHOT_KEYS = ("tick", "time", "map", "characters", "residents", "objects", "projects",
                 "sources", "piles", "weather", "pond", "path_wear", "places")
PRIVATE_KEYS = frozenset({
    "notes", "note", "knowledge", "brief", "persona_brief", "wait_delta", "delta",
    "section", "section_before", "section_after", "how_i_play", "play_section",
    "prompt", "private", "memory", "mark_sentence",
})
MAX_PUBLIC_STEPS = 5


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in PRIVATE_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    out = {k: _scrub(state[k]) for k in SNAPSHOT_KEYS if k in state}
    chars = out.get("characters")
    items = chars.values() if isinstance(chars, dict) else (chars or [])
    for ch in items:
        if isinstance(ch, dict):
            for key in ("steps", "queue"):
                if isinstance(ch.get(key), list):
                    ch[key] = ch[key][:MAX_PUBLIC_STEPS]
    return out


def session_out(row: VillageSession) -> dict[str, Any]:
    return {"id": row.id, "day": row.day, "kind": row.kind, "state": row.state,
            "length_min": row.length_min, "planned_start": _iso(row.planned_start),
            "planned_end": _iso(row.planned_end), "started_at": _iso(row.started_at),
            "ended_at": _iso(row.ended_at), "end_reason": row.end_reason,
            "quota_at_start": row.quota_at_start, "quota_reason": row.quota_reason}


def event_out(row: VillageEvent) -> dict[str, Any]:
    return {"seq": row.seq, "session_id": row.session_id, "tick": row.tick,
            "kind": row.kind, "actor": row.actor, "subjects": row.subjects or [],
            "public_text": row.public_text, "data": _scrub(row.data or {}),
            "superseded": bool(row.superseded), "at": _iso(row.created_at)}


def run_out(row: VillageRun) -> dict[str, Any]:
    return {"id": row.id, "session_id": row.session_id, "persona": row.persona,
            "role": row.role, "run_id": row.run_id, "slot": row.slot,
            "attempt": row.attempt, "state": row.state, "reason": row.reason,
            "launched_at": _iso(row.launched_at), "finished_at": _iso(row.finished_at)}


def cycle_out(row: VillagePlannerCycle) -> dict[str, Any]:
    """Metadata only (§10.5): never the wait delta or the ops themselves."""
    return {"persona": row.persona, "run_id": row.run_id, "wake_reason": row.wake_reason,
            "think_ms": row.think_ms, "ops_applied": row.ops_applied,
            "ops_refused": len(row.ops_refused or []),
            "wait_returned_at": _iso(row.wait_returned_at), "edit_at": _iso(row.edit_at)}


async def _current_session(s, session_id: str | None = None) -> VillageSession | None:
    if session_id:
        return await s.get(VillageSession, session_id)
    live = (await s.execute(select(VillageSession).where(
        VillageSession.state.in_(("live", "closing")))
        .order_by(VillageSession.created_at.desc()).limit(1))).scalar_one_or_none()
    if live is not None:
        return live
    return (await s.execute(select(VillageSession)
                            .order_by(VillageSession.created_at.desc()).limit(1))).scalar_one_or_none()


async def _latest_snapshot(s, session_id: str) -> VillageSnapshot | None:
    return (await s.execute(select(VillageSnapshot)
                            .where(VillageSnapshot.session_id == session_id)
                            .order_by(VillageSnapshot.tick.desc(),
                                      VillageSnapshot.created_at.desc()).limit(1))).scalar_one_or_none()


async def _last_seq(s, session_id: str) -> int:
    row = (await s.execute(select(VillageEvent.seq).where(VillageEvent.session_id == session_id)
                           .order_by(VillageEvent.seq.desc()).limit(1))).scalar_one_or_none()
    return int(row or 0)


async def _rules(s) -> dict[str, Any]:
    row = await s.get(VillageConfig, 1)
    return contract.merged_rules(row.rules if row else None)


def _snapshot_out(snap: VillageSnapshot | None) -> dict[str, Any] | None:
    if snap is None:
        return None
    return {"tick": snap.tick, "kind": snap.kind, "state_hash": snap.state_hash,
            "last_applied_seq": snap.last_applied_seq, "at": _iso(snap.created_at),
            "state": _public_state(snap.state or {})}


@router.get("/api/village/state")
async def get_state(request: Request, session_id: str | None = None,
                    caller: str = Depends(ADMIN)):
    """What a late joiner needs first: the session, the latest snapshot, the
    newest event seq (the SSE cursor) and the public rules."""
    async with request.app.state.session_factory() as s:
        sess = await _current_session(s, session_id)
        rules = await _rules(s)
        public_rules = {k: rules[k] for k in ("spectator_queue_visibility", "players",
                                              "session_start_local", "session_tz")}
        if sess is None:
            return {"session": None, "snapshot": None, "last_seq": 0, "rules": public_rules,
                    "now": _iso(utcnow())}
        return {"session": session_out(sess),
                "snapshot": _snapshot_out(await _latest_snapshot(s, sess.id)),
                "last_seq": await _last_seq(s, sess.id), "rules": public_rules,
                "now": _iso(utcnow())}


@router.get("/api/village/events")
async def list_events(request: Request, session_id: str | None = None, after: int = 0,
                      limit: int = 100, caller: str = Depends(ADMIN)):
    limit = max(1, min(limit, EVENT_PAGE))
    async with request.app.state.session_factory() as s:
        sess = await _current_session(s, session_id)
        if sess is None:
            return {"events": [], "last_seq": after}
        rows = (await s.execute(select(VillageEvent)
                                .where(VillageEvent.session_id == sess.id,
                                       VillageEvent.seq > after)
                                .order_by(VillageEvent.seq).limit(limit))).scalars().all()
    events = [event_out(r) for r in rows]
    return {"session_id": sess.id, "events": events,
            "last_seq": events[-1]["seq"] if events else after}


@router.get("/api/village/sessions")
async def list_sessions(request: Request, limit: int = 30, caller: str = Depends(ADMIN)):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(VillageSession)
                                .order_by(VillageSession.created_at.desc())
                                .limit(max(1, min(limit, 100))))).scalars().all()
    return {"sessions": [session_out(r) for r in rows]}


@router.get("/api/village/admin")
async def admin_strip(request: Request, caller: str = Depends(ADMIN)):
    """Session state, planner/reflection runs and per-persona think stats."""
    async with request.app.state.session_factory() as s:
        sess = await _current_session(s)
        if sess is None:
            return {"session": None, "runs": [], "personas": {}, "recent_cycles": []}
        runs = (await s.execute(select(VillageRun).where(VillageRun.session_id == sess.id)
                                .order_by(VillageRun.launched_at))).scalars().all()
        cycles = (await s.execute(select(VillagePlannerCycle)
                                  .where(VillagePlannerCycle.session_id == sess.id)
                                  .order_by(VillagePlannerCycle.wait_returned_at))).scalars().all()
    personas: dict[str, dict[str, Any]] = {}
    for c in cycles:
        p = personas.setdefault(c.persona, {"think_ms": [], "last_wait": None, "cycles": 0})
        p["cycles"] += 1
        if c.think_ms is not None:
            p["think_ms"].append(c.think_ms)
        p["last_wait"] = _iso(c.wait_returned_at)
    for p in personas.values():
        ms = sorted(p.pop("think_ms"))
        p["think_ms_p50"] = ms[len(ms) // 2] if ms else None
    return {"session": session_out(sess), "runs": [run_out(r) for r in runs],
            "personas": personas, "recent_cycles": [cycle_out(c) for c in cycles[-20:]]}


class ConfigBody(BaseModel):
    rules: dict[str, Any]


@router.get("/api/village/config")
async def get_config(request: Request, caller: str = Depends(ADMIN)):
    async with request.app.state.session_factory() as s:
        return {"rules": await _rules(s),
                "choices": {k: list(v) for k, v in contract.RULE_CHOICES.items()}}


@router.put("/api/village/config")
async def put_config(body: ConfigBody, request: Request, caller: str = Depends(ADMIN)):
    """Merge keys into the config row. Unknown keys and values outside a
    rule's closed choices are refused."""
    for key, value in body.rules.items():
        if key not in contract.DEFAULT_RULES:
            raise HTTPException(422, f"unknown rule {key!r}")
        choices = contract.RULE_CHOICES.get(key)
        if choices is not None and value not in choices:
            raise HTTPException(422, f"{key} must be one of {list(choices)}")
    async with request.app.state.session_factory() as s:
        row = await s.get(VillageConfig, 1)
        if row is None:
            row = VillageConfig(id=1, rules={})
            s.add(row)
        row.rules = {**(row.rules or {}), **body.rules}
        row.updated_by = caller
        await s.commit()
        return {"rules": contract.merged_rules(row.rules)}


async def _send_admin_command(request: Request, action: str, args: dict[str, Any]) -> dict[str, Any]:
    """STUB: the engine's command interface (U2/U4) is not merged yet. A sender
    is any async callable on `app.state.village_command_sender`; until one is
    installed the controls answer 503 rather than write a world table."""
    sender = getattr(request.app.state, "village_command_sender", None)
    if sender is None:
        raise HTTPException(503, "village engine command interface is not available")
    return await sender(action, args)


@router.post("/api/village/sessions/test")
async def start_test_session(request: Request, caller: str = Depends(ADMIN)):
    return await _send_admin_command(request, "start_test", {"by": caller})


@router.post("/api/village/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request, caller: str = Depends(ADMIN)):
    return await _send_admin_command(request, "stop", {"session_id": session_id, "by": caller})


@router.get("/api/village/glossary")
async def get_glossary(caller: str = Depends(ADMIN)):
    """Served from `village/glossary.py` (U5) once it exists."""
    try:
        mod = importlib.import_module("agentplatform.village.glossary")
    except ImportError:
        return {"terms": {}}
    return {"terms": dict(getattr(mod, "GLOSSARY", {}))}


def _positions(state: dict[str, Any]) -> list[dict[str, Any]]:
    chars = state.get("characters") or []
    items = ([{"id": k, **v} for k, v in chars.items() if isinstance(v, dict)]
             if isinstance(chars, dict) else [c for c in chars if isinstance(c, dict)])
    keep = ("id", "x", "y", "target", "speed", "now", "path")
    return [{k: c[k] for k in keep if k in c} for c in items]


@router.get("/api/village/stream", response_class=StreamingResponse)
async def stream(request: Request, session_id: str | None = None, after: int = 0,
                 caller: str = Depends(ADMIN)):
    """SSE: `event` frames (public narrative rows past the cursor), `frame`
    frames (positions from the latest snapshot), `session` frames on a state
    change, a heartbeat in between."""
    sf = request.app.state.session_factory
    cookie = stream_cookie(request)

    async def gen():
        cursor, last_tick, last_state, since_frame = after, None, None, FRAME_EVERY_S
        while True:
            if cookie and not await still_signed_in(request.app, cookie):
                return
            async with sf() as s:
                sess = await _current_session(s, session_id)
                rows, snap = [], None
                if sess is not None:
                    rows = (await s.execute(select(VillageEvent)
                                            .where(VillageEvent.session_id == sess.id,
                                                   VillageEvent.seq > cursor)
                                            .order_by(VillageEvent.seq)
                                            .limit(EVENT_PAGE))).scalars().all()
                    if since_frame >= FRAME_EVERY_S:
                        snap = await _latest_snapshot(s, sess.id)
                        since_frame = 0.0
            sent = False
            if sess is not None and sess.state != last_state:
                last_state = sess.state
                yield relay_api._frame("session", session_out(sess))
                sent = True
            for r in rows:
                cursor = r.seq
                yield relay_api._frame("event", event_out(r))
                sent = True
            if snap is not None and snap.tick != last_tick:
                last_tick = snap.tick
                yield relay_api._frame("frame", {
                    "tick": snap.tick,
                    "characters": _positions(_public_state(snap.state or {}))})
                sent = True
            if not sent:
                yield ": heartbeat\n\n"
            await asyncio.sleep(STREAM_POLL_S)
            since_frame += STREAM_POLL_S

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
