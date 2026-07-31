#!/usr/bin/env python3
"""Verify the github-app secret: sign a short app JWT with the private key and
fetch this installation — a 200 proves the key, app_id, and install_id all
match the real GitHub App. Runs in a sandboxed subprocess with only this
secret's keys in the environment. Exit 0 = valid; stdout is the detail line."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt


def main() -> int:
    app_id = os.environ.get("app_id", "").strip()
    install_id = os.environ.get("install_id", "").strip()
    pem = os.environ.get("private_key", "")
    if not (app_id and install_id and pem):
        print("incomplete: needs app_id, install_id, private_key")
        return 1
    now = int(time.time())
    try:
        token = jwt.encode({"iat": now - 60, "exp": now + 300, "iss": app_id},
                           pem, algorithm="RS256")
    except Exception as e:
        print(f"private key won't sign: {e}")
        return 1
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{install_id}",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "agent-platform"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            account = json.load(r).get("account", {}).get("login", "?")
            print(f"ok — installed on {account}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"github {e.code}: {e.reason}")
        return 1
    except urllib.error.URLError as e:
        print(f"unreachable: {e.reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
