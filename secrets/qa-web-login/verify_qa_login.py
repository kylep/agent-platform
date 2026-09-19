#!/usr/bin/env python3
"""Verify the `qa-web-login` secret: the stored password opens the named
principal. POSTs `/api/login {principal, password}` and expects 200 — the same
call `bin/ap-web-login` makes in the QA pod, so "valid" here means the walk
can log in. A 401 is the real failure (the row's hash and the stored value
have drifted: rotate). Anything else — the API down, a 5xx — is inconclusive
and fails CLOSED rather than call the credential good. Runs sandboxed with only
this secret's keys in the environment; the API is the process running the
check, so the default target is its own listener.
Exit 0 = valid; stdout is the detail line and never carries the password.
"""
import json
import os
import sys
import urllib.error
import urllib.request

REQUIRED = ["QA_WEB_USER", "QA_WEB_PASSWORD"]
DEFAULT_API_URL = "http://127.0.0.1:8000"


def main() -> int:
    missing = [k for k in REQUIRED if not os.environ.get(k, "").strip()]
    if missing:
        print(f"missing: {', '.join(missing)}")
        return 1
    user = os.environ["QA_WEB_USER"].strip()
    password = os.environ["QA_WEB_PASSWORD"]
    base = os.environ.get("AP_API_URL", "").strip() or DEFAULT_API_URL
    req = urllib.request.Request(
        base.rstrip("/") + "/api/login",
        data=json.dumps({"principal": user, "password": password}).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "agent-platform"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"login {e.code}: the stored password does not open `{user}` — "
                  "delete the value and clear the `qa-principal-v1` mark to rotate")
            return 1
        print(f"login {e.code}: inconclusive ({e.reason})")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1
    print(f"ok — `{user}` logs in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
