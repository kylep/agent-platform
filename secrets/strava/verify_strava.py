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
    # Probe the ACTIVITIES endpoint, not /athlete — that's the scope the tool
    # actually needs (activity:read_all). /athlete needs only `read`, so it
    # would pass even for a token that can't read a single run (exactly the trap
    # that let a scope-less token look valid).
    req = urllib.request.Request(
        "https://www.strava.com/api/v3/athlete/activities?per_page=1",
        headers={"Authorization": f"Bearer {token}",
                 "User-Agent": "agent-platform"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            body = e.read().decode("utf-8", "replace")
            if "activity:read" in body or "read_permission" in body:
                # Token authenticates but was granted without the activity scope
                # — refreshing can't fix it. This is a real failure, not staleness.
                print("MISSING activity scope — re-authorize the Strava app with "
                      "scope read,activity:read_all,profile:read_all (keep every "
                      "box checked) and paste the new access + refresh tokens")
                return 1
            # No scope complaint → the seed access token has just aged out, which
            # is expected: the tool refreshes on first use.
            print("ok — all four keys present; seed access token expired "
                  "(normal, the tool refreshes on first use)")
            return 0
        print(f"strava {e.code}: {e.reason}")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1
    print("ok — authenticated and the activity scope is present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
