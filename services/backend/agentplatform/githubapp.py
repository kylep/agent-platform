"""Mint short-lived GitHub App installation tokens.

The self-hosting loop authenticates to GitHub as the installed app (PericakAI)
rather than a personal token: sign a short JWT with the app private key, trade
it for an installation token (valid ~1h), and cache it. The resulting token
works for both git push (as the `x-access-token` password) and the REST PR API,
so it supersedes the push-only deploy key.
"""
import json
import time
import urllib.request

import jwt

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
# Refresh a little before the real ~1h expiry; conservative and avoids parsing.
TOKEN_TTL = 3300


class GitHubApp:
    def __init__(self, app_id: str, install_id: str, private_key_pem: str,
                 *, api_root: str = API_ROOT):
        self.app_id = str(app_id).strip()
        self.install_id = str(install_id).strip()
        self.private_key = private_key_pem
        self.api_root = api_root
        self._token: str | None = None
        self._expires_at: float = 0.0

    def app_jwt(self, now: float) -> str:
        now = int(now)
        return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                          self.private_key, algorithm="RS256")

    def installation_token(self, *, now: float | None = None, mint=None) -> str:
        """Return a cached installation token, minting a fresh one when the
        cache is empty or within 60s of expiry. `mint` is injectable for tests."""
        now = time.time() if now is None else now
        if self._token and now < self._expires_at - 60:
            return self._token
        self._token = (mint or self._http_mint)(now)
        self._expires_at = now + TOKEN_TTL
        return self._token

    def _http_mint(self, now: float) -> str:  # pragma: no cover - network
        req = urllib.request.Request(
            f"{self.api_root}/app/installations/{self.install_id}/access_tokens",
            method="POST", data=b"")
        req.add_header("Authorization", f"Bearer {self.app_jwt(now)}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        with urllib.request.urlopen(req) as r:
            return json.load(r)["token"]
