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
import inspect
import json
import sys
import time
import types
from pathlib import Path

import httpx
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
    # The two content blocks the artifact tools put in a ToolResult. The real
    # ones are pydantic models with exactly these fields, and the tools only
    # ever build them and read them back by attribute.
    mcp_pkg = types.ModuleType("mcp")
    mcp_types = types.ModuleType("mcp.types")

    class TextContent:
        def __init__(self, type="text", text=""):
            self.type, self.text = type, text

    class ImageContent:
        def __init__(self, type="image", data="", mimeType=""):
            self.type, self.data, self.mimeType = type, data, mimeType

    mcp_types.TextContent = TextContent
    mcp_types.ImageContent = ImageContent
    mcp_pkg.types = mcp_types
    sys.modules.update({"fastmcp": fastmcp, "fastmcp.server": server,
                        "fastmcp.server.dependencies": deps,
                        "fastmcp.tools": tools, "fastmcp.tools.tool": tool_mod,
                        "mcp": mcp_pkg, "mcp.types": mcp_types})


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


@pytest.fixture(autouse=True)
def caller(monkeypatch):
    """Who the broker thinks is calling. Every metered tool resolves this once
    per call, and the token bucket is keyed by it — the buckets are module
    state, so each test also starts with a full one."""
    async def _whoami():
        return {"agent": "news", "run_id": "r1", "initiated_by": "cron"}

    monkeypatch.setattr(broker, "_whoami", _whoami)
    broker._buckets.clear()
    yield
    broker._buckets.clear()


class FakeProducer:
    """The audit producer, recording what would have gone to Kafka — or, with
    `fail`, standing in for a broker that cannot reach it, or with `hang`, for
    the worse case: one that has not found out yet."""

    def __init__(self, fail=False, hang=False):
        self.sent = []
        self.fail = fail
        self.hang = hang

    async def send_and_wait(self, topic, value, key=None):
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise RuntimeError("kafka is unreachable")
        self.sent.append((topic, json.loads(value.decode()), key))


@pytest.fixture
def published(monkeypatch):
    """The audit envelopes the call publishes, as (topic, envelope, key)."""
    producer = FakeProducer()
    monkeypatch.setattr(broker, "_KAFKA", "kafka:9092")
    monkeypatch.setattr(broker, "_audit_producer", producer)
    return producer.sent


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


MESSAGE = "dd" * 16


def test_react(calls):
    relay(action="react", message_id=MESSAGE, emoji="🎉")
    assert calls == [("POST", f"/api/relay/messages/{MESSAGE}/reactions", None,
                      {"emoji": "🎉"})]


@pytest.mark.parametrize("given", ["m1", "x/../../whoami", "../runs", MESSAGE.upper()])
def test_a_reaction_target_that_is_not_a_message_id_is_refused(calls, given):
    """The id is interpolated into the path and httpx normalises `..` before
    the request leaves, so anything that is not an id is refused here rather
    than spent on whatever endpoint it turned out to name."""
    out = relay(action="react", message_id=given, emoji="👍")
    assert out == "error: message_id must be a message id (32 hex characters)"
    assert calls == []


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
    async def _request(method, path, params=None, json=None, timeout=20):
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
    async def _request(method, path, params=None, json=None, timeout=20):
        return FakeResponse(status, text)

    monkeypatch.setattr(broker, "_request", _request)
    assert asyncio.run(broker._call("GET", "/api/runs")) == want


# --- rate limit + audit trail (QA-5) ------------------------------------------
# `relay` is granted to nearly every agent and nothing downstream throttles a
# plain post or read, so the broker's own token bucket and audit trail — the
# ones every custom tool has always had — are the only backstop for a run that
# loops on it.

def test_every_call_lands_in_the_audit_trail_with_its_action(calls, published):
    relay(action="post", channel=GENERAL, body="morning")
    assert len(published) == 1
    topic, envelope, key = published[0]
    assert topic == broker._TOPIC_AUDIT
    data = envelope["data"]
    assert (data["tool"], data["action"]) == ("relay", "post")
    assert data["decision"] == "allow"
    assert (data["agent"], data["run_id"], data["initiated_by"]) == (
        "news", "r1", "cron")


def test_a_read_is_audited_as_a_read(calls, published):
    """The tool name alone cannot tell a read from a post, which is the whole
    point of auditing the busiest tool on the platform."""
    relay(action="read", channel=GENERAL)
    assert published[-1][1]["data"]["action"] == "read"


def test_the_audit_record_digests_the_arguments_rather_than_carrying_them(
        calls, published):
    relay(action="post", channel=GENERAL, body="the password is hunter2")
    envelope = published[0][1]
    assert "hunter2" not in json.dumps(envelope)
    assert len(envelope["data"]["args_digest"]) == 64


def test_an_api_refusal_is_audited_as_an_error(calls, published):
    calls.replies[f"/api/relay/channels/{GENERAL}/messages"] = (
        'error: 403 {"detail":"not a member of this channel"}')
    relay(action="post", channel=GENERAL, body="x")
    assert published[-1][1]["data"]["decision"] == "error:tool"


def test_over_the_limit_is_refused_without_touching_the_api(calls, published):
    burst = int(broker._RATE_CAPACITY)
    for _ in range(burst):
        assert not relay(action="channels").startswith("error:")
    assert len(calls) == burst
    out = relay(action="channels")
    assert out == ("error: rate limit exceeded for this tool — slow down and "
                   "retry shortly")
    assert len(calls) == burst          # the refusal costs the API nothing
    assert published[-1][1]["data"]["decision"] == "deny:rate-limit"


def test_a_summoned_agents_turn_is_nowhere_near_the_limit(calls):
    """Read the room, then answer: the shape of nearly every summoned run. A
    limit that catches this would break the platform, not protect it."""
    for _ in range(3):
        relay(action="read", channel=GENERAL)
    for _ in range(3):
        assert relay(action="post", channel=GENERAL, body="x") == '{"ok": true}'


def test_a_failing_audit_publish_does_not_change_the_answer(calls, monkeypatch):
    monkeypatch.setattr(broker, "_KAFKA", "kafka:9092")
    monkeypatch.setattr(broker, "_audit_producer", FakeProducer(fail=True))
    assert relay(action="post", channel=GENERAL, body="hi") == '{"ok": true}'
    assert calls == [("POST", f"/api/relay/channels/{GENERAL}/messages", None,
                      {"body": "hi", "reply_to": None})]


def test_metering_keeps_the_signature_fastmcp_turns_into_a_schema():
    params = inspect.signature(broker.relay).parameters
    assert ["action", "channel", "body"] == list(params)[:3]
    assert params["limit"].default == 30
    assert broker.relay.__name__ == "relay"


# --- the metering wrapper itself ----------------------------------------------
# `relay` and `tickets` share one wrapper, so its own failure modes are pinned
# once, here: what it costs, what it swallows, and what it must not refuse.

def test_a_stalled_audit_publish_does_not_stall_the_tool(calls, monkeypatch):
    """A broker that cannot reach Kafka does not always find out quickly —
    aiokafka waits out its request timeout first. The trail is worth a moment of
    a summoned agent's reply and no more."""
    monkeypatch.setattr(broker, "_KAFKA", "kafka:9092")
    monkeypatch.setattr(broker, "_audit_producer", FakeProducer(hang=True))
    monkeypatch.setattr(broker, "_AUDIT_TIMEOUT_S", 0.05)
    started = time.monotonic()
    out = relay(action="post", channel=GENERAL, body="hi")
    assert out == '{"ok": true}'
    assert time.monotonic() - started < 1.0
    assert calls == [("POST", f"/api/relay/channels/{GENERAL}/messages", None,
                      {"body": "hi", "reply_to": None})]


def test_an_unreachable_api_is_an_answer_and_a_record_not_a_crash(
        calls, published, monkeypatch):
    """The API pod restarting would otherwise escape as a raw MCP exception,
    with no row for an attempt that was made."""
    async def _call(method, path, params=None, json=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(broker, "_call", _call)
    out = relay(action="channels")
    assert out.startswith("error: the platform API is unreachable")
    assert out.endswith("— retry shortly")
    assert published[-1][1]["data"]["decision"] == "error:api-unreachable"


def test_an_unexpected_failure_is_an_answer_and_a_record_too(
        calls, published, monkeypatch):
    async def _call(method, path, params=None, json=None):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(broker, "_call", _call)
    out = relay(action="channels")
    assert out == ("error: relay failed unexpectedly: RuntimeError: "
                   "something nobody predicted")
    assert published[-1][1]["data"]["decision"] == "error:tool"


def test_an_unresolved_caller_is_recorded_but_never_rate_limited(calls, published,
                                                                 monkeypatch):
    """`""` is not an identity, it is every identity that failed to resolve:
    keying a bucket on it would turn one bad minute of /api/whoami into a
    platform-wide false rate limit."""
    async def _whoami():
        raise httpx.ConnectError("whoami is having a moment")

    monkeypatch.setattr(broker, "_whoami", _whoami)
    for _ in range(int(broker._RATE_CAPACITY) + 1):
        assert relay(action="channels") == json.dumps(CHANNELS)
    assert len(calls) == int(broker._RATE_CAPACITY) + 1
    assert published[-1][1]["data"]["agent"] == ""      # the blip stays visible
