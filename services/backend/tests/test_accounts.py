"""Human accounts, unit U1 (docs/design/40): server-side sessions, the fence
that holds the no-access `user` tier to USER_PATHS, and the route walk that
proves no route answers a `user` or an anonymous caller it shouldn't."""
import asyncio
from datetime import timedelta

import httpx
import pytest
from itsdangerous import URLSafeSerializer
from sqlalchemy import func, select, update
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentplatform.api import auth
from agentplatform.api import relay as relay_api
from agentplatform.api.app import create_app
from agentplatform.api.auth import USER_PATHS, ph
from agentplatform.config import Settings
from agentplatform.db import LoginSession, Principal, Run, RunState, utcnow
from agentplatform.pruning import SessionPruner

ADMIN_PW = "pw12345678"


async def _seed_user(sf, username="alice", password="user-pw", role="user") -> str:
    async with sf() as s:
        p = Principal(name=f"user:{username}", role=role, password_hash=ph.hash(password))
        s.add(p)
        await s.commit()
        return p.id


def _other(client) -> httpx.AsyncClient:
    """A second browser over the same app, with its own cookie jar."""
    return httpx.AsyncClient(transport=client._transport, base_url="http://t")


async def _sessions(sf) -> list[LoginSession]:
    async with sf() as s:
        return list((await s.execute(select(LoginSession))).scalars())


@pytest.fixture
async def user_client(admin_client, sf):
    await _seed_user(sf)
    async with _other(admin_client) as c:
        r = await c.post("/api/login", json={"principal": "alice", "password": "user-pw"})
        assert r.status_code == 200, r.text
        yield c


# --- sessions ----------------------------------------------------------------

async def test_login_stores_only_a_hash_and_sets_a_bounded_cookie(client, sf):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    r = await client.post("/api/login", json={"password": ADMIN_PW})
    assert r.status_code == 200
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
    assert f"max-age={30 * 86400}" in header and "secure" not in header
    [row] = await _sessions(sf)
    sid = URLSafeSerializer("dev-insecure", salt="ap-session").loads(
        client.cookies["ap_session"])["sid"]
    assert row.id_hash == auth.hash_token(sid) and sid not in row.id_hash
    async with sf() as s:
        admin = (await s.execute(select(Principal).where(Principal.name == "admin"))).scalar_one()
    assert row.principal_id == admin.id


async def test_an_old_format_cookie_is_refused(client):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    old = URLSafeSerializer("dev-insecure", salt="ap-session").dumps({"principal": "admin"})
    client.cookies.set("ap_session", old)
    assert (await client.get("/api/whoami")).status_code == 401


async def test_a_forged_cookie_is_refused(client):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    forged = URLSafeSerializer("not-the-secret", salt="ap-session").dumps({"sid": "x"})
    client.cookies.set("ap_session", forged)
    assert (await client.get("/api/whoami")).status_code == 401


async def test_logout_revokes_the_session_server_side(admin_client, sf):
    cookie = admin_client.cookies["ap_session"]
    assert (await admin_client.post("/api/logout")).status_code == 200
    # Replaying the cookie after logout gets nothing.
    async with _other(admin_client) as replay:
        replay.cookies.set("ap_session", cookie)
        assert (await replay.get("/api/whoami")).status_code == 401
    [row] = await _sessions(sf)
    assert row.revoked_at is not None


async def test_an_expired_session_is_refused(admin_client, sf):
    async with sf() as s:
        await s.execute(update(LoginSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        await s.commit()
    assert (await admin_client.get("/api/whoami")).status_code == 401


async def test_a_deleted_principal_takes_its_sessions_with_it(user_client, sf):
    assert (await user_client.get("/api/setup-state")).status_code == 200
    async with sf() as s:
        p = (await s.execute(select(Principal).where(Principal.name == "user:alice"))).scalar_one()
        await s.delete(p)
        await s.commit()
    assert (await user_client.post("/api/logout")).status_code == 200
    assert (await user_client.get("/api/whoami")).status_code == 401


async def test_admin_password_change_revokes_others_but_not_self(admin_client):
    async with _other(admin_client) as laptop:
        await laptop.post("/api/login", json={"password": ADMIN_PW})
        assert (await laptop.get("/api/whoami")).status_code == 200
        r = await admin_client.post("/api/change-password", json={
            "old_password": ADMIN_PW, "new_password": "newpw12345"})
        assert r.status_code == 200
        assert (await admin_client.get("/api/whoami")).status_code == 200
        assert (await laptop.get("/api/whoami")).status_code == 401


async def test_change_password_refuses_an_admin_api_key(admin_client, token_client):
    token = (await admin_client.post("/api/api-keys", json={
        "name": "ci", "role": "admin"})).json()["token"]
    r = await token_client.post("/api/change-password", json={
        "old_password": ADMIN_PW, "new_password": "newpw12345"},
        headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    # The password is unchanged.
    assert (await token_client.post("/api/login", json={"password": ADMIN_PW})).status_code == 200


async def test_a_login_racing_a_reset_never_yields_a_live_session(client, sf, monkeypatch):
    """The password verifies, then a reset lands before the session row is
    written: the locked re-check sees the new hash and refuses."""
    await client.post("/api/setup", json={"password": ADMIN_PW})
    real_lock = auth.lock_principal

    async def reset_then_lock(session, principal_id):
        async with sf() as other:
            row = await other.get(Principal, principal_id)
            row.password_hash = ph.hash("reset-by-admin")
            await other.commit()
        return await real_lock(session, principal_id)

    monkeypatch.setattr(auth, "lock_principal", reset_then_lock)
    r = await client.post("/api/login", json={"password": ADMIN_PW})
    assert r.status_code == 401 and "ap_session" not in r.cookies
    assert await _sessions(sf) == []


async def test_a_digit_leading_username_logs_in(client, sf):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    await _seed_user(sf, "7bob", "pw")
    r = await client.post("/api/login", json={"principal": "7bob", "password": "pw"})
    assert r.status_code == 200
    # The bare name `user:7bob` is not a login name (the prefix is storage).
    r = await client.post("/api/login", json={"principal": "user:7bob", "password": "pw"})
    assert r.status_code == 422


async def test_the_session_pruner_drops_week_old_dead_rows(sf):
    now = utcnow()
    async with sf() as s:
        p = Principal(name="admin", role="admin")
        s.add(p)
        await s.flush()
        for i, (expires, revoked) in enumerate([
                (now + timedelta(days=1), None),                       # live
                (now - timedelta(days=1), None),                       # just expired
                (now - timedelta(days=8), None),                       # long expired
                (now + timedelta(days=9), now - timedelta(days=8)),    # long revoked
                (now + timedelta(days=9), now - timedelta(days=1))]):  # just revoked
            s.add(LoginSession(id_hash=f"h{i}", principal_id=p.id, expires_at=expires,
                               revoked_at=revoked))
        await s.commit()
    assert await SessionPruner(sf).prune_once(now=now) == 2
    assert sorted(r.id_hash for r in await _sessions(sf)) == ["h0", "h1", "h4"]


# --- the fence ---------------------------------------------------------------

async def test_a_user_sees_that_it_is_signed_in_and_nothing_else(user_client):
    state = await user_client.get("/api/setup-state")
    assert state.status_code == 200 and state.json()["secrets"] == []
    for path in ("/api/whoami", "/api/runs", "/api/agents", "/api/help/topics"):
        assert (await user_client.get(path)).status_code == 403, path
    assert (await user_client.post("/api/logout")).status_code == 200
    assert (await user_client.get("/api/setup-state")).json()["secrets"] == []


async def test_a_webhook_carrying_a_user_cookie_is_refused(user_client, seed_agent,
                                                           agent_store):
    """Pinned (docs/design/40): optional-auth routes try the platform identity
    first, so a `user` cookie meets the fence before the webhook secret."""
    await seed_agent("awake", entrypoints={"crons": [], "topics": [], "timezone": "",
                                           "webhooks": [{"path": "ping"}]})
    await agent_store.reload()
    assert (await user_client.post("/api/webhooks/ping", json={})).status_code == 403


async def test_anonymous_project_creation_is_refused(client):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    r = await client.post("/api/projects", json={"slug": "x", "name": "x"})
    assert r.status_code == 401


# --- long-lived streams ------------------------------------------------------

async def test_an_sse_feed_closes_after_its_session_is_revoked(admin_client, sf, monkeypatch):
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.5)
    monkeypatch.setattr(auth, "SESSION_RECHECK_SECONDS", 0)
    app = admin_client._transport.app
    bodies: asyncio.Queue = asyncio.Queue()
    started = asyncio.Event()

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            started.set()
        elif message["type"] == "http.response.body":
            bodies.put_nowait(message.get("body", b""))

    cookie = admin_client.cookies["ap_session"]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "path": "/api/wiki/events", "raw_path": b"/api/wiki/events",
             "query_string": b"", "headers": [(b"cookie", f"ap_session={cookie}".encode())],
             "scheme": "http", "server": ("t", 80), "client": ("127.0.0.1", 1),
             "root_path": ""}
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert b"heartbeat" in await asyncio.wait_for(bodies.get(), 2)
        assert not task.done()
        # Sign out while the stream waits out its next heartbeat. The
        # in-memory sqlite is one shared connection, and the stream re-checks
        # right after each beat: a session closing there would roll back the
        # logout's uncommitted update.
        await asyncio.sleep(0.1)
        await admin_client.post("/api/logout")
        await asyncio.wait_for(task, 3)     # the stream ended on its own
    finally:
        task.cancel()


def _tail_app(producer, consumer):
    return create_app(Settings(), None, producer, consumer_factory=consumer)


def _seed_run(app):
    async def seed():
        async with app.state.session_factory() as s:
            s.add(Run(id="RUNID", agent="hello-world", trigger="manual",
                      requested_by="t", prompt="x", state=RunState.RUNNING))
            await s.commit()
    return seed


def test_tail_refuses_a_user_with_4403(producer):
    async def consumer():
        yield ("RUNID", {"terminal": True})

    app = _tail_app(producer, consumer)
    with TestClient(app) as tc:
        tc.portal.call(_seed_run(app))
        tc.post("/api/setup", json={"password": ADMIN_PW})

        async def seed_user():
            await _seed_user(app.state.session_factory)
        tc.portal.call(seed_user)
        assert tc.post("/api/login", json={"principal": "alice",
                                           "password": "user-pw"}).status_code == 200
        with pytest.raises(WebSocketDisconnect) as exc:
            with tc.websocket_connect("/api/runs/RUNID/tail") as ws:
                ws.receive_text()
        assert exc.value.code == 4403


def test_tail_closes_after_its_session_is_revoked(producer, monkeypatch):
    monkeypatch.setattr(auth, "SESSION_RECHECK_SECONDS", 0)
    holder = {}

    async def consumer():
        yield ("RUNID", {"type": "assistant", "seq": 1})
        async with holder["app"].state.session_factory() as s:
            await s.execute(update(LoginSession).values(revoked_at=utcnow()))
            await s.commit()
        yield ("RUNID", {"type": "assistant", "seq": 2})
        yield ("RUNID", {"terminal": True})

    app = holder["app"] = _tail_app(producer, consumer)
    with TestClient(app) as tc:
        tc.portal.call(_seed_run(app))
        tc.post("/api/setup", json={"password": ADMIN_PW})
        tc.post("/api/login", json={"password": ADMIN_PW})
        with pytest.raises(WebSocketDisconnect) as exc:
            with tc.websocket_connect("/api/runs/RUNID/tail") as ws:
                assert ws.receive_json()["seq"] == 1
                ws.receive_text()
        assert exc.value.code == 4401


# --- the route walk ----------------------------------------------------------
# Walks app.openapi()["paths"], which (unlike app.routes) includes every route
# nested in an included router. A `user` gets no 2xx outside USER_PATHS and
# PUBLIC_PATHS; an anonymous caller gets a 2xx only on PUBLIC_PATHS. Every
# entry below is a route that is meant to answer someone who is not signed in.

PUBLIC_PATHS = {
    ("GET", "/api/setup-state"),       # the first-launch gate; secrets hidden
    ("POST", "/api/setup"),            # once, before the admin exists
    ("POST", "/api/login"),
    ("GET", "/api/register"),          # the login page asks whether to show the tab
    ("POST", "/api/register"),         # self-registration, while open
    ("POST", "/api/logout"),           # only revokes the cookie it is sent
    ("POST", "/api/webhooks/{path}"),  # per-path secret (docs/design/16)
    ("POST", "/api/internal/quota"),   # shared internal secret (docs/design/22)
}


def _example(schema: dict, spec: dict, depth: int = 0):
    """A minimal instance of an OpenAPI schema: required fields only, so the
    walk reaches the handler instead of stopping at a 422."""
    if depth > 8:
        return None
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _example(spec["components"]["schemas"][name], spec, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"]
            return _example(options[0], spec, depth + 1) if options else None
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: _example(props[k], spec, depth + 1) for k in schema.get("required", [])
                if k in props}
    if t == "array":
        return []
    if t == "integer":
        return max(1, schema.get("minimum", 1))
    if t == "number":
        return 1
    if t == "boolean":
        return False
    if t == "string":
        return "x" * max(1, schema.get("minLength", 1))
    return None


async def _walk(c: httpx.AsyncClient, app) -> list[tuple[str, str, int | str]]:
    """Every (method, path, status) the caller got a 2xx (or a stream that
    never ended) from."""
    spec = app.openapi()
    hits = []
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            if (method.upper(), path) == ("POST", "/api/logout"):
                continue        # public, and would end the walk's own session
            url, query = path, {}
            for param in op.get("parameters", []):
                if param.get("in") == "path":
                    url = url.replace("{" + param["name"] + "}", "x")
                elif param.get("in") == "query" and param.get("required"):
                    query[param["name"]] = _example(param.get("schema", {}), spec)
            kwargs = {"params": query}
            content = (op.get("requestBody") or {}).get("content", {})
            if "application/json" in content:
                kwargs["json"] = _example(content["application/json"].get("schema", {}), spec)
            try:
                r = await asyncio.wait_for(c.request(method.upper(), url, **kwargs), 5)
                status = r.status_code
            except TimeoutError:
                status = "open stream"
            except Exception:                   # a handler crash is not a 2xx
                continue
            if status == "open stream" or 200 <= status < 300:
                hits.append((method.upper(), path, status))
    return hits


async def test_route_walk_a_user_gets_nothing_outside_user_paths(user_client):
    app = user_client._transport.app
    hits = await _walk(user_client, app)
    allowed = {p for _, p in PUBLIC_PATHS} | set(USER_PATHS)
    assert [h for h in hits if h[1] not in allowed] == []
    # Still signed in as the user after the walk (403, not 401), so every
    # refusal above was the fence and not a lost session.
    assert (await user_client.get("/api/whoami")).status_code == 403


async def test_route_walk_an_anonymous_caller_reaches_only_public_paths(admin_client):
    app = admin_client._transport.app
    async with _other(admin_client) as anon:
        hits = await _walk(anon, app)
    assert [h for h in hits if (h[0], h[1]) not in PUBLIC_PATHS] == []


# --- U2: register, me, users, groups -----------------------------------------

async def _register(c, username="bob", pw="bob-pw", confirm=None):
    return await c.post("/api/register", json={
        "username": username, "password": pw, "confirm": pw if confirm is None else confirm})


async def _id_of(c, username) -> str:
    users = (await c.get("/api/users")).json()
    return next(u["id"] for u in users if u["username"] == username)


async def _sessions_for(sf, pid):
    return [x for x in await _sessions(sf) if x.principal_id == pid]


async def test_register_signs_in_and_me_describes_the_user(client):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    assert (await client.get("/api/register")).json() == {"open": True}
    r = await _register(client, "Bob")
    assert r.status_code == 200, r.text
    me = (await client.get("/api/me")).json()
    assert me["username"] == "bob" and me["role"] == "user" and me["kind"] == "state"
    assert me["group"] is None and me["created_at"]
    # The fence still holds: a registered user reaches nothing else.
    assert (await client.get("/api/whoami")).status_code == 403
    await client.post("/api/logout")
    assert (await client.get("/api/me")).status_code == 401
    r = await client.post("/api/login", json={"principal": "bob", "password": "bob-pw"})
    assert r.status_code == 200


async def test_register_validation(client, sf):
    await client.post("/api/setup", json={"password": ADMIN_PW})
    assert (await _register(client, "bob", "x", confirm="y")).status_code == 422
    assert (await _register(client, "bob", "")).status_code == 422
    assert (await _register(client, "bob", "p" * 1025)).status_code == 422
    assert (await _register(client, "-bob")).status_code == 422
    assert (await _register(client, "has space")).status_code == 422
    assert (await _register(client, "admin")).status_code == 409
    assert (await _register(client, "qa")).status_code == 409
    assert (await _register(client, "7days")).status_code == 200      # digit-leading
    await client.post("/api/logout")
    assert (await client.post("/api/login", json={
        "principal": "7days", "password": "bob-pw"})).status_code == 200
    async with _other(client) as c2:
        assert (await _register(c2, "7DAYS")).status_code == 409       # duplicate, case-folded


async def test_registration_closed_refuses_and_reports(admin_client):
    async with _other(admin_client) as anon:
        assert (await admin_client.put("/api/settings/registration",
                                       json={"open": False})).json() == {"open": False}
        assert (await anon.get("/api/register")).json() == {"open": False}
        assert (await _register(anon)).status_code == 403
        await admin_client.put("/api/settings/registration", json={"open": True})
        assert (await _register(anon)).status_code == 200


async def test_user_changes_own_password_and_keeps_only_this_session(user_client):
    async with _other(user_client) as phone:
        await phone.post("/api/login", json={"principal": "alice", "password": "user-pw"})
        r = await user_client.post("/api/me/password", json={
            "current": "wrong", "new": "n", "confirm": "n"})
        assert r.status_code == 403
        r = await user_client.post("/api/me/password", json={
            "current": "user-pw", "new": "n", "confirm": "m"})
        assert r.status_code == 422
        r = await user_client.post("/api/me/password", json={
            "current": "user-pw", "new": "", "confirm": ""})
        assert r.status_code == 422
        r = await user_client.post("/api/me/password", json={
            "current": "user-pw", "new": "fresh", "confirm": "fresh"})
        assert r.status_code == 200
        assert (await user_client.get("/api/me")).status_code == 200
        assert (await phone.get("/api/me")).status_code == 401
    r = await user_client.post("/api/login", json={"principal": "alice", "password": "fresh"})
    assert r.status_code == 200


async def test_me_for_admin_is_system_and_me_password_is_refused(admin_client, token_client):
    me = (await admin_client.get("/api/me")).json()
    assert me["username"] == "admin" and me["kind"] == "system" and me["role"] == "admin"
    r = await admin_client.post("/api/me/password", json={
        "current": ADMIN_PW, "new": "n", "confirm": "n"})
    assert r.status_code == 403
    token = (await admin_client.post("/api/api-keys", json={
        "name": "ci", "role": "admin"})).json()["token"]
    assert (await token_client.get("/api/me", headers={
        "Authorization": f"Bearer {token}"})).status_code == 403


async def test_users_list_never_carries_the_hash_and_marks_kinds(admin_client, sf):
    await _seed_user(sf)
    users = (await admin_client.get("/api/users")).json()
    assert {u["username"]: u["kind"] for u in users} == {"admin": "system", "alice": "state"}
    for u in users:
        assert set(u) == {"id", "username", "kind", "group", "created_at"}


async def test_admin_reset_revokes_all_and_system_rows_are_untouchable(admin_client, user_client):
    uid = await _id_of(admin_client, "alice")
    assert (await admin_client.post(f"/api/users/{uid}/password", json={
        "password": "a", "confirm": "b"})).status_code == 422
    assert (await admin_client.post(f"/api/users/{uid}/password", json={
        "password": "reset", "confirm": "reset"})).status_code == 200
    assert (await user_client.get("/api/me")).status_code == 401
    assert (await user_client.post("/api/login", json={
        "principal": "alice", "password": "reset"})).status_code == 200
    admin_id = await _id_of(admin_client, "admin")
    for r in (await admin_client.post(f"/api/users/{admin_id}/password",
                                      json={"password": "x", "confirm": "x"}),
              await admin_client.delete(f"/api/users/{admin_id}"),
              await admin_client.put(f"/api/users/{admin_id}/group", json={"group_id": None}),
              await admin_client.delete("/api/users/nope")):
        assert r.status_code == 404


async def test_admin_delete_revokes_and_frees_the_name(admin_client, user_client, sf):
    uid = await _id_of(admin_client, "alice")
    assert (await admin_client.delete(f"/api/users/{uid}")).status_code == 200
    assert (await user_client.get("/api/me")).status_code == 401
    assert await _sessions_for(sf, uid) == []
    async with _other(admin_client) as c:
        assert (await _register(c, "alice")).status_code == 200
        # The re-registered name did not inherit the old session.
        assert (await user_client.get("/api/me")).status_code == 401


async def test_group_crud_and_membership(admin_client, user_client):
    assert (await admin_client.post("/api/groups", json={"name": " "})).status_code == 422
    r = await admin_client.post("/api/groups", json={"name": "Family"})
    assert r.status_code == 201 and r.json()["member_count"] == 0
    gid = r.json()["id"]
    assert (await admin_client.post("/api/groups", json={"name": "FAMILY"})).status_code == 409
    other = (await admin_client.post("/api/groups", json={"name": "Friends"})).json()["id"]
    assert (await admin_client.patch(f"/api/groups/{other}",
                                     json={"name": "family"})).status_code == 409
    assert (await admin_client.patch(f"/api/groups/{gid}",
                                     json={"name": "FAMILY"})).json()["name"] == "FAMILY"
    uid = await _id_of(admin_client, "alice")
    assert (await admin_client.put(f"/api/users/{uid}/group",
                                   json={"group_id": "nope"})).status_code == 422
    r = await admin_client.put(f"/api/users/{uid}/group", json={"group_id": gid})
    assert r.json()["group"] == {"id": gid, "name": "FAMILY"}
    assert (await user_client.get("/api/me")).json()["group"]["name"] == "FAMILY"
    groups = {g["name"]: g["member_count"] for g in (await admin_client.get("/api/groups")).json()}
    assert groups == {"FAMILY": 1, "Friends": 0}
    assert (await admin_client.delete(f"/api/groups/{gid}")).status_code == 200
    assert (await user_client.get("/api/me")).json()["group"] is None
    assert (await admin_client.delete(f"/api/groups/{gid}")).status_code == 404
    assert (await admin_client.patch(f"/api/groups/{gid}",
                                     json={"name": "x"})).status_code == 404


async def test_a_dangling_group_id_reads_as_none_and_is_cleared(admin_client, user_client, sf):
    async with sf() as s:
        await s.execute(update(Principal).where(Principal.name == "user:alice")
                        .values(group_id="d" * 32))
        await s.commit()
    assert (await user_client.get("/api/me")).json()["group"] is None
    async with sf() as s:
        p = (await s.execute(select(Principal).where(Principal.name == "user:alice"))).scalar_one()
        assert p.group_id is None


async def test_people_routes_refuse_an_admin_api_key_and_a_user(admin_client, user_client,
                                                                 token_client):
    token = (await admin_client.post("/api/api-keys", json={
        "name": "ci", "role": "admin"})).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    for method, path, body in (
            ("GET", "/api/users", None), ("GET", "/api/groups", None),
            ("POST", "/api/groups", {"name": "g"}),
            ("GET", "/api/settings/registration", None),
            ("PUT", "/api/settings/registration", {"open": False}),
            ("DELETE", "/api/users/x", None)):
        r = await token_client.request(method, path, json=body, headers=h)
        assert r.status_code == 403, (method, path)
        r = await user_client.request(method, path, json=body)
        assert r.status_code == 403, (method, path)
    assert (await admin_client.get("/api/settings/registration")).json() == {"open": True}


async def test_user_prefixed_api_key_names_are_refused(admin_client):
    r = await admin_client.post("/api/api-keys", json={"name": "user:eve", "role": "reader"})
    assert r.status_code == 422
