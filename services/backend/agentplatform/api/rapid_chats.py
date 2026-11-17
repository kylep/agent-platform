"""Rapid-chat REST surface (docs/design/45 §7.1). Admin only: an agent key gets
403, as for watchers. The controller in the dispatcher does the work; these
routes create the row, read it, and request a stop."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from agentplatform.ui_designer.errors import UiDesignerError
from agentplatform import rapid_chat
from agentplatform import wiki_store
from agentplatform.api import schemas as S
from agentplatform.api.auth import require_admin
from agentplatform.db import AgentDef, RapidChat, RapidChatTurn, utcnow
from agentplatform.scheduler import as_utc

router = APIRouter(tags=["rapid-chats"], dependencies=[Depends(require_admin)])


def _iso(ts):
    return as_utc(ts).isoformat() if ts else None


def _view(chat: RapidChat, counts: dict[str, int] | None = None) -> dict:
    return {
        "id": chat.id, "title": chat.title, "goal": chat.goal, "created_by": chat.created_by,
        "initiated_by": chat.initiated_by, "created_at": _iso(chat.created_at),
        "participants": chat.participants, "sink": chat.sink, "areas": chat.areas,
        "integrated_pass": chat.integrated_pass, "duration_minutes": chat.duration_minutes,
        "max_turns": chat.max_turns, "turn_timeout_seconds": chat.turn_timeout_seconds,
        "limits": chat.limits, "state": chat.state, "end_reason": chat.end_reason,
        "started_at": _iso(chat.started_at), "ended_at": _iso(chat.ended_at),
        "turns_total": chat.turns_total, "last_quota": chat.last_quota,
        "output_ref": chat.output_ref, "summary": chat.summary, "last_error": chat.last_error,
        "turn_counts": counts or {},
    }


def _turn_view(t: RapidChatTurn) -> dict:
    return {"id": t.id, "chat_id": t.chat_id, "seq": t.seq, "agent": t.agent,
            "area_key": t.area_key, "pass_no": t.pass_no, "role": t.role, "run_id": t.run_id,
            "state": t.state, "outcome": t.outcome, "prompt": t.prompt, "result": t.result,
            "parsed": t.parsed, "started_at": _iso(t.started_at),
            "finished_at": _iso(t.finished_at), "reason": t.reason}


async def _counts(s, chat_id: str) -> dict[str, int]:
    rows = (await s.execute(select(RapidChatTurn.agent, func.count()).where(
        RapidChatTurn.chat_id == chat_id, RapidChatTurn.run_id.is_not(None)
    ).group_by(RapidChatTurn.agent))).all()
    return {agent: n for agent, n in rows}


async def _get(s, chat_id: str) -> RapidChat:
    chat = await s.get(RapidChat, chat_id)
    if chat is None:
        raise HTTPException(404, "no such rapid-chat")
    return chat


@router.post("/api/rapid-chats", status_code=201, response_model=S.RapidChatView)
async def create_rapid_chat(request: Request, body: S.RapidChatIn,
                            caller: str = Depends(require_admin)):
    st = request.app.state
    if body.sink.kind not in rapid_chat.IMPLEMENTED_SINKS:
        raise HTTPException(422, f"sink kind {body.sink.kind!r} is not implemented")
    if len(set(body.participants)) != len(body.participants):
        raise HTTPException(422, "participants must be distinct")
    try:
        wiki_store._check_slug(body.sink.slug)
    except wiki_store.WikiRuleError as exc:
        raise HTTPException(422, str(exc)) from None
    areas = rapid_chat.make_areas([a if isinstance(a, str) else a.model_dump() for a in body.areas])
    async with st.session_factory() as s:
        for name in body.participants:
            row = await s.get(AgentDef, name)
            if row is None or not row.enabled or row.system_source:
                raise HTTPException(422, f"participant {name!r} is missing, disabled, or code-owned")
        busy = (await s.execute(select(RapidChat.id).where(
            RapidChat.state.in_(("running", "stopping"))).limit(1))).scalar_one_or_none()
        if busy:
            raise HTTPException(409, f"rapid-chat {busy} is still running")
        chat = RapidChat(
            id=uuid.uuid4().hex, title=body.title, goal=body.goal, created_by=caller,
            initiated_by=caller, participants=list(body.participants),
            sink=body.sink.model_dump(), areas=areas, integrated_pass=-1,
            duration_minutes=body.duration_minutes, max_turns=body.max_turns,
            turn_timeout_seconds=body.turn_timeout_seconds, limits=body.limits.model_dump(),
            state="running", started_at=utcnow(), turns_total=0)
        try:
            await rapid_chat.init_sink(st.session_factory, st.producer, st.settings, chat)
        except wiki_store.WikiRuleError as exc:
            raise HTTPException(422, str(exc)) from None
        except UiDesignerError as exc:
            from agentplatform.api.ui_designer import ud_http
            raise ud_http(exc) from None
        s.add(chat)
        await s.commit()
        return _view(chat)


@router.get("/api/rapid-chats", response_model=list[S.RapidChatView])
async def list_rapid_chats(request: Request, limit: int = Query(50, ge=1, le=200)):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(RapidChat).order_by(
            RapidChat.created_at.desc()).limit(limit))).scalars().all()
        return [_view(c) for c in rows]


@router.get("/api/rapid-chats/{chat_id}", response_model=S.RapidChatView)
async def get_rapid_chat(request: Request, chat_id: str):
    async with request.app.state.session_factory() as s:
        chat = await _get(s, chat_id)
        return _view(chat, await _counts(s, chat_id))


@router.get("/api/rapid-chats/{chat_id}/turns", response_model=list[S.RapidChatTurnView])
async def list_rapid_chat_turns(request: Request, chat_id: str,
                                limit: int = Query(100, ge=1, le=500),
                                offset: int = Query(0, ge=0)):
    async with request.app.state.session_factory() as s:
        await _get(s, chat_id)
        rows = (await s.execute(select(RapidChatTurn).where(
            RapidChatTurn.chat_id == chat_id).order_by(RapidChatTurn.seq)
            .limit(limit).offset(offset))).scalars().all()
        return [_turn_view(t) for t in rows]


@router.post("/api/rapid-chats/{chat_id}/stop", response_model=S.RapidChatView)
async def stop_rapid_chat(request: Request, chat_id: str):
    """Request a stop; idempotent. The controller cancels in-flight turns,
    writes the sink and sets done/stopped."""
    async with request.app.state.session_factory() as s:
        chat = await _get(s, chat_id)
        if chat.state == "running":
            chat.state = "stopping"
            await s.commit()
        return _view(chat, await _counts(s, chat_id))
