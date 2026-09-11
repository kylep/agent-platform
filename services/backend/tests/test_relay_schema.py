"""Relay schema (docs/design/19): the channel/message tables and the one-off
backfill that reframes every legacy conversation as a DM channel."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.db import (Base, Conversation, RELAY_BACKFILL_MARK, RelayBinding,
                              RelayInvocation, RelayMessage, RelayParticipant,
                              RelayReaction, RelaySession, RelayWake, Run, RunState,
                              SchemaMark, init_db, make_engine, make_session_factory,
                              utcnow)


@pytest.fixture
async def engine():
    """Tables but no relay migration yet — the shape init_db finds on the first
    boot after the relay ships, and the only way to stage legacy rows for a
    backfill that by design runs exactly once."""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
def sfx(engine):
    return make_session_factory(engine)


async def _mk_dm(sfx, *, connector="web", agent="hello-world", turns=2,
                 requested_by="admin", initiated_by="kyle", external_ref=None,
                 session_id="", blob=None, senders=None, created_at=None):
    """A legacy conversation as the pre-relay code would have written it."""
    t0 = created_at or utcnow() - timedelta(hours=1)
    async with sfx() as s:
        conv = Conversation(connector=connector, agent=agent, title="legacy",
                            external_ref=external_ref, claude_session_id=session_id,
                            session_blob=blob, created_at=t0)
        s.add(conv); await s.commit()
        rids = []
        for i in range(turns):
            run = Run(agent=agent, trigger="conversation",
                      requested_by=senders[i] if senders else requested_by,
                      initiated_by=initiated_by, prompt="ctx", conversation_id=conv.id,
                      user_message=f"q{i}", result=f"a{i}", state=RunState.SUCCEEDED,
                      created_at=t0 + timedelta(minutes=i),
                      finished_at=t0 + timedelta(minutes=i, seconds=30))
            s.add(run); await s.commit(); rids.append(run.id)
        return conv.id, rids


async def _messages(sfx, cid):
    async with sfx() as s:
        return (await s.execute(select(RelayMessage)
                                .where(RelayMessage.channel_id == cid)
                                .order_by(RelayMessage.created_at, RelayMessage.id))).scalars().all()


async def test_relay_models_round_trip(sfx):
    async with sfx() as s:
        s.add(Conversation(id="c1", connector="web", agent=None, kind="channel",
                           name="random", topic="anything", open=True))
        s.add(RelayParticipant(channel_id="c1", participant="agent:news", role="owner"))
        m = RelayMessage(channel_id="c1", author="user:admin", body="hi @news",
                         mentions=["news"], hop=1, card={"kind": "run"})
        s.add(m); await s.commit()
        s.add(RelayReaction(message_id=m.id, participant="agent:news", emoji="👀"))
        s.add(RelaySession(channel_id="c1", agent="news", claude_session_id="sess-1",
                           session_blob=b"\x00jsonl"))
        s.add(RelayBinding(channel_id="c1", connector="discord", external_ref="chan-9",
                           config={"webhook": "x"}))
        s.add(RelayWake(channel_id="c1", agent="news", since_message_id=m.id))
        s.add(RelayInvocation(channel_id="c1", message_id=m.id, agent="news",
                              decision="suppressed", reason="hop_limit", hop=4))
        await s.commit()
    async with sfx() as s:
        conv = await s.get(Conversation, "c1")
        assert (conv.kind, conv.name, conv.open, conv.archived_at) == ("channel", "random", True, None)
        got = (await s.execute(select(RelayMessage))).scalar_one()
        assert got.mentions == ["news"] and got.card == {"kind": "run"} and got.hop == 1
        assert got.kind == "text" and len(got.id) == 32
        sess = await s.get(RelaySession, ("c1", "news"))
        assert sess.session_blob == b"\x00jsonl"
        assert (await s.get(RelayBinding, (await s.execute(select(RelayBinding.id))).scalar_one())).connector == "discord"
        assert (await s.get(RelayWake, ("c1", "news"))).since_message_id == got.id
        assert (await s.get(RelayReaction, (got.id, "agent:news", "👀"))) is not None
        inv = (await s.execute(select(RelayInvocation))).scalar_one()
        assert inv.decision == "suppressed" and inv.reason == "hop_limit"


async def test_agentdef_icon_column(sfx):
    from agentplatform.db import AgentDef
    async with sfx() as s:
        s.add(AgentDef(name="news", icon="📰")); await s.commit()
    async with sfx() as s:
        assert (await s.get(AgentDef, "news")).icon == "📰"


async def test_backfill_turns_legacy_conversation_into_a_dm(engine, sfx):
    cid, rids = await _mk_dm(sfx)
    await init_db(engine)
    async with sfx() as s:
        conv = await s.get(Conversation, cid)
        assert conv.kind == "dm" and conv.name is None
        parts = {p.participant for p in (await s.execute(select(RelayParticipant)
                 .where(RelayParticipant.channel_id == cid))).scalars()}
    assert parts == {"user:kyle", "agent:hello-world"}
    msgs = await _messages(sfx, cid)
    assert [(m.author, m.body, m.run_id, m.hop) for m in msgs] == [
        ("user:kyle", "q0", None, 0), ("agent:hello-world", "a0", rids[0], 0),
        ("user:kyle", "q1", None, 0), ("agent:hello-world", "a1", rids[1], 0)]
    assert all(m.kind == "text" for m in msgs)


async def test_backfill_is_idempotent(engine, sfx):
    cid, _ = await _mk_dm(sfx)
    await init_db(engine)
    async def counts():
        async with sfx() as s:
            return [(await s.execute(select(func.count()).select_from(t))).scalar_one()
                    for t in (RelayMessage.__table__, RelayParticipant.__table__,
                              RelaySession.__table__, RelayBinding.__table__,
                              Conversation.__table__)]
    before = await counts()
    await init_db(engine)
    assert await counts() == before


async def test_backfill_discord_conversation_binds_and_names_the_human(engine, sfx):
    cid, _ = await _mk_dm(sfx, connector="discord", external_ref="thread-77",
                          requested_by="connector:discord:4242", turns=1)
    await init_db(engine)
    async with sfx() as s:
        parts = {p.participant for p in (await s.execute(select(RelayParticipant)
                 .where(RelayParticipant.channel_id == cid))).scalars()}
        binding = (await s.execute(select(RelayBinding)
                   .where(RelayBinding.channel_id == cid))).scalar_one()
    assert parts == {"discord:4242", "agent:hello-world"}
    assert (binding.connector, binding.external_ref) == ("discord", "thread-77")
    assert [m.author for m in await _messages(sfx, cid)] == ["discord:4242", "agent:hello-world"]


async def test_backfill_moves_the_session_blob(engine, sfx):
    cid, _ = await _mk_dm(sfx, session_id="sess-9", blob=b"\x01jsonl", turns=1)
    await init_db(engine)
    async with sfx() as s:
        sess = await s.get(RelaySession, (cid, "hello-world"))
    assert sess.claude_session_id == "sess-9" and sess.session_blob == b"\x01jsonl"


async def test_seeded_channels_exist_exactly_once(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    async with sfx() as s:
        rows = (await s.execute(select(Conversation)
                .where(Conversation.kind == "channel").order_by(Conversation.name))).scalars().all()
    assert [(c.name, c.topic, c.open, c.agent) for c in rows] == [
        ("general", "everyone", True, None),
        ("ops", "alerts and operations", True, None),
        ("standup", "what did you do today?", True, None)]


async def test_legacy_table_gains_the_channel_columns_and_backfills():
    """The live DB's conversations table predates the relay columns: init_db has
    to ALTER them in and then treat the rows it finds as dms. (Its agent column
    is NOT NULL there; dropping that is the postgres-only step in
    _ensure_relay_ddl, so this sqlite stand-in declares it nullable.)"""
    e = make_engine("sqlite+aiosqlite:///:memory:")
    async with e.begin() as c:
        await c.exec_driver_sql(
            "CREATE TABLE conversations (id VARCHAR(32) PRIMARY KEY, connector VARCHAR(32), "
            "external_ref VARCHAR(256), agent VARCHAR(128), title VARCHAR(256), "
            "status VARCHAR(16), claude_session_id VARCHAR(64), session_blob BLOB, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)")
        await c.exec_driver_sql(
            "INSERT INTO conversations VALUES ('old1', 'web', NULL, 'hello-world', 't', "
            "'active', 'sess-old', NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    await init_db(e)
    sfl = make_session_factory(e)
    async with sfl() as s:
        conv = await s.get(Conversation, "old1")
        assert conv.kind == "dm" and conv.open is False and conv.topic == ""
        assert (await s.get(RelaySession, ("old1", "hello-world"))).claude_session_id == "sess-old"
        parts = {p.participant for p in (await s.execute(select(RelayParticipant))).scalars()}
    assert parts == {"user:admin", "agent:hello-world"}
    await e.dispose()


async def test_backfill_runs_once_and_marks_itself(engine, sfx):
    await _mk_dm(sfx, turns=1)
    await init_db(engine)
    async with sfx() as s:
        assert (await s.get(SchemaMark, RELAY_BACKFILL_MARK)) is not None
    # A conversation created after the migration belongs to the live code paths
    # (T4), not to a second sweep of the backfill.
    later, _ = await _mk_dm(sfx, turns=1)
    await init_db(engine)
    assert await _messages(sfx, later) == []


async def test_backfill_gives_a_shared_external_ref_to_the_earliest_channel(engine, sfx):
    t0 = utcnow() - timedelta(hours=2)
    first, _ = await _mk_dm(sfx, connector="discord", external_ref="dupe", turns=1,
                            requested_by="connector:discord:1", created_at=t0)
    second, _ = await _mk_dm(sfx, connector="discord", external_ref="dupe", turns=1,
                             requested_by="connector:discord:1",
                             created_at=t0 + timedelta(minutes=5))
    await init_db(engine)   # must not die on the unique constraint
    async with sfx() as s:
        bindings = (await s.execute(select(RelayBinding))).scalars().all()
    assert [(b.channel_id, b.external_ref) for b in bindings] == [(first, "dupe")]
    assert second != first


async def test_backfill_attributes_each_turn_to_its_own_human(engine, sfx):
    cid, _ = await _mk_dm(sfx, connector="discord", external_ref="room-5", turns=2,
                          senders=["connector:discord:11", "connector:discord:22"])
    await init_db(engine)
    async with sfx() as s:
        parts = {p.participant for p in (await s.execute(select(RelayParticipant)
                 .where(RelayParticipant.channel_id == cid))).scalars()}
    assert parts == {"discord:11", "discord:22", "agent:hello-world"}
    assert [m.author for m in await _messages(sfx, cid)] == [
        "discord:11", "agent:hello-world", "discord:22", "agent:hello-world"]
