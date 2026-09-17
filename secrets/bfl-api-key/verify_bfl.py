#!/usr/bin/env python3
"""Verify the bfl-api-key secret. BFL exposes no free read-only endpoint that
identifies the key, so this polls `get_result` for a task id that cannot
exist: the API checks `x-key` before it looks up the task, so 401/403 means
the key was rejected while 404/422 (unknown task) or 200 means it was
accepted. Any other status (429, 5xx, a WAF page) proves nothing about the
key, so it fails CLOSED — marking the key valid through an outage would let
a dead key look green until the outage ends; the verifier heartbeat re-runs
every 600 s and recovers on its own. Nothing is generated and nothing is
billed. Runs in a sandboxed subprocess with only this secret's keys in the
environment. Exit 0 = valid; stdout is the detail line. Never prints the key.
"""
import os
import sys
import urllib.error
import urllib.request

PROBE_URL = "https://api.bfl.ai/v1/get_result?id=00000000-0000-0000-0000-000000000000"


def main() -> int:
    key = os.environ.get("BFL_API_KEY", "").strip()
    if not key:
        print("BFL_API_KEY is empty")
        return 1
    req = urllib.request.Request(
        PROBE_URL, headers={"x-key": key, "User-Agent": "agent-platform"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"bfl {e.code}: key rejected")
            return 1
        if e.code in (404, 422):
            print(f"ok — key accepted (bfl {e.code} for the sentinel task id)")
            return 0
        print(f"bfl {e.code}: inconclusive, will retry")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1
    print("ok — key accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
