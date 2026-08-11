#!/usr/bin/env python3
"""Verify the `strava` secret WITHOUT rotating anything.

Strava rotates the refresh token on every refresh and kills the old one, so a
verifier that refreshed would spend the seed the tool needs for its own first
refresh. This one never refreshes. It confirms all four keys are present and,
when the seed access token is still within its ~6h life, that it authenticates
against /athlete. An expired access token is NOT a failure — the credentials
are still good and the tool refreshes on use — so that case stays green with a
note. Runs sandboxed with only this secret's keys in the environment.
Exit 0 = valid; stdout is the detail line.
"""
import os
import sys
import urllib.error
import urllib.request

REQUIRED = ["STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET",
            "STRAVA_ACCESS_TOKEN", "STRAVA_REFRESH_TOKEN"]


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        print(f"missing: {', '.join(missing)}")
        return 1
    token = os.environ["STRAVA_ACCESS_TOKEN"].strip()
    req = urllib.request.Request(
        "https://www.strava.com/api/v3/athlete",
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": "agent-platform"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            import json
            who = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # Seed access token aged out — expected; the tool refreshes on use.
            print("ok — all four keys present; seed access token expired "
                  "(normal, the tool refreshes on first use)")
            return 0
        print(f"strava {e.code}: {e.reason}")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1
    name = f"{who.get('firstname', '')} {who.get('lastname', '')}".strip()
    print(f"ok — authenticated as {name or who.get('id', 'athlete')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
