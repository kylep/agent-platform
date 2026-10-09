"""Room watchers (docs/design/41): unaddressed human posts in a watched room
open a round, and each watcher takes one ordered turn."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from agentplatform import external_chat as chat
from agentplatform import room_watchers as rw
from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import (
    AgentDef,
    ChatIdentity,
    Conversation,
    RelayInvocation,
    Run,
    RunState,
    WatchRound,
    WatchTurn,
    utcnow,
)
from agentplatform.events import TOPIC_RELAY_MESSAGES
from agentplatform.relay_router import RelayRouter
from agentplatform.relay_store import relay_message_payload
from sqlalchemy import select

PERSONAS = (("pai", "11"), ("kai", "22"), ("olu", "33"))


@pytest.fixture
def world(sf, producer, seed_agent):
    async def _make(*, send=True, **settings):
        for name, _ in PERSONAS:
            await seed_agent(name, description="t")
        async with sf() as s:
            for name, bot_id in PERSONAS:
                (await s.get(AgentDef, name)).agent_type = "persona"
                s.add(ChatIdentity(id=f"discord-{name}", connector="discord",
                                   display_name=name, owner_agent=name, provider_user_id=bot_id))
            await s.commit()
        for name, _ in PERSONAS:
            async with sf() as s:
                await chat.snapshot(s, f"discord-{name}", 0, 1, [{
                    "external_ref": "123", "kind": "channel", "can_read": True,
                    "can_history": True, "can_send": send}])
                await s.commit()
        store = AgentStore(sf)
        await store.reload()
        return RelayRouter(Settings(**settings), sf, producer, store)
    return _make


async def _room(sf):
    async with sf() as s:
        ep = (await s.execute(select(chat.ExternalEndpoint).where(
            chat.ExternalEndpoint.external_ref == "123"))).scalar_one()
        return await s.get(Conversation, ep.channel_id)


async def _watch(sf, agents):
    async with sf() as s:
        conv = await _room(sf)
        conv = await s.get(Conversation, conv.id)
        warnings = await rw.set_watchers(s, conv, list(agents), by="test")
        await s.commit()
        return warnings


async def _post(router, sf, text, pid, *, who=("pai", "kai", "olu"), addressed=(), mentioned=(),
                author="human", bot=False):
    """One Discord post, reported by each listed bot's connector, routed."""
    msg_id = None
    for name in who:
        data = {"external_ref": "123", "provider_message_id": pid, "author_id": author,
                "text": text, "addressed": name in addressed, "author_bot": bot,
                "mentioned_bot_ids": list(mentioned)}
        async with sf() as s:
            ep, msg, obs = await chat.observe(s, f"discord-{name}", 0, data)
            conv = await s.get(Conversation, ep.channel_id)
            payload = {**relay_message_payload(msg, conv), "external_observation_id": obs.id}
            await s.commit()
            msg_id = msg.id
        await router._on_message(SimpleNamespace(topic=TOPIC_RELAY_MESSAGES), payload)
    return msg_id


async def _runs(sf):
    async with sf() as s:
        return list((await s.execute(select(Run).order_by(Run.created_at))).scalars())


async def _turns(sf):
    async with sf() as s:
        return [(t.agent, t.state, t.outcome) for t in (await s.execute(
            select(WatchTurn).order_by(WatchTurn.round_id, WatchTurn.position))).scalars()]


async def _end(sf, run_id, *, state=RunState.SUCCEEDED, delivery="accepted", decline=False,
               delivery_age=0):
    """What the recorder and connector do when a watcher's run ends."""
    async with sf() as s:
        run = await s.get(Run, run_id)
        run.state, run.finished_at, run.reply_published_at = state, utcnow(), utcnow()
        if decline:
            assert await rw.record_decline(s, run, "NO_REPLY.")
        elif delivery:
            s.add(chat.ExternalDelivery(identity_id=f"discord-{run.agent}", endpoint_id="x",
                  agent=run.agent, run_id=run.id, authorization_generation=0,
                  ownership_generation=0, answer_to=run.trigger_message_id, state=delivery,
                  created_at=utcnow() - timedelta(seconds=delivery_age)))
        await s.commit()


# --- configuration ---------------------------------------------------------

async def test_watchers_are_ordered_and_switch_dispatch_mode(world, sf):
    await world()
    assert await _watch(sf, ["pai", "kai", "olu"]) == []
    async with sf() as s:
        conv = await _room(sf)
        assert conv.dispatch_mode == "watchers"
        assert await rw.list_watchers(s, conv.id) == ["pai", "kai", "olu"]
    await _watch(sf, ["olu", "pai"])
    async with sf() as s:
        assert await rw.list_watchers(s, (await _room(sf)).id) == ["olu", "pai"]
    await _watch(sf, [])
    assert (await _room(sf)).dispatch_mode == "mentions"


async def test_watchers_are_validated(world, sf, seed_agent):
    await world(send=False)
    with pytest.raises(rw.WatcherConfigError):
        await _watch(sf, ["pai", "pai"])
    await seed_agent("worker", description="t")
    with pytest.raises(rw.WatcherConfigError):
        await _watch(sf, ["worker"])            # no identity in the room
    assert await _watch(sf, ["pai"]) == ["pai cannot currently send in this room"]


async def test_watchers_api_is_admin_only(world, sf, admin_client, token_client):
    from .test_relay_api import _agent_token, _human_token
    await world()
    cid = (await _room(sf)).id
    r = await admin_client.put(f"/api/relay/channels/{cid}/watchers", json={"agents": ["kai"]})
    assert r.status_code == 200 and r.json()["agents"] == ["kai"]
    assert r.json()["dispatch_mode"] == "watchers"
    for headers in (await _agent_token(sf, "kai", role="operator"),
                    await _human_token(sf, "someone", "operator")):
        r = await token_client.put(f"/api/relay/channels/{cid}/watchers",
                                   json={"agents": []}, headers=headers)
        assert r.status_code == 403, r.text
    assert (await admin_client.get(f"/api/relay/channels/{cid}/watch-turns")).json() == []


# --- classification --------------------------------------------------------

async def test_an_unaddressed_human_post_opens_one_round_with_ordered_turns(world, sf):
    router = await world()
    await _watch(sf, ["pai", "kai", "olu"])
    await _post(router, sf, "what do you all think?", "m1")
    async with sf() as s:
        assert len((await s.execute(select(WatchRound))).scalars().all()) == 1
    assert await _turns(sf) == [("pai", "running", ""), ("kai", "pending", ""), ("olu", "pending", "")]
    runs = await _runs(sf)
    assert [r.agent for r in runs] == ["pai"]
    assert "watcher 1 of 3" in runs[0].prompt and "NO_REPLY" in runs[0].prompt
    async with sf() as s:
        reasons = [r.reason for r in (await s.execute(select(RelayInvocation))).scalars()]
    assert reasons == ["watch"]


async def test_bot_posts_and_addressed_posts_never_open_a_round(world, sf):
    router = await world()
    await _watch(sf, ["pai", "kai", "olu"])
    await _post(router, sf, "status update", "b1", author="bot", bot=True)
    # @Kai: Kai's connector sees it addressed; the others see Kai's id mentioned.
    await _post(router, sf, "@Kai thoughts?", "m2", addressed=("kai",), mentioned=("22",))
    # a reply-ping to Pai: no written mention, but the connector reports Pai's bot
    await _post(router, sf, "agreed", "m3", addressed=("pai",), mentioned=("11",))
    assert await _turns(sf) == []
    assert [r.agent for r in await _runs(sf)] == ["kai", "pai"]   # the addressed path only


async def test_an_unwatched_room_ignores_unaddressed_posts(world, sf):
    router = await world()
    await _post(router, sf, "anyone?", "m1")
    assert await _turns(sf) == [] and await _runs(sf) == []


# --- ordering, decline, failure --------------------------------------------

async def test_turns_run_in_order_and_later_watchers_see_earlier_replies(world, sf):
    router = await world()
    await _watch(sf, ["pai", "kai", "olu"])
    await _post(router, sf, "what do you all think?", "m1")
    first = (await _runs(sf))[0]
    await _end(sf, first.id, delivery="pending")          # posted, not yet confirmed
    await router._advance_watch(None)
    assert [r.agent for r in await _runs(sf)] == ["pai"]   # waits for the delivery
    async with sf() as s:
        (await s.execute(select(chat.ExternalDelivery))).scalar_one().state = "accepted"
        await s.commit()
    await _post(router, sf, "Pai's answer", "p1", author="pai-bot", bot=True)
    await router._advance_watch(None)
    runs = await _runs(sf)
    assert [r.agent for r in runs] == ["pai", "kai"]
    assert "watcher 2 of 3" in runs[1].prompt and "Pai's answer" in runs[1].prompt
    await _end(sf, runs[1].id, decline=True)
    await router._advance_watch(None)
    olu = (await _runs(sf))[2]
    await _end(sf, olu.id, state=RunState.FAILED, delivery=None)
    await router._advance_watch(None)
    assert await _turns(sf) == [("pai", "done", "answered"), ("kai", "done", "declined"),
                                ("olu", "done", "failed")]
    async with sf() as s:
        assert (await s.execute(select(WatchRound))).scalar_one().state == "done"


async def test_an_unresolved_or_refused_delivery_does_not_stall_the_room(world, sf):
    router = await world(relay_watch_delivery_seconds=60)
    await _watch(sf, ["pai", "kai"])
    await _post(router, sf, "hello?", "m1")
    await _end(sf, (await _runs(sf))[0].id, delivery="pending", delivery_age=120)
    await router._advance_watch(None)
    kai = (await _runs(sf))[1]
    await _end(sf, kai.id, delivery="failed")
    await router._advance_watch(None)
    assert await _turns(sf) == [("pai", "done", "delivery_failed"), ("kai", "done", "delivery_failed")]


async def test_a_watcher_whose_connector_never_reports_is_skipped(world, sf):
    router = await world(relay_watch_observation_seconds=0)
    await _watch(sf, ["pai", "kai"])
    await _post(router, sf, "hello?", "m1", who=("kai",))   # Pai's bot never saw it
    await router._advance_watch(None)
    assert await _turns(sf) == [("pai", "done", "skipped_no_observation"), ("kai", "running", "")]


async def test_a_burst_joins_the_open_round(world, sf):
    router = await world()
    await _watch(sf, ["pai", "kai"])
    await _post(router, sf, "first", "m1")
    await _post(router, sf, "second", "m2")
    await _post(router, sf, "third", "m3")
    async with sf() as s:
        rounds = (await s.execute(select(WatchRound))).scalars().all()
    assert len(rounds) == 1 and len(await _runs(sf)) == 1
    await _end(sf, (await _runs(sf))[0].id)
    await router._advance_watch(None)
    kai = (await _runs(sf))[1]
    assert "third" in kai.prompt and "second" in kai.prompt
    await _end(sf, kai.id)
    await router._advance_watch(None)
    await _post(router, sf, "a new topic", "m4")
    async with sf() as s:
        assert len((await s.execute(select(WatchRound))).scalars().all()) == 2


async def test_a_removed_watcher_is_skipped_and_reorder_applies_to_new_rounds(world, sf):
    router = await world()
    await _watch(sf, ["pai", "kai"])
    await _post(router, sf, "hello?", "m1")
    await _watch(sf, ["pai"])
    await _end(sf, (await _runs(sf))[0].id)
    await router._advance_watch(None)
    assert await _turns(sf) == [("pai", "done", "answered"), ("kai", "done", "skipped_removed")]


async def test_the_watch_budget_is_separate_from_mentions(world, sf):
    router = await world(relay_watch_turns_per_room_hour=1)
    await _watch(sf, ["pai", "kai"])
    await _post(router, sf, "hello?", "m1")
    await _end(sf, (await _runs(sf))[0].id)
    await router._advance_watch(None)
    assert ("kai", "done", "skipped_budget") in await _turns(sf)
    await _post(router, sf, "@Kai still there?", "m2", addressed=("kai",), mentioned=("22",))
    assert (await _runs(sf))[-1].agent == "kai"


# --- decline matching and the delivery fence -------------------------------

def test_decline_matching_is_tolerant_but_exact():
    for text in ("NO_REPLY", "NO_REPLY.", "`no_reply`", "  **NO_REPLY**\n"):
        assert rw.is_decline(text)
    for text in ("NO_REPLY because nothing to add", "no reply", "", None):
        assert not rw.is_decline(text)


async def test_decline_only_applies_to_a_running_watch_turn(world, sf):
    await world()
    async with sf() as s:
        run = Run(id="f" * 32, agent="pai", prompt="p", trigger="mention",
                  requested_by="t", state="running")
        s.add(run)
        await s.flush()
        assert not await rw.record_decline(s, run, "NO_REPLY")


async def test_a_watch_run_may_only_answer_its_own_post(world, sf):
    router = await world()
    await _watch(sf, ["pai"])
    msg_id = await _post(router, sf, "hello?", "m1")
    run = (await _runs(sf))[0]
    async with sf() as s:
        run = await s.get(Run, run.id)
        run.state, run.authorization_generation = "running", 0
        await s.flush()
        with pytest.raises(chat.ExternalChatError):
            await chat.queue_send(s, agent="pai", run_id=run.id, identity_id="discord-pai",
                                  external_ref="123", text="unprompted post")
        with pytest.raises(chat.ExternalChatError):
            await chat.queue_send(s, agent="pai", run_id=run.id, identity_id="discord-pai",
                                  external_ref="123", text="x", answer_to="0" * 32)
        row = await chat.queue_final(s, run, "Pai's answer")
        assert row is not None and row.answer_to == msg_id


async def test_the_sweep_skips_watched_rooms(world, sf):
    router = await world()
    await _post(router, sf, "anyone?", "m1")
    async with sf() as s:
        assert len(await chat.scan_batch(s, "pai", "discord-pai")) == 1
    await _watch(sf, ["pai"])
    async with sf() as s:
        assert await chat.scan_batch(s, "pai", "discord-pai") == []
