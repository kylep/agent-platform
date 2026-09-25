"""discord_chat tool: post to an unambiguous Discord channel via REST.

Auth: the `discord-bot` secret's `token` key (same credential the connector
logs in with — REST-only here, no gateway). Discord sits behind Cloudflare,
which 403s without a real User-Agent.

Safety mirrors the connector: 1900-char chunks, and mentions are suppressed
API-side (allowed_mentions: parse []) so a prompt-injected agent can never
mass-ping.
"""
import json
import os
import sys
import urllib.request

API = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/kylep/agent-platform, 1.0)"
CHUNK = 1900


def _req(path: str, token: str, payload: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bot {token}", "User-Agent": UA,
                 "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def chunks(text: str, size: int = CHUNK) -> list[str]:
    """Split on line boundaries where possible, hard-split otherwise."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            out.append(cur + line[:size]); cur, line = "", line[size:]
        if len(cur) + len(line) > size:
            out.append(cur); cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out or [""]


def find_channel(token: str, name: str) -> dict | None:
    """Find one text channel by name; refuse an ambiguous external target."""
    matches = []
    for guild in _req("/users/@me/guilds", token):
        for ch in _req(f"/guilds/{guild['id']}/channels", token):
            if ch.get("type") == 0 and ch.get("name") == name:
                matches.append(ch)
    if len(matches) > 1:
        raise ValueError(f"#{name} exists in multiple servers; use channel_id")
    return matches[0] if matches else None


def channel_by_id(token: str, channel_id: str) -> dict | None:
    """Use an immutable destination ID and reject non-text destinations."""
    if not channel_id.isdigit() or not 15 <= len(channel_id) <= 22:
        raise ValueError("channel_id must be a Discord channel ID")
    ch = _req(f"/channels/{channel_id}", token)
    return ch if ch.get("type") == 0 and str(ch.get("id")) == channel_id else None


def main() -> int:
    args = json.load(sys.stdin)
    token = os.environ.get("token", "").removeprefix("Bot ").strip()
    if not token:
        print("discord-bot secret is not configured", file=sys.stderr)
        return 2
    try:
        if ("channel" in args) == ("channel_id" in args):
            raise ValueError("provide exactly one of channel or channel_id")
        channel = (channel_by_id(token, args["channel_id"])
                   if "channel_id" in args else find_channel(token, args["channel"].lstrip("#")))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if channel is None:
        print("Discord text channel unavailable to the bot", file=sys.stderr)
        return 2
    ids = []
    for part in chunks(args["text"]):
        msg = _req(f"/channels/{channel['id']}/messages", token,
                   {"content": part, "allowed_mentions": {"parse": []}})
        ids.append(msg["id"])
    print(json.dumps({"posted": len(ids), "channel": f"#{channel['name']}",
                      "message_ids": ids}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
