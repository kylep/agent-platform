"""Tickets schema (docs/design/20): the ticket tables, the project prefix a
channel carries, and the one-off seed that makes #general and #ops projects."""
import pytest
from sqlalchemy import func, select

from agentplatform.db import (Base, Conversation, Run, RunState, SchemaMark,
                              TICKETS_SEED_MARK, Ticket, TicketEvent, TicketPriority,
                              TicketState, init_db, make_engine, make_session_factory)


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
    # #standup gets none: it is the ceremony room, not a project.
    assert [(c.name, c.ticket_prefix, c.ticket_seq) for c in rows] == [
        ("general", "GEN", 0), ("ops", "OPS", 0), ("standup", None, 0)]


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
