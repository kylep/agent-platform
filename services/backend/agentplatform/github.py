"""Minimal GitHub REST client for the tier-2 PR path and the Pending Changes
view. Stdlib-only (urllib) — no new dependency. Request construction is kept
separate from sending so it can be unit-tested without network access; the
live calls need a repo write token (supplied as a secret)."""
import json
import urllib.request

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo  # "owner/name"

    def build_request(self, method: str, path: str, body: dict | None = None) -> urllib.request.Request:
        url = f"{API_ROOT}/repos/{self.repo}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return req

    def _send(self, req: urllib.request.Request) -> dict | list:
        with urllib.request.urlopen(req) as r:  # pragma: no cover - network
            raw = r.read().decode()
            return json.loads(raw) if raw else {}   # DELETE returns 204/no body

    def open_pull_request(self, *, head: str, base: str, title: str, body: str = "") -> dict:
        return self._send(self.build_request(
            "POST", "/pulls", {"head": head, "base": base, "title": title, "body": body}))

    def list_pull_requests(self, *, state: str = "open") -> list:
        return self._send(self.build_request("GET", f"/pulls?state={state}"))

    def find_open_pull_request(self, head_branch: str) -> dict | None:
        owner = self.repo.split("/")[0]
        res = self._send(self.build_request(
            "GET", f"/pulls?state=open&head={owner}:{head_branch}"))
        return res[0] if res else None

    def pull_request_files(self, number: int) -> list:
        return self._send(self.build_request("GET", f"/pulls/{number}/files"))

    def merge_pull_request(self, number: int, *, method: str = "squash") -> dict:
        return self._send(self.build_request(
            "PUT", f"/pulls/{number}/merge", {"merge_method": method}))

    def close_pull_request(self, number: int) -> dict:
        return self._send(self.build_request(
            "PATCH", f"/pulls/{number}", {"state": "closed"}))

    def pull_request(self, number: int) -> dict:
        return self._send(self.build_request("GET", f"/pulls/{number}"))

    def list_issue_comments(self, number: int) -> list:
        return self._send(self.build_request("GET", f"/issues/{number}/comments"))

    def create_issue_comment(self, number: int, body: str) -> dict:
        return self._send(self.build_request("POST", f"/issues/{number}/comments",
                                             {"body": body}))

    def delete_branch(self, branch: str) -> None:
        """Delete a head branch (no clutter after merge/discard). 422 = already
        gone — fine; per-block branches are recreated fresh on the next propose
        (force-pushed), so deletion is always safe."""
        import urllib.error
        try:
            self._send(self.build_request("DELETE", f"/git/refs/heads/{branch}"))
        except urllib.error.HTTPError as e:
            if e.code not in (404, 422):
                raise
