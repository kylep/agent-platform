"""The `get_quota_usage` broker tool (docs/design/22 T4): one call, and the
sentences it renders from the answer.

The tool is thin in the same way `relay` and `wiki` are — one HTTP call the
caller's own token authorizes — but it carries something they do not: the
TEXT. The broker cannot import the backend, so `quota.render_text` exists twice
and the two copies have to agree to the character; `THE_DESIGNED_SHAPE` below
is the same string `services/backend/tests/test_quota.py` pins for the same
snapshot at the same `now`, and the pair is what makes a drift in either one a
red test rather than two formats for one fact. The fastmcp stub and the loaded
broker module come from `test_relay_tool.py`, so there is exactly one place
that knows how to import `broker.py` without an MCP runtime.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_quota_tool.py

Tests are synchronous and drive the coroutines with `asyncio.run`."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
# `caller` is an autouse fixture: importing it registers it for this module
# too, so the call resolves an identity and starts with a full bucket.
from test_relay_tool import (Calls, broker, caller,  # noqa: F401
                             published)

REFRESH = "/api/quota/refresh"
QUOTA = "/api/quota"

# The clock every relative time in this file is measured against — the same
# instant the backend's own render test uses.
NOW = datetime(2026, 9, 14, 15, 2, 11, tzinfo=timezone.utc)
RESET_5H = "2026-09-14T19:00:00+00:00"
RESET_7D = "2026-09-17T23:00:00+00:00"

# `quota_store.serialize` plus the refresh route's `probe`.
SNAPSHOT = {"five_hour": {"utilization": 0.22, "resets_at": RESET_5H},
            "seven_day": {"utilization": 0.81, "resets_at": RESET_7D},
            "status": "allowed", "observed_at": "2026-09-14T15:02:09+00:00",
            "source": "refresh", "stale": False, "age_seconds": 2,
            "probe": "count_tokens"}
EMPTY = {"five_hour": {"utilization": None, "resets_at": None},
         "seven_day": {"utilization": None, "resets_at": None},
         "status": None, "observed_at": None, "source": None,
         "stale": True, "age_seconds": None, "probe": None}

THE_DESIGNED_SHAPE = (
    "5-hour window: 22% used, resets in 3h 57m (2026-09-14 19:00 UTC).\n"
    "7-day window: 81% used, resets in 3d 7h (2026-09-17 23:00 UTC).\n"
    "Observed 2s ago (refresh). Status: allowed.")


@pytest.fixture
def calls(monkeypatch):
    recorded = Calls()

    # The parameter names are broker._call's own — a stub that renamed them
    # would pass a test the real call fails.
    async def _call(method, path, params=None, json=None):
        recorded.append((method, path, params, json))
        return recorded.replies.get(path, json_body(SNAPSHOT))

    monkeypatch.setattr(broker, "_call", _call)
    monkeypatch.setattr(broker, "_quota_now", lambda: NOW)
    return recorded


def json_body(snapshot: dict, **fields) -> str:
    return json.dumps({**snapshot, **fields})


def usage():
    return asyncio.run(broker.get_quota_usage())


# --- the one call -------------------------------------------------------------

def test_asking_is_one_refresh_and_nothing_else(calls):
    """A refresh, not a read: the API decides whether that costs a probe or
    replays the reading it already has, and it is the only thing that can."""
    usage()
    assert calls == [("POST", REFRESH, None, None)]


def test_the_tool_takes_no_arguments():
    import inspect
    assert not inspect.signature(broker.get_quota_usage).parameters


# --- the text -----------------------------------------------------------------

def test_the_answer_is_the_designed_shape(calls):
    assert usage() == THE_DESIGNED_SHAPE


def test_above_ninety_percent_the_answer_advises_deferring(calls):
    calls.replies[REFRESH] = json_body(
        SNAPSHOT, seven_day={"utilization": 0.93, "resets_at": RESET_7D})
    out = usage()
    assert "7-day window: 93% used" in out
    assert out.endswith(
        "Usage is above 90%: defer heavy work until the window resets.")


def test_at_exactly_ninety_percent_it_does_not(calls):
    calls.replies[REFRESH] = json_body(
        SNAPSHOT, seven_day={"utilization": 0.90, "resets_at": RESET_7D})
    assert "defer heavy work" not in usage()


def test_a_window_nobody_has_seen_reads_as_unknown(calls):
    calls.replies[REFRESH] = json_body(
        SNAPSHOT, five_hour={"utilization": None, "resets_at": None},
        status=None)
    assert usage() == ("5-hour window: unknown.\n"
                       "7-day window: 81% used, resets in 3d 7h "
                       "(2026-09-17 23:00 UTC).\n"
                       "Observed 2s ago (refresh).")


def test_before_the_first_observation_it_says_so(calls):
    calls.replies[REFRESH] = json_body(EMPTY)
    assert usage() == "No usage observation yet."


def test_a_percent_rounds_toward_the_bad_news(calls):
    """Half-UP, as the backend's `_percent` is: 22.5% reading as 22% is the
    kind of quiet off-by-one nobody ever finds."""
    calls.replies[REFRESH] = json_body(
        SNAPSHOT, five_hour={"utilization": 0.225, "resets_at": None})
    assert "5-hour window: 23% used." in usage()


# --- when the probe cannot run ------------------------------------------------

def test_a_503_falls_back_to_the_cached_reading(calls):
    """Only the probe can be unavailable — the snapshot is a row — and a stale
    reading an agent KNOWS is stale still answers "should I start this now"."""
    calls.replies[REFRESH] = 'error: 503 {"detail":"the claude proxy could not be reached"}'
    calls.replies[QUOTA] = json_body(
        SNAPSHOT, observed_at="2026-09-14T14:32:11+00:00", source="proxy",
        age_seconds=1800)
    out = usage()
    assert [(c[0], c[1]) for c in calls] == [("POST", REFRESH), ("GET", QUOTA)]
    assert out == (
        "5-hour window: 22% used, resets in 3h 57m (2026-09-14 19:00 UTC).\n"
        "7-day window: 81% used, resets in 3d 7h (2026-09-17 23:00 UTC).\n"
        "Observed 30m ago (proxy). Status: allowed.\n"
        "The usage probe is unavailable, so this is the cached reading from "
        "30m ago.")


def test_a_503_with_nothing_cached_is_an_error(calls):
    calls.replies[REFRESH] = "error: 503 no claude proxy configured"
    calls.replies[QUOTA] = json_body(EMPTY)
    assert usage() == ("error: usage could not be refreshed and nothing has "
                       "been observed yet")


def test_any_other_refusal_comes_back_as_an_error(calls):
    calls.replies[REFRESH] = 'error: 403 {"detail":"forbidden"}'
    assert usage() == 'error: 403 {"detail":"forbidden"}'
    assert [c[1] for c in calls] == [REFRESH]        # no second call


def test_an_unreadable_answer_is_an_error_not_a_reading(calls):
    calls.replies[REFRESH] = "<html>gateway</html>"
    assert usage().startswith("error: unreadable usage answer")


# --- the trail ----------------------------------------------------------------

def test_the_call_lands_in_the_audit_trail_as_the_quota_tool(calls, published):
    usage()
    topic, envelope, key = published[0]
    assert topic == broker._TOPIC_AUDIT
    data = envelope["data"]
    assert data["tool"] == "quota"
    assert data["decision"] == "allow"
    assert (data["agent"], data["run_id"], data["initiated_by"]) == (
        "news", "r1", "cron")


def test_a_refusal_is_audited_as_an_error(calls, published):
    calls.replies[REFRESH] = 'error: 403 {"detail":"forbidden"}'
    usage()
    assert published[-1][1]["data"]["decision"] == "error:tool"


# --- the gate (docs/design/24) ------------------------------------------------
# `quota_ok` is the reading turned into a decision the API already made: the
# tool's job is to hand the JSON back untouched — the model decides on a
# FIELD, not on a reading of prose — and to add the one sentence the design
# asks for after it. The only computing it does is the fallback: when the
# gate route itself is unavailable, the cached snapshot is judged here against
# the column defaults, and the answer says that is what happened.

QUOTA_OK = "/api/quota/ok"

GATE = {"ok": True, "five_hour_pct": 22, "seven_day_pct": 41,
        "five_hour_max_pct": 95, "seven_day_max_pct": 90,
        "stale": False, "reason": "ok"}


def gate(**fields) -> str:
    return json.dumps({**GATE, **fields})


def ok():
    return asyncio.run(broker.quota_ok())


def test_the_gate_is_one_get_of_the_ok_route(calls):
    calls.replies[QUOTA_OK] = gate()
    ok()
    assert calls == [("GET", QUOTA_OK, None, None)]


def test_the_gate_tool_takes_no_arguments():
    import inspect
    assert not inspect.signature(broker.quota_ok).parameters


def test_the_answer_is_the_json_line_then_the_sentence(calls):
    """The design's shape: `{...}` on the first line, exactly the API's
    answer, and "ok: 5h 22% ≤ 95, 7d 41% ≤ 90" on the second."""
    calls.replies[QUOTA_OK] = gate()
    first, second = ok().split("\n")
    assert json.loads(first) == GATE
    assert second == "ok: 5h 22% ≤ 95, 7d 41% ≤ 90"


def test_a_no_shows_which_window_failed(calls):
    calls.replies[QUOTA_OK] = gate(
        ok=False, five_hour_pct=92, five_hour_max_pct=80, seven_day_max_pct=50,
        reason="the 5-hour window is at 92%, over its 80% limit")
    first, second = ok().split("\n")
    assert json.loads(first)["ok"] is False
    assert second == "no: 5h 92% > 80, 7d 41% ≤ 50"


def test_a_stale_reading_says_so_in_the_sentence(calls):
    calls.replies[QUOTA_OK] = gate(stale=True)
    assert ok().endswith("ok: 5h 22% ≤ 95, 7d 41% ≤ 90 (stale reading)")


def test_no_reading_is_a_no_with_the_reason(calls):
    calls.replies[QUOTA_OK] = gate(ok=False, five_hour_pct=None, seven_day_pct=None,
                                   stale=True, reason="no reading yet")
    first, second = ok().split("\n")
    assert json.loads(first)["reason"] == "no reading yet"
    assert second == "no: no reading yet"


def test_a_503_judges_the_cached_reading_against_the_defaults(calls):
    """The route is the one that knows the caller's thresholds; when it cannot
    answer, the cached snapshot is judged here against the column defaults
    (80/50), marked stale, and the sentence says which thresholds it used."""
    calls.replies[QUOTA_OK] = 'error: 503 {"detail":"service unavailable"}'
    calls.replies[QUOTA] = json_body(
        SNAPSHOT, five_hour={"utilization": 0.22, "resets_at": RESET_5H},
        seven_day={"utilization": 0.81, "resets_at": RESET_7D})
    out = ok()
    assert [(c[0], c[1]) for c in calls] == [("GET", QUOTA_OK), ("GET", QUOTA)]
    first, second = out.split("\n")
    assert json.loads(first) == {
        "ok": False, "five_hour_pct": 22, "seven_day_pct": 81,
        "five_hour_max_pct": 80, "seven_day_max_pct": 50, "stale": True,
        "reason": "the 7-day window is at 81%, over its 50% limit"}
    assert second == ("no: 5h 22% ≤ 80, 7d 81% > 50 (stale reading, judged "
                      "against the platform defaults)")


def test_a_503_with_nothing_cached_is_a_no(calls):
    calls.replies[QUOTA_OK] = "error: 503 service unavailable"
    calls.replies[QUOTA] = json_body(EMPTY)
    first, second = ok().split("\n")
    assert json.loads(first)["ok"] is False
    assert json.loads(first)["reason"] == "no reading yet"
    assert second.startswith("no: no reading yet")


def test_any_other_gate_refusal_comes_back_as_an_error(calls):
    calls.replies[QUOTA_OK] = 'error: 403 {"detail":"forbidden"}'
    assert ok() == 'error: 403 {"detail":"forbidden"}'
    assert [c[1] for c in calls] == [QUOTA_OK]


def test_an_unreadable_gate_answer_is_an_error_not_a_decision(calls):
    calls.replies[QUOTA_OK] = "<html>gateway</html>"
    assert ok().startswith("error: unreadable usage answer")
    calls.replies[QUOTA_OK] = json_body(SNAPSHOT)      # a snapshot is not a decision
    assert ok().startswith("error: unreadable usage answer")


def test_the_gate_lands_in_the_audit_trail_as_quota_ok(calls, published):
    calls.replies[QUOTA_OK] = gate()
    ok()
    topic, envelope, key = published[0]
    assert topic == broker._TOPIC_AUDIT
    assert envelope["data"]["tool"] == "quota_ok"
    assert envelope["data"]["decision"] == "allow"


@pytest.fixture
def ungranted(monkeypatch):
    """A caller whose grant set names the reader but not the gate: the two are
    different grants on purpose (the gate is the engineer's and the QA's)."""
    async def _whoami():
        return {"agent": "news", "run_id": "r1", "initiated_by": "cron",
                "tools": ["mcp__platform__relay", "mcp__platform__get_quota_usage"]}

    monkeypatch.setattr(broker, "_whoami", _whoami)


def test_an_agent_not_granted_the_gate_is_refused_before_the_api(calls, published, ungranted):
    """Explicitly granted, so explicitly checked — the way `artifacts` and
    `image_gen` are: the refusal is in the tool's own words, no request
    reaches the API, and the attempt lands in the trail."""
    calls.replies[QUOTA_OK] = gate()
    assert ok() == "error: your agent does not declare the quota_ok tool"
    assert calls == []
    assert published[-1][1]["data"]["decision"] == "deny:undeclared"
