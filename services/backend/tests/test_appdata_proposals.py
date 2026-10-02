"""Proposals (design 39, "The authority model" → "Always proposals",
"Proposals"; revision 3 "Proposals"): a widening bundle, a wider rollback or an
ownership transfer is frozen, content-addressed, and published by Kyle's
approval atomically, or marked stale if the App moved.

Every test runs on SQLite; with AP_TEST_PG_URL naming a scratch database they
run on Postgres too.
"""
import os
import uuid

import pytest
from sqlalchemy import select

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import proposals as P
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.models import (AppDataApp, AppDataBuildOp, AppDataDefinition,
                                          AppDataProposal)
from agentplatform.appdata.quotas import describe, set_quota
from agentplatform.appdata.records import create_record, load_app
from agentplatform.db import (AgentDef, Base, Conversation, RelayMessage, RelayParticipant,
                              make_engine, make_session_factory)

PG_URL = os.environ.get("AP_TEST_PG_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])
TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("app_data_")] \
    + [AgentDef.__table__]

PAI = Actor("agent:pai", run_id="run1")
BOB = Actor("agent:bob")
KYLE = Actor("kyle")


@pytest.fixture(params=BACKENDS)
async def engine(request, tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/appdata.db" if request.param == "sqlite" \
        else PG_URL
    e = make_engine(url)
    async with e.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(sc, tables=TABLES))
        if request.param == "postgres":
            await conn.exec_driver_sql("TRUNCATE " + ", ".join(t.name for t in TABLES))
    yield e
    await e.dispose()


@pytest.fixture
def sf(engine):
    return make_session_factory(engine)


def rid() -> str:
    return uuid.uuid4().hex


def habits(**extra):
    body = {"collection": "habits",
            "fields": {"habit": {"type": "string", "required": True, "max": 40},
                       "day": {"type": "date", "required": True},
                       "done": {"type": "bool"}}}
    body.update(extra)
    return body


SHARED = {"read": ["owner", "kyle", "agent:bob"]}
RECENT = {"view": "recent", "collection": "habits", "sort": [{"field": "day", "dir": "desc"}]}


async def refused(code, awaitable):
    with pytest.raises(RecordError) as caught:
        await awaitable
    assert caught.value.code == code, caught.value
    return caught.value


async def new_app(sf, actor=PAI):
    async with sf() as s:
        out = await L.create(s, actor, request_id=rid(), name=f"a{rid()[:10]}")
    return out["app_id"]


async def draft(sf, app_id, kind, body, actor=PAI, **kw):
    async with sf() as s:
        return await L.draft(s, actor, app_id, request_id=rid(), kind=kind, definition=body,
                             **kw)


async def publish(sf, app_id, expected, actor=PAI, **kw):
    async with sf() as s:
        return await L.publish(s, actor, app_id, request_id=rid(),
                               expected_approved_version=expected, **kw)


async def built(sf, *defs, actor=PAI):
    app_id = await new_app(sf, actor)
    for kind, body in defs:
        await draft(sf, app_id, kind, body, actor=actor)
    await publish(sf, app_id, None, actor=actor)
    return app_id


async def add_record(sf, app_id, collection, values, who=Caller("agent:pai")):
    async with sf() as s:
        ctx = await load_app(s, app_id)
        return await create_record(s, ctx, who, collection, values)


async def propose(sf, app_id, actor=PAI, request_id=None, **kw):
    async with sf() as s:
        return await P.propose(s, actor, app_id, request_id=request_id or rid(), **kw)


async def approve(sf, proposal_id, digest, actor=KYLE):
    async with sf() as s:
        return await P.approve(s, actor, proposal_id, request_id=rid(), digest=digest)


async def app_row(sf, app_id) -> AppDataApp:
    async with sf() as s:
        return await s.get(AppDataApp, app_id)


async def approved(sf, app_id, actor=PAI) -> dict:
    async with sf() as s:
        got = await L.get_app(s, actor, app_id)
    return {(d["kind"], d["name"]): d["definition"] for d in got["approved"]}


async def proposal(sf, proposal_id, actor=PAI) -> dict:
    async with sf() as s:
        return await P.get(s, actor, proposal_id)


async def shared_draft(sf):
    """An App at version 1 with a draft that shares habits with bob."""
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "collection", habits(access=SHARED), expected_revision=0)
    return app_id


async def test_proposal_relay_card_is_single_editable_notice(sf, engine, monkeypatch):
    async with engine.begin() as conn:
        await conn.run_sync(lambda sc: Base.metadata.create_all(
            sc, tables=[Conversation.__table__, RelayParticipant.__table__,
                        RelayMessage.__table__]))
    app_id = await shared_draft(sf)
    p = await propose(sf, app_id)
    real_post = P.post_relay_message

    async def unavailable(*args, **kwargs):
        raise RuntimeError("relay unavailable")

    monkeypatch.setattr(P, "post_relay_message", unavailable)
    await P.notify(sf, None, p["id"])
    assert (await proposal(sf, p["id"]))["state"] == "open"
    monkeypatch.setattr(P, "post_relay_message", real_post)
    await P.notify(sf, None, p["id"])
    await P.notify(sf, None, p["id"])
    async with sf() as s:
        messages = list((await s.execute(select(RelayMessage))).scalars())
        assert len(messages) == 1
        card = messages[0].card
        assert card["type"] == "app_proposal" and card["state"] == "open"
        assert card["url"] == f"/apps/state/{app_id}/proposals/{p['id']}"
        assert "actions" not in card and "Approve" not in messages[0].body
        message_id = messages[0].id
    await approve(sf, p["id"], p["digest"])
    await P.notify(sf, None, p["id"])
    async with sf() as s:
        messages = list((await s.execute(select(RelayMessage))).scalars())
        assert len(messages) == 1 and messages[0].id == message_id
        assert messages[0].card["state"] == "published"


# --- propose ----------------------------------------------------------------------------

async def test_a_widening_bundle_is_proposed_not_published(sf):
    app_id = await shared_draft(sf)
    async with sf() as s:
        await refused("AL-NEEDS-PROPOSAL", L.publish(s, PAI, app_id, request_id=rid(),
                                                     expected_approved_version=1))
    out = await propose(sf, app_id, reason="share with bob")
    assert out["state"] == "open" and out["kind"] == "bundle"
    assert len(out["digest"]) == 64 and out["base_version"] == 1
    assert any("agent:bob can read habits" in w for w in out["delta"]["widening"])
    got = await proposal(sf, out["id"])
    assert got["digest"] == out["digest"] and got["proposer"] == "pai"
    assert got["reason"] == "share with bob" and got["run_id"] == "run1"
    assert got["bundle"]["changes"] == [{"kind": "collection", "name": "habits",
                                         "definition": habits(access=SHARED)}]
    assert (await app_row(sf, app_id)).approved_version == 1


async def test_a_self_publishing_bundle_is_refused_with_use_publish(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "view", RECENT)
    err = await refused("AL-SELF-PUBLISHES", propose(sf, app_id))
    assert err.detail["suggest"] == "publish"


async def test_propose_refuses_errors_and_a_stale_base(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "view", {"view": "orphan", "collection": "missing"})
    await refused("AL-INVALID", propose(sf, app_id))
    async with sf() as s:
        await L.draft(s, PAI, app_id, request_id=rid(), kind="view", name="orphan",
                      discard=True, expected_revision=1)
    # A draft written against a version older than habits' published one.
    await draft(sf, app_id, "collection", habits(access=SHARED), expected_revision=0)
    async with sf() as s:
        row = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app_id, AppDataDefinition.state == "draft"))
        ).scalar_one()
        row.base_version = 0
        await s.commit()
    await refused("AL-STALE-BASE", propose(sf, app_id))


async def test_only_the_owner_proposes(sf):
    app_id = await shared_draft(sf)
    await refused("AL-NOT-OWNER", propose(sf, app_id, actor=BOB))


async def test_propose_needs_something_to_propose(sf):
    app_id = await built(sf, ("collection", habits()))
    await refused("AL-NOTHING-TO-PROPOSE", propose(sf, app_id))
    await refused("AL-ARGS", propose(sf, app_id, rollback_to=1, transfer_to="kyle"))


async def test_a_replayed_request_id_returns_the_stored_receipt(sf):
    app_id = await shared_draft(sf)
    request_id = rid()
    first = await propose(sf, app_id, request_id=request_id)
    again = await propose(sf, app_id, request_id=request_id)
    assert again["id"] == first["id"] and again["replayed"] is True
    async with sf() as s:
        assert len((await s.execute(select(AppDataProposal))).scalars().all()) == 1
    await refused("AL-REQUEST-REUSED", propose(sf, app_id, request_id=request_id,
                                               reason="other"))


# --- approve ----------------------------------------------------------------------------

async def test_approval_publishes_exactly_the_frozen_bundle(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    generation = (await app_row(sf, app_id)).authority_generation
    # The draft moves on after proposing; the proposal doesn't.
    edited = habits(access={"read": ["owner", "kyle", "agent:bob", "agent:eve"]})
    await draft(sf, app_id, "collection", edited, expected_revision=1)
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "published" and done["approved_version"] == 2
    state = await approved(sf, app_id)
    assert state[("collection", "habits")]["access"] == SHARED
    app = await app_row(sf, app_id)
    assert app.approved_version == 2 and app.authority_generation == generation + 1
    async with sf() as s:
        row = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app_id, AppDataDefinition.version == 2))
        ).scalar_one()
        # The proposer stays the author; Kyle's approval is stamped (D11).
        assert (row.author, row.approved_by, row.proposal_id, row.run_id) == (
            "agent:pai", "kyle", out["id"], "run1")
        # The edited draft wasn't what published, so it stays for the builder.
        left = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app_id, AppDataDefinition.state == "draft"))
        ).scalars().all()
        assert [d.body for d in left] == [edited]
    got = await proposal(sf, out["id"])
    assert got["state"] == "published" and got["decided_by"] == "kyle"
    assert got["decided_at"] is not None


async def test_approval_clears_the_drafts_it_published(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await approve(sf, out["id"], out["digest"])
    async with sf() as s:
        got = await L.get_app(s, PAI, app_id)
    assert got["drafts"] == []


async def test_a_wrong_digest_is_refused(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await refused("AL-PROPOSAL-DIGEST", approve(sf, out["id"], "0" * 64))
    await refused("AL-PROPOSAL-DIGEST", approve(sf, out["id"], None))
    assert (await proposal(sf, out["id"]))["state"] == "open"
    assert (await app_row(sf, app_id)).approved_version == 1


async def test_only_kyle_approves_or_declines(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await refused("AL-NOT-KYLE", approve(sf, out["id"], out["digest"], actor=PAI))
    async with sf() as s:
        await refused("AL-NOT-KYLE", P.decline(s, PAI, out["id"], request_id=rid()))
    via_tool = Actor("kyle", via_tool="tool:x")
    await refused("AL-NOT-KYLE", approve(sf, out["id"], out["digest"], actor=via_tool))
    assert (await proposal(sf, out["id"]))["state"] == "open"


async def test_a_publish_between_propose_and_approve_makes_it_stale(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "collection", habits(access=SHARED), expected_revision=0)
    out = await propose(sf, app_id)
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1, only=[{"kind": "view", "name": "recent"}])
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "stale" and done["published"] is False
    assert done["why"]
    assert (await app_row(sf, app_id)).approved_version == 2
    state = await approved(sf, app_id)
    assert "access" not in state[("collection", "habits")]
    assert (await proposal(sf, out["id"]))["state"] == "stale"


async def test_a_rollback_between_propose_and_approve_makes_it_stale(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1)
    await draft(sf, app_id, "collection", habits(access=SHARED), expected_revision=0)
    out = await propose(sf, app_id)
    async with sf() as s:
        await L.rollback(s, PAI, app_id, request_id=rid(), to_version=1,
                         expected_approved_version=2)
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "stale"
    assert (await app_row(sf, app_id)).approved_version == 3


async def test_a_record_that_now_breaks_a_rule_makes_it_stale(sf):
    app_id = await built(sf, ("collection", habits()))
    # Shares habits and adds a unique rule; no record breaks it yet.
    await draft(sf, app_id, "collection",
                habits(access=SHARED, rules=[{"kind": "unique", "fields": ["habit", "day"]}]),
                expected_revision=0)
    out = await propose(sf, app_id)
    for _ in range(2):
        await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "stale"
    assert any("share values" in w for w in done["why"])
    assert (await app_row(sf, app_id)).approved_version == 1


async def test_an_owner_change_makes_it_stale(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    async with sf() as s:
        await L.transfer_owned_apps(s, "pai")
        await s.commit()
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "stale"


@pytest.mark.parametrize("first", ["approve", "decline", "withdraw"])
async def test_approve_decline_and_withdraw_are_each_final(sf, first):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    async with sf() as s:
        if first == "approve":
            await P.approve(s, KYLE, out["id"], request_id=rid(), digest=out["digest"])
        elif first == "decline":
            done = await P.decline(s, KYLE, out["id"], request_id=rid(), reason="no")
            assert done["state"] == "declined"
        else:
            done = await P.withdraw(s, PAI, out["id"], request_id=rid())
            assert done["state"] == "withdrawn"
    async with sf() as s:
        await refused("AL-PROPOSAL-CLOSED", P.approve(s, KYLE, out["id"], request_id=rid(),
                                                      digest=out["digest"]))
        await refused("AL-PROPOSAL-CLOSED", P.decline(s, KYLE, out["id"], request_id=rid()))
        await refused("AL-PROPOSAL-CLOSED", P.withdraw(s, PAI, out["id"], request_id=rid()))
    got = await proposal(sf, out["id"], actor=KYLE)
    assert got["state"] == {"approve": "published", "decline": "declined",
                            "withdraw": "withdrawn"}[first]


async def test_a_stale_proposal_is_final(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1, only=[{"kind": "view", "name": "recent"}])
    assert (await approve(sf, out["id"], out["digest"]))["state"] == "stale"
    await refused("AL-PROPOSAL-CLOSED", approve(sf, out["id"], out["digest"]))


async def test_get_and_withdraw_are_for_the_proposer_owner_and_kyle(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await refused("AL-NOT-OWNER", proposal(sf, out["id"], actor=BOB))
    assert (await proposal(sf, out["id"], actor=KYLE))["id"] == out["id"]
    async with sf() as s:
        await refused("AL-NOT-OWNER", P.withdraw(s, BOB, out["id"], request_id=rid()))
    await refused("AL-NO-PROPOSAL", proposal(sf, "nope"))


async def test_the_proposer_withdraws_after_the_app_moves_to_kyle(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    async with sf() as s:
        await L.transfer_owned_apps(s, "pai")
        await s.commit()
    async with sf() as s:
        done = await P.withdraw(s, PAI, out["id"], request_id=rid())
    assert done["state"] == "withdrawn"


async def test_list_proposals_filters_by_state(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    async with sf() as s:
        assert [p["id"] for p in await P.list_proposals(s, PAI, app_id)] == [out["id"]]
        assert await P.list_proposals(s, PAI, app_id, state="published") == []
        await refused("AL-NOT-OWNER", P.list_proposals(s, BOB, app_id))


# --- data dropping and rollbacks --------------------------------------------------------

async def test_a_data_dropping_change_goes_through_a_proposal(sf):
    body = habits()
    body["fields"]["mood"] = {"type": "string"}
    app_id = await built(sf, ("collection", body))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30",
                                             "mood": "ok"})
    await draft(sf, app_id, "collection", habits(), expected_revision=0)
    async with sf() as s:
        await refused("AL-NEEDS-PROPOSAL", L.publish(s, PAI, app_id, request_id=rid(),
                                                     expected_approved_version=1))
    out = await propose(sf, app_id)
    assert out["validation"]["data_dropping"] == ["habits.mood holds data in 1 record"]
    # Another record holding mood: more of the same data drops, which isn't a
    # new kind of change, so the proposal still stands.
    await add_record(sf, app_id, "habits", {"habit": "swim", "day": "2026-09-30",
                                             "mood": "ok"})
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "published"
    assert "mood" not in (await approved(sf, app_id))[("collection", "habits")]["fields"]


async def test_a_wider_rollback_goes_through_a_proposal(sf):
    app_id = await shared_draft(sf)
    first = await propose(sf, app_id)
    await approve(sf, first["id"], first["digest"])
    await draft(sf, app_id, "collection", habits(), expected_revision=0)
    await publish(sf, app_id, 2)
    async with sf() as s:
        await refused("AL-NEEDS-PROPOSAL", L.rollback(
            s, PAI, app_id, request_id=rid(), to_version=2, expected_approved_version=3))
    out = await propose(sf, app_id, rollback_to=2)
    assert out["kind"] == "rollback" and out["bundle"]["rollback_to"] == 2
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "published" and done["approved_version"] == 4
    assert (await approved(sf, app_id))[("collection", "habits")]["access"] == SHARED


async def test_a_narrowing_rollback_is_refused_with_use_rollback(sf):
    app_id = await built(sf, ("collection", habits()))
    await draft(sf, app_id, "view", RECENT)
    await publish(sf, app_id, 1)
    err = await refused("AL-SELF-PUBLISHES", propose(sf, app_id, rollback_to=1))
    assert err.detail["suggest"] == "rollback"
    await refused("AL-ROLLBACK-TARGET", propose(sf, app_id, rollback_to=5))


# --- transfer ---------------------------------------------------------------------------

async def grant(sf, agent, tools):
    async with sf() as s:
        s.add(AgentDef(name=agent, platform_tools=tools))
        await s.commit()


async def test_a_transfer_changes_the_owner_and_moves_quotas(sf):
    await grant(sf, "bob", ["mcp__platform__apps"])
    app_id = await built(sf, ("collection", habits()))
    await add_record(sf, app_id, "habits", {"habit": "run", "day": "2026-09-30"})
    async with sf() as s:
        before = (await describe(s, "owner", "agent:pai"))["used"]["records"]
    assert before == 1
    out = await propose(sf, app_id, transfer_to="agent:bob")
    assert out["kind"] == "transfer"
    assert any("bob" in line for line in out["delta"]["widening"])
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "published" and done["owner"] == "agent:bob"
    app = await app_row(sf, app_id)
    assert (app.owner_kind, app.owner_id) == ("agent", "bob")
    async with sf() as s:
        assert (await describe(s, "owner", "agent:pai"))["used"]["records"] == 0
        assert (await describe(s, "owner", "agent:bob"))["used"]["records"] == 1
    await refused("AL-NOT-OWNER", approved(sf, app_id, actor=PAI))


async def test_a_transfer_to_kyle_needs_no_grant(sf):
    app_id = await built(sf, ("collection", habits()))
    out = await propose(sf, app_id, transfer_to="kyle")
    done = await approve(sf, out["id"], out["digest"])
    assert done["owner"] == "kyle"
    assert (await app_row(sf, app_id)).owner_kind == "kyle"


async def test_a_transfer_needs_an_apps_holder_other_than_the_owner(sf):
    await grant(sf, "eve", ["mcp__platform__app_data"])
    app_id = await built(sf, ("collection", habits()))
    await refused("AL-TRANSFER-TARGET", propose(sf, app_id, transfer_to="agent:eve"))
    await refused("AL-TRANSFER-TARGET", propose(sf, app_id, transfer_to="agent:ghost"))
    await refused("AL-TRANSFER-TARGET", propose(sf, app_id, transfer_to="agent:pai"))
    await refused("AL-TRANSFER-TARGET", propose(sf, app_id, transfer_to="tool:x"))


async def test_a_transfer_goes_stale_when_the_target_loses_apps(sf):
    await grant(sf, "bob", ["mcp__platform__apps"])
    app_id = await built(sf, ("collection", habits()))
    out = await propose(sf, app_id, transfer_to="agent:bob")
    async with sf() as s:
        (await s.get(AgentDef, "bob")).platform_tools = []
        await s.commit()
    done = await approve(sf, out["id"], out["digest"])
    assert done["state"] == "stale"
    assert (await app_row(sf, app_id)).owner_id == "pai"


# --- quota ------------------------------------------------------------------------------

async def test_the_proposal_quota_fails_closed(sf):
    app_id = await shared_draft(sf)
    async with sf() as s:
        await set_quota(s, "app", app_id, {"max_open_proposals": 1}, set_by="kyle",
                        is_kyle=True)
    first = await propose(sf, app_id)
    err = await refused("AD-QUOTA-PROPOSALS", propose(sf, app_id))
    assert err.status == 413
    # Withdrawing frees the slot.
    async with sf() as s:
        await P.withdraw(s, PAI, first["id"], request_id=rid())
    assert (await propose(sf, app_id))["state"] == "open"


async def test_the_owner_proposal_quota_counts_across_apps(sf):
    one = await shared_draft(sf)
    two = await shared_draft(sf)
    async with sf() as s:
        await set_quota(s, "owner", "agent:pai", {"max_open_proposals": 1}, set_by="kyle",
                        is_kyle=True)
    await propose(sf, one)
    await refused("AD-QUOTA-PROPOSALS", propose(sf, two))


async def test_every_write_is_a_build_op(sf):
    app_id = await shared_draft(sf)
    out = await propose(sf, app_id)
    await approve(sf, out["id"], out["digest"])
    async with sf() as s:
        ops = {(o.principal, o.op) for o in (await s.execute(select(AppDataBuildOp))
                                              ).scalars().all()}
    assert ("agent:pai", "propose") in ops and ("kyle", "proposal_approve") in ops
