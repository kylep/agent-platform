"""Tickets schema (docs/design/20): the ticket tables, the project prefix a
channel carries, and the one-off seed that makes #general and #ops projects."""
import pytest
from sqlalchemy import func, select

from agentplatform.db import (HEALTH_MONITOR_TICKET_RULE, RELAY_STANDUP_PROMPT_V2,
                              TICKETS_GRANT_MARK, TICKETS_HEALTH_MONITOR_MARK,
                              TICKETS_SEED_MARK, TICKETS_STANDUP_MARK,
                              TICKETS_SYSTEM_KEYS_MARK, AgentDef, ApiKey,
                              AgentVersion, Base, Conversation, Run, RunState,
                              ScheduledJob, SchemaMark, Ticket, TicketEvent,
                              TicketPriority, TicketState, init_db, make_engine,
                              make_session_factory, utcnow)


@pytest.fixture
async def engine():
    """Tables but no ticket migration yet — the shape init_db finds on the first
    boot after Tickets ships."""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def sfx(engine):
    return make_session_factory(engine)


async def test_ticket_models_round_trip(sfx):
    async with sfx() as s:
        s.add(Conversation(id="c1", connector="web", agent=None, kind="channel",
                           name="ops", topic="alerts", open=True, ticket_prefix="OPS",
                           ticket_seq=12))
        t = Ticket(key="OPS-12", channel_id="c1", title="Fix stale weather dedup",
                   body="the forecast repeats", reporter="user:admin",
                   assignee="agent:news", labels=["news", "bug"],
                   root_message_id="m1", run_id="r1")
        s.add(t); await s.commit()
        sub = Ticket(key="OPS-13", channel_id="c1", title="subtask", parent_id=t.id,
                     reporter="agent:news", state=TicketState.IN_PROGRESS,
                     priority=TicketPriority.P0)
        s.add(sub)
        s.add(TicketEvent(ticket_id=t.id, actor="agent:news", kind="moved",
                          from_value="open", to_value="in_progress",
                          reason="picked it up", message_id="m2", run_id="r1"))
        await s.commit()
    async with sfx() as s:
        conv = await s.get(Conversation, "c1")
        assert (conv.ticket_prefix, conv.ticket_seq) == ("OPS", 12)
        got = (await s.execute(select(Ticket).where(Ticket.key == "OPS-12"))).scalar_one()
        assert len(got.id) == 32 and got.state == TicketState.OPEN
        assert got.priority == TicketPriority.P2 and got.labels == ["news", "bug"]
        assert (got.assignee, got.reporter) == ("agent:news", "user:admin")
        assert got.closed_at is None and got.due_at is None
        assert got.created_at is not None and got.last_activity_at is not None
        child = (await s.execute(select(Ticket).where(Ticket.key == "OPS-13"))).scalar_one()
        assert child.parent_id == got.id and child.labels == []
        assert (child.state, child.priority) == ("in_progress", "p0")
        ev = (await s.execute(select(TicketEvent))).scalar_one()
        assert (ev.ticket_id, ev.kind, ev.to_value) == (got.id, "moved", "in_progress")
        assert len(ev.id) == 32 and ev.created_at is not None


async def test_a_dm_carries_no_prefix(sfx):
    """The prefix is a project marker, and only a channel is a project — a dm
    leaves it null (uniqueness among channels is the postgres partial index,
    which sqlite does not get; the API is what refuses a duplicate there)."""
    async with sfx() as s:
        s.add(Conversation(id="d1", connector="web", agent="news", kind="dm"))
        await s.commit()
    async with sfx() as s:
        conv = await s.get(Conversation, "d1")
        assert conv.ticket_prefix is None and conv.ticket_seq == 0


async def test_run_carries_the_ticket_it_was_summoned_from(sfx):
    async with sfx() as s:
        run = Run(agent="news", trigger="relay", requested_by="admin", prompt="p",
                  state=RunState.QUEUED, ticket_id="t1")
        s.add(run); await s.commit()
        rid = run.id
    async with sfx() as s:
        assert (await s.get(Run, rid)).ticket_id == "t1"


async def test_prefixes_are_seeded_on_the_shipped_projects(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    async with sfx() as s:
        rows = (await s.execute(select(Conversation)
                .where(Conversation.kind == "channel").order_by(Conversation.name))).scalars().all()
        assert (await s.get(SchemaMark, TICKETS_SEED_MARK)) is not None
    # #standup gets none (it is the ceremony room) and #wiki gets none
    # (it is a feed) — neither is a project.
    assert [(c.name, c.ticket_prefix, c.ticket_seq) for c in rows] == [
        ("general", "GEN", 0), ("ops", "OPS", 0), ("standup", None, 0),
        ("wiki", None, 0)]


async def test_the_seed_never_overrules_an_edited_prefix(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        ops = (await s.execute(select(Conversation)
               .where(Conversation.name == "ops"))).scalar_one()
        ops.ticket_prefix = "SRE"
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        ops = (await s.execute(select(Conversation)
               .where(Conversation.name == "ops"))).scalar_one()
    assert ops.ticket_prefix == "SRE"


async def test_init_db_is_idempotent(engine, sfx):
    await init_db(engine)
    async def counts():
        async with sfx() as s:
            return [(await s.execute(select(func.count()).select_from(t))).scalar_one()
                    for t in (Ticket.__table__, TicketEvent.__table__,
                              Conversation.__table__, SchemaMark.__table__)]
    before = await counts()
    await init_db(engine)
    assert await counts() == before


async def test_legacy_tables_gain_the_ticket_columns():
    """conversations and runs both predate Tickets on the live DB: init_db has
    to ALTER the new columns in, not just create the new tables."""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as c:
        await c.exec_driver_sql(
            "CREATE TABLE conversations (id VARCHAR(32) PRIMARY KEY, connector VARCHAR(32), "
            "external_ref VARCHAR(256), agent VARCHAR(128), kind VARCHAR(16), "
            "name VARCHAR(128), topic VARCHAR(256), title VARCHAR(256), "
            "status VARCHAR(16), claude_session_id VARCHAR(64), session_blob BLOB, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)")
        await c.exec_driver_sql(
            "INSERT INTO conversations (id, connector, kind, name, topic) "
            "VALUES ('ch1', 'web', 'channel', 'ops', 'alerts and operations')")
    await init_db(e)
    sfl = make_session_factory(e)
    async with sfl() as s:
        conv = await s.get(Conversation, "ch1")
        assert conv.ticket_prefix == "OPS"
        # ADD COLUMN cannot backfill a default: without the heal, every row
        # that predates Tickets carries a NULL seq and the first key allocated
        # under it is NULL + 1.
        assert conv.ticket_seq == 0
        s.add(Run(agent="news", trigger="relay", requested_by="admin", prompt="p",
                  ticket_id="t9"))
        await s.commit()
    await e.dispose()


# --- the tickets default grant (docs/design/20) ------------------------------
# A second sweep over the same rows as design-19's, with its own mark: the two
# ship a release apart, so an agent that predates Tickets has to be reached even
# though the relay sweep already ran and marked itself.

async def _grants(sfx, name: str) -> list[str]:
    async with sfx() as s:
        return (await s.get(AgentDef, name)).platform_tools


async def test_tickets_grant_backfill_covers_the_agents_that_already_exist(engine, sfx):
    # `wiki_grant=False` here and below: the Wiki (docs/design/21) sweeps the
    # same rows under its own mark, and letting it run would have every
    # assertion in this section carry a grant it is not about.
    async with sfx() as s:
        s.add(AgentDef(name="news", prompt="p", description="d",
                       platform_tools=["mcp__platform__relay"]))
        s.add(AgentDef(name="retired", prompt="p", description="d",
                       platform_tools=[], enabled=False))
        await s.commit()
    await init_db(engine, wiki_grant=False)
    assert await _grants(sfx, "news") == ["mcp__platform__relay",
                                          "mcp__platform__tickets"]
    assert await _grants(sfx, "retired") == []      # disabled agents are left alone
    async with sfx() as s:
        assert await s.get(SchemaMark, TICKETS_GRANT_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "news"))).scalars())
    # The sweep continues the design-15 change log, attributed to itself, so an
    # operator can find out later why an agent holds a tool nobody granted it.
    assert [(v.changed_by, v.changed_via) for v in versions] == [
        ("platform:tickets-default-grant", "migration")]


async def test_tickets_grant_backfill_honours_the_setting_and_runs_once(engine, sfx):
    """Off means the sweep does not run AND does not mark itself, so turning it
    on later still backfills; on means exactly one pass, ever."""
    async with sfx() as s:
        s.add(AgentDef(name="news", prompt="p", description="d", platform_tools=[]))
        await s.commit()
    await init_db(engine, tickets_grant=False, wiki_grant=False)
    assert await _grants(sfx, "news") == ["mcp__platform__relay"]
    async with sfx() as s:
        assert await s.get(SchemaMark, TICKETS_GRANT_MARK) is None
    await init_db(engine, tickets_grant=True, wiki_grant=False)
    assert await _grants(sfx, "news") == ["mcp__platform__relay",
                                          "mcp__platform__tickets"]
    # An admin taking it away afterwards is not undone by the next boot.
    async with sfx() as s:
        (await s.get(AgentDef, "news")).platform_tools = []
        await s.commit()
    await init_db(engine, wiki_grant=False)
    assert await _grants(sfx, "news") == []


# --- standup v2 and the health-monitor prompt (docs/design/20) ---------------
# Two one-time rewrites of things an operator can edit afterwards, so each is
# gated on its own mark and each refuses to touch text that is no longer the
# text it knows.

async def _standup(sfx) -> ScheduledJob:
    async with sfx() as s:
        return (await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "relay-standup"))).scalar_one()


async def test_the_standup_asks_about_tickets(engine, sfx):
    """A fresh database ends at v2 — the seed is still the v1 row, and this
    rewrite runs in the same init_db — so a new install and an upgraded one ask
    the room the same question."""
    await init_db(engine)
    job = await _standup(sfx)
    assert job.prompt == RELAY_STANDUP_PROMPT_V2
    assert "which tickets did you move" in job.prompt
    assert (job.cron, job.relay_channel) == ("0 9 * * *", "standup")
    async with sfx() as s:
        assert await s.get(SchemaMark, TICKETS_STANDUP_MARK) is not None
    await init_db(engine)
    assert (await _standup(sfx)).prompt == RELAY_STANDUP_PROMPT_V2


async def test_the_standup_rewrite_never_overrules_an_edited_prompt(engine, sfx):
    """The job is a row an admin owns. Rewriting a prompt somebody changed
    would be the platform overruling the operator once a night, so the rewrite
    only ever replaces the exact v1 text it shipped."""
    await init_db(engine)
    async with sfx() as s:
        job = (await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "relay-standup"))).scalar_one()
        job.prompt = "@all — ship anything?"
        # The mark is what makes this a ONE-time rewrite; without it the edit
        # below would be the case the guard has to survive on the next boot.
        await s.delete(await s.get(SchemaMark, TICKETS_STANDUP_MARK))
        await s.commit()
    await init_db(engine)
    assert (await _standup(sfx)).prompt == "@all — ship anything?"


async def _health_monitor(sfx, prompt: str) -> None:
    async with sfx() as s:
        s.add(AgentDef(name="health-monitor", description="d", prompt=prompt))
        await s.commit()


OPS_PROMPT = ("# health-monitor\nYou watch the platform.\n\n"
              "When something is wrong, post an alert in #ops.\n")


async def test_health_monitor_learns_to_open_tickets(engine, sfx):
    """Its alerts become work items with an owner (docs/design/20), and the
    change goes through the design-15 change log like any other definition
    write, attributed to the migration that made it."""
    await _health_monitor(sfx, OPS_PROMPT)
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "health-monitor")
        assert row.prompt.startswith(OPS_PROMPT.rstrip("\n"))
        assert row.prompt.endswith(HEALTH_MONITOR_TICKET_RULE)
        assert "\n\n" + HEALTH_MONITOR_TICKET_RULE in row.prompt
        assert await s.get(SchemaMark, TICKETS_HEALTH_MONITOR_MARK) is not None
        versions = list((await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "health-monitor").order_by(
            AgentVersion.version))).scalars())
    # Behind the three default-grant sweeps, which also write the log, and in
    # the order init_db runs them.
    assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
        (1, "platform:relay-default-grant", "migration"),
        (2, "platform:tickets-default-grant", "migration"),
        (3, "system:tickets", "migration"),
        (4, "platform:wiki-default-grant", "migration")]
    assert versions[-1].snapshot["prompt"] == (await _agent_prompt(sfx))
    # Once only: the appended paragraph is not re-appended on the next boot.
    before = await _agent_prompt(sfx)
    await init_db(engine)
    assert await _agent_prompt(sfx) == before


async def _agent_prompt(sfx) -> str:
    async with sfx() as s:
        return (await s.get(AgentDef, "health-monitor")).prompt


async def test_health_monitor_that_is_not_there_yet_is_reached_on_a_later_boot(
        engine, sfx):
    """The mark is written only when the rewrite is APPLIED: a platform whose
    health-monitor is created after Tickets ships still gets the instruction on
    the next boot, rather than having missed its one chance."""
    await init_db(engine)
    async with sfx() as s:
        assert await s.get(SchemaMark, TICKETS_HEALTH_MONITOR_MARK) is None
    await _health_monitor(sfx, OPS_PROMPT)
    await init_db(engine)
    assert HEALTH_MONITOR_TICKET_RULE in await _agent_prompt(sfx)


async def test_a_prompt_that_already_says_it_is_left_alone(engine, sfx):
    """The live prompt is edited through the API, so the phrase may already be
    there — by hand, or from a rollback to a snapshot that had it. Appending it
    again would say the same thing twice in the agent's own instructions."""
    already = OPS_PROMPT + "\nAlso: open an OPS ticket when a human is needed.\n"
    await _health_monitor(sfx, already)
    await init_db(engine)
    assert await _agent_prompt(sfx) == already
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(
            AgentVersion.__table__).where(
            AgentVersion.changed_by == "system:tickets"))).scalar_one() == 0


async def test_a_version_collision_leaves_the_boot_standing(engine, sfx, monkeypatch):
    """init_db's advisory lock serializes the other init_db callers and nothing
    else: an admin saving health-monitor through the API at the same moment
    takes the version number this rewrite computed. Losing that race must cost
    the rewrite a boot, not take the pod down with it — and the whole of
    init_db has to survive, not just this function."""
    import uuid as uuid_mod
    from agentplatform import db as db_mod
    # The first pass marks the grant sweeps (health-monitor does not exist yet),
    # so on the second one this is the only thing left writing a version row.
    await init_db(engine)
    await _health_monitor(sfx, OPS_PROMPT)
    taken = "f" * 32
    async with sfx() as s:
        s.add(AgentVersion(id=taken, agent="someone-else", version=1, snapshot={},
                           changed_by="admin", changed_via="admin"))
        await s.commit()
    monkeypatch.setattr(db_mod.uuid, "uuid4", lambda: uuid_mod.UUID(taken))

    await init_db(engine)

    assert await _agent_prompt(sfx) == OPS_PROMPT
    async with sfx() as s:
        # Neither half landed, and the rest of init_db did: the savepoint is
        # what keeps a refused insert from abandoning the whole transaction.
        assert await s.get(SchemaMark, TICKETS_HEALTH_MONITOR_MARK) is None
        assert await s.get(SchemaMark, TICKETS_STANDUP_MARK) is not None
        assert (await s.execute(select(func.count()).select_from(
            AgentVersion.__table__).where(
            AgentVersion.agent == "health-monitor"))).scalar_one() == 0
    # The next boot, against whatever the admin left behind, applies it.
    monkeypatch.undo()
    await init_db(engine)
    assert HEALTH_MONITOR_TICKET_RULE in await _agent_prompt(sfx)


# --- the system keys design/20 R1 orphaned ------------------------------------

async def test_the_per_agent_system_keys_are_revoked(engine, sfx):
    """Before R1 a system agent held ONE `system:<agent>` key with no run, and
    the dispatcher revoked the predecessor each time it re-minted. Per-run keys
    replaced that minting, so nothing reaps the last one any more: without this
    sweep every system agent keeps a live annotator credential forever."""
    async with sfx() as s:
        for agent in ("health-monitor", "run-summarizer"):
            s.add(ApiKey(name=f"system:{agent}", role="annotator", agent=agent,
                         key_hash=f"h-{agent}", prefix=f"ap_{agent[:6]}"))
        # The shape R1 mints now: same name, but it belongs to a run and is
        # revoked with it.
        s.add(ApiKey(name="system:health-monitor", role="annotator",
                     agent="health-monitor", run_id="r" * 32,
                     key_hash="h-live", prefix="ap_live"))
        s.add(ApiKey(name="system:gone", role="annotator", agent="gone",
                     key_hash="h-gone", prefix="ap_gone", revoked_at=utcnow()))
        # An admin-minted key that happens to be called `system:backup`. The
        # mint route validates no name and scopes no agent, so the sweep has to
        # be able to tell somebody's key from the launcher's.
        s.add(ApiKey(name="system:backup", role="annotator", agent=None,
                     key_hash="h-admin", prefix="ap_admin"))
        await s.commit()
        was_revoked = (await s.execute(select(ApiKey.revoked_at).where(
            ApiKey.key_hash == "h-gone"))).scalar_one()

    await init_db(engine)

    async def revoked():
        async with sfx() as s:
            return {k.key_hash: k.revoked_at
                    for k in (await s.execute(select(ApiKey))).scalars()}
    after = await revoked()
    assert after["h-health-monitor"] is not None
    assert after["h-run-summarizer"] is not None
    # A key that names its run is the live credential of a live run, and a key
    # with no agent was minted by a person, not by the launcher.
    assert after["h-live"] is None and after["h-admin"] is None
    assert after["h-gone"] == was_revoked
    async with sfx() as s:
        assert await s.get(SchemaMark, TICKETS_SYSTEM_KEYS_MARK) is not None

    # A second boot is a no-op: a new per-run key minted since must survive it,
    # and an orphan cannot be revoked twice at two different times.
    async with sfx() as s:
        s.add(ApiKey(name="system:health-monitor", role="annotator",
                     agent="health-monitor", run_id="s" * 32,
                     key_hash="h-later", prefix="ap_later"))
        await s.commit()
    await init_db(engine)
    assert await revoked() == {**after, "h-later": None}
