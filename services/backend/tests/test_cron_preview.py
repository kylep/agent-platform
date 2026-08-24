"""The cron preview: one English renderer, and fire times that come from the
scheduler itself.

The preview exists so an operator can see what a schedule means before saving
it. That is only worth anything if it tells the truth — so the times here are
asserted against the same `next_fire` the scheduler runs on, daylight saving
included, and the sentences are pinned exactly (croniter is deterministic, so
there is nothing approximate to hedge about)."""
from datetime import datetime, timezone

import pytest

from agentplatform import cronenglish
from agentplatform.scheduler import next_fire, next_fires

# 2026-07-20 is a Monday, 10:02 UTC — a stable "now" for the frozen cases.
BASE = datetime(2026, 7, 20, 10, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize("expr,english", [
    # The shapes the schedule builder emits, in its own order.
    ("* * * * *", "Every minute"),
    ("*/15 * * * *", "Every 15 minutes"),
    ("0 * * * *", "Every hour, on the hour"),
    ("35 * * * *", "At minute 35 past every hour"),
    ("0 9 * * *", "At 09:00"),
    ("30 17 * * *", "At 17:30"),
    ("0 9 * * 1-5", "At 09:00, Monday through Friday"),
    ("0 9 * * 1,5", "At 09:00, only on Monday and Friday"),
    ("0 9 * * 0", "At 09:00, only on Sunday"),
    ("0 9 * * 7", "At 09:00, only on Sunday"),          # 7 is Sunday too
    ("0 9 15 * *", "At 09:00, on day 15 of the month"),
    # Hand-written expressions still get a reading, not a shrug.
    ("*/7 3-5 * * 1#2", "Every 7 minutes of hours 03 through 05, "
                        "on the 2nd Monday of the month"),
    ("0 0 L * *", "At 00:00, on the last day of the month"),
    ("0 22 * * 5L", "At 22:00, on the last Friday of the month"),
    ("0 9,17 * * *", "At 09:00 and 17:00"),
    ("0,30 9 * * *", "At 09:00 and 09:30"),
    ("0 0 1 1 *", "At 00:00, on day 1 of the month, in January"),
    ("0 0 1 */3 *", "At 00:00, on day 1 of the month, every 3rd month"),
    ("0 0 1 JAN-MAR MON", "At 00:00, on day 1 of the month, "
                          "from January through March, only on Monday"),
    ("15 2 */2 * *", "At 02:15, on every 2nd day of the month"),
    ("* 9 * * *", "Every minute of hour 09"),
    ("0 9-17/2 * * *", "At minute 00 past every 2nd hour from 09 through 17"),
    ("@daily", "At 00:00"),
])
def test_english_is_exact(expr, english):
    assert cronenglish.describe(expr) == english


@pytest.mark.parametrize("expr,why", [
    ("", "a cron expression is required"),
    ("0 9 * *", "expected 5 fields, got 4"),
    ("0 9 * * * *", "expected 5 fields, got 6"),
    ("0 99 * * *", "99 is outside 0-23"),
    ("0 nine * * *", "'nine' is not a number"),
])
def test_unreadable_expressions_say_why(expr, why):
    with pytest.raises(ValueError, match=why.replace("(", r"\(")):
        cronenglish.describe(expr)


def test_next_fires_chains_through_the_schedulers_own_next_fire():
    got = next_fires("*/15 * * * *", BASE, "", 3)
    assert got == [datetime(2026, 7, 20, 10, 15, tzinfo=timezone.utc),
                   datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc),
                   datetime(2026, 7, 20, 10, 45, tzinfo=timezone.utc)]
    # Not a reimplementation: each step IS next_fire from the one before.
    assert got[1] == next_fire("*/15 * * * *", got[0])


def test_next_fires_holds_wall_clock_across_a_dst_boundary():
    """Toronto falls back at 02:00 on 2026-11-01. A 09:00 daily job is 13:00
    UTC before the switch and 14:00 UTC after — the preview has to show that,
    because it is what the scheduler will do."""
    before = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)   # 16:00 in Toronto
    assert next_fires("0 9 * * *", before, "America/Toronto", 3) == [
        datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc),   # EDT, UTC-4
        datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc),    # EST, UTC-5
        datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc),
    ]


async def test_preview_endpoint_returns_english_and_the_next_three(admin_client, monkeypatch):
    monkeypatch.setattr("agentplatform.api.cron.utcnow", lambda: BASE)
    r = await admin_client.get("/api/cron/preview", params={"expr": "0 9 * * 1-5"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["english"] == "At 09:00, Monday through Friday"
    # 09:00 Monday is already past at 10:02, so the list starts Tuesday — the
    # preview never promises a fire the scheduler has missed.
    assert body["next"] == ["2026-07-21T09:00:00Z", "2026-07-22T09:00:00Z",
                            "2026-07-23T09:00:00Z"]


async def test_preview_endpoint_reads_the_expression_in_the_given_zone(admin_client, monkeypatch):
    monkeypatch.setattr("agentplatform.api.cron.utcnow", lambda: BASE)
    r = await admin_client.get("/api/cron/preview",
                               params={"expr": "0 9 * * *", "tz": "America/Toronto"})
    body = r.json()
    assert body["english"] == "At 09:00"
    # 09:00 Toronto in July is 13:00 UTC — the zone changes the instants, never
    # the sentence, which describes wall-clock time. 10:02 UTC is 06:02 there,
    # so today's fire is still ahead.
    assert body["next"][0] == "2026-07-20T13:00:00Z"


async def test_preview_endpoint_reports_an_invalid_expression_as_data(admin_client):
    r = await admin_client.get("/api/cron/preview", params={"expr": "0 99 * * *"})
    # 200, not 4xx: this is called per keystroke.
    assert r.status_code == 200
    body = r.json()
    assert body["error"] == "99 is outside 0-23"
    assert body["english"] == "" and body["next"] == []


async def test_preview_endpoint_rejects_a_garbage_timezone(admin_client):
    r = await admin_client.get("/api/cron/preview",
                               params={"expr": "0 9 * * *", "tz": "Mars/Olympus"})
    assert r.status_code == 200
    assert "Mars/Olympus" in r.json()["error"]
    assert r.json()["next"] == []


async def test_preview_endpoint_wants_an_expression(admin_client):
    assert (await admin_client.get("/api/cron/preview")).json()["error"] == \
        "a cron expression is required"


async def test_preview_endpoint_requires_a_reader(client):
    assert (await client.get("/api/cron/preview", params={"expr": "0 9 * * *"})).status_code == 401
