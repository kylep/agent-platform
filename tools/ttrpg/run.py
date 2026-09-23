"""Brokered bridge to the hosted TTRPG service.

The broker authenticates the run before this subprocess starts; the executor
injects TOOL_CALLER_AGENT and TOOL_RUN_ID from that verified identity, never
from model arguments. The app key is read from the platform-provisioned secret.
"""
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://agent-platform-app-ttrpg:8000/_internal"


def main() -> None:
    try:
        args = json.load(sys.stdin)
        action = args.get("action")
        token = os.environ["AP_API_TOKEN"]
        caller = os.environ["TOOL_CALLER_AGENT"]
        run_id = os.environ["TOOL_RUN_ID"]
        if not token or not caller or not run_id:
            raise ValueError("verified identity or app key missing")
        headers = {"Authorization": "Bearer " + token,
                   "X-Tool-Caller-Agent": caller,
                   "Content-Type": "application/json"}
        if action == "view":
            request = Request(BASE + "/view", headers=headers)
        elif action == "gm_view":
            request = Request(BASE + "/gm-view", headers=headers)
        elif action == "command":
            request_id = args.get("request_id", "")
            argv = args.get("argv")
            if not isinstance(request_id, str) or not request_id or not isinstance(argv, list):
                raise ValueError("command requires request_id and argv")
            payload = {"request_id": run_id + ":" + request_id, "argv": argv}
            request = Request(BASE + "/command", headers=headers,
                              data=json.dumps(payload).encode(), method="POST")
        else:
            raise ValueError("action must be view or command")
        with urlopen(request, timeout=95) as response:
            result = json.load(response)
        print(json.dumps(result, separators=(",", ":")))
    except (HTTPError, URLError, KeyError, ValueError, OSError) as exc:
        detail = exc.read().decode("utf-8", "replace")[:500] if isinstance(exc, HTTPError) else str(exc)
        print(f"ttrpg tool failed: {detail}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
