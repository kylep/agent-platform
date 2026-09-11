"""Relay (docs/design/19) is the platform's messenger: channels whose messages
are rows and Kafka events, where `@name` summons an agent as a run. This module
is its pure layer — participant strings, mention parsing, faces, membership —
with no I/O, so the router, the API and the UI all resolve a mention the same
way and the loop guards have one definition of who was addressed."""
import hashlib
import re

from .agentspec import validate_agent_name

# Participants are namespaced strings, not foreign keys: the same seam as
# `initiated_by` (docs/design/13), so a Discord user needs no platform row.
AGENT_PREFIX = "agent:"
USER_PREFIX = "user:"
# Addressing the room. The router expands ALL to the channel's agent members;
# only humans may use it (an agent that could page everyone is a loop).
ROOM_MENTIONS = ("all", "channel", "here", "everyone")
ALL = "*"

# Deterministic fallback faces for agents with no AgentDef.icon. APPEND-ONLY:
# the emoji is chosen by hash index, so inserting or reordering entries silently
# repaints every existing agent. Single code points only (no flags, skin tones
# or ZWJ sequences) so every client renders them and `len(e) == 1` holds.
FACES = ("🐶", "🐱", "🦊", "🐻", "🐼", "🐯", "🦁", "🐸", "🐵", "🐧",
         "🐦", "🦉", "🦆", "🐢", "🐙", "🦑", "🦋", "🐝", "🐞", "🦀",
         "🐳", "🐬", "🐟", "🌵", "🌲", "🌳", "🌸", "🌻", "🍁", "🍄",
         "⭐", "🌙", "🔥", "💧", "🍀", "🎈", "🎨", "🎲", "🎸", "🚀",
         "🛸", "🧭", "🔭", "🧩", "🪐", "🍕")

# A mention opens a token: the `@` is at the start or follows whitespace or an
# opening bracket/quote, which is what keeps `a@news.com` an email address. The
# trailing lookahead lets `@news,` end the name while `@news-bot` keeps going.
_MENTION_RE = re.compile(r"(?<![^\s(\[\"])@([A-Za-z0-9][A-Za-z0-9-]*)(?![A-Za-z0-9-])")
# Fenced and inline code is quoted text, never an address — an agent explaining
# `@news` to a human must not summon it. An unclosed fence swallows the rest of
# the body, as markdown renderers do: LLM output truncated at a token limit
# routinely ends mid-block, and the fallback must be "still code", not "now a
# live mention".
_FENCE = r"```.*?(?:```|\Z)"
_CODE_RE = re.compile(_FENCE + r"|`[^`]*`", re.DOTALL)
_CODE_OR_ROOM_RE = re.compile(
    r"(?P<code>" + _FENCE + r"|`[^`]*`)"
    r"|(?<![^\s(\[\"])@(?P<word>" + "|".join(ROOM_MENTIONS) + r")(?![A-Za-z0-9-])",
    re.DOTALL | re.IGNORECASE)
# Connectors name a bridge (`discord`, `slack`); the two platform prefixes are
# reserved so an inbound bridge id can never forge `agent:`/`user:`.
_CONNECTOR_RE = re.compile(r"[a-z][a-z0-9_-]*")


def participant_of(*, agent: str | None = None, principal: str | None = None,
                   connector: str | None = None, external_user: str | None = None) -> str:
    """Build a participant string from exactly one identity kind: a platform
    agent, a platform principal, or a connector's own user. Raise ValueError on
    none, on more than one, or on a part that would produce an ambiguous
    string."""
    kinds = [agent is not None, principal is not None,
             connector is not None or external_user is not None]
    if sum(kinds) != 1:
        raise ValueError("participant needs exactly one of agent, principal, "
                         "or connector+external_user")
    if agent is not None:
        return AGENT_PREFIX + validate_agent_name(agent)
    if principal is not None:
        if not re.fullmatch(r"\S+", principal):
            raise ValueError("principal must be non-empty and contain no whitespace")
        return USER_PREFIX + principal
    if connector is None or external_user is None:
        raise ValueError("a connector identity needs both connector and external_user")
    if not _CONNECTOR_RE.fullmatch(connector) or connector in ("agent", "user"):
        raise ValueError("connector must be a lowercase slug and not agent/user")
    if not re.fullmatch(r"\S+", external_user):
        raise ValueError("external_user must be non-empty and contain no whitespace")
    return f"{connector}:{external_user}"


def is_agent(participant: str) -> bool:
    return (participant or "").startswith(AGENT_PREFIX)


def agent_name(participant: str) -> str | None:
    """The agent behind a participant string, or None if it is not an agent."""
    return participant[len(AGENT_PREFIX):] if is_agent(participant) else None


def parse_mentions(body: str, agents: set[str], author: str) -> list[str]:
    """Resolve `@name` tokens in `body` to agent names, first-seen order, no
    duplicates. Only names in `agents` count, an agent never summons itself, and
    a room mention resolves to the single token ALL — for human authors only,
    since an agent addressing the room is how a two-agent exchange becomes an
    all-agent storm."""
    author_agent = agent_name(author)
    out: list[str] = []
    for m in _MENTION_RE.finditer(_CODE_RE.sub(" ", body or "")):
        token = m.group(1).lower()
        if token in ROOM_MENTIONS:
            if author_agent is not None:
                continue
            resolved = ALL
        elif token in agents and token != author_agent:
            resolved = token
        else:
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def strip_room_mentions(body: str) -> str:
    """Drop the `@` from room mentions, keeping the word so the sentence still
    reads. Applied to agent-authored text at post time: the message survives
    verbatim in Discord and in the UI, but nothing downstream can read it as an
    address to everyone."""
    return _CODE_OR_ROOM_RE.sub(
        lambda m: m.group("code") if m.group("code") else m.group("word"), body or "")


def face_for(name: str) -> dict:
    """A stable emoji + hue for an agent with no icon of its own, so `news`
    looks the same in every channel, bridge and dashboard forever."""
    h = hashlib.sha256(name.encode()).hexdigest()
    return {"emoji": FACES[int(h[8:16], 16) % len(FACES)], "hue": int(h[:8], 16) % 360}


def is_member(channel, participant: str, enabled_agents: set[str],
              explicit: set[str]) -> bool:
    """Whether a participant belongs to a channel. Open channels carry no
    participant rows — every enabled agent and every human is in them by
    definition — so only dm/group/closed rooms consult `explicit`, the
    relay_participants strings. `channel` is duck-typed (.kind, .open) so the
    ORM row and a plain object both work."""
    if getattr(channel, "kind", "") == "channel" and bool(getattr(channel, "open", False)):
        name = agent_name(participant)
        return name in enabled_agents if name is not None else True
    return participant in explicit
