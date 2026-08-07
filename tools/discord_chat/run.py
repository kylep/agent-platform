"""discord_chat tool: post to a named channel via the Discord REST API.

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
    """First text channel (type 0) named `name` across the bot's guilds."""
    for guild in _req("/users/@me/guilds", token):
        for ch in _req(f"/guilds/{guild['id']}/channels", token):
            if ch.get("type") == 0 and ch.get("name") == name:
                return ch
    return None


def main() -> int:
    args = json.load(sys.stdin)
    token = os.environ.get("token", "").removeprefix("Bot ").strip()
    if not token:
        print("discord-bot secret is not configured", file=sys.stderr)
        return 2
    channel = find_channel(token, args["channel"].lstrip("#"))
    if channel is None:
        print(f"no channel named #{args['channel']} visible to the bot", file=sys.stderr)
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
