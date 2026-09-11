"""The router (docs/design/19, T7): who a message summons, and every reason it
summons nobody.

The guards are what make a room full of agents safe to leave running, so each
one is tested by driving a real message through `handle` rather than by calling
the guard: the interesting failures are in how they COMBINE (a busy agent at
the hop limit, a wake that fires into an over-budget hour), and only the whole
router has an opinion about that."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import (ACTIVE_STATES, Conversation, RelayInvocation,
                              RelayMessage, RelayParticipant, RelayWake, Run,
                              RunState, utcnow)
from agentplatform.events import TOPIC_RELAY_INVOCATIONS
from agentplatform.relay_router import BUDGET_PREFIX, HOP_LIMIT_BODY, RelayRouter
from agentplatform.relay_store import post_relay_message, relay_message_payload


@pytest.fixture
def make_router(sf, producer, seed_agent):
    """A router over a room's worth of agents. Settings are per-test because
    the guards ARE the settings: a cooldown of 0 is how a hop test stops being
    a cooldown test."""
    async def _make(*, agents=("ada", "bob"), **settings):
        for name in agents:
            await seed_agent(name, description="t")
        store = AgentStore(sf)
        await store.reload()
        return RelayRouter(Settings(**settings), sf, producer, store)
    return _make


async def _channel(sf, *, kind="channel", open=True, name=None, agent=None,
                   participants=()) -> str:
    """A room. The name is unique per call because `init_db` seeds the real
    #general and a channel slug is unique among channels."""
    name = name or f"room-{uuid.uuid4().hex[:8]}"
    async with sf() as s:
        conv = Conversation(connector="web", kind=kind, open=open, agent=agent,
                            name=name if kind == "channel" else None,
                            title=f"#{name}")
        s.add(conv)
        await s.flush()
        for p in participants:
            s.add(RelayParticipant(channel_id=conv.id, participant=p))
        await s.commit()
        return conv.id


async def _say(router, sf, channel_id: str, author: str, body: str, *,
               kind="text", hop=0, run_id=None, reply_to=None) -> str:
    """Post a message and route it, exactly as the API/recorder → Kafka →
    router path does. `mentions` is deliberately left empty: the router
    re-parses the body, and a test that pre-computed the list would be
    asserting the poster's parse rather than the router's."""
    async with sf() as s:
        conv = await s.get(Conversation, channel_id)
        msg = await post_relay_message(s, conv, author=author, body=body, kind=kind,
                                       hop=hop, run_id=run_id, reply_to=reply_to)
        await s.commit()
        payload = relay_message_payload(msg, conv)
    await router.handle(payload)
    return payload["id"]


async def _finish(sf, agent: str) -> None:
    """What the recorder does before an agent's reply lands: the run that was
    speaking is over, so the agent is free again."""
    async with sf() as s:
        for run in (await s.execute(select(Run).where(
                Run.agent == agent, Run.state.in_(ACTIVE_STATES)))
                ).scalars():
            run.state = RunState.SUCCEEDED
            run.finished_at = utcnow()
        await s.commit()


async def _decisions(sf, channel_id: str | None = None) -> list[tuple[str, str, str]]:
    async with sf() as s:
        rows = (await s.execute(select(RelayInvocation).order_by(
            RelayInvocation.created_at, RelayInvocation.id))).scalars().all()
    return [(r.agent, r.decision, r.reason) for r in rows
            if channel_id in (None, r.channel_id)]


async def _runs(sf) -> list[Run]:
    async with sf() as s:
        return list((await s.execute(select(Run).order_by(Run.created_at))).scalars())


async def _system(sf, channel_id: str) -> list[RelayMessage]:
    async with sf() as s:
        return list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == channel_id, RelayMessage.kind == "system")
            .order_by(RelayMessage.created_at))).scalars())


async def _wakes(sf) -> list[RelayWake]:
    async with sf() as s:
        return list((await s.execute(select(RelayWake))).scalars())


def _invocation_events(producer) -> list[dict]:
    return [d for t, _, d in producer.published if t == TOPIC_RELAY_INVOCATIONS]


# --- the ordinary case -------------------------------------------------------


async def test_a_human_mention_invokes_each_agent_at_hop_zero(make_router, sf, producer):
    router = await make_router()
    cid = await _channel(sf)
    mid = await _say(router, sf, cid, "user:admin", "@ada @bob please look at this")

    runs = await _runs(sf)
    assert sorted(r.agent for r in runs) == ["ada", "bob"]
    for run in runs:
        assert (run.trigger, run.depth, run.initiated_by) == ("mention", 0, "admin")
        assert run.requested_by == "user:admin" and run.parent_run_id is None
        assert run.conversation_id == cid and run.trigger_message_id == mid
        # The summoning text reaches the model only inside the untrusted block.
        block = run.prompt.split("<relay-messages ")[1].split("</relay-messages>")[0]
        assert "please look at this" in block
        assert run.user_message == run.prompt
    assert sorted(await _decisions(sf)) == [("ada", "invoked", "mention"),
                                            ("bob", "invoked", "mention")]

    events = _invocation_events(producer)
    assert {(e["agent"], e["decision"], e["message_id"]) for e in events} == {
        ("ada", "invoked", mid), ("bob", "invoked", mid)}
    assert all(e["run_id"] and e["hop"] == 0 and e["channel_id"] == cid for e in events)
    assert [e["type"] for e in producer.envelopes].count("relay.invocation") == 2


@pytest.mark.parametrize("author, initiated_by", [
    ("user:admin", "admin"), ("discord:42", "discord:42")])
async def test_a_human_authors_own_principal_roots_the_chain(
        make_router, sf, author, initiated_by):
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, author, "@ada hello")
    run = (await _runs(sf))[0]
    assert (run.initiated_by, run.requested_by, run.depth) == (initiated_by, author, 0)


async def test_initiated_by_survives_the_chain(make_router, sf):
    """An agent invoking an agent does not become the root of the work: the
    human (or the bridge's user) it started with stays the principal."""
    router = await make_router()
    cid = await _channel(sf)
    async with sf() as s:
        s.add(Run(id="parent", agent="ada", trigger="mention", requested_by="discord:42",
                  initiated_by="discord:42", conversation_id=cid, prompt="p",
                  state=RunState.SUCCEEDED))
        await s.commit()
    await _say(router, sf, cid, "agent:ada", "@bob over to you", hop=1, run_id="parent")

    run = [r for r in await _runs(sf) if r.agent == "bob"][0]
    assert run.initiated_by == "discord:42" and run.parent_run_id == "parent"
    assert run.requested_by == "agent:ada" and run.depth == 1


async def test_an_orphaned_reply_falls_back_to_admin(make_router, sf):
    """The triggering run is gone (pruned, or never existed): the chain still
    has to name a principal, and the single-operator stub is the honest one."""
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "@bob over to you", hop=1, run_id="missing")
    assert [(r.agent, r.initiated_by) for r in await _runs(sf)] == [("bob", "admin")]


# --- who may be summoned at all ----------------------------------------------


async def test_nothing_is_summoned_without_a_mention(make_router, sf, producer):
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "just thinking out loud")
    assert await _runs(sf) == [] and await _decisions(sf) == []
    assert _invocation_events(producer) == []


async def test_an_agent_does_not_summon_itself(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "@ada note to self", hop=1,
               run_id=uuid.uuid4().hex)
    assert await _runs(sf) == [] and await _decisions(sf) == []


async def test_system_messages_never_summon(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "system:relay", f"{HOP_LIMIT_BODY} @ada", kind="system")
    assert await _runs(sf) == [] and await _decisions(sf) == []


async def test_an_agent_cannot_address_the_room(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "@all heads up", hop=1,
               run_id=uuid.uuid4().hex)
    assert await _runs(sf) == [] and await _decisions(sf) == []


async def test_a_human_at_all_invokes_every_agent_member(make_router, sf):
    """A closed room's `@all` is its own roster, not the platform's: an agent
    that is not in the group is not in the room."""
    router = await make_router(agents=("ada", "bob", "cy"))
    cid = await _channel(sf, kind="group", open=False,
                         participants=("user:admin", "agent:ada", "agent:bob"))
    await _say(router, sf, cid, "user:admin", "@all standup please")
    assert sorted(r.agent for r in await _runs(sf)) == ["ada", "bob"]
    assert sorted(await _decisions(sf)) == [("ada", "invoked", "mention"),
                                            ("bob", "invoked", "mention")]


@pytest.mark.parametrize("fields", [{"enabled": False}, {"role": "not-a-role"}])
async def test_a_disabled_or_quarantined_agent_is_never_invoked(
        make_router, sf, seed_agent, fields):
    """Membership reads the AgentStore's state, so an agent that is switched
    off — or whose row no longer validates — is as absent as one that was never
    in the room."""
    router = await make_router()
    await seed_agent("ada", description="t", **fields)
    await router.agents.reload()
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@ada @bob hello")
    assert [r.agent for r in await _runs(sf)] == ["bob"]
    assert await _decisions(sf) == [("bob", "invoked", "mention")]


# --- hops --------------------------------------------------------------------


async def test_an_agent_at_the_hop_limit_is_suppressed_and_said_once(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    root = await _say(router, sf, cid, "user:admin", "kick this off @ada")
    await _finish(sf, "ada")
    cap = router.settings.relay_max_hops
    for _ in range(2):
        await _say(router, sf, cid, "agent:ada", "@bob take it from here", hop=cap,
                   run_id=uuid.uuid4().hex, reply_to=root)

    assert [r.agent for r in await _runs(sf)] == ["ada"]
    assert await _decisions(sf) == [("ada", "invoked", "mention"),
                                    ("bob", "suppressed", "hop_limit"),
                                    ("bob", "suppressed", "hop_limit")]
    notices = await _system(sf, cid)
    assert [(m.body, m.thread_root, m.author) for m in notices] == [
        (HOP_LIMIT_BODY, root, "system:relay")]


async def test_a_human_in_the_thread_re_arms_the_hop_notice(make_router, sf):
    """The notice is "paused", not "closed": once a person speaks in the thread
    the room is live again, and the next pause has to be announced again."""
    router = await make_router()
    cid = await _channel(sf)
    root = await _say(router, sf, cid, "user:admin", "start here")
    cap = router.settings.relay_max_hops
    await _say(router, sf, cid, "agent:ada", "@bob take it", hop=cap,
               run_id=uuid.uuid4().hex, reply_to=root)
    await _say(router, sf, cid, "user:admin", "carry on then", reply_to=root)
    await _say(router, sf, cid, "agent:ada", "@bob take it", hop=cap,
               run_id=uuid.uuid4().hex, reply_to=root)
    assert [m.body for m in await _system(sf, cid)] == [HOP_LIMIT_BODY, HOP_LIMIT_BODY]


async def test_a_ping_pong_stops_after_max_hops(make_router, sf):
    """The guard that matters: two agents answering each other forever. The
    cooldown is off so the hop counter is the only thing stopping them."""
    router = await make_router(relay_agent_cooldown_seconds=0)
    cid = await _channel(sf)
    root = await _say(router, sf, cid, "user:admin", "@ada start")

    author, target, hop = "ada", "bob", 1
    for _ in range(6):
        await _finish(sf, author)
        await _say(router, sf, cid, f"agent:{author}", f"@{target} your turn", hop=hop,
                   run_id=uuid.uuid4().hex, reply_to=root)
        author, target, hop = target, author, hop + 1

    invoked = [d for d in await _decisions(sf) if d[1] == "invoked"]
    assert len(invoked) == router.settings.relay_max_hops
    assert len(await _runs(sf)) == router.settings.relay_max_hops
    assert [d[2] for d in await _decisions(sf) if d[1] == "suppressed"] == ["hop_limit"] * 3
    assert len(await _system(sf, cid)) == 1


async def test_the_run_chain_depth_is_a_second_fence(make_router, sf):
    """`max_run_chain_depth` bounds every chain in the platform, relay or not.
    A hop budget raised past it must not smuggle a deeper chain through."""
    router = await make_router(relay_max_hops=9, max_run_chain_depth=2)
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "@bob keep going", hop=3,
               run_id=uuid.uuid4().hex)
    assert await _runs(sf) == []
    assert await _decisions(sf) == [("bob", "suppressed", "hop_limit")]


# --- budgets -----------------------------------------------------------------


async def _seed_invocations(sf, channel_id: str, n: int) -> None:
    async with sf() as s:
        for i in range(n):
            s.add(RelayInvocation(channel_id=channel_id, message_id=f"seed{i}",
                                  agent="ada", decision="invoked", reason="mention"))
        await s.commit()


async def test_the_channel_budget_suppresses_and_says_so_once_an_hour(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    await _seed_invocations(sf, cid, router.settings.relay_channel_invocations_per_hour)
    for i in range(3):
        await _say(router, sf, cid, "user:admin", f"@ada ping {i}")

    assert await _runs(sf) == []
    assert [d for d in await _decisions(sf) if d[1] == "suppressed"] == [
        ("ada", "suppressed", "budget")] * 3
    notices = await _system(sf, cid)
    assert len(notices) == 1
    assert notices[0].body.startswith(BUDGET_PREFIX)
    assert f"({router.settings.relay_channel_invocations_per_hour}/hour)" in notices[0].body


async def test_the_global_budget_binds_a_quiet_channel_too(make_router, sf):
    """The global cap is the platform's spend, so an hour burned in one room
    suppresses the next room too — and a human mention is not exempt."""
    router = await make_router()
    busy = await _channel(sf, name="busy")
    quiet = await _channel(sf, name="quiet")
    await _seed_invocations(sf, busy, router.settings.relay_global_invocations_per_hour)
    await _say(router, sf, quiet, "user:admin", "@ada ping")

    assert await _runs(sf) == []
    assert await _decisions(sf, quiet) == [("ada", "suppressed", "budget")]
    assert (f"({router.settings.relay_global_invocations_per_hour}/hour)"
            in (await _system(sf, quiet))[0].body)


async def test_an_old_hour_does_not_count(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    await _seed_invocations(sf, cid, router.settings.relay_channel_invocations_per_hour)
    async with sf() as s:
        stale = utcnow().replace(year=utcnow().year - 1)
        for row in (await s.execute(select(RelayInvocation))).scalars():
            row.created_at = stale
        await s.commit()
    await _say(router, sf, cid, "user:admin", "@ada ping")
    assert [r.agent for r in await _runs(sf)] == ["ada"]


# --- cooldown, coalescing and wakes ------------------------------------------


async def _busy_run(sf, channel_id: str, agent: str, run_id="busy") -> None:
    async with sf() as s:
        s.add(Run(id=run_id, agent=agent, trigger="mention", requested_by="user:admin",
                  initiated_by="admin", conversation_id=channel_id, prompt="p",
                  state=RunState.RUNNING))
        await s.commit()


async def test_mentions_of_a_busy_agent_become_one_wake_then_one_run(make_router, sf):
    router = await make_router()
    cid = await _channel(sf)
    opener = await _say(router, sf, cid, "user:admin", "hello room")
    await _busy_run(sf, cid, "ada")

    mentions = [await _say(router, sf, cid, "user:admin", f"@ada thing {i}")
                for i in range(3)]
    wakes = await _wakes(sf)
    assert [(w.channel_id, w.agent, w.since_message_id) for w in wakes] == [
        (cid, "ada", mentions[0])]
    assert await _decisions(sf) == [("ada", "suppressed", "coalesced")] * 3
    assert [r.id for r in await _runs(sf)] == ["busy"]

    # The recorder posts ada's reply once its run is over: the router sees the
    # agent is free, finds the wake, and answers all three at once.
    await _finish(sf, "ada")
    await _say(router, sf, cid, "agent:ada", "done with the other thing", hop=1,
               run_id="busy")

    runs = [r for r in await _runs(sf) if r.id != "busy"]
    assert len(runs) == 1 and runs[0].agent == "ada"
    assert runs[0].trigger_message_id == mentions[0] and runs[0].depth == 0
    assert all(f"thing {i}" in runs[0].prompt for i in range(3))
    # The window resumes from before the first missed message, so the opener
    # the agent had already read is not replayed at it.
    assert "hello room" not in runs[0].prompt
    assert ("ada", "invoked", "wake") in await _decisions(sf)
    assert await _wakes(sf) == []


async def test_a_human_mention_fires_a_pending_wake_itself(make_router, sf):
    """A person should never have to wait for an agent's own reply to unstick
    a room: mentioning a now-free agent fires the wake it is still carrying."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada")
    missed = await _say(router, sf, cid, "user:admin", "@ada while you were out")
    await _finish(sf, "ada")
    now = await _say(router, sf, cid, "user:admin", "@ada still there?")

    runs = [r for r in await _runs(sf) if r.id != "busy"]
    assert len(runs) == 1 and runs[0].trigger_message_id == now
    assert "while you were out" in runs[0].prompt and "still there?" in runs[0].prompt
    assert await _wakes(sf) == []
    assert [d[2] for d in await _decisions(sf)] == ["coalesced", "mention"]
    assert missed  # the anchor is what the window resumed from


async def test_an_agent_mention_inside_the_cooldown_coalesces(make_router, sf):
    """An agent mentioned seconds after it last spoke is mid-thought as far as
    the room is concerned — three agents piling on become one follow-up."""
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "here is what I found", hop=1,
               run_id=uuid.uuid4().hex)
    await _say(router, sf, cid, "agent:bob", "@ada what about the other one?", hop=2,
               run_id=uuid.uuid4().hex)

    assert await _runs(sf) == []
    assert await _decisions(sf) == [("ada", "suppressed", "coalesced")]
    assert [w.agent for w in await _wakes(sf)] == ["ada"]


async def test_a_human_mention_inside_the_cooldown_still_invokes(make_router, sf):
    """The cooldown is a brake on agents talking to each other, not on people:
    a human's mention always gets a run if the agent is free."""
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "here is what I found", hop=1,
               run_id=uuid.uuid4().hex)
    await _say(router, sf, cid, "user:admin", "@ada what about the other one?")
    assert [r.agent for r in await _runs(sf)] == ["ada"]
    assert await _wakes(sf) == []


async def test_a_redelivered_message_does_not_invoke_twice(make_router, sf, producer):
    """Kafka is at-least-once, so the same message can arrive again. A mention
    that already has its run is already answered."""
    router = await make_router()
    cid = await _channel(sf)
    async with sf() as s:
        conv = await s.get(Conversation, cid)
        msg = await post_relay_message(s, conv, author="user:admin", body="@ada hello")
        await s.commit()
        payload = relay_message_payload(msg, conv)
    await router.handle(payload)
    await router.handle(payload)
    assert [r.agent for r in await _runs(sf)] == ["ada"]
    assert await _decisions(sf) == [("ada", "invoked", "mention")]


async def test_an_agent_removed_from_a_group_is_not_summoned_back(make_router, sf):
    """The membership re-check is not decoration: a wake outlives the room's
    roster, and a group's history must not follow an agent out of the door."""
    router = await make_router()
    cid = await _channel(sf, kind="group", open=False,
                         participants=("user:admin", "agent:ada"))
    await _busy_run(sf, cid, "ada")
    await _say(router, sf, cid, "user:admin", "@ada one more thing")
    assert [w.agent for w in await _wakes(sf)] == ["ada"]

    async with sf() as s:
        await s.delete(await s.get(RelayParticipant, (cid, "agent:ada")))
        await s.commit()
    await _finish(sf, "ada")
    await _say(router, sf, cid, "agent:ada", "back", hop=1, run_id="busy")

    assert [r.id for r in await _runs(sf)] == ["busy"]
    assert await _decisions(sf) == [("ada", "suppressed", "coalesced"),
                                    ("ada", "suppressed", "not_member")]
    assert await _wakes(sf) == []


async def test_a_tool_post_at_the_cap_summons_nobody(make_router, sf):
    """The `relay` tool's posts are how an agent-to-agent chain actually runs:
    the API stamps them with the poster's run depth + 1, and at the cap they
    must stop exactly as the recorder's replies do."""
    router = await make_router()
    cid = await _channel(sf)
    await _say(router, sf, cid, "agent:ada", "@bob one more thought",
               hop=router.settings.relay_max_hops, run_id=uuid.uuid4().hex)
    assert await _runs(sf) == []
    assert await _decisions(sf) == [("bob", "suppressed", "hop_limit")]
    assert [m.body for m in await _system(sf, cid)] == [HOP_LIMIT_BODY]


async def test_a_dm_turn_is_left_to_the_conversation_facade(make_router, sf):
    """A DM is one room seen from two sides: `/api/conversations` posts the
    human's message AND materializes the reply. The router must not answer it
    a second time — it publishes before the run exists, so the ordinary
    "already answered" check cannot see it."""
    router = await make_router(agents=("news", "ada"))
    cid = await _channel(sf, kind="dm", open=False, agent="news",
                         participants=("user:admin", "agent:news"))
    await _say(router, sf, cid, "user:admin", "@news hi")
    assert await _runs(sf) == []
    assert await _decisions(sf) == [("news", "suppressed", "facade_owns_turn")]


async def test_a_wake_survives_an_over_budget_hour(make_router, sf):
    """The budget is an answer about the hour, not about the backlog: dropping
    the wake would discard messages nobody has read, minutes before the room
    could afford to answer them."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada")
    missed = await _say(router, sf, cid, "user:admin", "@ada while you were out")
    await _seed_invocations(sf, cid, router.settings.relay_channel_invocations_per_hour)
    await _finish(sf, "ada")
    await _say(router, sf, cid, "agent:ada", "back", hop=1, run_id="busy")

    assert [d for d in await _decisions(sf) if d[1] == "suppressed"] == [
        ("ada", "suppressed", "coalesced"), ("ada", "suppressed", "budget")]
    assert [w.since_message_id for w in await _wakes(sf)] == [missed]
    assert [r.id for r in await _runs(sf)] == ["busy"]

    # The hour passes; the next thing that frees the agent fires the wake.
    async with sf() as s:
        stale = utcnow() - timedelta(hours=2)
        for row in (await s.execute(select(RelayInvocation).where(
                RelayInvocation.message_id.like("seed%")))).scalars():
            row.created_at = stale
        await s.commit()
    await _say(router, sf, cid, "agent:ada", "and back again", hop=1, run_id="busy")

    runs = [r for r in await _runs(sf) if r.id != "busy"]
    assert len(runs) == 1 and runs[0].agent == "ada"
    assert "while you were out" in runs[0].prompt
    assert ("ada", "invoked", "wake") in await _decisions(sf)
    assert await _wakes(sf) == []


async def test_a_failed_run_still_releases_its_wake(make_router, sf):
    """A run that died posts the platform's notice instead of an answer. The
    agent is free either way, and the backlog it was carrying must not be held
    hostage by the failure."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada")
    await _say(router, sf, cid, "user:admin", "@ada could you look?")
    assert [w.agent for w in await _wakes(sf)] == ["ada"]

    async with sf() as s:
        run = await s.get(Run, "busy")
        run.state, run.finished_at = RunState.FAILED, utcnow()
        await s.commit()
    # Exactly what the recorder writes for a failed run (recorder._post_reply).
    await _say(router, sf, cid, "system:relay", "😵 ada couldn't answer: boom",
               kind="system", run_id="busy")

    runs = [r for r in await _runs(sf) if r.id != "busy"]
    assert len(runs) == 1 and runs[0].agent == "ada"
    assert "could you look?" in runs[0].prompt
    assert await _decisions(sf) == [("ada", "suppressed", "coalesced"),
                                    ("ada", "invoked", "wake")]
    assert await _wakes(sf) == []
