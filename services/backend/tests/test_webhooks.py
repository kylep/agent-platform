from agentplatform.events import TOPIC_RUN_INBOUND


import pytest


@pytest.fixture(autouse=True)
async def declare_webhook(seed_agent):
    # Webhook paths must be DECLARED (docs/design/10); hello-world opts in with
    # a path matching its name.
    await seed_agent("hello-world",
                     entrypoints={"webhooks": [{"path": "hello-world"}]})


async def _mint(client, role):
    r = await client.post("/api/api-keys", json={"name": f"wh-{role}", "role": role, "agent": None})
    return r.json()["token"]


async def test_webhook_requires_auth(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.post("/api/webhooks/hello-world", json={"x": 1})).status_code == 401


async def test_webhook_with_operator_key_emits_inbound(admin_client, producer):
    token = await _mint(admin_client, "operator")
    admin_client.cookies.clear()  # only the bearer key authenticates now
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"event": "push", "ref": "main"})
    assert r.status_code == 202
    rid = r.json()["id"]
    # Event-sourced: produced to run.inbound (no synchronous Run row); the
    # ingest consumer materializes it.
    inbound = [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]
    assert len(inbound) == 1
    _, key, data = inbound[0]
    assert key == rid and data["agent"] == "hello-world" and data["trigger"] == "webhook"
    assert "push" in data["prompt"] and "webhook" in data["prompt"].lower()


async def test_webhook_reader_key_forbidden(admin_client):
    token = await _mint(admin_client, "reader")
    admin_client.cookies.clear()
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code == 403   # reader can't trigger runs


async def test_webhook_unknown_agent_404(admin_client):
    r = await admin_client.post("/api/webhooks/ghost", json={})
    assert r.status_code == 404


async def test_webhook_undeclared_path_404(admin_client, seed_agent):
    # the agent exists, but stops declaring the path -> the endpoint vanishes
    await seed_agent("hello-world", entrypoints={"webhooks": []})
    r = await admin_client.post("/api/webhooks/hello-world", json={})
    assert r.status_code == 404


async def test_webhook_decoupled_path_routes_to_declaring_agent(admin_client, seed_agent, producer):
    from agentplatform.events import TOPIC_RUN_INBOUND
    await seed_agent("hello-world", entrypoints={"webhooks": [{"path": "newsflash"}]})
    r = await admin_client.post("/api/webhooks/newsflash", json={"k": 1})
    assert r.status_code == 202
    inbound = [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]
    assert inbound[-1][2]["agent"] == "hello-world"


# --- shared-secret auth (docs/design/16) --------------------------------------

SECRET = "hunter2-hunter2-hunter2"
HEADER = "X-AP-Webhook-Secret"


async def _secret_mode(admin_client, seed_agent, *, set_secret=True, path="hello-world"):
    """`hello-world` declaring `path` in secret mode, optionally with a live
    secret — then drop the admin cookie so the client is an OUTSIDE caller."""
    await seed_agent("hello-world",
                     entrypoints={"webhooks": [{"path": path, "auth": "secret"}]})
    if set_secret:
        r = await admin_client.put(
            f"/api/agents/hello-world/webhooks/{path}/secret", json={"secret": SECRET})
        assert r.status_code == 200
    admin_client.cookies.clear()


async def test_secret_header_alone_fires_the_webhook(admin_client, seed_agent, producer):
    """The point of the whole feature: an external caller with no platform key."""
    await _secret_mode(admin_client, seed_agent)
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={HEADER: SECRET}, json={"event": "push"})
    assert r.status_code == 202
    inbound = [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]
    assert inbound[-1][2]["agent"] == "hello-world"
    assert inbound[-1][2]["trigger"] == "webhook"
    assert SECRET not in inbound[-1][2]["requested_by"]


async def test_wrong_secret_rejected(admin_client, seed_agent, producer):
    await _secret_mode(admin_client, seed_agent)
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={HEADER: "not-the-secret-at-all"}, json={})
    assert r.status_code == 401
    assert not [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]


async def test_missing_secret_header_rejected(admin_client, seed_agent):
    await _secret_mode(admin_client, seed_agent)
    assert (await admin_client.post("/api/webhooks/hello-world", json={})).status_code == 401


async def test_secret_mode_with_no_stored_secret_fails_closed(admin_client, seed_agent,
                                                              producer):
    """Mode says `secret`, nothing was ever set (a rollback, or a half-finished
    edit): reject rather than fall open. To an ANONYMOUS caller it is the plain
    uniform 401 — telling a stranger *why* would make this the one response
    that distinguishes a declared path from a ghost."""
    await _secret_mode(admin_client, seed_agent, set_secret=False)
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={HEADER: SECRET}, json={})
    assert r.status_code == 401 and "no secret" not in r.text.lower()
    ghost = await admin_client.post("/api/webhooks/ghost",
                                    headers={HEADER: SECRET}, json={})
    assert (r.status_code, r.text) == (ghost.status_code, ghost.text)
    assert not [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]


async def test_authenticated_caller_is_told_about_the_missing_secret(admin_client,
                                                                     seed_agent):
    """Diagnosis is for people already inside. A reader key can't fire the
    webhook either way, so naming the misconfiguration to it costs nothing and
    is how "why is my webhook 401ing" gets answered."""
    token = await _mint(admin_client, "reader")
    await _secret_mode(admin_client, seed_agent, set_secret=False)
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}",
                                         HEADER: SECRET}, json={})
    assert r.status_code == 503 and "no secret" in r.text.lower()


async def test_reader_key_cannot_enumerate_paths_either(admin_client, seed_agent):
    """The authenticated-but-insufficient rung of the same rule: a reader gets
    an identical 403 for a declared path and a ghost, body included, so no
    future detail string leaks what the status code doesn't."""
    token = await _mint(admin_client, "reader")
    await seed_agent("hello-world",
                     entrypoints={"webhooks": [{"path": "hello-world"}]})
    admin_client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    declared = await admin_client.post("/api/webhooks/hello-world",
                                       headers=headers, json={})
    ghost = await admin_client.post("/api/webhooks/ghost", headers=headers, json={})
    assert declared.status_code == ghost.status_code == 403
    assert declared.text == ghost.text


async def test_platform_key_still_works_in_secret_mode(admin_client, seed_agent, producer):
    """A valid operator key is accepted whatever the mode says — the secret is
    an ADDITIONAL door, not a replacement."""
    token = await _mint(admin_client, "operator")
    await _secret_mode(admin_client, seed_agent)
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code == 202
    assert [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]


async def test_secret_does_not_open_a_none_mode_path(admin_client, seed_agent):
    """Auth is per PATH: a secret set on a path later switched back to `none`
    grants nothing — none means platform key, exactly as before."""
    await _secret_mode(admin_client, seed_agent)
    await seed_agent("hello-world",
                     entrypoints={"webhooks": [{"path": "hello-world", "auth": "none"}]})
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={HEADER: SECRET}, json={})
    assert r.status_code == 401


async def test_secret_does_not_open_another_agents_path(admin_client, seed_agent):
    """The hash is keyed by (agent, path); one agent's secret must not
    authenticate another agent's webhook. `other` has no hash of its own, so
    an anonymous caller gets the uniform 401 — not a 503 that would confirm
    `other-hook` is a real path."""
    await seed_agent("other", entrypoints={"webhooks": [{"path": "other-hook",
                                                         "auth": "secret"}]})
    await _secret_mode(admin_client, seed_agent)
    r = await admin_client.post("/api/webhooks/other-hook",
                                headers={HEADER: SECRET}, json={})
    ghost = await admin_client.post("/api/webhooks/ghost",
                                    headers={HEADER: SECRET}, json={})
    assert r.status_code == 401
    assert (r.status_code, r.text) == (ghost.status_code, ghost.text)


async def test_anonymous_cannot_enumerate_paths(admin_client, seed_agent):
    """An unauthenticated caller gets the same 401 for EVERY shape a path can
    have — resolving the path before authenticating must not turn the endpoint
    into a directory of declared webhooks. All four states below are reachable
    with a wordlist, so all four must be indistinguishable."""
    await seed_agent("hello-world", entrypoints={"webhooks": [
        {"path": "mode-none"},
        {"path": "with-secret", "auth": "secret"},
        {"path": "no-secret-yet", "auth": "secret"}]})
    r = await admin_client.put("/api/agents/hello-world/webhooks/with-secret/secret",
                               json={"secret": SECRET})
    assert r.status_code == 200
    admin_client.cookies.clear()
    answers = {p: await admin_client.post(f"/api/webhooks/{p}", json={})
               for p in ("mode-none", "with-secret", "no-secret-yet", "ghost")}
    assert {(r.status_code, r.text) for r in answers.values()} == {(401, answers["ghost"].text)}


async def test_disabled_agent_still_409s_for_a_secret_caller(admin_client, seed_agent):
    await seed_agent("hello-world", enabled=False,
                     entrypoints={"webhooks": [{"path": "hello-world",
                                                "auth": "secret"}]})
    r = await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                               json={"secret": SECRET})
    assert r.status_code == 200
    admin_client.cookies.clear()
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={HEADER: SECRET}, json={})
    assert r.status_code == 409
