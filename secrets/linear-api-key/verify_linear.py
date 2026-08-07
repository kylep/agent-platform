#!/usr/bin/env python3
"""Verify the linear-api-key secret: Linear's API is GraphQL-only (every
operation, reads included, is a POST), so the declarative GET probe can't
check it — this script asks for the key's own viewer instead. A 200 with a
viewer id proves the key is live. Runs in a sandboxed subprocess with only
this secret's keys in the environment. Exit 0 = valid; stdout is the detail
line. Note: a personal API key is sent RAW in Authorization (no Bearer)."""
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        print("LINEAR_API_KEY is empty")
        return 1
    body = json.dumps({"query": "{ viewer { id displayName } }"}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql", data=body, method="POST",
        headers={"Authorization": key, "Content-Type": "application/json",
                 "User-Agent": "agent-platform"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"linear {e.code}: {e.reason}")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1
    # Linear returns HTTP 200 even for failures — check the GraphQL envelope.
    if payload.get("errors"):
        print(f"rejected: {payload['errors'][0].get('message', 'unknown error')[:120]}")
        return 1
    viewer = (payload.get("data") or {}).get("viewer") or {}
    if not viewer.get("id"):
        print("no viewer in response — key likely invalid")
        return 1
    print(f"ok — key belongs to {viewer.get('displayName') or viewer['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
