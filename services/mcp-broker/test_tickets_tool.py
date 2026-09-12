"""The `tickets` broker tool (docs/design/20 T5): what each action does to the
platform API.

Same shape as `test_relay_tool.py`, and the same reason: the tool is thin, so
what is worth pinning is the mapping from an action word to one HTTP call —
method, path, query, body — plus the refusals it answers itself rather than
letting the model read a 4xx. The fastmcp stub and the loaded broker module
come from that file, so there is exactly one place that knows how to import
`broker.py` without an MCP runtime.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_tickets_tool.py

Tests are synchronous and drive the coroutines with `asyncio.run`."""
import asyncio
import json

import pytest
from test_relay_tool import CHANNELS, Calls, broker

GENERAL, DESK = CHANNELS[0]["id"], CHANNELS[1]["id"]
KEY = "OPS-12"

# Rows as /api/tickets answers them, trimmed to the fields the tool reads.
OPEN_ROW = {"id": "11" * 16, "key": KEY, "state": "in_progress", "priority": "p1",
            "assignee": "agent:news", "title": "dedup the forecast"}
DONE_ROW = {"id": "22" * 16, "key": "OPS-9", "state": "done", "priority": "p2",
            "assignee": None, "title": "ship the board"}


@pytest.fixture
def calls(monkeypatch):
    recorded = Calls()
    listing = json.dumps(CHANNELS)

    async def _call(method, path, params=None, json=None):
        recorded.append((method, path, params, json))
        if (method, path) == ("GET", "/api/relay/channels"):
            return recorded.replies.get("channels", listing)
        return recorded.replies.get(path, '{"ok": true}')

    monkeypatch.setattr(broker, "_call", _call)
    return recorded


def tickets(**kw):
    return asyncio.run(broker.tickets(**kw))


# --- create -------------------------------------------------------------------

def test_create_sends_only_what_was_given(calls):
    tickets(action="create", channel=GENERAL, title="dedup the forecast")
    assert calls == [("POST", "/api/tickets", None,
                      {"channel": GENERAL, "title": "dedup the forecast"})]


def test_create_carries_every_optional_field(calls):
    tickets(action="create", channel=GENERAL, title="t", body="why",
            assignee="agent:news", priority="p0", labels=["bug"], parent="OPS-1",
            due="2026-09-20T00:00:00Z", notify=False)
    assert calls[-1][3] == {"channel": GENERAL, "title": "t", "body": "why",
                            "assignee": "agent:news", "priority": "p0",
                            "labels": ["bug"], "parent": "OPS-1",
                            "due_at": "2026-09-20T00:00:00Z", "notify": False}


def test_create_resolves_a_channel_name(calls):
    tickets(action="create", channel="#news-desk", title="t")
    assert [c[1] for c in calls] == ["/api/relay/channels", "/api/tickets"]
    assert calls[-1][3]["channel"] == DESK


def test_create_refuses_an_unknown_channel_without_filing(calls):
    out = tickets(action="create", channel="#nowhere", title="t")
    assert out == "error: no channel named #nowhere"
    assert [c[1] for c in calls] == ["/api/relay/channels"]


def test_create_needs_a_title_before_it_touches_the_channel(calls):
    out = tickets(action="create", channel=GENERAL)
    assert out.startswith("error:") and "title" in out
    assert calls == []


def test_create_needs_a_channel(calls):
    out = tickets(action="create", title="t")
    assert out.startswith("error:") and "channel" in out
    assert calls == []


def test_a_refused_create_is_the_budget_refusal_the_agent_reads(calls):
    calls.replies["/api/tickets"] = (
        "error: 429 {\"detail\":\"you have opened 20 tickets in the last hour\"}")
    out = tickets(action="create", channel=GENERAL, title="t")
    assert out.startswith("error: 429") and "20 tickets" in out


# --- get ----------------------------------------------------------------------

DETAIL = {"ticket": {"key": KEY, "channel_id": GENERAL, "state": "open"},
          "events": [{"kind": "created"}], "root_message_id": "m1",
          "runs": [{"id": "r1"}], "thinking": None}
THREAD = [{"id": "m1", "body": "OPS-12 opened"}, {"id": "m2", "body": "on it"}]


def test_get_returns_the_ticket_its_events_the_thread_and_the_runs(calls):
    calls.replies[f"/api/tickets/{KEY}"] = json.dumps(DETAIL)
    calls.replies[f"/api/relay/channels/{GENERAL}/messages"] = json.dumps(THREAD)
    out = json.loads(tickets(action="get", key=KEY))
    assert calls == [
        ("GET", f"/api/tickets/{KEY}", None, None),
        ("GET", f"/api/relay/channels/{GENERAL}/messages",
         {"thread": "m1", "limit": 40}, None),
    ]
    assert out == {"ticket": DETAIL["ticket"], "events": DETAIL["events"],
                   "thread": THREAD, "runs": DETAIL["runs"], "thinking": None}


def test_get_skips_the_thread_when_there_is_no_card(calls):
    calls.replies[f"/api/tickets/{KEY}"] = json.dumps({**DETAIL, "root_message_id": None})
    out = json.loads(tickets(action="get", key=KEY))
    assert [c[1] for c in calls] == [f"/api/tickets/{KEY}"]
    assert out["thread"] == []


def test_get_passes_a_refused_ticket_straight_back(calls):
    calls.replies[f"/api/tickets/{KEY}"] = "error: 404 {\"detail\":\"unknown ticket\"}"
    out = tickets(action="get", key=KEY)
    assert out.startswith("error: 404")
    assert [c[1] for c in calls] == [f"/api/tickets/{KEY}"]


def test_get_keeps_the_ticket_when_only_the_thread_is_refused(calls):
    """The ticket is the answer and the thread is context: losing the read
    because the room would not answer would be the worse failure."""
    calls.replies[f"/api/tickets/{KEY}"] = json.dumps(DETAIL)
    calls.replies[f"/api/relay/channels/{GENERAL}/messages"] = "error: 403 nope"
    out = json.loads(tickets(action="get", key=KEY))
    assert out["ticket"] == DETAIL["ticket"]
    assert out["thread"] == "error: 403 nope"


# --- list and search ----------------------------------------------------------

def test_list_defaults_to_mine_and_hides_the_closed_ones(calls):
    calls.replies["/api/tickets"] = json.dumps([OPEN_ROW, DONE_ROW])
    out = tickets(action="list")
    assert calls == [("GET", "/api/tickets",
                      {"channel": None, "state": None, "assignee": None,
                       "mine": "true", "limit": 50}, None)]
    assert out.splitlines() == ["OPS-12  in_progress  p1  agent:news  dedup the forecast"]


def test_an_explicit_state_is_asked_for_and_kept(calls):
    calls.replies["/api/tickets"] = json.dumps([DONE_ROW])
    out = tickets(action="list", state="done")
    assert calls[-1][2]["state"] == "done"
    assert out == "OPS-9  done  p2  unassigned  ship the board"


def test_an_assignee_replaces_mine(calls):
    tickets(action="list", assignee="agent:health-monitor")
    assert calls[-1][2]["assignee"] == "agent:health-monitor"
    assert calls[-1][2]["mine"] is None


def test_assignee_any_is_the_whole_board(calls):
    tickets(action="list", assignee="any")
    assert calls[-1][2]["assignee"] is None and calls[-1][2]["mine"] is None


def test_list_resolves_a_channel_name(calls):
    tickets(action="list", channel="#news-desk")
    assert [c[1] for c in calls] == ["/api/relay/channels", "/api/tickets"]
    assert calls[-1][2]["channel"] == DESK


def test_an_empty_board_says_so(calls):
    calls.replies["/api/tickets"] = "[]"
    assert tickets(action="list") == "no tickets"


def test_a_refused_list_is_not_rendered_as_rows(calls):
    calls.replies["/api/tickets"] = "error: 403 not your business"
    assert tickets(action="list") == "error: 403 not your business"


def test_search_scopes_to_a_channel_and_keeps_closed_work(calls):
    calls.replies["/api/tickets"] = json.dumps([DONE_ROW])
    out = tickets(action="search", q="board", channel=GENERAL)
    assert calls[-1] == ("GET", "/api/tickets",
                         {"q": "board", "channel": GENERAL, "limit": 50}, None)
    assert out == "OPS-9  done  p2  unassigned  ship the board"


@pytest.mark.parametrize("given,want", [(5000, 500), (0, 1)])
def test_limits_are_clamped_to_what_the_api_accepts(calls, given, want):
    tickets(action="list", limit=given)
    assert calls[-1][2]["limit"] == want


# --- update, move, assign, comment --------------------------------------------

def test_update_sends_only_the_named_fields(calls):
    tickets(action="update", key=KEY, title="new title", due="2026-10-01")
    assert calls == [("PATCH", f"/api/tickets/{KEY}", None,
                      {"title": "new title", "due_at": "2026-10-01"})]


def test_update_with_nothing_to_change_is_refused(calls):
    out = tickets(action="update", key=KEY, reason="because")
    assert out.startswith("error:") and "title" in out
    assert calls == []


def test_move_sends_the_state_and_its_reason(calls):
    tickets(action="move", key=KEY, state="review", reason="ready for a look")
    assert calls == [("POST", f"/api/tickets/{KEY}/move", None,
                      {"state": "review", "reason": "ready for a look"})]


def test_move_without_a_state_lists_the_real_ones(calls):
    out = tickets(action="move", key=KEY)
    assert out.startswith("error:") and "in_progress" in out
    assert calls == []


def test_an_unknown_state_is_named_not_attempted(calls):
    out = tickets(action="move", key=KEY, state="wip")
    assert out == ("error: state must be one of "
                   "open|in_progress|blocked|review|done|cancelled")
    assert calls == []


def test_blocked_without_a_reason_is_refused(calls):
    out = tickets(action="move", key=KEY, state="blocked")
    assert out.startswith("error:") and "reason" in out
    assert calls == []


def test_assign_hands_over_and_notifies_by_default(calls):
    tickets(action="assign", key=KEY, to="agent:news")
    assert calls == [("POST", f"/api/tickets/{KEY}/assign", None,
                      {"to": "agent:news"})]


def test_assign_none_is_the_unassign(calls):
    tickets(action="assign", key=KEY, to="none", notify=False)
    assert calls[-1][3] == {"to": None, "notify": False}


def test_a_bare_name_is_sent_as_an_agent(calls):
    """A model writes `to='pai'`, and the API would store a participant that
    summons nobody. The broker cannot know which agents exist — it prefixes and
    lets the API refuse a name that is not one — but a bare name is the only
    kind an agent holds, so `agent:` is the only reading."""
    tickets(action="assign", key=KEY, to="pai")
    assert calls[-1][3] == {"to": "agent:pai"}
    tickets(action="create", channel=GENERAL, title="t", assignee="pai")
    assert calls[-1][3]["assignee"] == "agent:pai"


def test_an_already_qualified_participant_is_left_alone(calls):
    """`user:` and `discord:` are participants this platform does not own, and
    `none`/`any` are the tool's own words, not names."""
    for to in ("user:kyle", "discord:12345"):
        tickets(action="assign", key=KEY, to=to)
        assert calls[-1][3] == {"to": to}
    tickets(action="assign", key=KEY, to="none")
    assert calls[-1][3] == {"to": None}
    tickets(action="list", assignee="any")
    assert calls[-1][2]["assignee"] is None and calls[-1][2]["mine"] is None


def test_a_bare_name_the_board_does_not_know_points_at_the_person_syntax(calls):
    """The tool guessed `agent:` — so when the API answers that there is no
    such agent, the guess is the likeliest thing that was wrong, and the
    refusal has to say what a person is written as. A caller who wrote the
    prefix itself already knows."""
    calls.replies[f"/api/tickets/{KEY}/assign"] = (
        'error: 400 {"detail":"unknown or disabled agent: admin"}')
    assert tickets(action="assign", key=KEY, to="admin").endswith(
        " — for a person write user:<name>")
    assert "for a person" not in tickets(action="assign", key=KEY, to="agent:admin")
    calls.replies["/api/tickets"] = (
        'error: 400 {"detail":"unknown or disabled agent: admin"}')
    assert tickets(action="create", channel=GENERAL, title="t",
                   assignee="admin").endswith(" — for a person write user:<name>")


def test_assign_needs_somebody_to_assign_to(calls):
    out = tickets(action="assign", key=KEY)
    assert out.startswith("error:") and "to" in out
    assert calls == []


def test_comment_replies_in_the_thread(calls):
    tickets(action="comment", key=KEY, body="blocked on the API")
    assert calls == [("POST", f"/api/tickets/{KEY}/comments", None,
                      {"body": "blocked on the API"})]


def test_comment_needs_something_to_say(calls):
    out = tickets(action="comment", key=KEY)
    assert out.startswith("error:") and "body" in out
    assert calls == []


# --- what the agent typed -----------------------------------------------------

@pytest.mark.parametrize("given", ["OPS-12", "ops-12", " OPS-12 "])
def test_a_key_is_read_as_a_key_however_it_was_typed(calls, given):
    tickets(action="comment", key=given, body="x")
    assert calls[-1][1] == f"/api/tickets/{KEY}/comments"


def test_an_id_goes_through_untouched(calls):
    ident = OPEN_ROW["id"]
    tickets(action="comment", key=ident, body="x")
    assert calls[-1][1] == f"/api/tickets/{ident}/comments"


def test_a_parent_key_is_normalised_too(calls):
    tickets(action="create", channel=GENERAL, title="t", parent="ops-1")
    assert calls[-1][3]["parent"] == "OPS-1"


@pytest.mark.parametrize("action", ["get", "update", "move", "assign", "comment"])
def test_every_write_needs_a_ticket_to_write_to(calls, action):
    out = tickets(action=action)
    assert out.startswith("error:") and "key" in out
    assert calls == []


def test_an_unknown_action_lists_the_real_ones(calls):
    out = tickets(action="close", key=KEY)
    assert out.startswith("error: action must be one of")
    for action in ("create", "get", "list", "update", "move", "assign",
                   "comment", "search"):
        assert action in out
    assert calls == []


def test_an_unknown_priority_is_named_not_attempted(calls):
    out = tickets(action="create", channel=GENERAL, title="t", priority="urgent")
    assert out == "error: priority must be one of p0|p1|p2|p3"
    assert calls == []


# --- authorship is the token, never an argument -------------------------------

EVERY_ACTION = [
    {"action": "create", "channel": GENERAL, "title": "t", "body": "b",
     "assignee": "agent:news", "priority": "p1", "labels": ["x"], "parent": "OPS-1",
     "due": "2026-10-01", "notify": True},
    {"action": "get", "key": KEY},
    {"action": "list", "channel": GENERAL, "state": "open", "assignee": "agent:news"},
    {"action": "update", "key": KEY, "title": "t", "body": "b", "priority": "p3",
     "labels": ["x"], "due": "2026-10-01", "reason": "r"},
    {"action": "move", "key": KEY, "state": "blocked", "reason": "r"},
    {"action": "assign", "key": KEY, "to": "agent:news", "notify": False},
    {"action": "comment", "key": KEY, "body": "b"},
    {"action": "search", "q": "weather", "channel": GENERAL},
]


def test_no_action_can_claim_to_be_somebody_else(calls):
    """The reporter, the actor and the comment's author are the forwarded
    bearer. A tool that accepted any of them as text is how a prompt-injected
    agent files work under another agent's name."""
    for kwargs in EVERY_ACTION:
        tickets(**kwargs)
    sent = [c[3] for c in calls if c[3]] + [c[2] for c in calls if c[2]]
    for payload in sent:
        assert not {"actor", "reporter", "author", "agent", "run_id"} & set(payload)


def test_the_tool_takes_no_authorship_argument():
    import inspect
    names = set(inspect.signature(broker.tickets).parameters)
    assert not names & {"actor", "reporter", "author", "as_agent", "agent"}


def test_the_docstring_teaches_the_rules_briefly():
    """The docstring IS the tool's interface to the model: the four things an
    agent cannot discover by trying are that it acts as itself, when to move a
    ticket, that it must not close what it did not do, and that an assign wakes
    somebody."""
    doc = broker.tickets.__doc__
    assert len([line for line in doc.splitlines() if line.strip()]) <= 10
    lowered = doc.lower()
    for phrase in ("ops-12", "#name", "blocked", "never close", "assign", "hop",
                   "comment", "user:<name>", "discord:<id>"):
        assert phrase in lowered


# --- what may become a path ---------------------------------------------------

TRAVERSALS = ["X/../../whoami", "../runs", "OPS-12/move", "OPS 12", "OPS-12?x=1"]


@pytest.mark.parametrize("action", ["get", "update", "move", "assign", "comment"])
def test_a_key_that_could_escape_the_tool_is_never_a_path(calls, action):
    """`/api/tickets/{key}` is built from what the model typed, and httpx
    normalises `..` before it leaves: an unchecked key is a way to spend the
    caller's bearer on any GET its role allows."""
    for given in TRAVERSALS:
        out = tickets(action=action, key=given)
        assert out == ("error: invalid ticket key or id — a key looks like "
                       "OPS-12, an id is the ticket's own id")
    assert calls == []


def test_a_traversing_parent_is_refused_before_the_ticket_is_filed(calls):
    out = tickets(action="create", channel=GENERAL, title="t", parent="../../whoami")
    assert out.startswith("error: invalid ticket key or id")
    assert calls == []


@pytest.mark.parametrize("given", ["`OPS-12`", '"OPS-12"', "'ops-12'", "  OPS-12  "])
def test_a_key_the_model_wrapped_in_punctuation_is_still_a_key(calls, given):
    """Models write keys as code spans and in quotes constantly; a 404 for
    punctuation teaches the agent nothing about what it actually got wrong."""
    tickets(action="comment", key=given, body="x")
    assert calls[-1][1] == f"/api/tickets/{KEY}/comments"


# --- a page that did not fit --------------------------------------------------

def test_a_full_page_says_there_is_more(calls):
    calls.replies["/api/tickets"] = json.dumps([OPEN_ROW] * 3)
    out = tickets(action="list", limit=3).splitlines()
    assert len(out) == 4
    assert out[-1] == ("more tickets than shown — pass state=open (or in_progress/"
                       "blocked/review) to see one state fully")


def test_a_page_of_only_closed_tickets_does_not_read_as_an_empty_board(calls):
    """Dropping the closed rows happens after the API's page, so "nothing came
    back" and "nothing survived the filter" are different answers."""
    calls.replies["/api/tickets"] = json.dumps([DONE_ROW, DONE_ROW])
    out = tickets(action="list")
    assert out.startswith("only closed tickets in the first 2 — pass state=")


# --- clearing a field ---------------------------------------------------------

def test_update_can_clear_the_parent_and_the_due_date(calls):
    tickets(action="update", key=KEY, parent="none", due="none")
    assert calls[-1][3] == {"parent": None, "due_at": None}


def test_create_ignores_a_none_parent_rather_than_filing_one(calls):
    tickets(action="create", channel=GENERAL, title="t", parent="none")
    assert calls[-1][3] == {"channel": GENERAL, "title": "t"}
