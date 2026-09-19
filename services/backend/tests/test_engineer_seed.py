"""The engineer (docs/design/24): the seeded dev agent that takes an assigned
ticket, works on a branch and opens a PR for a human to merge — with its
home project `#eng` and the weekday `eng-queue` job that gives a run deferred
for quota another chance. Three one-time seeds behind their own marks, in the
artist's, the art channel's and the gardener's shapes: an admin who edits or
deletes any of them keeps their version. NOT a system agent: `@all` and the
#standup are meant to reach it."""
import uuid

import pytest
from sqlalchemy import func, select

from agentplatform.agents import AgentStore
from agentplatform.agentspec import (TOOL_ARTIFACTS, TOOL_QUOTA_OK, TOOL_RELAY,
                                     TOOL_TICKETS, TOOL_WIKI)
from agentplatform.config import Settings
from agentplatform.db import (ENG_CHANNEL_MARK, ENG_QUEUE_MARK, ENG_WELCOME_BODY,
                              ENGINEER_PROMPT, ENGINEER_SEED_MARK, AgentDef,
                              AgentVersion, Base, Conversation, RelayMessage,
                              Run, ScheduledJob, SchemaMark, Ticket, init_db,
                              make_engine, make_session_factory)
from agentplatform.relay_router import RelayRouter
from agentplatform.relay_store import relay_message_payload
from agentplatform.ticket_store import create_ticket


@pytest.fixture
async def engine():
    """Tables but no migration yet — the shape init_db finds on the first boot
    after the Workbench ships."""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def sfx(engine):
    return make_session_factory(engine)


async def _versions(sfx, name: str) -> list[AgentVersion]:
    async with sfx() as s:
        return list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == name).order_by(AgentVersion.version))).scalars())


async def _eng_channels(sfx) -> list[Conversation]:
    async with sfx() as s:
        return list((await s.execute(select(Conversation).where(
            Conversation.kind == "channel", Conversation.name == "eng"))).scalars())


async def _eng_queue_jobs(sfx) -> list[ScheduledJob]:
    async with sfx() as s:
        return list((await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "eng-queue"))).scalars())


# --- the row ------------------------------------------------------------------


async def test_the_engineer_is_seeded_with_its_grants_role_and_thresholds(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "engineer")
        assert row is not None
        # Not `system`: the librarian hides from `@all`, the engineer does not.
        assert (row.system, row.enabled, row.can_invoke) == (False, True, False)
        # Named, not the CLI default: the default resolved to sonnet live and
        # a coding run is where the strong one earns its cost. `dev` is the
        # run-profile rung, not an API scope.
        assert (row.model, row.role) == ("opus", "dev")
        assert (row.timeout_seconds, row.concurrency) == (5400, 1)
        assert (row.quota_5h_max_pct, row.quota_7d_max_pct) == (95, 90)
        assert row.platform_tools == [TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI,
                                      TOOL_QUOTA_OK, TOOL_ARTIFACTS]
        # The shell tools come from the profile, not the grant; WebFetch is
        # deliberately absent — the repo and the wiki are its sources.
        assert row.harness_tools == ["Glob", "Grep"]
        assert (row.skills, row.secrets) == ([], [])
        assert (row.push_path_globs, row.may_delete_tests) == ([], False)
        assert row.description == (
            "Writes code for the platform: takes an assigned ticket, works on a "
            "branch, verifies, and opens a PR for a human to merge.")
        assert len(row.description) <= 512
        assert row.prompt == ENGINEER_PROMPT
        assert await s.get(SchemaMark, ENGINEER_SEED_MARK) is not None


def test_the_prompt_carries_the_unconditional_rules():
    """The rules that cost trust if forgotten, pinned by their exact words so
    a rewrite that loses one fails here rather than on a branch."""
    for phrase in ("never remove or weaken a test", "never `git push`",
                   "`git reset --hard`", ".github/", "quota_ok",
                   "bin/ap-verify --changed", ".ap/pr.md", "in_progress",
                   "blocked", "git log origin/main..HEAD", "UNTRUSTED"):
        assert phrase in ENGINEER_PROMPT, phrase


def test_the_prompt_orders_process_rules_and_hand_back():
    """Who it is, then the process, then the unconditional rules, then the
    hand-back — the order the design specifies, and the order a model reads
    hardest at the ends."""
    i_process = ENGINEER_PROMPT.index("## Process")
    i_rules = ENGINEER_PROMPT.index("## Unconditional rules")
    i_handback = ENGINEER_PROMPT.index("## Hand-back")
    assert 0 < i_process < i_rules < i_handback


async def test_the_engineer_has_exactly_one_version_after_a_fresh_init(engine, sfx):
    """The seed runs AFTER the default-grant sweeps, which have marked
    themselves by then: born holding every grant it needs, the engineer's
    change log opens with one row, the seed's own."""
    await init_db(engine)
    versions = await _versions(sfx, "engineer")
    assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
        (1, "system:engineer", "seed")]
    snap = versions[0].snapshot
    assert snap["platform_tools"] == [TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI,
                                      TOOL_QUOTA_OK, TOOL_ARTIFACTS]
    assert (snap["system"], snap["role"], snap["model"]) == (False, "dev", "opus")
    assert (snap["quota_5h_max_pct"], snap["quota_7d_max_pct"]) == (95, 90)


async def test_an_existing_engineer_is_adopted_not_overwritten(engine, sfx):
    async with sfx() as s:
        s.add(AgentDef(name="engineer", prompt="mine", description="mine",
                       platform_tools=[]))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "engineer")
        # The grant sweeps still reach an adopted row — that is their job, not
        # the seed's — so what proves adoption is the prompt and the role.
        assert (row.prompt, row.role) == ("mine", "operator")
        assert await s.get(SchemaMark, ENGINEER_SEED_MARK) is not None
    assert "system:engineer" not in [v.changed_by for v in await _versions(sfx, "engineer")]


async def test_the_engineer_mark_is_the_off_switch(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.delete(await s.get(AgentDef, "engineer"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert await s.get(AgentDef, "engineer") is None


# --- #eng ---------------------------------------------------------------------


async def test_eng_is_seeded_as_a_project_with_a_welcome(engine, sfx):
    await init_db(engine)
    rooms = await _eng_channels(sfx)
    assert len(rooms) == 1
    room = rooms[0]
    assert (room.open, room.agent, room.ticket_prefix, room.ticket_seq) == (
        True, None, "ENG", 0)
    assert room.topic == "engineering: tickets for the engineer, and what it shipped"
    async with sfx() as s:
        msgs = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == room.id))).scalars())
        assert [(m.kind, m.author, m.body) for m in msgs] == [
            ("system", "system:relay", ENG_WELCOME_BODY)]
        assert await s.get(SchemaMark, ENG_CHANNEL_MARK) is not None
    assert ENG_WELCOME_BODY == ("Assign a ticket to @engineer and it opens a PR; the "
                                "platform publishes, humans merge")


async def test_an_existing_eng_is_adopted_and_only_a_null_prefix_is_set(engine, sfx):
    """A hand-made #eng keeps its topic and its rows; a prefix it already has
    is somebody's decision, and a prefix it lacks is the one thing the seed
    adds — the publish door posts to `#eng` and a ticket needs a key."""
    async with sfx() as s:
        s.add(Conversation(connector="web", kind="channel", open=True, name="eng",
                           title="#eng", topic="ours", ticket_prefix=None, ticket_seq=0))
        s.add(Conversation(connector="web", kind="channel", open=True, name="eng2",
                           title="#eng2", topic="theirs", ticket_prefix="X", ticket_seq=0))
        await s.commit()
    await init_db(engine)
    rooms = await _eng_channels(sfx)
    assert len(rooms) == 1
    assert (rooms[0].topic, rooms[0].ticket_prefix) == ("ours", "ENG")
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(RelayMessage.__table__)
                                .where(RelayMessage.__table__.c.channel_id == rooms[0].id))
                ).scalar_one() == 0
        other = (await s.execute(select(Conversation).where(
            Conversation.name == "eng2"))).scalar_one()
        assert other.ticket_prefix == "X"


async def test_an_existing_eng_with_a_prefix_keeps_it(engine, sfx):
    async with sfx() as s:
        s.add(Conversation(connector="web", kind="channel", open=True, name="eng",
                           title="#eng", topic="ours", ticket_prefix="ENGX", ticket_seq=3))
        await s.commit()
    await init_db(engine)
    rooms = await _eng_channels(sfx)
    assert (rooms[0].ticket_prefix, rooms[0].ticket_seq) == ("ENGX", 3)


# --- eng-queue ----------------------------------------------------------------


async def test_the_eng_queue_job_is_seeded(engine, sfx):
    await init_db(engine)
    jobs = await _eng_queue_jobs(sfx)
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.cron, job.timezone) == ("0 7 * * 1-5", "America/Toronto")
    # A relay-post job, not an agent run: the summons has to come from the
    # platform, because an agent's own @mention carries a hop.
    assert (job.relay_channel, job.agent) == ("eng", None)
    assert job.prompt == ("@engineer — anything assigned to you that is still open: "
                          "pick up the oldest one, or say why not")
    assert (job.enabled, job.next_fire) == (True, None)
    async with sfx() as s:
        assert await s.get(SchemaMark, ENG_QUEUE_MARK) is not None


async def test_an_eng_queue_job_that_already_exists_is_adopted(engine, sfx):
    async with sfx() as s:
        s.add(ScheduledJob(name="eng-queue", relay_channel="eng",
                           cron="0 6 * * 1", prompt="mine"))
        await s.commit()
    await init_db(engine)
    jobs = await _eng_queue_jobs(sfx)
    assert [(j.cron, j.prompt) for j in jobs] == [("0 6 * * 1", "mine")]


async def test_the_eng_queue_mark_is_the_off_switch(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.execute(ScheduledJob.__table__.delete().where(
            ScheduledJob.__table__.c.name == "eng-queue"))
        await s.commit()
    await init_db(engine)
    assert await _eng_queue_jobs(sfx) == []


# --- idempotence, all three at once ------------------------------------------


async def test_the_three_seeds_are_idempotent(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    assert len(await _versions(sfx, "engineer")) == 1
    assert len(await _eng_channels(sfx)) == 1
    assert len(await _eng_queue_jobs(sfx)) == 1
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(AgentDef.__table__)
                                .where(AgentDef.__table__.c.name == "engineer"))
                ).scalar_one() == 1


# --- summonable ---------------------------------------------------------------


async def test_assigning_a_ticket_to_the_engineer_yields_a_dev_run_about_it(sf, producer):
    """AC-4 through the real store and the real router: the seeded row is a
    live, valid agent, so an assignment in a project is a mention in the
    ticket's thread, and the run it summons names the ticket and carries the
    dev profile."""
    store = AgentStore(sf)
    await store.reload()
    assert store.get("engineer").manifest.role == "dev"
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", open=True,
                            name=f"proj-{uuid.uuid4().hex[:8]}", title="#proj",
                            ticket_prefix="PRJ", ticket_seq=0)
        s.add(conv)
        await s.flush()
        t = await create_ticket(s, producer, conv, actor="user:admin",
                                title="Fix the stale dedup", body="the forecast repeats",
                                assignee="agent:engineer", notify=True)
        ticket_id, root = t.id, t.root_message_id
        mention = (await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == conv.id, RelayMessage.id != root))).scalar_one()
        payload = relay_message_payload(mention, conv)
    await RelayRouter(Settings(), sf, producer, store).handle(payload)
    async with sf() as s:
        runs = list((await s.execute(select(Run))).scalars())
        assert (await s.get(Ticket, ticket_id)).assignee == "agent:engineer"
    assert [(r.agent, r.trigger, r.depth, r.ticket_id) for r in runs] == [
        ("engineer", "mention", 0, ticket_id)]
    assert "<body>the forecast repeats</body>" in runs[0].prompt
