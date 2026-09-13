"""The router (docs/design/19, T7): who a message summons, and every reason it
summons nobody.

The guards are what make a room full of agents safe to leave running, so each
one is tested by driving a real message through `handle` rather than by calling
the guard: the interesting failures are in how they COMBINE (a busy agent at
the hop limit, a wake that fires into an over-budget hour), and only the whole
router has an opinion about that."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import (ACTIVE_STATES, Conversation, RelayInvocation,
                              RelayMessage, RelayParticipant, RelayWake, Run,
                              RunState, utcnow)
from agentplatform.events import (TOPIC_RELAY_INVOCATIONS, TOPIC_RUN_EVENTS,
                                  make_envelope)
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
               kind="text", hop=0, run_id=None, reply_to=None,
               trigger_message_id=None) -> str:
    """Post a message and route it, exactly as the API/recorder → Kafka →
    router path does. `mentions` is deliberately left empty: the router
    re-parses the body, and a test that pre-computed the list would be
    asserting the poster's parse rather than the router's."""
    async with sf() as s:
        conv = await s.get(Conversation, channel_id)
        msg = await post_relay_message(s, conv, author=author, body=body, kind=kind,
                                       hop=hop, run_id=run_id, reply_to=reply_to,
                                       trigger_message_id=trigger_message_id)
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


async def _with_health_monitor(router, seed_agent):
    """A PLATFORM agent in the room: enabled, valid, and `system` — which is the
    one thing `@all` has to notice."""
    await seed_agent("health-monitor", description="t", system=True)
    await router.agents.reload()


async def test_at_all_skips_the_platforms_own_agents(make_router, sf, seed_agent):
    """A system agent is infrastructure, not a participant. Left in the roster,
    the 09:00 #standup would buy a Claude run from the health monitor every
    morning to report work nobody asked it about — and so would any human's
    `@all` in any open channel."""
    router = await make_router()
    await _with_health_monitor(router, seed_agent)
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@all standup please")
    assert sorted(r.agent for r in await _runs(sf)) == ["ada", "bob"]
    # Not even a suppression row: it was never addressed, so there is nothing
    # to explain.
    assert all(agent != "health-monitor" for agent, _, _ in await _decisions(sf))


async def test_at_all_skips_system_agents_in_a_closed_room_too(make_router, sf, seed_agent):
    """The explicit-member path expands the same roster, so a group that lists
    the health monitor by hand still does not page it with `@all`."""
    router = await make_router()
    await _with_health_monitor(router, seed_agent)
    cid = await _channel(sf, kind="group", open=False,
                         participants=("user:admin", "agent:ada",
                                       "agent:health-monitor"))
    await _say(router, sf, cid, "user:admin", "@all standup please")
    assert [r.agent for r in await _runs(sf)] == ["ada"]


async def test_a_system_agent_still_answers_its_own_name(make_router, sf, seed_agent):
    """The whole point of filtering the ROSTER rather than membership:
    `@health-monitor why?` is exactly how design-19 says an operator asks #ops
    for the reasoning behind an alert."""
    router = await make_router()
    await _with_health_monitor(router, seed_agent)
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@health-monitor why?")
    assert [r.agent for r in await _runs(sf)] == ["health-monitor"]
    assert await _decisions(sf) == [("health-monitor", "invoked", "mention")]


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


async def _busy_run(sf, channel_id: str, agent: str, run_id="busy",
                    trigger=None) -> None:
    """A run the agent is already in the middle of. It carries a
    `trigger_message_id` because every real run in a room does — the mention or
    the turn that asked for it — and that is what its reply will be stamped
    with when it ends (`_reply`). A stand-in id is enough where the test never
    posted the triggering message itself."""
    async with sf() as s:
        s.add(Run(id=run_id, agent=agent, trigger="mention", requested_by="user:admin",
                  initiated_by="admin", conversation_id=channel_id, prompt="p",
                  trigger_message_id=trigger or f"{run_id}-trigger",
                  state=RunState.RUNNING))
        await s.commit()


async def _reply(router, sf, channel_id: str, agent: str, body: str, *,
                 run_id: str, hop=1) -> str:
    """The RECORDER's message: a run's answer, carrying its run AND the message
    that triggered it (`recorder._post_reply`). The trigger is the half the
    router reads — it is what separates a run's last word from a tool post the
    same run made while it was still working."""
    async with sf() as s:
        run = await s.get(Run, run_id)
        trigger = run.trigger_message_id if run is not None else None
    return await _say(router, sf, channel_id, f"agent:{agent}", body, hop=hop,
                      run_id=run_id, trigger_message_id=trigger)


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
    await _reply(router, sf, cid, "ada", "done with the other thing", run_id="busy")

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
    await _reply(router, sf, cid, "ada", "back", run_id="busy")

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
    await _reply(router, sf, cid, "ada", "back", run_id="busy")

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
    await _reply(router, sf, cid, "ada", "and back again", run_id="busy")

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


async def _end(sf, run_id: str, state=RunState.SUCCEEDED) -> None:
    """What the recorder does when the terminal state finally lands — AFTER the
    reply, which rides the other topic."""
    async with sf() as s:
        run = await s.get(Run, run_id)
        run.state, run.finished_at = state, utcnow()
        await s.commit()


def _state_event(run_id: str, state) -> tuple:
    """A `run.events` state message as the dispatcher publishes it."""
    data = {"run_id": run_id, "type": "state", "state": str(state), "detail": ""}
    msg = SimpleNamespace(topic=TOPIC_RUN_EVENTS, key=run_id.encode(),
                          value=json.dumps(make_envelope(
                              type="run.state", key=run_id, data=data,
                              source="test")).encode())
    return msg, data


async def test_two_agents_introduced_to_each_other_both_get_their_wake(make_router, sf):
    """The live failure (repair R2), reproduced exactly.

    A reply reaches the router on `run.transcript`; the terminal state that
    ends the same run reaches it on `run.events`, later. So when an agent's own
    reply arrives, its run row still says RUNNING — and the room's two pending
    wakes both hung on that, leaving "@news @health-monitor say hi to each
    other" answered by nobody. The reply IS the run's last word, and the state
    is the backstop for a reply the router never sees."""
    router = await make_router(agents=("news", "health-monitor"))
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@news @health-monitor say hi to each other")
    first = {r.agent: r.id for r in await _runs(sf)}
    assert sorted(first) == ["health-monitor", "news"]

    # health-monitor answers first, mentioning news — who is still running, so
    # the mention becomes news's wake.
    hm_reply = await _reply(router, sf, cid, "health-monitor",
                            "hi @news, nice to meet you",
                            run_id=first["health-monitor"])
    assert [w.agent for w in await _wakes(sf)] == ["news"]

    # news answers seconds later, its OWN run row still RUNNING. That reply is
    # what frees news: its wake fires here, and its mention of health-monitor
    # (still busy) becomes health-monitor's wake.
    news_reply = await _reply(router, sf, cid, "news", "hello @health-monitor",
                              run_id=first["news"])
    woken = [r for r in await _runs(sf) if r.id not in first.values()]
    assert [r.agent for r in woken] == ["news"]
    assert woken[0].trigger_message_id == hm_reply and woken[0].depth == 1
    assert "nice to meet you" in woken[0].prompt
    assert [w.agent for w in await _wakes(sf)] == ["health-monitor"]

    # health-monitor's run ends without the router ever seeing another message
    # from it: the terminal state is what fires the wake it was carrying.
    await _end(sf, first["health-monitor"])
    await router.on_run_terminal(first["health-monitor"])

    woken = [r for r in await _runs(sf) if r.id not in first.values()]
    assert sorted(r.agent for r in woken) == ["health-monitor", "news"]
    hm_run = next(r for r in woken if r.agent == "health-monitor")
    assert hm_run.trigger_message_id == news_reply
    assert "hello @health-monitor" in hm_run.prompt
    assert await _wakes(sf) == []
    assert sorted(await _decisions(sf, cid)) == sorted([
        ("news", "invoked", "mention"), ("health-monitor", "invoked", "mention"),
        ("news", "suppressed", "coalesced"), ("news", "invoked", "wake"),
        ("health-monitor", "suppressed", "coalesced"),
        ("health-monitor", "invoked", "wake")])


async def test_a_wake_fires_once_across_the_reply_and_the_terminal_state(make_router, sf):
    """The two paths are the same wake seen twice, and a wake is consumed by
    being acted on: the reply fires it, the state that follows finds nothing."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    missed = await _say(router, sf, cid, "user:admin", "@ada while you were out")
    assert [w.since_message_id for w in await _wakes(sf)] == [missed]

    await _reply(router, sf, cid, "ada", "back", run_id="ada-run")
    await _end(sf, "ada-run")
    await router.on_run_terminal("ada-run")

    runs = [r for r in await _runs(sf) if r.id != "ada-run"]
    assert [r.agent for r in runs] == ["ada"]
    assert [d for d in await _decisions(sf, cid) if d[1] == "invoked"] == [
        ("ada", "invoked", "wake")]
    assert await _wakes(sf) == []


async def test_a_reply_that_arrives_after_its_terminal_state_wakes_nobody(make_router, sf):
    """The other order. Nothing sequences `run.transcript` against
    `run.events`, so the state can win and the reply arrive at a router that
    has already fired the wake on it. The reply is then a message about a run
    that is finished and a backlog that is answered: it must not fire a second
    follow-up, and it must not put the wake back."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    missed = await _say(router, sf, cid, "user:admin", "@ada while you were out")

    await _end(sf, "ada-run")
    await router.on_run_terminal("ada-run")
    woken = [r for r in await _runs(sf) if r.id != "ada-run"]
    assert [r.agent for r in woken] == ["ada"] and woken[0].trigger_message_id == missed

    # ... and only now does the reply that ended `ada-run` reach the router.
    await _reply(router, sf, cid, "ada", "back", run_id="ada-run")

    assert [r.id for r in await _runs(sf)] == ["ada-run", woken[0].id]
    assert await _wakes(sf) == []
    assert await _decisions(sf, cid) == [("ada", "suppressed", "coalesced"),
                                         ("ada", "invoked", "wake")]


async def test_a_redelivered_terminal_state_wakes_nobody_twice(make_router, sf):
    """Kafka is at-least-once on `run.events` too, and a run's terminal state
    is published by the dispatcher AND by the job watcher. The wake is consumed
    by the first one: the second finds nothing to fire."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    await _say(router, sf, cid, "user:admin", "@ada while you were out")
    await _end(sf, "ada-run")

    for _ in range(2):
        await router._on_message(*_state_event("ada-run", RunState.SUCCEEDED))
    await router.on_run_terminal("ada-run")

    assert [r.agent for r in await _runs(sf) if r.id != "ada-run"] == ["ada"]
    assert [d for d in await _decisions(sf, cid) if d[1] == "invoked"] == [
        ("ada", "invoked", "wake")]
    assert await _wakes(sf) == []


async def test_a_terminal_state_does_not_wake_an_agent_that_is_busy_again(make_router, sf):
    """Excluding the run that just ended is not excluding every run: an agent
    already working on something else in the room is still busy, and its wake
    waits for that one to end."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    await _say(router, sf, cid, "user:admin", "@ada while you were out")
    await _busy_run(sf, cid, "ada", run_id="ada-next")

    await _end(sf, "ada-run")
    await router.on_run_terminal("ada-run")
    assert [w.agent for w in await _wakes(sf)] == ["ada"]
    assert sorted(r.id for r in await _runs(sf)) == ["ada-next", "ada-run"]

    await _end(sf, "ada-next")
    await router.on_run_terminal("ada-next")
    assert [r.agent for r in await _runs(sf) if r.id not in ("ada-run", "ada-next")] == ["ada"]
    assert await _wakes(sf) == []


async def test_a_wake_fires_on_the_state_event_even_before_the_row_catches_up(
        make_router, sf):
    """`run.events` has two readers — the recorder, which writes the row, and
    the router, which reads it — and no order between them. So the router
    believes the event it was handed rather than re-reading a row that may not
    have been written yet."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    await _say(router, sf, cid, "user:admin", "@ada while you were out")

    msg, data = _state_event("ada-run", RunState.SUCCEEDED)
    await router._on_message(msg, data)  # the row still says RUNNING

    assert [r.agent for r in await _runs(sf) if r.id != "ada-run"] == ["ada"]
    assert await _wakes(sf) == []


async def test_a_non_terminal_state_event_wakes_nobody(make_router, sf):
    """Every run on the platform rides this topic, and most of the states are
    a run starting, not ending."""
    router = await make_router()
    cid = await _channel(sf)
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    await _say(router, sf, cid, "user:admin", "@ada while you were out")

    for state in (RunState.QUEUED, RunState.DISPATCHED, RunState.RUNNING):
        await router._on_message(*_state_event("ada-run", state))
    await router._on_message(*_state_event("no-such-run", RunState.SUCCEEDED))

    assert [r.id for r in await _runs(sf)] == ["ada-run"]
    assert [w.agent for w in await _wakes(sf)] == ["ada"]


async def test_the_router_reads_both_topics(make_router, sf):
    """The router only has a backstop if it is subscribed to the topic the
    terminal state arrives on."""
    from agentplatform.events import TOPIC_RELAY_MESSAGES
    from agentplatform.relay_router import TOPICS
    assert TOPICS == (TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS)


async def test_a_pause_notice_reaches_a_bound_room(make_router, sf, producer):
    """The bridge is told why the room went quiet (docs/design/19 T10): a
    Discord channel that just watched two agents stop mid-thread needs the same
    "paused" line the web pane shows."""
    from agentplatform.db import RelayBinding
    from agentplatform.events import TOPIC_CONVERSATION_OUTBOUND
    router = await make_router()
    cid = await _channel(sf)
    async with sf() as s:
        s.add(RelayBinding(channel_id=cid, connector="discord", external_ref="chan-7"))
        await s.commit()
    root = await _say(router, sf, cid, "user:admin", "kick this off @ada")
    await _finish(sf, "ada")
    await _say(router, sf, cid, "agent:ada", "@bob take it from here",
               hop=router.settings.relay_max_hops, run_id=uuid.uuid4().hex,
               reply_to=root)
    out = [d for t, _, d in producer.published if t == TOPIC_CONVERSATION_OUTBOUND]
    assert [(d["kind"], d["author"], d["text"], d["external_ref"]) for d in out] == [
        ("system", "system:relay", HOP_LIMIT_BODY, "chan-7")]


# --- tickets: the thread-aware summons (docs/design/20 T6) -------------------


async def _project(sf, prefix="OPS") -> str:
    cid = await _channel(sf)
    async with sf() as s:
        (await s.get(Conversation, cid)).ticket_prefix = prefix
        await s.commit()
    return cid


async def _ticket(sf, producer, channel_id: str, *, title="Fix the stale dedup",
                  body="", assignee=None, actor="user:admin", state=None) -> dict:
    """A ticket opened the way everything opens one: through the store, so the
    card in the room really is the thread root the router has to recognise."""
    from agentplatform.db import Ticket
    from agentplatform.ticket_store import create_ticket
    async with sf() as s:
        conv = await s.get(Conversation, channel_id)
        t = await create_ticket(s, producer, conv, actor=actor, title=title,
                                body=body, assignee=assignee, notify=False)
        if state:
            (await s.get(Ticket, t.id)).state = state
            await s.commit()
        return {"id": t.id, "key": t.key, "root": t.root_message_id}


async def test_a_summons_in_a_ticket_thread_carries_the_ticket(make_router, sf, producer):
    """The assignment mention is a reply under the card, so the run it summons
    is about that ticket: it says so on the Run row, it opens with the ticket,
    and it reads the thread rather than the room."""
    router = await make_router()
    cid = await _project(sf)
    t = await _ticket(sf, producer, cid, title="Fix the stale dedup",
                      body="the forecast repeats")
    await _say(router, sf, cid, "user:admin", "unrelated room chatter")
    await _say(router, sf, cid, "user:admin", "some background", reply_to=t["root"])
    await _say(router, sf, cid, "user:admin", f"@ada you've been assigned {t['key']}",
               reply_to=t["root"])

    runs = await _runs(sf)
    assert [(r.agent, r.ticket_id) for r in runs] == [("ada", t["id"])]
    assert f'<ticket key="{t["key"]}" state="open"' in runs[0].prompt
    assert "<body>the forecast repeats</body>" in runs[0].prompt
    assert "Move the ticket with the `tickets` tool" in runs[0].prompt
    # The window is the thread: the card, the background, the summons — and
    # not the room's chatter, which is about something else entirely.
    assert "some background" in runs[0].prompt
    assert "unrelated room chatter" not in runs[0].prompt
    # A ticket thread is where the work is; the agent's whole queue is not.
    assert "<your-tickets" not in runs[0].prompt


async def test_a_summons_outside_a_ticket_thread_gets_the_agents_queue(
        make_router, sf, producer):
    """The #standup shape: no ticket in hand, so the prompt ends with the
    agent's own open work and the room page it always had."""
    router = await make_router()
    cid = await _project(sf)
    mine = await _ticket(sf, producer, cid, title="Fix the stale dedup",
                         assignee="agent:ada")
    await _ticket(sf, producer, cid, title="Someone else's problem",
                  assignee="agent:bob")
    done = await _ticket(sf, producer, cid, title="Long since finished",
                         assignee="agent:ada", state="done")
    await _say(router, sf, cid, "user:admin", "unrelated room chatter")
    await _say(router, sf, cid, "user:admin", "@ada what did you do today?")

    runs = await _runs(sf)
    assert [(r.agent, r.ticket_id) for r in runs] == [("ada", None)]
    # The queue is ada's own open work — bob's ticket and ada's finished one
    # are in the room (their cards were posted there), but not in the list.
    _, _, queue = runs[0].prompt.partition("<your-tickets")
    assert queue.startswith(' count="1">\n')
    assert f"{mine['key']} · open · Fix the stale dedup" in queue
    assert "Someone else's problem" not in queue and done["key"] not in queue
    assert "unrelated room chatter" in runs[0].prompt
    assert "<ticket key=" not in runs[0].prompt


async def test_a_ticket_thread_summons_with_no_ticket_is_still_a_thread(
        make_router, sf, producer):
    """A thread that is not a ticket's still reads as a thread — the shape is
    about the conversation, not the board — and the agent's queue rides along
    because there is no ticket to lead with."""
    router = await make_router()
    cid = await _project(sf)
    await _ticket(sf, producer, cid, title="Fix the stale dedup", assignee="agent:ada")
    root = await _say(router, sf, cid, "user:admin", "let's talk about the deploy")
    await _say(router, sf, cid, "user:admin", "elsewhere in the room")
    await _say(router, sf, cid, "user:admin", "@ada thoughts?", reply_to=root)

    runs = await _runs(sf)
    assert [(r.agent, r.ticket_id) for r in runs] == [("ada", None)]
    assert "let's talk about the deploy" in runs[0].prompt
    assert "elsewhere in the room" not in runs[0].prompt
    assert '<your-tickets count="1">' in runs[0].prompt


async def test_a_mid_run_tool_post_does_not_free_the_agent(make_router, sf, producer):
    """A run that comments on a ticket while it works posts a message carrying
    its `run_id` — but that is not the run's last word, and treating it as one
    starts a SECOND run of the same agent while the first is still executing.
    Only the recorder's reply (the one message that also carries
    `trigger_message_id`) frees the agent."""
    router = await make_router()
    cid = await _project(sf)
    t = await _ticket(sf, producer, cid, title="Fix the stale dedup")
    await _busy_run(sf, cid, "ada", run_id="ada-run")
    missed = await _say(router, sf, cid, "user:admin", "@ada while you were out")
    assert [w.since_message_id for w in await _wakes(sf)] == [missed]

    # Exactly what `ticket_store.comment_ticket` writes mid-run: the actor's
    # own message, attributed to the run, threaded under the card.
    await _say(router, sf, cid, "agent:ada", f"looking at {t['key']} now", hop=1,
               run_id="ada-run", reply_to=t["root"])
    assert [r.id for r in await _runs(sf)] == ["ada-run"]
    assert [w.since_message_id for w in await _wakes(sf)] == [missed]

    # The recorder's reply is the run's last word, and it fires the wake.
    await _finish(sf, "ada")
    await _reply(router, sf, cid, "ada", "done", run_id="ada-run")
    assert [r.agent for r in await _runs(sf) if r.id != "ada-run"] == ["ada"]
    assert await _wakes(sf) == []


async def test_a_ticket_handed_back_and_forth_hits_the_hop_cap(make_router, sf, producer):
    """Assign = summon (docs/design/20), so a hand-off between two agents is an
    agent-authored mention chain — and it runs into the fence that stops every
    other one. Each assignment posts at the assigner's `run.depth + 1`, the run
    it summons is created at that hop, and the fourth one is refused with the
    room told once, in the ticket's own thread."""
    from agentplatform.db import Ticket
    from agentplatform.ticket_store import assign_ticket
    router = await make_router(relay_agent_cooldown_seconds=0)
    cid = await _project(sf)
    t = await _ticket(sf, producer, cid, title="Fix the stale dedup")
    # A human's summons rooted the chain: ada is answering it at depth 0.
    async with sf() as s:
        s.add(Run(id="ada-0", agent="ada", trigger="mention", requested_by="user:admin",
                  initiated_by="admin", conversation_id=cid, prompt="p", depth=0,
                  ticket_id=t["id"], trigger_message_id=t["root"],
                  state=RunState.RUNNING))
        await s.commit()

    holder, run_id, hops = "ada", "ada-0", []
    for _ in range(6):
        other = "bob" if holder == "ada" else "ada"
        async with sf() as s:
            conv = await s.get(Conversation, cid)
            event = await assign_ticket(s, producer, await s.get(Ticket, t["id"]),
                                        actor=f"agent:{holder}",
                                        assignee=f"agent:{other}",
                                        run=await s.get(Run, run_id))
            msg = await s.get(RelayMessage, event.message_id)
            hops.append(msg.hop)
            payload = relay_message_payload(msg, conv)
        # The assigning run ends on its own message, as the recorder's reply
        # would: nothing here is testing the busy guard.
        await _finish(sf, holder)
        await router.handle(payload)
        summoned = {r.trigger_message_id: r for r in await _runs(sf)}
        if payload["id"] not in summoned:
            break
        run_id, holder = summoned[payload["id"]].id, other

    # hop 4 is `relay_max_hops`, and the mention that would have carried it is
    # where the ping-pong stops.
    assert hops == [1, 2, 3, 4]
    assert await _decisions(sf, cid) == [
        ("bob", "invoked", "mention"), ("ada", "invoked", "mention"),
        ("bob", "invoked", "mention"), ("ada", "suppressed", "hop_limit")]
    # Every run the chain summoned is work on the ticket, not just a reply in
    # a room that happens to contain one.
    assert {r.ticket_id for r in await _runs(sf)} == {t["id"]}
    # And the room is told where it stopped: in the ticket's thread, under the
    # card, which is where a human picking the work up is already looking.
    notices = [m for m in await _system(sf, cid) if m.body == HOP_LIMIT_BODY]
    assert [(m.reply_to, m.thread_root) for m in notices] == [(t["root"], t["root"])]


# --- the wiki block (docs/design/21 T5) --------------------------------------
# What the room is talking about, matched against the pages, and offered to the
# run as slugs to cite. The match is the interesting part: a page nobody is
# talking about costs every run in the room context, and an archived page is
# knowledge the wiki has withdrawn.


async def _page(sf, slug: str, title: str, *, body="", summary="", tags=(),
                archived=False) -> None:
    from agentplatform.db import WikiPage
    async with sf() as s:
        s.add(WikiPage(slug=slug, title=title, body=body,
                       summary=summary or title, tags=list(tags),
                       created_by="user:admin", updated_by="user:admin",
                       archived_at=utcnow() if archived else None))
        await s.commit()


async def test_a_summons_carries_the_pages_it_is_talking_about(make_router, sf):
    router = await make_router()
    await _page(sf, "dedup-rule", "Dedup rule", body="one story per day",
                summary="one story per day")
    await _page(sf, "pto-policy", "PTO policy", body="ask first")
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@ada what is our dedup rule?")

    prompt = (await _runs(sf))[0].prompt
    assert "[[dedup-rule]] · Dedup rule · one story per day" in prompt
    assert "[[pto-policy]]" not in prompt
    assert "Cite a page as `[[slug]]` when you use it." in prompt


async def test_an_archived_page_is_never_offered(make_router, sf):
    """Archiving is how a page stops counting — a withdrawn page that still
    turned up in every prompt would be knowledge nobody can take back."""
    router = await make_router()
    await _page(sf, "dedup-rule", "Dedup rule", body="one story per day",
                archived=True)
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@ada what is our dedup rule?")
    assert "[[dedup-rule]]" not in (await _runs(sf))[0].prompt


async def test_a_room_with_no_matching_page_gets_no_block(make_router, sf):
    router = await make_router()
    await _page(sf, "dedup-rule", "Dedup rule", body="one story per day")
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@ada ping")
    prompt = (await _runs(sf))[0].prompt
    assert "<wiki count=" not in prompt and "[[slug]]" not in prompt


async def test_the_thread_is_matched_too_not_just_the_summons(make_router, sf):
    """A summons inside a thread is answered from the thread, so the thread is
    what the agent is being asked about — "any thoughts?" under a page's worth
    of discussion must still reach the page."""
    router = await make_router()
    await _page(sf, "dedup-rule", "Dedup rule", body="one story per day")
    cid = await _channel(sf)
    root = await _say(router, sf, cid, "user:admin",
                      "the dedup rule bit us again this morning")
    await _say(router, sf, cid, "user:admin", "@ada thoughts?", reply_to=root)
    assert "[[dedup-rule]]" in (await _runs(sf))[0].prompt


async def test_the_summoned_agents_own_name_is_not_a_search_term(make_router, sf):
    """The address is not part of the question. `@news where does Kyle live?`
    is about Kyle; matching on "news" as well pulls in every page that happens
    to mention the agent and — on postgres, where the terms were ANDed — cost
    the room its `<wiki>` block entirely (the LIVE bug). Room mentions go the
    same way: `@all` is who is being asked, not what about."""
    router = await make_router(agents=("news", "bob"))
    await _page(sf, "kyle-location", "Kyle's location", summary="Whitby, Ontario",
                body="Kyle lives in Whitby, east of Toronto.")
    await _page(sf, "news-desk", "News desk", body="how the news job runs")
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin", "@news where does Kyle live?")

    prompt = (await _runs(sf))[0].prompt
    assert "[[kyle-location]] · Kyle's location · Whitby, Ontario" in prompt
    assert "[[news-desk]]" not in prompt


async def test_a_pasted_traceback_still_matches_what_it_is_about(make_router, sf):
    """Code is prose to the search. `@pytest` in a pasted stack trace is a
    decorator, not an address — blanking it the way mention PARSING does would
    lose the one word that says what the room is asking about."""
    router = await make_router(agents=("news", "bob"))
    await _page(sf, "pytest-runner", "Pytest", summary="how we run it",
                body="our runner, invoked by the platform")
    cid = await _channel(sf)
    await _say(router, sf, cid, "user:admin",
               "@news why does this flake?\n```py\n@pytest.mark.flaky\ndef f(): ...\n```")
    assert "[[pytest-runner]]" in (await _runs(sf))[0].prompt
