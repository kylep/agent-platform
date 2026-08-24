"""Per-webhook shared secrets (docs/design/16).

Two things are under test here and they are deliberately separate concerns:

- the STORAGE rule — a secret lives in `webhook_secrets`, never on the
  definition, so it can't reach `agent_versions`, the API, or a rollback;
- the write API — same authority as an entrypoints edit, write-only, and
  scoped to paths the agent actually declares.

Ingress (who may fire a webhook) lives in test_webhooks.py.
"""
import pytest
from sqlalchemy import select

from agentplatform.db import AgentDef, AgentVersion, WebhookSecret
from agentplatform.webhooksecrets import (MAX_SECRET_LENGTH, MIN_SECRET_LENGTH,
                                          hash_secret, new_salt, verify_secret)
from tests.test_agents_api import a_def, bearer

SECRET = "s" * MIN_SECRET_LENGTH
AGENTS_EDIT = "mcp__platform__agents_edit"
AGENTS_GRANT = "mcp__platform__agents_grant"


def ep(*paths, auth="secret") -> dict:
    return {"crons": [], "topics": [], "timezone": "",
            "webhooks": [{"path": p, "auth": auth} for p in paths]}


@pytest.fixture
async def hooked(seed_agent, agent_store):
    """`hello-world` declaring one secret-mode webhook path."""
    await seed_agent("hello-world", entrypoints=ep("hello-world"))
    await agent_store.reload()


async def rows(sf) -> list[WebhookSecret]:
    async with sf() as s:
        return list((await s.execute(select(WebhookSecret))).scalars().all())


# --- hashing -----------------------------------------------------------------

def test_hash_is_salted_and_verifies():
    salt = new_salt()
    digest = hash_secret(SECRET, salt)
    assert SECRET not in digest and salt != new_salt()
    assert verify_secret(SECRET, salt, digest)
    assert not verify_secret(SECRET + "x", salt, digest)
    # Same secret under a different salt is a different digest — that is the
    # whole point of the per-row salt.
    assert hash_secret(SECRET, new_salt()) != digest


# --- the write API -----------------------------------------------------------

async def test_set_stores_a_hash_and_never_the_secret(admin_client, sf, hooked):
    r = await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                               json={"secret": SECRET})
    assert r.status_code == 200
    assert SECRET not in r.text
    (row,) = await rows(sf)
    assert row.agent == "hello-world" and row.path == "hello-world"
    assert SECRET not in row.secret_hash and row.salt
    assert verify_secret(SECRET, row.salt, row.secret_hash)


async def test_rotation_replaces_the_hash(admin_client, sf, hooked):
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    (first,) = await rows(sf)
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": "rotated-" + SECRET})
    (second,) = await rows(sf)          # replaced, not appended
    assert second.secret_hash != first.secret_hash
    assert not verify_secret(SECRET, second.salt, second.secret_hash)
    assert verify_secret("rotated-" + SECRET, second.salt, second.secret_hash)


async def test_short_secret_rejected(admin_client, sf, hooked):
    r = await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                               json={"secret": "s" * (MIN_SECRET_LENGTH - 1)})
    assert r.status_code == 422 and str(MIN_SECRET_LENGTH) in r.text
    assert await rows(sf) == []


async def test_absurdly_long_secret_rejected(admin_client, sf, hooked):
    """Every inbound webhook hashes what it is given; an unbounded secret would
    be a CPU-burn primitive on an endpoint reachable without a platform key."""
    r = await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                               json={"secret": "s" * (MAX_SECRET_LENGTH + 1)})
    assert r.status_code == 422 and str(MAX_SECRET_LENGTH) in r.text
    assert await rows(sf) == []
    # The boundary itself is fine.
    r = await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                               json={"secret": "s" * MAX_SECRET_LENGTH})
    assert r.status_code == 200


async def test_overlong_webhook_path_rejected(admin_client, hooked):
    """The path is `webhook_secrets`' key, in a String(256) column: a path that
    table cannot store is a path that cannot be secured, so it never lands."""
    from agentplatform.agentdefs import WEBHOOK_PATH_MAX_LENGTH
    long_path = "p" * (WEBHOOK_PATH_MAX_LENGTH + 1)
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", entrypoints=ep(long_path)))
    assert r.status_code == 422 and str(WEBHOOK_PATH_MAX_LENGTH) in r.text
    # A row already holding one stays READABLE — reading it is how you fix it.
    assert (await admin_client.get("/api/agents/hello-world")).status_code == 200


async def test_undeclared_path_404(admin_client, sf, hooked):
    r = await admin_client.put("/api/agents/hello-world/webhooks/nope/secret",
                               json={"secret": SECRET})
    assert r.status_code == 404
    assert await rows(sf) == []


async def test_unknown_agent_404(admin_client, sf, hooked):
    r = await admin_client.put("/api/agents/ghost/webhooks/hello-world/secret",
                               json={"secret": SECRET})
    assert r.status_code == 404


async def test_delete_removes_the_hash(admin_client, sf, hooked):
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    r = await admin_client.delete("/api/agents/hello-world/webhooks/hello-world/secret")
    assert r.status_code == 200
    assert await rows(sf) == []


async def test_delete_on_undeclared_path_404(admin_client, hooked):
    r = await admin_client.delete("/api/agents/hello-world/webhooks/nope/secret")
    assert r.status_code == 404


# --- authority ---------------------------------------------------------------

async def test_anonymous_rejected(client, hooked):
    await client.post("/api/setup", json={"password": "pw12345678"})
    r = await client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                         json={"secret": SECRET})
    assert r.status_code == 401


async def test_agents_edit_may_set_a_secret(client, sf, seed_agent, agent_store):
    """Same authority as editing entrypoints — an agent that may declare the
    path may set the secret guarding it."""
    await seed_agent("hello-world", entrypoints=ep("hello-world"))
    await seed_agent("editor", platform_tools=[AGENTS_EDIT])
    await agent_store.reload()
    r = await client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                         json={"secret": SECRET},
                         headers=await bearer(sf, "editor"))
    assert r.status_code == 200
    assert len(await rows(sf)) == 1


async def test_grant_only_agent_may_not_set_a_secret(client, sf, seed_agent, agent_store):
    """`agents_grant` is the OTHER half of the split: it changes what an agent
    may do, not what its entrypoints look like."""
    await seed_agent("hello-world", entrypoints=ep("hello-world"))
    await seed_agent("granter", platform_tools=[AGENTS_GRANT])
    await agent_store.reload()
    r = await client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                         json={"secret": SECRET},
                         headers=await bearer(sf, "granter"))
    assert r.status_code == 403
    assert await rows(sf) == []


# --- the secret never joins the definition -----------------------------------

async def test_secret_set_is_derived_on_read(admin_client, hooked):
    hooks = (await admin_client.get("/api/agents/hello-world")).json()["entrypoints"]["webhooks"]
    assert hooks == [{"path": "hello-world", "auth": "secret", "secret_set": False}]
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    body = (await admin_client.get("/api/agents/hello-world")).json()
    assert body["entrypoints"]["webhooks"][0]["secret_set"] is True
    assert SECRET not in (await admin_client.get("/api/agents/hello-world")).text
    # ...and the listing carries the same derived flag.
    listed = next(a for a in (await admin_client.get("/api/agents")).json()
                  if a["name"] == "hello-world")
    assert listed["entrypoints"]["webhooks"][0]["secret_set"] is True


async def test_secret_set_never_reaches_the_stored_definition(admin_client, sf, hooked):
    """The UI reads a definition and PUTs it back; the derived flag must ride
    along without ever landing in the column or the change log."""
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    body = (await admin_client.get("/api/agents/hello-world")).json()
    body["prompt"] = "edited"
    r = await admin_client.put("/api/agents/hello-world", json=body)
    assert r.status_code == 200
    async with sf() as s:
        row = await s.get(AgentDef, "hello-world")
        assert row.entrypoints["webhooks"] == [{"path": "hello-world", "auth": "secret"}]
        versions = (await s.execute(select(AgentVersion))).scalars().all()
    assert versions
    for v in versions:
        blob = str(v.snapshot)
        assert SECRET not in blob and "secret_set" not in blob


async def test_mode_and_secret_survive_a_definition_update(admin_client, sf, hooked):
    """A definition rewrite that keeps the path must not disturb the stored
    hash — the secret is not part of what a save replaces."""
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    (before,) = await rows(sf)
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", prompt="new prompt",
                                          entrypoints=ep("hello-world")))
    assert r.status_code == 200
    assert r.json()["entrypoints"]["webhooks"][0]["auth"] == "secret"
    (after,) = await rows(sf)
    assert (after.agent, after.path, after.secret_hash) == \
           (before.agent, before.path, before.secret_hash)


async def test_undeclaring_the_path_drops_its_secret(admin_client, sf, hooked):
    """A secret outlives no path. Leaving the row behind would mean
    re-declaring the path later silently re-arms a credential nobody can see."""
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", entrypoints=ep()))
    assert r.status_code == 200
    assert await rows(sf) == []
    # ...and bringing it back starts from nothing, not from the old secret.
    r = await admin_client.put("/api/agents/hello-world",
                               json=a_def("hello-world", entrypoints=ep("hello-world")))
    assert r.json()["entrypoints"]["webhooks"][0]["secret_set"] is False
    assert await rows(sf) == []


async def test_creating_an_agent_starts_with_no_secrets(admin_client, sf, hooked):
    """Pruning is an invariant of every write that LANDS, not of three of the
    four. Unreachable through deletion (which already clears them), so this
    plants the row behind the API's back and pins the rule directly."""
    async with sf() as s:
        s.add(WebhookSecret(agent="reborn", path="ghost-hook",
                            salt=new_salt(), secret_hash="x" * 64))
        await s.commit()
    r = await admin_client.post("/api/agents",
                                json=a_def("reborn", entrypoints=ep("live-hook")))
    assert r.status_code == 201
    assert [(w.agent, w.path) for w in await rows(sf)] == []


async def test_deleting_the_agent_cleans_its_hashes(admin_client, sf, hooked):
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    assert len(await rows(sf)) == 1
    assert (await admin_client.delete("/api/agents/hello-world")).status_code == 200
    assert await rows(sf) == []


async def test_rollback_restores_the_mode_only(admin_client, sf, hooked):
    """docs/design/16: a rollback to `secret` with no live hash lands on the
    fail-closed case, never on a resurrected secret."""
    await admin_client.put("/api/agents/hello-world/webhooks/hello-world/secret",
                           json={"secret": SECRET})
    # v1: mode secret, with a live hash. v2: back to none, hash cleared.
    await admin_client.put("/api/agents/hello-world",
                           json=a_def("hello-world", prompt="v1",
                                      entrypoints=ep("hello-world")))
    await admin_client.put("/api/agents/hello-world",
                           json=a_def("hello-world", prompt="v2",
                                      entrypoints=ep("hello-world", auth="none")))
    await admin_client.delete("/api/agents/hello-world/webhooks/hello-world/secret")
    r = await admin_client.post("/api/agents/hello-world/rollback/1")
    assert r.status_code == 200
    assert r.json()["entrypoints"]["webhooks"][0]["auth"] == "secret"
    assert r.json()["entrypoints"]["webhooks"][0]["secret_set"] is False
    assert await rows(sf) == []


async def test_rollback_cannot_resurrect_another_agents_path(admin_client, sf,
                                                             seed_agent, agent_store):
    """Since design/16 the path is the AUTH ROUTING key: whoever owns a path
    owns which secret guards it. So a rollback gets the same cross-agent
    uniqueness check a normal save gets — the exact sequence being A declares a
    path, A drops it, B claims it, A rolls back."""
    await seed_agent("hello-world", entrypoints=ep())
    await seed_agent("rival", entrypoints=ep())
    await agent_store.reload()
    # v1: hello-world owns `shared`. v2: it lets go.
    assert (await admin_client.put("/api/agents/hello-world",
                                   json=a_def("hello-world", prompt="v1",
                                              entrypoints=ep("shared")))).status_code == 200
    assert (await admin_client.put("/api/agents/hello-world",
                                   json=a_def("hello-world", prompt="v2",
                                              entrypoints=ep()))).status_code == 200
    # rival claims it and sets its own secret.
    assert (await admin_client.put("/api/agents/rival",
                                   json=a_def("rival", entrypoints=ep("shared")))).status_code == 200
    assert (await admin_client.put("/api/agents/rival/webhooks/shared/secret",
                                   json={"secret": SECRET})).status_code == 200

    r = await admin_client.post("/api/agents/hello-world/rollback/1")
    assert r.status_code == 422
    assert "shared" in r.text and "rival" in r.text and "hello-world" in r.text
    # rival still owns the path, and its secret is untouched.
    rival = (await admin_client.get("/api/agents/rival")).json()
    assert rival["entrypoints"]["webhooks"][0]["secret_set"] is True
    assert [(w.agent, w.path) for w in await rows(sf)] == [("rival", "shared")]


# --- the mode is part of the definition --------------------------------------

def test_unknown_auth_mode_rejected():
    from pydantic import ValidationError

    from agentplatform.agentdefs import AgentDefModel
    with pytest.raises(ValidationError):
        AgentDefModel(name="news", entrypoints={"webhooks": [{"path": "p",
                                                              "auth": "mtls"}]})


def test_auth_defaults_to_none():
    from agentplatform.agentdefs import AgentDefModel
    m = AgentDefModel(name="news", entrypoints={"webhooks": [{"path": "p"}]})
    assert m.entrypoints.webhooks[0].auth == "none"
