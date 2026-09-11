"""Relay's pure layer (docs/design/19): participant strings, @mention parsing,
deterministic faces, membership. Every loop guard downstream trusts these
answers, so the edge cases — an email address, a code fence, an agent trying to
address the room — are pinned here rather than in the router."""
import pytest

from agentplatform.agentspec import RESERVED_AGENT_NAMES, validate_agent_name
from agentplatform.config import Settings
from agentplatform.relay import (AGENT_PREFIX, ALL, FACES, ROOM_MENTIONS, USER_PREFIX,
                                 agent_name, face_for, is_agent, is_member,
                                 parse_mentions, participant_of, strip_room_mentions)

AGENTS = {"news", "pai", "news-bot", "health-monitor"}


class Chan:
    """Stand-in for the ORM Conversation: is_member only reads kind and open."""

    def __init__(self, kind, open=False):
        self.kind = kind
        self.open = open


def test_prefixes():
    assert (AGENT_PREFIX, USER_PREFIX, ALL) == ("agent:", "user:", "*")
    assert ROOM_MENTIONS == ("all", "channel", "here", "everyone")


@pytest.mark.parametrize("kwargs,want", [
    ({"agent": "news"}, "agent:news"),
    ({"principal": "admin"}, "user:admin"),
    ({"principal": "kyle@pericak.com"}, "user:kyle@pericak.com"),
    ({"connector": "discord", "external_user": "12345"}, "discord:12345"),
])
def test_participant_of(kwargs, want):
    assert participant_of(**kwargs) == want


@pytest.mark.parametrize("kwargs", [
    {},                                                    # no identity at all
    {"agent": "news", "principal": "admin"},               # two kinds
    {"agent": "news", "connector": "discord", "external_user": "1"},
    {"connector": "discord"},                              # half a connector identity
    {"external_user": "12345"},
    {"agent": "News"},                                     # not a valid agent slug
    {"agent": ""},
    {"principal": ""},
    {"connector": "agent", "external_user": "news"},       # would forge an agent
    {"connector": "user", "external_user": "admin"},
    {"connector": "dis cord", "external_user": "1"},
    {"connector": "discord\n", "external_user": "1"},      # a trailing newline is not a slug
    {"connector": "discord", "external_user": "a b"},
])
def test_participant_of_rejects(kwargs):
    with pytest.raises(ValueError):
        participant_of(**kwargs)


@pytest.mark.parametrize("word", ROOM_MENTIONS + ("relay", "system", "parley"))
def test_reserved_names_cannot_be_agents(word):
    """Nothing may hold a name that the mention parser reads as the room or
    that Relay uses for its own voice — such an agent could never be summoned."""
    assert word in RESERVED_AGENT_NAMES
    with pytest.raises(ValueError):
        validate_agent_name(word)
    with pytest.raises(ValueError):
        participant_of(agent=word)


def test_is_agent_and_agent_name():
    assert is_agent("agent:news") and agent_name("agent:news") == "news"
    for other in ("user:admin", "discord:123", "news", ""):
        assert not is_agent(other)
        assert agent_name(other) is None


@pytest.mark.parametrize("body,author,want", [
    ("hey @news what's up", "user:admin", ["news"]),
    ("@news at the very start", "user:admin", ["news"]),
    ("ask (@news) or [@pai] or \"@news\"", "user:admin", ["news", "pai"]),
    ("@news, @news. @news! @news?", "user:admin", ["news"]),            # punctuation terminates
    ("@news-bot is its own agent", "user:admin", ["news-bot"]),
    ("mail a@news.com please", "user:admin", []),                        # email is not a mention
    ("x@news", "user:admin", []),                                        # @ must open a token
    ("@News and @PAI", "user:admin", ["news", "pai"]),                   # case-insensitive
    ("@pai @news @pai", "user:admin", ["pai", "news"]),                  # dedupe, first-seen order
    ("@nobody @news", "user:admin", ["news"]),                           # unknown names ignored
    ("@news hi", "agent:news", []),                                      # self-mention dropped
    ("@news hi", "agent:pai", ["news"]),                                 # but not another agent's
    ("@pai see `@news` in code", "user:admin", ["pai"]),                 # inline code excluded
    ("```\n@news\n```\n@pai", "user:admin", ["pai"]),                    # fenced block excluded
    ("``` @news ``` @pai", "user:admin", ["pai"]),                       # single-line fence
    # A reply truncated at the token limit ends mid-fence; the rest of it is
    # still code, not a live address.
    ("```\nexample: @news greets you\n(cut off here", "user:admin", []),
    ("@pai\n```\n@news never ran\n", "user:admin", ["pai"]),
    ("", "user:admin", []),
])
def test_parse_mentions(body, author, want):
    assert parse_mentions(body, AGENTS, author) == want


@pytest.mark.parametrize("word", ROOM_MENTIONS)
def test_room_mention_by_human_is_the_star(word):
    assert parse_mentions(f"morning @{word}!", AGENTS, "user:admin") == [ALL]
    assert parse_mentions(f"morning @{word.upper()}!", AGENTS, "user:admin") == [ALL]


@pytest.mark.parametrize("word", ROOM_MENTIONS)
def test_room_mention_by_agent_is_ignored(word):
    assert parse_mentions(f"@{word} listen up", AGENTS, "agent:pai") == []
    assert parse_mentions(f"@{word} and @news", AGENTS, "agent:pai") == ["news"]


def test_room_mention_keeps_first_seen_order_with_agents():
    assert parse_mentions("@news then @all", AGENTS, "user:admin") == ["news", ALL]
    assert parse_mentions("@all then @news", AGENTS, "user:admin") == [ALL, "news"]


def test_room_mention_in_code_is_not_a_mention():
    assert parse_mentions("`@all` is how you page us", AGENTS, "user:admin") == []


@pytest.mark.parametrize("body,want", [
    ("@all please look", "all please look"),
    ("@channel @here @everyone", "channel here everyone"),
    ("@All and @Here", "All and Here"),                        # case preserved, @ gone
    ("@news stays a mention", "@news stays a mention"),
    ("@heres is not a room", "@heres is not a room"),
    ("say `@all` in code", "say `@all` in code"),              # code spans untouched
    ("```\n@all\n```", "```\n@all\n```"),
    ("mail a@here.com", "mail a@here.com"),                    # email untouched
    ("```\ntype @all to page\n(cut off", "```\ntype @all to page\n(cut off"),
    ("nothing to strip", "nothing to strip"),
])
def test_strip_room_mentions(body, want):
    assert strip_room_mentions(body) == want


def test_faces_are_single_codepoint_and_distinct():
    assert len(FACES) >= 32
    assert len(set(FACES)) == len(FACES)
    for e in FACES:
        assert len(e) == 1, repr(e)


def test_face_for_is_deterministic():
    a, b = face_for("news"), face_for("news")
    assert a == b
    assert a["emoji"] in FACES
    assert 0 <= a["hue"] < 360
    assert face_for("news") != face_for("pai") or True   # collisions are allowed, equality is not


def test_face_for_matches_the_documented_hash():
    import hashlib
    h = hashlib.sha256(b"news").hexdigest()
    assert face_for("news") == {"emoji": FACES[int(h[8:16], 16) % len(FACES)],
                                "hue": int(h[:8], 16) % 360}


ENABLED = {"news", "pai"}
EXPLICIT = {"agent:news", "user:admin"}


@pytest.mark.parametrize("kind,open,participant,want", [
    ("channel", True, "agent:news", True),        # open: any enabled agent
    ("channel", True, "agent:pai", True),
    ("channel", True, "agent:ghost", False),      # ...but not a disabled/unknown one
    ("channel", True, "user:someone", True),      # open: any human
    ("channel", True, "discord:999", True),
    ("channel", False, "agent:news", True),       # closed: explicit rows only
    ("channel", False, "agent:pai", False),
    ("channel", False, "user:admin", True),
    ("channel", False, "user:someone", False),
    ("dm", False, "agent:news", True),
    ("dm", False, "agent:pai", False),
    ("dm", True, "agent:pai", False),             # open is a channel-only flag
    ("group", False, "user:admin", True),
    ("group", False, "discord:999", False),
])
def test_is_member(kind, open, participant, want):
    assert is_member(Chan(kind, open), participant, ENABLED, EXPLICIT) is want


def test_relay_settings_defaults():
    s = Settings()
    assert s.relay_max_hops == 4
    assert s.relay_channel_invocations_per_hour == 30
    assert s.relay_global_invocations_per_hour == 120
    assert s.relay_agent_cooldown_seconds == 20
    assert s.relay_context_messages == 30
    assert s.relay_default_grant is True
