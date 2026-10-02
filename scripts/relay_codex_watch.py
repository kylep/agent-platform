#!/usr/bin/env python3
"""Wake one Codex session when #coding-agents receives a new Relay message.

The watcher is deliberately not an agent run: Relay's SSE stream costs no model
quota while idle. Only a new message queues a cheap Codex turn. Keep its bearer
token and cursor in a private local directory, never in the repository.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LOG = logging.getLogger("relay-codex-watch")
PAGE_SIZE = 200
RETRY_MAX_SECONDS = 60
WAKE_COOLDOWN_SECONDS = 10


def private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def get_json(base: str, token: str, path: str) -> list:
    req = Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def message_path(channel: str, *, after: str | None = None, limit: int = PAGE_SIZE) -> str:
    query = {"limit": limit}
    if after:
        query["after"] = after
    return f"/api/relay/channels/{channel}/messages?{urlencode(query)}"


def queue_wake(args: argparse.Namespace, count: int) -> None:
    # The message is fixed text, not Relay content. Relay authors cannot inject
    # instructions into the local Codex session through this bridge.
    message = (
        f"There are {count} new message(s) in Agent Platform Relay #coding-agents. "
        "Read the channel using the platform MCP tools. Respond there if a message "
        "needs your attention, then continue the user's active task."
    )
    subprocess.run(
        [args.codex, "queue", "--thread", args.thread, "--model", args.model,
         "--message", message],
        check=True, timeout=30, stdout=subprocess.DEVNULL,
        # launchd's default PATH omits Homebrew. The codex executable uses
        # /usr/bin/env node, so add the executable's own bin directory.
        env={**os.environ, "PATH": str(Path(args.codex).parent) + os.pathsep
             + os.environ.get("PATH", "")},
    )
    LOG.info("Queued Codex wake for %d new message(s)", count)


def handle_message(args: argparse.Namespace, state: dict, row: dict) -> None:
    message_id = row.get("id")
    if not message_id or message_id == state.get("cursor"):
        return
    if row.get("kind") == "text" and row.get("author") != "user:codex-laptop":
        state["pending"] = state.get("pending", 0) + 1
    state["cursor"] = message_id
    private_json(args.state, state)
    flush_pending(args, state)


def flush_pending(args: argparse.Namespace, state: dict) -> None:
    pending = state.get("pending", 0)
    now = time.time()
    if pending and now - state.get("last_wake", 0) >= WAKE_COOLDOWN_SECONDS:
        queue_wake(args, pending)
        state["last_wake"] = now
        state["pending"] = 0
        private_json(args.state, state)


def catch_up(args: argparse.Namespace, token: str, state: dict) -> None:
    if not state.get("cursor"):
        # First startup begins at the current end of the room, not months of
        # history. Reconnects always use the durable cursor below.
        newest = get_json(args.base, token, message_path(args.channel, limit=1))
        if newest:
            state["cursor"] = newest[0]["id"]
            private_json(args.state, state)
        return
    while True:
        rows = get_json(args.base, token,
                        message_path(args.channel, after=state["cursor"]))
        for row in rows:
            handle_message(args, state, row)
        flush_pending(args, state)
        if len(rows) < PAGE_SIZE:
            return


def stream(args: argparse.Namespace, token: str, state: dict) -> None:
    query = urlencode({"after": state["cursor"]}) if state.get("cursor") else ""
    path = f"/api/relay/channels/{args.channel}/events"
    if query:
        path += "?" + query
    req = Request(args.base + path,
                  headers={"Authorization": f"Bearer {token}",
                           "Accept": "text/event-stream"})
    with urlopen(req, timeout=45) as response:
        LOG.info("Connected to Relay #%s", args.channel)
        event = "message"
        data = []
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if event == "message" and data:
                    handle_message(args, state, json.loads("\n".join(data)))
                elif event == "overflow":
                    LOG.warning("Relay stream overflow; reconnecting for REST catch-up")
                    return
                elif event == "closed":
                    raise RuntimeError("Relay closed this channel stream")
                flush_pending(args, state)
                event, data = "message", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://pai:8090")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--codex", default="/opt/homebrew/bin/codex")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true",
                        help="verify Relay access and exit without queueing")
    args = parser.parse_args()
    args.state = args.data_dir / "state.json"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token_file = args.data_dir / "token"
    if not token_file.exists():
        parser.error(f"missing local token file: {token_file}")
    if args.state.exists():
        state = json.loads(args.state.read_text())
    else:
        state = {}
    delay = 2
    while True:
        try:
            token = token_file.read_text().strip()
            if not token:
                raise RuntimeError("local token file is empty")
            if args.check:
                get_json(args.base, token, message_path(args.channel, limit=1))
                print("Relay access OK; watcher configuration valid")
                return 0
            catch_up(args, token, state)
            stream(args, token, state)
            delay = 2
        except (HTTPError, URLError, OSError, ValueError, RuntimeError,
                subprocess.SubprocessError) as exc:
            # Never log the URL or request headers: they could gain sensitive
            # parameters if this script is adapted for another connector.
            LOG.warning("Watcher disconnected (%s); retrying in %ds",
                        type(exc).__name__, delay)
            if args.check:
                return 1
            time.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
