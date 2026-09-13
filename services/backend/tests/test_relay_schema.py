"""Relay schema (docs/design/19): the channel/message tables and the one-off
backfill that reframes every legacy conversation as a DM channel."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from agentplatform.db import (Base, Conversation, RELAY_BACKFILL_MARK,
                              RELAY_DM_KEY_MARK, RELAY_STANDUP_MARK,
                              RELAY_STANDUP_PROMPT_V2, RelayBinding,
                              RelayInvocation, RelayMessage, RelayParticipant,
                              RelayReaction, RelaySession, RelayWake, Run, RunState,
                              ScheduledJob, SchemaMark, dm_key_of, init_db,
                              make_engine, make_session_factory, utcnow)


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
        ("standup", "what did you do today?", True, None),
        ("wiki", "every edit, as a diff card", True, None)]


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


async def test_dm_key_backfills_and_is_unique(engine, sfx):
    """`dm_key` is what makes opening a DM a lookup on an index instead of a
    lookup-then-insert, so every legacy DM has to carry one too."""
    cid, _ = await _mk_dm(sfx, turns=1)
    await init_db(engine)
    async with sfx() as s:
        assert (await s.get(Conversation, cid)).dm_key == dm_key_of(
            ["user:kyle", "agent:hello-world"])
        assert (await s.get(SchemaMark, RELAY_DM_KEY_MARK)) is not None
        rows = (await s.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'uq_conversations_dm_key'"))).all()
    assert rows, "the partial unique index on dm_key is missing"
    # A second boot neither re-derives nor duplicates.
    await init_db(engine)
    async with sfx() as s:
        assert (await s.get(Conversation, cid)).dm_key == dm_key_of(
            ["agent:hello-world", "user:kyle"])


async def test_a_duplicate_dm_pair_leaves_the_younger_row_keyless(engine, sfx):
    """Two rows for the same pair predate the index. The oldest is the real DM;
    the duplicate stays keyless rather than failing the boot."""
    t0 = utcnow() - timedelta(hours=2)
    first, _ = await _mk_dm(sfx, turns=1, created_at=t0)
    second, _ = await _mk_dm(sfx, turns=1, created_at=t0 + timedelta(hours=1))
    await init_db(engine)
    async with sfx() as s:
        assert (await s.get(Conversation, first)).dm_key is not None
        assert (await s.get(Conversation, second)).dm_key is None


# --- the relay default grant backfill (docs/design/19) ------------------------
# The other half of "default-granted": creation covers every agent made from
# here on, this covers the ones that already exist. Once, guarded by its mark,
# and through the design-15 change log so the grant is attributable.

RELAY = "mcp__platform__relay"


async def _defs(sfx, **agents):
    """Stage agent definition rows the way they stood before Relay shipped."""
    from agentplatform.db import AgentDef
    async with sfx() as s:
        for name, fields in agents.items():
            s.add(AgentDef(name=name, **fields))
        await s.commit()


async def _grants(sfx, name: str) -> list[str]:
    from agentplatform.db import AgentDef
    async with sfx() as s:
        return (await s.get(AgentDef, name)).platform_tools


async def _versions(sfx, name: str) -> list[tuple]:
    from agentplatform.db import AgentVersion
    async with sfx() as s:
        return [(v.version, v.changed_by, v.changed_via,
                 tuple(v.snapshot.get("platform_tools", ())))
                for v in (await s.execute(select(AgentVersion)
                          .where(AgentVersion.agent == name)
                          .order_by(AgentVersion.version))).scalars()]


# `tickets_grant=False` throughout: these are design-19's sweep, and Tickets
# (docs/design/20) runs a second one over the same rows with its own mark, whose
# own test lives in test_tickets_schema.py. Letting both run here would have
# every assertion below carry a grant it is not about.

async def test_default_grant_backfill_covers_the_agents_that_already_exist(engine, sfx):
    await _defs(sfx,
                news={"platform_tools": ["mcp__platform__query_app"]},
                chatty={"platform_tools": [RELAY]},
                retired={"platform_tools": [], "enabled": False})
    await init_db(engine, tickets_grant=False)
    assert await _grants(sfx, "news") == ["mcp__platform__query_app", RELAY]
    assert await _grants(sfx, "chatty") == [RELAY]      # already held it
    assert await _grants(sfx, "retired") == []          # disabled agents are left alone
    assert await _versions(sfx, "news") == [
        (1, "platform:relay-default-grant", "migration",
         ("mcp__platform__query_app", RELAY))]
    # An agent that needed no change files no version.
    assert await _versions(sfx, "chatty") == []
    assert await _versions(sfx, "retired") == []


async def test_default_grant_backfill_continues_the_agents_change_log(engine, sfx):
    from agentplatform.db import AgentVersion
    await _defs(sfx, news={"platform_tools": []})
    async with sfx() as s:
        s.add(AgentVersion(agent="news", version=7, snapshot={"name": "news"},
                           changed_by="admin", changed_via="admin"))
        await s.commit()
    await init_db(engine, tickets_grant=False)
    assert [v for v, *_ in await _versions(sfx, "news")] == [7, 8]


async def test_default_grant_backfill_runs_once_and_marks_itself(engine, sfx):
    from agentplatform.db import RELAY_GRANT_MARK
    await _defs(sfx, news={"platform_tools": []})
    await init_db(engine, tickets_grant=False)
    async with sfx() as s:
        assert await s.get(SchemaMark, RELAY_GRANT_MARK) is not None
    before = await _versions(sfx, "news")
    await init_db(engine, tickets_grant=False)
    assert await _versions(sfx, "news") == before
    # An agent created after the migration belongs to the create path, not to
    # a second sweep.
    await _defs(sfx, later={"platform_tools": []})
    await init_db(engine, tickets_grant=False)
    assert await _grants(sfx, "later") == []


async def test_a_removed_relay_grant_is_not_re_added(engine, sfx):
    from agentplatform.db import AgentDef
    await _defs(sfx, news={"platform_tools": []})
    await init_db(engine, tickets_grant=False)
    async with sfx() as s:
        row = await s.get(AgentDef, "news")
        row.platform_tools = []          # an admin takes it away via agents_grant
        await s.commit()
    await init_db(engine, tickets_grant=False)
    assert await _grants(sfx, "news") == []


async def test_default_grant_backfill_honours_the_setting(engine, sfx):
    """`relay_default_grant` off means the migration does not run — and does
    not mark itself either, so turning the setting on later still backfills."""
    from agentplatform.db import RELAY_GRANT_MARK
    await _defs(sfx, news={"platform_tools": []})
    await init_db(engine, default_grant=False, tickets_grant=False)
    assert await _grants(sfx, "news") == []
    async with sfx() as s:
        assert await s.get(SchemaMark, RELAY_GRANT_MARK) is None
    await init_db(engine, default_grant=True, tickets_grant=False)
    assert await _grants(sfx, "news") == [RELAY]


async def test_default_grant_backfill_survives_a_quarantined_row(engine, sfx):
    """A definition that no longer validates is repaired through the API, so a
    boot-time migration must step over it rather than crashloop every service
    on it. The agents around it are still backfilled."""
    from agentplatform.db import AgentDef
    await _defs(sfx, news={"platform_tools": []})
    async with sfx() as s:
        s.add(AgentDef(name="broken", role="not-a-role", platform_tools=[]))
        await s.commit()
    await init_db(engine, tickets_grant=False)
    assert await _grants(sfx, "news") == [RELAY]
    assert await _grants(sfx, "broken") == []
    assert await _versions(sfx, "broken") == []


async def test_default_grant_backfill_reads_a_null_enabled_as_enabled(engine, sfx):
    """`enabled` is an ADD COLUMN away from being NULL on rows written before
    it existed, and every other reader takes NULL as the column default. A
    backfill that tested for `= true` would silently skip the entire estate."""
    await _defs(sfx, news={"platform_tools": []})
    # The pre-`enabled` shape: drop the column so init_db's own additive
    # migration re-adds it — nullable, and NULL on the row already there.
    async with engine.begin() as c:
        await c.exec_driver_sql("ALTER TABLE agent_defs DROP COLUMN enabled")
    await init_db(engine, tickets_grant=False)
    async with sfx() as s:
        assert (await s.execute(text(
            "SELECT enabled FROM agent_defs WHERE name = 'news'"))).scalar() is None
    assert await _grants(sfx, "news") == [RELAY]


# --- the seeded #standup job (docs/design/19) --------------------------------
# Seeded ONCE and gated on its mark, which is the whole off-switch: the row is
# an operator's to disable or delete, and a seed that came back every boot would
# be the platform overruling them once a night.

async def _standup_jobs(sfx):
    async with sfx() as s:
        return (await s.execute(select(ScheduledJob).where(
            ScheduledJob.relay_channel == "standup"))).scalars().all()


async def test_standup_job_is_seeded_once(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    jobs = await _standup_jobs(sfx)
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.name, job.agent, job.cron, job.timezone, job.enabled) == \
        ("relay-standup", None, "0 9 * * *", "America/Toronto", True)
    # The v1 text this seeds is rewritten in the same init_db by Tickets
    # (docs/design/20, `_ensure_tickets_standup_v2`), so what a fresh database
    # ends up with is the question that asks about the board.
    assert job.prompt == RELAY_STANDUP_PROMPT_V2
    # Armed by the scheduler's first tick, not by the seed: a job seeded at
    # 08:59 must not go off the moment the API boots.
    assert job.next_fire is None
    async with sfx() as s:
        assert (await s.get(SchemaMark, RELAY_STANDUP_MARK)) is not None


async def test_disabling_the_standup_job_sticks(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        job = (await _standup_jobs(sfx))[0]
        (await s.get(ScheduledJob, job.id)).enabled = False
        await s.commit()
    await init_db(engine)
    assert [j.enabled for j in await _standup_jobs(sfx)] == [False]


async def test_deleting_the_standup_job_does_not_bring_it_back(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.delete(await s.get(ScheduledJob, (await _standup_jobs(sfx))[0].id))
        await s.commit()
    await init_db(engine)
    assert await _standup_jobs(sfx) == []
