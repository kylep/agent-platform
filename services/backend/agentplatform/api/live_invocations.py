"""Trusted Live View actions: explicit grant, short intent, durable receipt."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform import ticket_store
from agentplatform.api.auth import (
    INVOKE_ROLES,
    require_admin,
    role_allows,
    validate_session_cookie,
)
from agentplatform.api.live_views import TypedDefinition, _accessible_app, _reader
from agentplatform.db import (
    LiveIntent,
    LiveInvocation,
    LiveOperationGrant,
    LiveView,
    LiveViewVersion,
    RelayBinding,
    RelayParticipant,
    utcnow,
)
from agentplatform.relay import is_member, participant_of
from agentplatform.relay_store import (
    channel_by_ref,
    post_relay_message,
    publish_relay_message,
    relay_message_payload,
)
from agentplatform.ticket_store import TicketRuleError

router = APIRouter()
log = logging.getLogger(__name__)
OP_TICKET_CREATE = "tickets.create@1"
OP_RELAY_POST = "relay.channel.post@1"
INTENT_LIFETIME = timedelta(minutes=5)


def _interactive(request: Request, ident: tuple[str, str]) -> None:
    """Trusted page actions require the authenticated browser session."""
    if validate_session_cookie(request.app, request.cookies.get("ap_session")) != ident[0]:
        raise HTTPException(403, "browser session required")


class GrantIn(BaseModel):
    app_name: str
    principal_id: str = Field(min_length=1, max_length=128)
    operation: str


@router.get("/api/live-operation-grants")
async def list_live_operation_grants(request: Request, app_name: str,
                                     actor: str = Depends(require_admin)):
    """Show an App author who can use its trusted page actions."""
    async with request.app.state.session_factory() as session:
        await _accessible_app(session, app_name, (actor, "admin"))
        rows = (await session.execute(select(LiveOperationGrant).where(
            LiveOperationGrant.app_name == app_name).order_by(
                LiveOperationGrant.principal_id, LiveOperationGrant.operation))
        ).scalars().all()
        return [{"principal_id": row.principal_id, "operation": row.operation,
                 "enabled": row.revoked_at is None} for row in rows]


@router.post("/api/live-operation-grants")
async def grant_live_operation(request: Request, body: GrantIn,
                               actor: str = Depends(require_admin)):
    if body.operation not in (OP_TICKET_CREATE, OP_RELAY_POST):
        raise HTTPException(422, "unknown live operation")
    async with request.app.state.session_factory() as session:
        await _accessible_app(session, body.app_name, (actor, "admin"))
        pk = (body.app_name, body.principal_id, body.operation)
        row = await session.get(LiveOperationGrant, pk)
        if row is None:
            session.add(LiveOperationGrant(app_name=body.app_name,
                                           principal_id=body.principal_id,
                                           operation=body.operation,
                                           granted_by=actor))
        else:
            row.revoked_at = None
            row.granted_by = actor
            row.granted_at = utcnow()
        await session.commit()
    return {"app_name": body.app_name, "principal_id": body.principal_id,
            "operation": body.operation, "enabled": True}


@router.post("/api/live-operation-grants/revoke")
async def revoke_live_operation(request: Request, body: GrantIn,
                                actor: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        row = await session.get(LiveOperationGrant,
                                (body.app_name, body.principal_id, body.operation))
        if row is None:
            raise HTTPException(404, "unknown grant")
        row.revoked_at = utcnow()
        await session.commit()
    return {"enabled": False}


class TicketArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=4000)


class RelayPostArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=1000)

    @field_validator("body")
    @classmethod
    def no_mentions(cls, value: str) -> str:
        if not value.strip() or "@" in value:
            raise ValueError("a live-page post needs text without mentions")
        return value


class IntentIn(BaseModel):
    alias: str
    arguments: dict


class CallIn(BaseModel):
    intent_id: str = Field(min_length=32, max_length=32)
    idempotency_key: str = Field(min_length=16, max_length=64,
                                 pattern=r"^[a-zA-Z0-9_-]+$")


def _digest(arguments: dict) -> str:
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _published_action(session, view_id: str, alias: str,
                            ident: tuple[str, str]):
    view = await session.get(LiveView, view_id)
    if view is None or view.published_version is None:
        raise HTTPException(404, "unknown view")
    await _accessible_app(session, view.app_name, ident)
    version = await session.get(LiveViewVersion, (view_id, view.published_version))
    if version is None:
        raise HTTPException(503, "published version unavailable")
    try:
        definition = TypedDefinition.model_validate(version.definition)
    except ValidationError:
        raise HTTPException(503, "published definition incompatible")
    action = next((a for a in definition.actions if a.alias == alias), None)
    if action is None or action.operation not in (OP_TICKET_CREATE, OP_RELAY_POST):
        raise HTTPException(404, "unknown action")
    grant = await session.get(LiveOperationGrant,
                              (view.app_name, ident[0], action.operation))
    if grant is None or grant.revoked_at is not None:
        raise HTTPException(403, "operation grant required")
    if not role_allows(ident[1], INVOKE_ROLES):
        raise HTTPException(403, "action write role required")
    conv = await channel_by_ref(session, action.channel)
    if conv is None or conv.archived_at is not None:
        raise HTTPException(409, "action destination unavailable")
    if action.operation == OP_TICKET_CREATE:
        if conv.ticket_prefix is None:
            raise HTTPException(409, "ticket destination unavailable")
    else:
        await _internal_relay_target(session, conv, ident)
    return view, action, conv


async def _internal_relay_target(session, conv, ident: tuple[str, str]) -> None:
    """A Live App cannot turn a Relay post into an unreviewed external send."""
    if (conv.kind != "channel" or conv.home != "relay"
            or conv.connector != "web" or conv.external_ref):
        raise HTTPException(409, "internal Relay destination required")
    bridge = (await session.execute(select(RelayBinding.id).where(
        RelayBinding.channel_id == conv.id).limit(1))).scalar_one_or_none()
    if bridge is not None:
        raise HTTPException(409, "bridged Relay destination unavailable")
    explicit = set((await session.execute(select(RelayParticipant.participant).where(
        RelayParticipant.channel_id == conv.id))).scalars())
    if not is_member(conv, participant_of(principal=ident[0]), set(), explicit):
        raise HTTPException(403, "Relay membership required")


@router.post("/api/live-views/{view_id}/intents", status_code=201)
async def create_live_intent(request: Request, view_id: str, body: IntentIn,
                             ident: tuple[str, str] = Depends(_reader)):
    _interactive(request, ident)
    async with request.app.state.session_factory() as session:
        view, action, conv = await _published_action(session, view_id, body.alias, ident)
        argument_type = (TicketArguments if action.operation == OP_TICKET_CREATE
                         else RelayPostArguments)
        try:
            arguments = argument_type.model_validate(body.arguments).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, "invalid action arguments") from exc
        intent = LiveIntent(view_id=view_id, view_version=view.published_version,
                            principal_id=ident[0], alias=action.alias,
                            operation=action.operation, target=conv.id,
                            arguments=arguments, args_digest=_digest(arguments),
                            expires_at=utcnow() + INTENT_LIFETIME)
        session.add(intent)
        await session.commit()
        return {"intent_id": intent.id, "expires_at": intent.expires_at.isoformat(),
                "operation": action.operation, "target": f"#{conv.name}",
                "arguments": arguments}


def _receipt(row: LiveInvocation) -> dict:
    return {"id": row.id, "status": row.status, "result": row.result,
            "view_id": row.view_id, "operation": row.operation,
            "created_at": row.created_at.isoformat()}


@router.get("/api/live-actions/observation")
async def live_action_observation(request: Request, days: int = 7,
                                  actor: str = Depends(require_admin)):
    """Bounded receipt evidence for a canary window; no action arguments."""
    if not 1 <= days <= 30:
        raise HTTPException(422, "days must be between 1 and 30")
    since = utcnow() - timedelta(days=days)
    async with request.app.state.session_factory() as session:
        counts = (await session.execute(select(
            LiveInvocation.status, func.count(LiveInvocation.id)).where(
                LiveInvocation.created_at >= since).group_by(LiveInvocation.status)
        )).all()
        unresolved = (await session.execute(select(
            LiveInvocation.id, LiveInvocation.view_id, LiveInvocation.created_at).where(
                LiveInvocation.created_at >= since,
                LiveInvocation.status == "outcome_unknown").order_by(
                    LiveInvocation.created_at.desc()).limit(10)
        )).all()
        revoked = (await session.execute(select(func.count()).select_from(
            LiveOperationGrant).where(LiveOperationGrant.revoked_at.is_not(None))
        )).scalar_one()
    return {"since": since.isoformat(), "days": days,
            "invocations_by_status": {status: count for status, count in counts},
            "unresolved": [{"id": row.id, "view_id": row.view_id,
                            "created_at": row.created_at.isoformat()} for row in unresolved],
            "revoked_grants_current": revoked}


@router.get("/api/live-invocations/{invocation_id}")
async def get_live_invocation(request: Request, invocation_id: str,
                              ident: tuple[str, str] = Depends(_reader)):
    async with request.app.state.session_factory() as session:
        row = await session.get(LiveInvocation, invocation_id)
        if row is None or (row.principal_id != ident[0] and ident[1] != "admin"):
            raise HTTPException(404, "unknown invocation")
        updated = row.updated_at.replace(tzinfo=row.updated_at.tzinfo or timezone.utc)
        if row.status == "dispatched" and utcnow() - updated > timedelta(minutes=2):
            row.status = "outcome_unknown"
            row.updated_at = utcnow()
            await session.commit()
        return _receipt(row)


@router.post("/api/live-views/{view_id}/calls")
async def call_live_action(request: Request, view_id: str, body: CallIn,
                           ident: tuple[str, str] = Depends(_reader)):
    _interactive(request, ident)
    sf = request.app.state.session_factory
    # Reserve the intent once. A concurrent replay collides on the unique
    # intent/key constraints and returns its existing receipt, never dispatching.
    async with sf() as session:
        intent = (await session.execute(select(LiveIntent).where(
            LiveIntent.id == body.intent_id).with_for_update())).scalar_one_or_none()
        if intent is None or intent.view_id != view_id or intent.principal_id != ident[0]:
            raise HTTPException(404, "unknown intent")
        previous = (await session.execute(select(LiveInvocation).where(
            LiveInvocation.intent_id == intent.id))).scalar_one_or_none()
        if previous is not None:
            if previous.idempotency_key != body.idempotency_key:
                raise HTTPException(409, "intent already used")
            return _receipt(previous)
        expires = intent.expires_at.replace(tzinfo=intent.expires_at.tzinfo or timezone.utc)
        if intent.consumed_at is not None or expires <= utcnow():
            raise HTTPException(409, "intent expired or used")
        intent_id = intent.id
        collision = (await session.execute(select(LiveInvocation).where(
            LiveInvocation.principal_id == ident[0],
            LiveInvocation.idempotency_key == body.idempotency_key))).scalar_one_or_none()
        if collision is not None:
            raise HTTPException(409, "idempotency key already used")
        row = LiveInvocation(intent_id=intent.id, view_id=view_id,
                             view_version=intent.view_version,
                             principal_id=ident[0], alias=intent.alias,
                             operation=intent.operation, target=intent.target,
                             args_digest=intent.args_digest,
                             idempotency_key=body.idempotency_key)
        session.add(row)
        intent.consumed_at = utcnow()
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            existing = (await session.execute(select(LiveInvocation).where(
                LiveInvocation.intent_id == intent_id))).scalar_one_or_none()
            if existing is not None and existing.idempotency_key == body.idempotency_key:
                return _receipt(existing)
            raise HTTPException(409, "idempotency conflict")
        invocation_id = row.id

    # Current permissions and published target are checked again at dispatch.
    async with sf() as session:
        row = (await session.execute(select(LiveInvocation).where(
            LiveInvocation.id == invocation_id).with_for_update())).scalar_one()
        intent = await session.get(LiveIntent, row.intent_id)
        try:
            view, action, conv = await _published_action(session, view_id, row.alias, ident)
            if (view.published_version != row.view_version or
                    action.channel != conv.name or conv.id != row.target or
                    _digest(intent.arguments) != row.args_digest):
                raise HTTPException(409, "action changed before dispatch")
        except HTTPException as exc:
            row.status = "denied_at_dispatch"
            row.result = {"reason": exc.detail}
            row.updated_at = utcnow()
            await session.commit()
            return _receipt(row)
        row.status = "dispatched"
        row.updated_at = utcnow()
        target_id = intent.target
        arguments = dict(intent.arguments)
        operation = row.operation
        await session.commit()

    try:
        async with sf() as session:
            conv = await channel_by_ref(session, target_id)
            if conv is None or conv.archived_at is not None:
                raise TicketRuleError("action destination unavailable")
            if operation == OP_TICKET_CREATE:
                if conv.ticket_prefix is None:
                    raise TicketRuleError("ticket destination unavailable")
                ticket = await ticket_store.create_ticket(
                    session, request.app.state.producer, conv,
                    actor=participant_of(principal=ident[0]),
                    title=arguments["title"], body=arguments["body"],
                    assignee=None, notify=False)
                result = {"ticket_key": ticket.key}
            else:
                await _internal_relay_target(session, conv, ident)
                message = await post_relay_message(
                    session, conv, author=participant_of(principal=ident[0]),
                    body=arguments["body"], mentions=[])
                await session.commit()
                request.app.state.feed.publish(conv.id, "message",
                                               relay_message_payload(message, conv))
                await publish_relay_message(request.app.state.producer, conv, message)
                result = {"message_id": message.id}
    except (TicketRuleError, HTTPException) as exc:
        status, result = "failed", {"reason": exc.detail if isinstance(exc, HTTPException)
                                    else str(exc)}
    except Exception:
        # Either store may have committed before the response failed. Never
        # silently retry a potentially successful effect.
        log.exception("live invocation %s has an uncertain outcome", invocation_id)
        status, result = "outcome_unknown", {"reason": "action outcome needs review"}
    else:
        status = "succeeded"
    async with sf() as session:
        row = await session.get(LiveInvocation, invocation_id)
        row.status, row.result, row.updated_at = status, result, utcnow()
        await session.commit()
        return _receipt(row)
