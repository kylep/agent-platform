"""The `relay` broker tool (docs/design/19 T6): what each action does to the
platform API.

The tool is thin by design — it turns an action word into one HTTP call the
caller's own token authorizes — so what is worth pinning is exactly that
mapping: method, path, query and body, per action. `_call` is stubbed, which
also keeps this runnable with the backend venv: `broker.py` is imported with
fastmcp stood in for (the decorator's only job here is to hand back the
function), so no MCP server is ever started.

    cd services/backend && .venv/bin/python -m pytest -q ../mcp-broker/test_relay_tool.py

Tests are synchronous and drive the coroutines with `asyncio.run`, so the file
needs no asyncio plugin configuration of its own."""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _stub_fastmcp():
    """Stand in for fastmcp so broker.py imports without an MCP runtime. The
    decorator returns the plain function, which is what the tests call — the
    real server wraps the same function in a Tool, and the wrapping is
    fastmcp's business, not this module's."""
    fastmcp = types.ModuleType("fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name
            self.local_provider = types.SimpleNamespace(remove_tool=lambda name: None)

        def tool(self, fn):
            return fn

        def add_tool(self, tool):
            pass

    fastmcp.FastMCP = FastMCP
    server = types.ModuleType("fastmcp.server")
    deps = types.ModuleType("fastmcp.server.dependencies")
    deps.get_http_request = lambda: None
    tools = types.ModuleType("fastmcp.tools")
    tool_mod = types.ModuleType("fastmcp.tools.tool")

    class Tool:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class ToolResult:
        def __init__(self, content=""):
            self.content = content

    tools.Tool = Tool
    tool_mod.ToolResult = ToolResult
    sys.modules.update({"fastmcp": fastmcp, "fastmcp.server": server,
                        "fastmcp.server.dependencies": deps,
                        "fastmcp.tools": tools, "fastmcp.tools.tool": tool_mod})


def _load_broker():
    _stub_fastmcp()
    sys.path.insert(0, str(HERE))          # broker.py imports its own agenttools
    spec = importlib.util.spec_from_file_location("broker", HERE / "broker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


broker = _load_broker()

CHANNELS = [{"id": "aa" * 16, "kind": "channel", "name": "general"},
            {"id": "bb" * 16, "kind": "channel", "name": "News-Desk"},
            {"id": "cc" * 16, "kind": "dm", "name": None}]
GENERAL, DESK, DM = (c["id"] for c in CHANNELS)


class Calls(list):
    """The recorded (method, path, params, json) calls, with the canned bodies
    the stub answers with hanging off the same object."""

    def __init__(self):
        super().__init__()
        self.replies: dict = {}


@pytest.fixture
def calls(monkeypatch):
    recorded = Calls()
    listing = json.dumps(CHANNELS)

    # The parameter names are broker._call's own — the tool passes `json=` by
    # keyword, and a stub that renamed it would pass a test the real call fails.
    async def _call(method, path, params=None, json=None):
        recorded.append((method, path, params, json))
        if (method, path) == ("GET", "/api/relay/channels"):
            return recorded.replies.get("channels", listing)
        return recorded.replies.get(path, '{"ok": true}')

    monkeypatch.setattr(broker, "_call", _call)
    return recorded


def relay(**kw):
    return asyncio.run(broker.relay(**kw))


def test_post_takes_a_channel_id_without_a_lookup(calls):
    relay(action="post", channel=GENERAL, body="morning")
    assert calls == [("POST", f"/api/relay/channels/{GENERAL}/messages", None,
                      {"body": "morning", "reply_to": None})]


def test_post_resolves_a_name_and_keeps_the_reply(calls):
    relay(action="post", channel="#general", body="hi", reply_to="m1")
    assert calls == [
        ("GET", "/api/relay/channels", None, None),
        ("POST", f"/api/relay/channels/{GENERAL}/messages", None,
         {"body": "hi", "reply_to": "m1"}),
    ]


@pytest.mark.parametrize("given", ["news-desk", "#News-Desk", "NEWS-DESK"])
def test_a_name_is_matched_case_insensitively(calls, given):
    relay(action="post", channel=given, body="x")
    assert calls[-1][1] == f"/api/relay/channels/{DESK}/messages"


def test_an_unknown_name_is_an_error_not_a_call(calls):
    assert relay(action="post", channel="#nowhere", body="x") == (
        "error: no channel named #nowhere")
    assert [c[1] for c in calls] == ["/api/relay/channels"]


def test_read_pages_the_room(calls):
    relay(action="read", channel=GENERAL, limit=5, before="m9")
    assert calls == [("GET", f"/api/relay/channels/{GENERAL}/messages",
                      {"limit": 5, "before": "m9"}, None)]


def test_channels_lists_the_rooms(calls):
    out = relay(action="channels")
    assert calls == [("GET", "/api/relay/channels", None, None)]
    assert json.loads(out) == CHANNELS


def test_dm_opens_the_room_then_posts_into_it(calls):
    calls.replies["/api/relay/dm"] = json.dumps({"id": DM, "kind": "dm"})
    calls.replies[f"/api/relay/channels/{DM}/messages"] = '{"id": "m5"}'
    out = relay(action="dm", to="agent:news", body="got a minute?")
    assert calls == [
        ("POST", "/api/relay/dm", None, {"with": "agent:news"}),
        ("POST", f"/api/relay/channels/{DM}/messages", None,
         {"body": "got a minute?", "reply_to": None}),
    ]
    # The POSTED MESSAGE is the answer: the room is plumbing, the message is
    # what the agent actually did.
    assert out == '{"id": "m5"}'


def test_dm_surfaces_the_apis_refusal_rather_than_posting(calls):
    calls.replies["/api/relay/dm"] = '{"detail":"unknown agent"}'
    out = relay(action="dm", to="agent:ghost", body="hello?")
    assert [c[1] for c in calls] == ["/api/relay/dm"]
    assert out.startswith("error:") and "unknown agent" in out


def test_react(calls):
    relay(action="react", message_id="m1", emoji="🎉")
    assert calls == [("POST", "/api/relay/messages/m1/reactions", None,
                      {"emoji": "🎉"})]


def test_search_scopes_to_a_channel_when_given(calls):
    relay(action="search", q="kafka")
    relay(action="search", q="kafka", channel="#general", limit=5)
    assert calls[0] == ("GET", "/api/relay/search",
                        {"q": "kafka", "channel": None, "limit": 30}, None)
    assert calls[-1] == ("GET", "/api/relay/search",
                         {"q": "kafka", "channel": GENERAL, "limit": 5}, None)


@pytest.mark.parametrize("kwargs,missing", [
    ({"action": "post", "body": "x"}, "channel"),
    ({"action": "post", "channel": GENERAL}, "body"),
    ({"action": "read"}, "channel"),
    ({"action": "dm", "body": "x"}, "to"),
    ({"action": "dm", "to": "agent:news"}, "body"),
    ({"action": "react", "emoji": "👍"}, "message_id"),
    ({"action": "react", "message_id": "m1"}, "emoji"),
    ({"action": "search"}, "q"),
])
def test_a_missing_argument_is_explained_not_attempted(calls, kwargs, missing):
    out = relay(**kwargs)
    assert out.startswith("error:") and missing in out
    assert calls == []


def test_an_unknown_action_lists_the_real_ones(calls):
    out = relay(action="shout", channel=GENERAL, body="x")
    assert out.startswith("error: action must be one of")
    for action in ("post", "read", "channels", "dm", "react", "search"):
        assert action in out
    assert calls == []


def test_the_docstring_teaches_the_hop_rule_briefly():
    """The docstring IS the tool's interface to the model: what it does not say
    there, no agent knows. Short enough to be read, and it has to carry the
    three things an agent cannot discover by trying — that it speaks as itself,
    that a mention costs a hop, and that its final answer is already posted."""
    doc = broker.relay.__doc__
    assert len([line for line in doc.splitlines() if line.strip()]) <= 6
    lowered = doc.lower()
    for phrase in ("agent:", "@name", "hop", "final answer", "read"):
        assert phrase in lowered


# --- name resolution, the sharp end -----------------------------------------

OPS = [{"id": "11" * 16, "name": "ops"}, {"id": "22" * 16, "name": "Ops"}]


def test_an_exact_name_beats_a_case_variant(calls):
    calls.replies["channels"] = json.dumps(OPS)
    relay(action="post", channel="Ops", body="x")
    assert calls[-1][1] == f"/api/relay/channels/{OPS[1]['id']}/messages"
    relay(action="post", channel="ops", body="x")
    assert calls[-1][1] == f"/api/relay/channels/{OPS[0]['id']}/messages"


def test_two_rooms_answering_loosely_is_an_error_not_a_guess(calls):
    """The wrong room is the wrong audience, so a tie is refused rather than
    resolved by whichever row came back first."""
    calls.replies["channels"] = json.dumps(OPS)
    out = relay(action="post", channel="#OPS", body="x")
    assert out.startswith("error: ambiguous channel name #OPS")
    assert "ops, Ops" in out or "Ops, ops" in out
    assert [c[1] for c in calls] == ["/api/relay/channels"]


def test_a_hex_name_is_read_as_a_name_once_the_id_misses(calls):
    """A channel name may be 32 hex characters — it is a legal slug — so an id
    that no room answers to is re-read as the name the agent typed."""
    hexname = "ab" * 16
    room = {"id": "99" * 16, "name": hexname}
    calls.replies["channels"] = json.dumps([room])
    calls.replies[f"/api/relay/channels/{hexname}/messages"] = "error: 404 unknown channel"
    relay(action="post", channel=hexname, body="x")
    assert [c[1] for c in calls] == [
        f"/api/relay/channels/{hexname}/messages",
        "/api/relay/channels",
        f"/api/relay/channels/{room['id']}/messages",
    ]


def test_an_id_that_misses_and_names_nothing_keeps_the_apis_answer(calls):
    hexname = "ab" * 16
    calls.replies[f"/api/relay/channels/{hexname}/messages"] = "error: 404 unknown channel"
    assert relay(action="post", channel=hexname, body="x") == "error: 404 unknown channel"


def test_a_refused_listing_is_not_read_as_no_such_room(calls):
    calls.replies["channels"] = "error: 403 not your business"
    assert relay(action="read", channel="#general") == "error: 403 not your business"


def test_a_refused_dm_never_posts(calls):
    calls.replies["/api/relay/dm"] = "error: 404 unknown agent"
    assert relay(action="dm", to="agent:ghost", body="hi") == "error: 404 unknown agent"
    assert [c[1] for c in calls] == ["/api/relay/dm"]


@pytest.mark.parametrize("action,given,want,extra", [
    ("read", 5000, 200, {"channel": GENERAL}),
    ("read", 0, 1, {"channel": GENERAL}),
    ("search", 5000, 100, {"q": "kafka"}),
    ("search", -3, 1, {"q": "kafka"}),
])
def test_limits_are_clamped_to_what_the_api_accepts(calls, action, given, want, extra):
    """The API answers an out-of-range page with a 422 the model then has to
    interpret. Clamping here turns "too big" into "the biggest there is"."""
    relay(action=action, limit=given, **extra)
    assert calls[-1][2]["limit"] == want


# --- `_call` itself, which every core tool speaks through ---------------------

class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


def test_call_marks_an_api_refusal_as_an_error(monkeypatch):
    """Unprefixed, a 403 body reads exactly like data — which is how an agent
    comes to report work the platform refused to do."""
    async def _request(method, path, params=None, json=None):
        return FakeResponse(403, '{"detail":"not a member of this channel"}')

    monkeypatch.setattr(broker, "_request", _request)
    out = asyncio.run(broker._call("POST", "/api/relay/channels/x/messages"))
    assert out == 'error: 403 {"detail":"not a member of this channel"}'


@pytest.mark.parametrize("status,text,want", [
    (200, '{"id": "m1"}', '{"id": "m1"}'),
    (200, "", "ok"),          # an empty 200 is still a success
    (404, "", "error: 404"),
])
def test_call_passes_success_through_untouched(monkeypatch, status, text, want):
    async def _request(method, path, params=None, json=None):
        return FakeResponse(status, text)

    monkeypatch.setattr(broker, "_request", _request)
    assert asyncio.run(broker._call("GET", "/api/runs")) == want
