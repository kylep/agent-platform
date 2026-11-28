"""U2: storage pruning (plan §2.1, App. A "clip windows kept")."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agentplatform.db import (VillageCommand, VillageEvent, VillageMark, VillagePromptChange,
                              VillageSnapshot)
from agentplatform.village.store import CLIP_HALF_WINDOW_TICKS, VillagePruner

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)


def ago(days):
    return NOW - timedelta(days=days)


async def tick_ticks(sf, model):
    async with sf() as s:
        return sorted((await s.execute(select(model))).scalars().all(), key=lambda r: r.created_at)


async def test_periodic_snapshots_older_than_two_days_go_open_and_close_stay(sf):
    async with sf() as s:
        for kind in ("periodic", "open", "close"):
            for days in (1, 3, 200):
                s.add(VillageSnapshot(session_id="s", tick=days, state_hash="h", state={}, kind=kind,
                                      created_at=ago(days)))
        await s.commit()
    out = await VillagePruner(sf, now=lambda: NOW).prune_once()
    assert out["snapshots"] == 2
    async with sf() as s:
        left = {(r.kind, r.tick) for r in (await s.execute(select(VillageSnapshot))).scalars()}
    assert left == {("periodic", 1), ("open", 1), ("open", 3), ("open", 200),
                    ("close", 1), ("close", 3), ("close", 200)}


async def test_events_and_commands_older_than_90_days_go_except_clip_windows(sf):
    mark_tick = 1000
    async with sf() as s:
        s.add(VillageMark(id="m1", agent="pai", session_id="old", tick=mark_tick, x=1, y=1,
                          sentence="first rope", created_at=ago(120)))
        s.add(VillagePromptChange(id="c1", agent="pai", session_id="old", mark_id="m1"))
        s.add(VillagePromptChange(id="c2", agent="pai", session_id="old", mark_id=None))
        edge = CLIP_HALF_WINDOW_TICKS
        ticks = [mark_tick - edge - 1, mark_tick - edge, mark_tick, mark_tick + edge, mark_tick + edge + 1]
        for t in ticks:
            s.add(VillageEvent(session_id="old", epoch="e", tick=t, kind="say", created_at=ago(120)))
        s.add(VillageEvent(session_id="other", epoch="e", tick=mark_tick, kind="say", created_at=ago(120)))
        s.add(VillageEvent(session_id="old", epoch="e", tick=mark_tick, kind="say", created_at=ago(10)))
        s.add(VillageCommand(session_id="old", source="admin", action="say", created_at=ago(91)))
        s.add(VillageCommand(session_id="old", source="admin", action="say", created_at=ago(89)))
        await s.commit()
    out = await VillagePruner(sf, now=lambda: NOW).prune_once()
    assert out["events"] == 3 and out["commands"] == 1      # two outside the window + the other session's
    async with sf() as s:
        left = sorted((e.session_id, e.tick) for e in (await s.execute(select(VillageEvent))).scalars())
    assert left == sorted([("old", mark_tick - edge), ("old", mark_tick), ("old", mark_tick + edge),
                           ("old", mark_tick)])


async def test_a_clip_is_dropped_once_no_change_points_at_it(sf):
    async with sf() as s:
        s.add(VillageMark(id="m1", agent="pai", session_id="old", tick=50, x=1, y=1, sentence="x"))
        s.add(VillageEvent(session_id="old", epoch="e", tick=50, kind="say", created_at=ago(200)))
        await s.commit()
    assert (await VillagePruner(sf, now=lambda: NOW).prune_once())["events"] == 1


async def test_nothing_is_pruned_while_a_session_is_live(sf):
    async with sf() as s:
        s.add(VillageSnapshot(session_id="s", tick=1, state_hash="h", state={}, kind="periodic",
                              created_at=ago(10)))
        await s.commit()

    async def live():
        return True
    assert await VillagePruner(sf, now=lambda: NOW, live=live).prune_once() == {}
    assert len(await tick_ticks(sf, VillageSnapshot)) == 1


async def test_row_counts_are_logged_once_a_day(sf, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="village.store")
    pruner = VillagePruner(sf, now=lambda: NOW)
    await pruner.prune_once()
    await pruner.prune_once()
    assert sum("village table rows" in r.message for r in caplog.records) == 1
