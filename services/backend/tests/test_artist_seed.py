"""The artist (docs/design/23): the seeded agent that answers `@artist` with
an image. One-time seed behind its own mark, in the librarian's shape, so an
admin who edits or deletes it keeps their version. It is not lifecycle
protected, and independently opts out of room-wide broadcasts."""
import uuid

import pytest
from sqlalchemy import func, select

from agentplatform.agents import AgentStore
from agentplatform.agentspec import TOOL_ARTIFACTS, TOOL_IMAGE_GEN, TOOL_RELAY
from agentplatform.config import Settings
from agentplatform.db import (ARTIST_SEED_MARK, ARTIST_PROMPT, CODEX_ARTIST_PROMPT,
                              CODEX_ARTIST_SEED_MARK, CODEX_ARTIST_SYSTEM_MARK,
                              AgentDef, AgentVersion,
                              Base, Conversation, RelayInvocation, Run, SchemaMark,
                              init_db, make_engine, make_session_factory)
from agentplatform.relay_router import RelayRouter
from agentplatform.relay_store import post_relay_message, relay_message_payload

NEVER_CLAIM = "Never claim an image exists without an artifact id from the tool"


@pytest.fixture
async def engine():
    """Tables but no migration yet — the shape init_db finds on the first boot
    after the Studio ships."""
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


async def test_the_artist_is_seeded_with_its_grants(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "artist")
        assert row is not None
        # Lifecycle and broadcast participation are independent policies.
        assert (row.system, row.enabled, row.can_invoke) == (False, True, False)
        assert row.responds_to_all is False
        assert (row.model, row.role) == ("sonnet", "operator")
        assert row.platform_tools == [TOOL_IMAGE_GEN, TOOL_ARTIFACTS, TOOL_RELAY, "mcp__platform__memory"]
        assert (row.harness_tools, row.skills, row.secrets) == ([], [], [])
        assert row.entrypoints == {"crons": [], "webhooks": [], "topics": [],
                                   "timezone": ""}
        assert row.description == ("Makes images on request: portraits, avatars, "
                                   "scene art, icons. Summon with @artist and a brief.")
        assert len(row.description) <= 512
        assert row.prompt == ARTIST_PROMPT
        assert await s.get(SchemaMark, ARTIST_SEED_MARK) is not None


async def test_codex_artist_is_a_separate_subscription_backed_specialist(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "codex-artist")
        assert row is not None
        assert (row.runtime, row.model, row.role) == ("codex", "gpt-5.6-luna", "operator")
        assert (row.system, row.enabled, row.can_invoke) == (True, True, False)
        assert row.platform_tools == [TOOL_ARTIFACTS, TOOL_RELAY, "mcp__platform__memory"]
        assert TOOL_IMAGE_GEN not in row.platform_tools
        assert row.skills == ["imagegen"]
        assert row.prompt == CODEX_ARTIST_PROMPT
        assert "$imagegen" in row.prompt and "mcp__platform__image_gen" in row.prompt
        assert await s.get(SchemaMark, CODEX_ARTIST_SEED_MARK) is not None
        assert await s.get(SchemaMark, CODEX_ARTIST_SYSTEM_MARK) is not None
    versions = await _versions(sfx, "codex-artist")
    assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
        (1, "system:codex-artist", "seed")]
    assert versions[0].snapshot["system"] is True


async def test_an_existing_codex_artist_becomes_system_once(engine, sfx):
    """The shipped non-system row moves with a versioned, one-time migration."""
    async with sfx() as s:
        s.add(AgentDef(name="codex-artist", runtime="codex", system=False))
        s.add(SchemaMark(name=CODEX_ARTIST_SEED_MARK))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "codex-artist")
        assert row.system is True
        assert await s.get(SchemaMark, CODEX_ARTIST_SYSTEM_MARK) is not None
    versions = await _versions(sfx, "codex-artist")
    system_versions = [v for v in versions
                       if v.changed_by == "platform:codex-artist-system"]
    assert [(v.changed_via, v.snapshot["system"]) for v in system_versions] == [
        ("migration", True)]
    await init_db(engine)
    assert len([v for v in await _versions(sfx, "codex-artist")
                if v.changed_by == "platform:codex-artist-system"]) == 1


def test_the_prompt_carries_the_rules_that_matter():
    """The two that cost money or trust if forgotten, plus the house styles
    and the model guidance the design names — pinned by their exact words so
    a rewrite that loses one fails here rather than in #art."""
    assert NEVER_CLAIM in ARTIST_PROMPT
    for phrase in ("warm, heroic storybook fantasy illustration; painterly colour "
                   "with clean ink linework; no text, no watermark, no border",
                   "minimalist flat vector, dark charcoal background, subject fills "
                   "70 %, square",
                   "gpt-image-2.5-flare", "flux-2-klein-4b", "flux-2-pro",
                   "[[artifact:<id>]]", "UNTRUSTED"):
        assert phrase in ARTIST_PROMPT, phrase


def test_the_prompt_tells_it_when_not_to_draw():
    """Direct mentions can still be conversation rather than image briefs."""
    assert "do NOT call `image_gen`" in ARTIST_PROMPT
    assert 'artifacts(action="list", owner="agent:artist")' in ARTIST_PROMPT
    assert "nothing today" in ARTIST_PROMPT


async def test_the_artist_has_exactly_one_version_after_a_fresh_init(engine, sfx):
    """The seed runs AFTER the default-grant sweeps, which have marked
    themselves by then: born holding every grant it needs, the artist's change
    log opens with one row, the seed's own, and nothing stamps a migration
    version on top of it."""
    await init_db(engine)
    versions = await _versions(sfx, "artist")
    assert [(v.version, v.changed_by, v.changed_via) for v in versions] == [
        (1, "system:artist", "seed")]
    assert versions[0].snapshot["platform_tools"] == [TOOL_IMAGE_GEN, TOOL_ARTIFACTS,
                                                       TOOL_RELAY, "mcp__platform__memory"]
    assert versions[0].snapshot["system"] is False
    assert versions[0].snapshot["model"] == "sonnet"


async def test_the_seed_is_idempotent(engine, sfx):
    await init_db(engine)
    await init_db(engine)
    assert len(await _versions(sfx, "artist")) == 1
    async with sfx() as s:
        assert (await s.execute(select(func.count()).select_from(AgentDef.__table__)
                                .where(AgentDef.__table__.c.name == "artist"))
                ).scalar_one() == 1


async def test_an_existing_artist_is_adopted_not_overwritten(engine, sfx):
    async with sfx() as s:
        s.add(AgentDef(name="artist", prompt="mine", description="mine",
                       platform_tools=[]))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        row = await s.get(AgentDef, "artist")
        assert (row.prompt, row.model) == ("mine", "")
        assert await s.get(SchemaMark, ARTIST_SEED_MARK) is not None
    assert "system:artist" not in [v.changed_by for v in await _versions(sfx, "artist")]


async def test_the_artist_mark_is_the_off_switch(engine, sfx):
    await init_db(engine)
    async with sfx() as s:
        await s.delete(await s.get(AgentDef, "artist"))
        await s.commit()
    await init_db(engine)
    async with sfx() as s:
        assert await s.get(AgentDef, "artist") is None


# --- summonable ---------------------------------------------------------------


async def test_at_artist_in_a_channel_wakes_the_artist(sf, producer):
    """AC-3's first half, through the real router: the seeded row is a live,
    valid agent, so `@artist <brief>` is a mention run like any other."""
    store = AgentStore(sf)
    await store.reload()
    async with sf() as s:
        conv = Conversation(connector="web", kind="channel", open=True,
                            name=f"room-{uuid.uuid4().hex[:8]}", title="#room")
        s.add(conv)
        await s.flush()
        msg = await post_relay_message(s, conv, author="user:admin",
                                       body="@artist a fox, flat icon please")
        await s.commit()
        payload = relay_message_payload(msg, conv)
    await RelayRouter(Settings(), sf, producer, store).handle(payload)
    async with sf() as s:
        runs = list((await s.execute(select(Run))).scalars())
        decided = [(i.agent, i.decision, i.reason) for i in
                   (await s.execute(select(RelayInvocation))).scalars()]
    assert [(r.agent, r.trigger, r.depth) for r in runs] == [("artist", "mention", 0)]
    assert decided == [("artist", "invoked", "mention")]
    assert "a fox, flat icon please" in runs[0].prompt
