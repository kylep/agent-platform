"""The QA (docs/design/25): the seeded dev agent that owns the tests, keeps
the TCMS current and QAs the live UI — with its home project `#qa` and the
02:00 `qa-nightly` job that summons it. Three one-time seeds behind their own
marks, in the coder's, #eng's and eng-queue's shapes: an admin who edits
or deletes any of them keeps their version. A normal, replaceable worker like
coder; `responds_to_all` independently keeps it out of `@all` and standup,
while its own job or an `@qa` by name wakes it."""
import pytest
from sqlalchemy import func, select

from agentplatform.agents import AgentStore
from agentplatform.agentspec import (TOOL_ARTIFACTS, TOOL_PLAYWRIGHT_MCP, TOOL_QUOTA_OK,
                                     TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI)
from agentplatform.config import Settings
from agentplatform.db import (QA_CHANNEL_MARK, QA_NIGHTLY_MARK, QA_NORMAL_AGENT_MARK,
                              QA_PROMPT, QA_SEED_MARK, QA_WELCOME_BODY, AgentDef, AgentVersion, Base,
                              Conversation, RelayMessage, Run, ScheduledJob, SchemaMark,
                              init_db, make_engine, make_session_factory)
from agentplatform.relay_router import RelayRouter
from agentplatform.testpaths import TEST_PATH_GLOBS


@pytest.fixture
async def engine():
    """Tables but no migration yet — the shape init_db finds on the first boot
    after design 25 ships."""
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


async def _qa_channels(sfx) -> list[Conversation]:
    async with sfx() as s:
        return list((await s.execute(select(Conversation).where(
            Conversation.kind == "channel", Conversation.name == "qa"))).scalars())


async def _nightly_jobs(sfx) -> list[ScheduledJob]:
    async with sfx() as s:
        return list((await s.execute(select(ScheduledJob).where(
            ScheduledJob.name == "qa-nightly"))).scalars())


# --- the row ------------------------------------------------------------------


async def test_the_qa_is_seeded_with_its_grants_role_and_thresholds(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "qa")
        assert row is not None
        # Lifecycle ownership and broadcast participation are explicit.
        assert (row.system, row.enabled, row.can_invoke) == (False, True, False)
        assert row.responds_to_all is False
        # sonnet: the nightly is bookkeeping most of the time. `dev` is the
        # run-profile rung, not an API scope.
        assert (row.model, row.role) == ("sonnet", "dev")
        assert (row.timeout_seconds, row.concurrency) == (7200, 1)
        # The expensive-browser gate: tighter than the coder's 95/90.
        assert (row.quota_5h_max_pct, row.quota_7d_max_pct) == (80, 50)
        # No `query_app`: that is the wide `annotator` rung, and the tcms
        # tool's read actions answer the same questions.
        assert row.platform_tools == [TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI,
                                      TOOL_QUOTA_OK, TOOL_ARTIFACTS,
                                      "mcp__platform__tcms", "mcp__platform__memory"]
        assert "mcp__platform__query_app" not in row.platform_tools
        assert row.harness_tools == ["Glob", "Grep", TOOL_PLAYWRIGHT_MCP]
        assert (row.skills, row.secrets) == ([], ["qa-web-login"])
        # One definition of "test code": the row's fence IS the module's list.
        assert row.push_path_globs == TEST_PATH_GLOBS
        assert row.push_path_globs is not TEST_PATH_GLOBS
        assert row.may_delete_tests is True
        assert row.description == (
            "Owns the tests: writes and prunes unit, integration and e2e tests, "
            "keeps the TCMS current, measures the suite and QAs the live UI — "
            "spending the browser only when the quota allows.")
        assert len(row.description) <= 512
        assert row.prompt == QA_PROMPT
        assert await s.get(SchemaMark, QA_SEED_MARK) is not None


def test_the_prompt_carries_the_rules_that_cost_trust():
    """The phrases the design names, pinned by their exact words so a rewrite
    that loses one fails here rather than on a branch."""
    for phrase in ("not spending the browser", "never weaken", "quota_ok",
                   "tcms", "sync_cases", "record_results", "bin/ap-verify --all",
                   "bin/ap-verify --changed", "bin/ap-upload", "bin/ap-web-login",
                   "walk.mjs", "index.json", ".ap/pr.md", "agent:coder",
                   "three times", "runtime_report", "flaky", "coverage_gaps",
                   "prune_candidates", "tcms/cases/", "UNTRUSTED", "blocked"):
        assert phrase in QA_PROMPT, phrase


def test_the_prompt_orders_nightly_walk_session_rules_and_hand_back():
    """Who it is and what it never touches, then the nightly, the walk, the
    session rule, the test rules, the hand-back — the design's order."""
    marks = ["## The nightly", "## The walk", "## The session rule",
             "## Test rules", "## Hand-back"]
    positions = [QA_PROMPT.index(m) for m in marks]
    assert positions == sorted(positions) and positions[0] > 0


async def test_the_qa_has_exactly_one_version_after_a_fresh_init(engine, sfx):
    """Seeded AFTER the default-grant sweeps, which have marked themselves by
    then: born holding every grant it needs, the QA's change log opens with
    one row, the seed's own."""
    await init_db(engine)
    versions = await _versions(sfx, "qa")
    assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
        (1, "system:qa", "seed")]
    snap = versions[0].snapshot
    assert "mcp__platform__tcms" in snap["platform_tools"]
    assert "mcp__platform__memory" in snap["platform_tools"]
    assert (snap["system"], snap["role"], snap["model"]) == (False, "dev", "sonnet")
    assert (snap["quota_5h_max_pct"], snap["quota_7d_max_pct"]) == (80, 50)
    assert (snap["push_path_globs"], snap["may_delete_tests"]) == (TEST_PATH_GLOBS, True)


async def test_an_existing_qa_is_adopted_not_overwritten(engine, sfx):
    async with sfx() as s:
        s.add(AgentDef(name="qa", prompt="mine", description="mine", platform_tools=[]))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "qa")
        assert (row.prompt, row.role, row.system) == ("mine", "operator", False)
        assert await s.get(SchemaMark, QA_SEED_MARK) is not None
    assert "system:qa" not in [v.changed_by for v in await _versions(sfx, "qa")]


async def test_an_existing_system_qa_is_reclassified_with_history(engine, sfx):
    async with sfx() as s:
        s.add(AgentDef(name="qa", prompt="mine", description="mine", system=True,
                       responds_to_all=False, platform_tools=[]))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "qa")
        assert row.system is False
        assert await s.get(SchemaMark, QA_NORMAL_AGENT_MARK) is not None
    versions = await _versions(sfx, "qa")
    assert ("platform:qa-normal-agent", "migration") in [
        (v.changed_by, v.changed_via) for v in versions]
    assert versions[-1].snapshot["system"] is False


async def test_the_qa_mark_is_the_off_switch(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.delete(await s.get(AgentDef, "qa"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert await s.get(AgentDef, "qa") is None


# --- #qa ----------------------------------------------------------------------


async def test_qa_is_seeded_as_a_project_with_a_welcome(engine, sfx):
    await init_db(engine)
    rooms = await _qa_channels(sfx)
    assert len(rooms) == 1
    room = rooms[0]
    assert (room.open, room.agent, room.ticket_prefix, room.ticket_seq) == (
        True, None, "QA", 0)
    async with sfx() as s:
        msgs = list((await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == room.id))).scalars())
        assert [(m.kind, m.author, m.body) for m in msgs] == [
            ("system", "system:relay", QA_WELCOME_BODY)]
        assert await s.get(SchemaMark, QA_CHANNEL_MARK) is not None
    assert QA_WELCOME_BODY == ("QA findings land here as QA-n tickets; the nightly "
                               "note says what ran")


async def test_an_existing_qa_room_is_adopted_and_only_a_null_prefix_is_set(engine, sfx):
    """The live site already has a `#qa` project with keys QA-1…QA-n: the
    seed must adopt it — topic, rows and sequence are theirs — and add a
    prefix only when the room has none."""
    async with sfx() as s:
        s.add(Conversation(connector="web", kind="channel", open=True, name="qa",
                           title="#qa", topic="ours", ticket_prefix=None, ticket_seq=0))
        await s.commit()
    await init_db(engine)
    rooms = await _qa_channels(sfx)
    assert len(rooms) == 1
    assert (rooms[0].topic, rooms[0].ticket_prefix) == ("ours", "QA")
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(RelayMessage.__table__)
                                .where(RelayMessage.__table__.c.channel_id == rooms[0].id))
                ).scalar_one() == 0


async def test_an_existing_qa_room_with_a_prefix_and_keys_keeps_them(engine, sfx):
    async with sfx() as s:
        s.add(Conversation(connector="web", kind="channel", open=True, name="qa",
                           title="#qa", topic="ours", ticket_prefix="QA", ticket_seq=16))
        await s.commit()
    await init_db(engine)
    rooms = await _qa_channels(sfx)
    assert (rooms[0].ticket_prefix, rooms[0].ticket_seq) == ("QA", 16)


async def test_a_qa_prefix_held_elsewhere_is_left_alone_and_logged(engine, sfx, caplog):
    """A prefix is unique across projects: when some other room already
    stamps `QA-n` keys, the seed neither steals it nor crashes the boot — it
    leaves both rooms as they are and says so."""
    async with sfx() as s:
        s.add(Conversation(connector="web", kind="channel", open=True, name="quality",
                           title="#quality", topic="theirs", ticket_prefix="QA",
                           ticket_seq=4))
        s.add(Conversation(connector="web", kind="channel", open=True, name="qa",
                           title="#qa", topic="ours", ticket_prefix=None, ticket_seq=0))
        await s.commit()
    with caplog.at_level("WARNING", logger="db"):
        await init_db(engine)
    rooms = await _qa_channels(sfx)
    assert (rooms[0].topic, rooms[0].ticket_prefix) == ("ours", None)
    async with sfx() as s:
        other = (await s.execute(select(Conversation).where(
            Conversation.name == "quality"))).scalar_one()
        assert (other.ticket_prefix, other.ticket_seq) == ("QA", 4)
        assert await s.get(SchemaMark, QA_CHANNEL_MARK) is not None
    assert any("QA" in r.getMessage() and "quality" in r.getMessage()
               for r in caplog.records)


# --- qa-nightly ---------------------------------------------------------------


async def test_the_nightly_job_is_seeded(engine, sfx):
    await init_db(engine)
    jobs = await _nightly_jobs(sfx)
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.cron, job.timezone) == ("0 2 * * *", "America/Toronto")
    # A relay-post job: the summons comes from the platform, because an
    # agent's own @mention carries a hop; QA independently opts out of `@all`.
    assert (job.relay_channel, job.agent) == ("qa", None)
    assert job.prompt == ("@qa — run the nightly: sync cases, run everything, record "
                          "the results, fix or file what you find, and leave a note here.")
    assert (job.enabled, job.next_fire) == (True, None)
    async with sfx() as s:
        assert await s.get(SchemaMark, QA_NIGHTLY_MARK) is not None


async def test_a_nightly_job_that_already_exists_is_adopted(engine, sfx):
    async with sfx() as s:
        s.add(ScheduledJob(name="qa-nightly", relay_channel="qa",
                           cron="0 3 * * *", prompt="mine"))
        await s.commit()
    await init_db(engine)
    jobs = await _nightly_jobs(sfx)
    assert [(j.cron, j.prompt) for j in jobs] == [("0 3 * * *", "mine")]


async def test_the_nightly_mark_is_the_off_switch(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.execute(ScheduledJob.__table__.delete().where(
            ScheduledJob.__table__.c.name == "qa-nightly"))
        await s.commit()
    await init_db(engine)
    assert await _nightly_jobs(sfx) == []


# --- idempotence, all three at once ------------------------------------------


async def test_the_three_seeds_are_idempotent(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    assert len(await _versions(sfx, "qa")) == 1
    assert len(await _qa_channels(sfx)) == 1
    assert len(await _nightly_jobs(sfx)) == 1
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(AgentDef.__table__)
                                .where(AgentDef.__table__.c.name == "qa"))
                ).scalar_one() == 1


# --- summonable, and only by name --------------------------------------------


async def _post(sf, channel_name: str, body: str):
    from agentplatform.relay_store import post_relay_message, relay_message_payload
    async with sf() as s:
        conv = (await s.execute(select(Conversation).where(
            Conversation.kind == "channel", Conversation.name == channel_name))).scalar_one()
        msg = await post_relay_message(s, conv, author="user:admin", body=body)
        await s.commit()
        return relay_message_payload(msg, conv)


async def test_at_qa_in_qa_summons_the_qa_as_a_dev_run(sf, producer):
    """AC-4 through the real store and the real router: a focused agent is
    filtered from `@all`'s roster, not from its own name — the nightly's
    `@qa` in `#qa` is exactly how the job wakes it."""
    store = AgentStore(sf)
    await store.reload()
    assert store.get("qa").manifest.role == "dev"
    payload = await _post(sf, "qa", "@qa — run the nightly")
    await RelayRouter(Settings(), sf, producer, store).handle(payload)
    async with sf() as s:
        runs = list((await s.execute(select(Run))).scalars())
    assert [(r.agent, r.trigger, r.depth) for r in runs] == [("qa", "mention", 0)]


async def test_at_all_in_standup_skips_focused_dev_agents(sf, producer):
    """The 09:00 standup must not buy full QA or coder dev pods."""
    store = AgentStore(sf)
    await store.reload()
    payload = await _post(sf, "standup", "@all — what did you do?")
    await RelayRouter(Settings(), sf, producer, store).handle(payload)
    async with sf() as s:
        agents = sorted(r.agent for r in (await s.execute(select(Run))).scalars())
    assert "coder" not in agents and "qa" not in agents


# --- the readiness gate ------------------------------------------------------


async def test_the_qa_is_blocked_until_its_login_secret_exists(admin_client, secret_store):
    """The row binds `qa-web-login` by name, and a binding is required-present
    (docs/design/10): the pod would otherwise get no `QA_WEB_*` env and
    `bin/ap-web-login` would fail confusingly at runtime. The spec's
    `required: false` is the SETUP flag (gates /api/setup, alarms the
    Dashboard), not the binding's strictness. In production the API mints the
    secret at boot before any run can dispatch, so the gate only shows during
    a rotation — and then the reason is the fix."""
    rows = {a["name"]: a for a in (await admin_client.get("/api/agents")).json()}
    assert rows["qa"]["blocked"] is True
    assert rows["qa"]["blocked_reason"] == "blocked: secret `qa-web-login` is not set"
    # Set out-of-band, never probed: existence is what a binding demands.
    await secret_store.set("qa-web-login", {"QA_WEB_USER": "qa", "QA_WEB_PASSWORD": "x"})
    rows = {a["name"]: a for a in (await admin_client.get("/api/agents")).json()}
    assert (rows["qa"]["blocked"], rows["qa"]["blocked_reason"]) == (False, None)
