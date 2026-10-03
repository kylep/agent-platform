"""Human accounts (docs/design/40): self-registration, the caller's own
profile and password, and the admin's people, group and registration routes.

State users are `principals` rows named `user:<username>` with role `user`.
Accounts are addressed by principal id. Everything that manages people answers
the admin's BROWSER SESSION only — an admin API key, the MCP facade and agents
can't — and every row lock and session revocation goes through the helpers in
`auth`, so a reset or delete can never race a login into a live session."""
import asyncio
import re

from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from agentplatform.api import schemas as S
from agentplatform.api.auth import (SYSTEM_PRINCIPALS, USER_PREFIX, USER_ROLE, USERNAME_RE,
                                    authenticate, lock_principal, ph, require_admin_session,
                                    require_browser_session, revoke_sessions, start_session)
from agentplatform.db import LoginSession, PlatformSetting, Principal, UserGroup, utcnow

router = APIRouter()

MAX_PASSWORD = 1024
MAX_GROUP_NAME = 64
REGISTRATION_KEY = "registration_open"


async def require_session(request: Request) -> None:
    """Any signed-in browser session, the no-access `user` tier included
    (/api/me* is in USER_PATHS, so the fence in `authenticate` lets it by)."""
    if await authenticate(request) is None:
        raise HTTPException(401)
    require_browser_session(request)


def _is_state(p: Principal) -> bool:
    return p.role == USER_ROLE and p.name.startswith(USER_PREFIX)


def _kind(p: Principal) -> str:
    return "state" if _is_state(p) else "system"


def _username(p: Principal) -> str:
    return p.name[len(USER_PREFIX):] if _is_state(p) else p.name


def _check_new_password(pw: str, confirm: str) -> None:
    if not pw or len(pw) > MAX_PASSWORD:
        raise HTTPException(422, f"password must be 1 to {MAX_PASSWORD} characters")
    if pw != confirm:
        raise HTTPException(422, "passwords do not match")


async def _registration_open(s) -> bool:
    row = await s.get(PlatformSetting, REGISTRATION_KEY)
    return True if row is None else bool(row.value)


async def _group_refs(s, principals: list[Principal]) -> dict[str, S.GroupRef | None]:
    """principal id -> its group. A group id that resolves to nothing reads as
    none (the postgres FK already nulls it; sqlite or a race may leave one)."""
    wanted = {p.group_id for p in principals if p.group_id}
    names = {}
    if wanted:
        names = {g.id: g.name for g in (await s.execute(
            select(UserGroup).where(UserGroup.id.in_(wanted)))).scalars()}
    return {p.id: (S.GroupRef(id=p.group_id, name=names[p.group_id])
                   if p.group_id in names else None) for p in principals}


async def _user_view(s, p: Principal) -> dict:
    return {"id": p.id, "username": _username(p), "kind": _kind(p),
            "group": (await _group_refs(s, [p]))[p.id], "created_at": p.created_at}


# --- register ----------------------------------------------------------------

class RegisterIn(BaseModel):
    username: str
    password: str
    confirm: str


@router.get("/api/register", response_model=S.RegistrationState)
async def registration_state(request: Request):
    async with request.app.state.session_factory() as s:
        return {"open": await _registration_open(s)}


@router.post("/api/register", response_model=S.Ok)
async def register(request: Request, response: Response, body: RegisterIn):
    async with request.app.state.session_factory() as s:
        if not await _registration_open(s):
            raise HTTPException(403, "registration is closed")
    username = body.username.strip().lower()
    if not re.fullmatch(USERNAME_RE, username):
        raise HTTPException(422, "username must be lowercase letters, digits, - or _, "
                                 "starting with a letter or digit, up to 64 characters")
    _check_new_password(body.password, body.confirm)
    if username in SYSTEM_PRINCIPALS:
        raise HTTPException(409, "that username is reserved")
    hashed = await asyncio.to_thread(ph.hash, body.password)
    p = Principal(name=USER_PREFIX + username, role=USER_ROLE, password_hash=hashed)
    async with request.app.state.session_factory() as s:
        s.add(p)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, "that username is taken")
    if not await start_session(request, response, p.id, hashed):
        raise HTTPException(409, "could not sign in; try again")
    return {"ok": True}


# --- the caller's own profile ------------------------------------------------

@router.get("/api/me", response_model=S.MeOut, dependencies=[Depends(require_session)])
async def me(request: Request):
    async with request.app.state.session_factory() as s:
        p = await s.get(Principal, request.state.principal_id)
        if p is None:
            raise HTTPException(401)
        return {**await _user_view(s, p), "role": p.role}


class MyPasswordIn(BaseModel):
    current: str
    new: str
    confirm: str


@router.post("/api/me/password", response_model=S.Ok, dependencies=[Depends(require_session)])
async def change_my_password(request: Request, body: MyPasswordIn):
    _check_new_password(body.new, body.confirm)
    async with request.app.state.session_factory() as s:
        p = await lock_principal(s, request.state.principal_id)
        if p is None or not p.password_hash:
            raise HTTPException(401)
        if not _is_state(p):
            raise HTTPException(403, "system accounts change their password in Settings")
        try:
            await asyncio.to_thread(ph.verify, p.password_hash, body.current)
        except VerifyMismatchError:
            raise HTTPException(403, "current password is incorrect")
        p.password_hash = await asyncio.to_thread(ph.hash, body.new)
        await revoke_sessions(s, p.id, keep_sid=request.state.session_id)
        await s.commit()
    return {"ok": True}


# --- people (admin browser session) ------------------------------------------

@router.get("/api/users", response_model=list[S.UserOut],
            dependencies=[Depends(require_admin_session)])
async def list_users(request: Request):
    async with request.app.state.session_factory() as s:
        rows = list((await s.execute(
            select(Principal).order_by(Principal.created_at, Principal.name))).scalars())
        groups = await _group_refs(s, rows)
        return [{"id": p.id, "username": _username(p), "kind": _kind(p),
                 "group": groups[p.id], "created_at": p.created_at} for p in rows]


class ResetPasswordIn(BaseModel):
    password: str
    confirm: str


async def _state_user(s, user_id: str) -> Principal:
    p = await lock_principal(s, user_id)
    if p is None or not _is_state(p):
        raise HTTPException(404, "no such user")
    return p


@router.post("/api/users/{user_id}/password", response_model=S.Ok,
             dependencies=[Depends(require_admin_session)])
async def reset_user_password(request: Request, user_id: str, body: ResetPasswordIn):
    _check_new_password(body.password, body.confirm)
    async with request.app.state.session_factory() as s:
        p = await _state_user(s, user_id)
        p.password_hash = await asyncio.to_thread(ph.hash, body.password)
        await revoke_sessions(s, p.id)
        await s.commit()
    return {"ok": True}


class UserGroupIn(BaseModel):
    group_id: str | None = None


@router.put("/api/users/{user_id}/group", response_model=S.UserOut,
            dependencies=[Depends(require_admin_session)])
async def set_user_group(request: Request, user_id: str, body: UserGroupIn):
    async with request.app.state.session_factory() as s:
        p = await _state_user(s, user_id)
        if body.group_id is not None and await s.get(UserGroup, body.group_id) is None:
            raise HTTPException(422, "unknown group")
        p.group_id = body.group_id
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(422, "unknown group")
        return await _user_view(s, p)


@router.delete("/api/users/{user_id}", response_model=S.Ok,
               dependencies=[Depends(require_admin_session)])
async def delete_user(request: Request, user_id: str):
    async with request.app.state.session_factory() as s:
        p = await _state_user(s, user_id)
        # Postgres cascades; sqlite has no FK enforcement.
        await s.execute(delete(LoginSession).where(LoginSession.principal_id == p.id))
        await s.delete(p)
        await s.commit()
    return {"ok": True}


# --- groups ------------------------------------------------------------------

class GroupIn(BaseModel):
    name: str


def _group_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > MAX_GROUP_NAME:
        raise HTTPException(422, f"name must be 1 to {MAX_GROUP_NAME} characters")
    return name


async def _name_taken(s, name: str, except_id: str | None = None) -> bool:
    q = select(UserGroup.id).where(func.lower(UserGroup.name) == name.lower())
    if except_id:
        q = q.where(UserGroup.id != except_id)
    return (await s.execute(q)).first() is not None


async def _group_view(s, g: UserGroup) -> dict:
    n = (await s.execute(select(func.count()).select_from(Principal)
                         .where(Principal.group_id == g.id))).scalar_one()
    return {"id": g.id, "name": g.name, "created_at": g.created_at, "member_count": n}


@router.get("/api/groups", response_model=list[S.GroupOut],
            dependencies=[Depends(require_admin_session)])
async def list_groups(request: Request):
    async with request.app.state.session_factory() as s:
        counts = dict((await s.execute(
            select(Principal.group_id, func.count()).where(Principal.group_id.is_not(None))
            .group_by(Principal.group_id))).all())
        groups = (await s.execute(select(UserGroup).order_by(func.lower(UserGroup.name)))).scalars()
        return [{"id": g.id, "name": g.name, "created_at": g.created_at,
                 "member_count": counts.get(g.id, 0)} for g in groups]


@router.post("/api/groups", status_code=201, response_model=S.GroupOut,
             dependencies=[Depends(require_admin_session)])
async def create_group(request: Request, body: GroupIn):
    name = _group_name(body.name)
    async with request.app.state.session_factory() as s:
        if await _name_taken(s, name):
            raise HTTPException(409, "a group with that name exists")
        g = UserGroup(name=name, created_at=utcnow())
        s.add(g)
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, "a group with that name exists")
        return await _group_view(s, g)


@router.patch("/api/groups/{group_id}", response_model=S.GroupOut,
              dependencies=[Depends(require_admin_session)])
async def rename_group(request: Request, group_id: str, body: GroupIn):
    name = _group_name(body.name)
    async with request.app.state.session_factory() as s:
        g = await s.get(UserGroup, group_id)
        if g is None:
            raise HTTPException(404, "no such group")
        if await _name_taken(s, name, except_id=g.id):
            raise HTTPException(409, "a group with that name exists")
        g.name = name
        try:
            await s.commit()
        except IntegrityError:
            raise HTTPException(409, "a group with that name exists")
        return await _group_view(s, g)


@router.delete("/api/groups/{group_id}", response_model=S.Ok,
               dependencies=[Depends(require_admin_session)])
async def delete_group(request: Request, group_id: str):
    async with request.app.state.session_factory() as s:
        g = await s.get(UserGroup, group_id)
        if g is None:
            raise HTTPException(404, "no such group")
        # Members drop to none. Postgres' FK does it too; sqlite has no FK.
        await s.execute(update(Principal).where(Principal.group_id == g.id)
                        .values(group_id=None))
        await s.delete(g)
        await s.commit()
    return {"ok": True}


# --- registration switch -----------------------------------------------------

@router.get("/api/settings/registration", response_model=S.RegistrationState,
            dependencies=[Depends(require_admin_session)])
async def get_registration(request: Request):
    async with request.app.state.session_factory() as s:
        return {"open": await _registration_open(s)}


@router.put("/api/settings/registration", response_model=S.RegistrationState,
            dependencies=[Depends(require_admin_session)])
async def set_registration(request: Request, body: S.RegistrationState):
    async with request.app.state.session_factory() as s:
        row = await s.get(PlatformSetting, REGISTRATION_KEY)
        if row is None:
            s.add(PlatformSetting(key=REGISTRATION_KEY, value=body.open))
        else:
            row.value = body.open
        await s.commit()
    return {"open": body.open}
