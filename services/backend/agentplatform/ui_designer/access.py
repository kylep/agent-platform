"""Document ACLs, independent of global tool/role gates and restorable scenes."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import exists, or_, select, true

from agentplatform.db import (RelayParticipant, Team, TeamAgent, UiDesignerAccess,
                              UiDesignerAccessEntry, UiDesignerDocument)


@dataclass(frozen=True)
class AccessSubject:
    participant: str
    is_admin: bool = False
    team_ids: frozenset[str] = frozenset()

    @property
    def principals(self):
        return (self.participant, *(f"team:{identifier}" for identifier in self.team_ids))


def owner_principal(owner: str) -> str:
    return owner if owner.startswith(("user:", "agent:")) else "user:" + owner


async def subject(request, caller=None) -> AccessSubject:
    from agentplatform.api.auth import authenticate
    role = getattr(request.state, 'resolved_role', None)
    if caller is None or role is None:
        identity = await authenticate(request)
        if identity is None:
            raise HTTPException(401)
        name, role = identity
        agent = getattr(request.state, 'api_key_agent', None)
        from agentplatform.relay import participant_of
        participant = participant_of(agent=agent) if agent else participant_of(principal=name)
    else:
        participant = caller.participant
        agent = caller.agent
    async with request.app.state.session_factory() as session:
        return await subject_for(session, participant, is_admin=role == 'admin' and agent is None)


async def subject_for(session, participant: str, *, is_admin: bool = False) -> AccessSubject:
    if participant.startswith('agent:'):
        teams = select(Team.id).join(TeamAgent, TeamAgent.team_id == Team.id).where(
            TeamAgent.agent == participant[6:], Team.archived_at.is_(None))
    else:
        teams = select(Team.id).join(RelayParticipant,
            RelayParticipant.channel_id == Team.relay_channel_id).where(
            RelayParticipant.participant == participant, Team.archived_at.is_(None))
    return AccessSubject(participant, is_admin, frozenset((await session.execute(teams)).scalars()))


def _owner_clause(who: AccessSubject, document_model):
    names = [who.participant]
    if who.participant.startswith('user:'):
        legacy = who.participant[5:]
        if not legacy.startswith(('user:', 'agent:')):
            names.append(legacy)
    return document_model.owner.in_(names)


def readable_clause(who: AccessSubject, document_model=UiDesignerDocument):
    if who.is_admin:
        return true()
    policy = exists(select(UiDesignerAccess.document_id).where(
        UiDesignerAccess.document_id == document_model.id))
    workspace = exists(select(UiDesignerAccess.document_id).where(
        UiDesignerAccess.document_id == document_model.id, UiDesignerAccess.visibility == 'workspace'))
    member = exists(select(UiDesignerAccessEntry.document_id).where(
        UiDesignerAccessEntry.document_id == document_model.id,
        UiDesignerAccessEntry.principal.in_(who.principals)))
    return or_(_owner_clause(who, document_model), ~policy, workspace, member)


def writable_clause(who: AccessSubject, document_model=UiDesignerDocument):
    if who.is_admin:
        return true()
    policy = exists(select(UiDesignerAccess.document_id).where(
        UiDesignerAccess.document_id == document_model.id))
    workspace = exists(select(UiDesignerAccess.document_id).where(
        UiDesignerAccess.document_id == document_model.id, UiDesignerAccess.visibility == 'workspace'))
    editor = exists(select(UiDesignerAccessEntry.document_id).where(
        UiDesignerAccessEntry.document_id == document_model.id,
        UiDesignerAccessEntry.principal.in_(who.principals), UiDesignerAccessEntry.role == 'editor'))
    return or_(_owner_clause(who, document_model), ~policy, workspace, editor)


async def _require(session, identifier: str, who: AccessSubject, *, write=False, allow_deleted_owner=False):
    clause = writable_clause(who) if write else readable_clause(who)
    if allow_deleted_owner:
        deletion = or_(UiDesignerDocument.deleted_at.is_(None), _owner_clause(who, UiDesignerDocument),
                       true() if who.is_admin else False)
    else:
        deletion = UiDesignerDocument.deleted_at.is_(None)
    row = (await session.execute(select(UiDesignerDocument).where(
        UiDesignerDocument.id == identifier, deletion, clause))).scalars().first()
    if row is None:
        raise HTTPException(404, 'design document is unavailable')
    return row


async def require_read_by_uuid(session, identifier: str, who: AccessSubject, *, allow_deleted_owner=True):
    return await _require(session, identifier, who, allow_deleted_owner=allow_deleted_owner)


async def require_write_by_uuid(session, identifier: str, who: AccessSubject):
    return await _require(session, identifier, who, write=True)


async def capabilities(session, row, who: AccessSubject) -> dict:
    policy = await session.get(UiDesignerAccess, row.id)
    readers = list(policy.readers) if policy else []
    editors = list(policy.editors) if policy else []
    owner = owner_principal(row.owner)
    manage = who.is_admin or who.participant == owner
    workspace = policy is None or policy.visibility == 'workspace'
    return {'document_id': row.id, 'owner': owner,
            'visibility': policy.visibility if policy else 'workspace', 'readers': readers, 'editors': editors,
            'access_version': policy.access_version if policy else 0,
            'can_read': manage or workspace or bool(set(who.principals) & set(readers + editors)),
            'can_write': manage or workspace or bool(set(who.principals) & set(editors)),
            'can_manage_sharing': manage}


async def is_workspace(session, document_id: str) -> bool:
    row = await session.get(UiDesignerAccess, document_id)
    return row is None or row.visibility == 'workspace'
