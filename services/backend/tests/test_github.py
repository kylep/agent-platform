import json

from agentplatform.github import API_VERSION, GitHubClient


def client():
    return GitHubClient(token="ghtok", repo="kylep/agent-platform")


def test_open_pr_request_shape():
    req = client().build_request("POST", "/pulls",
                                 {"head": "coder/x", "base": "main", "title": "t", "body": "b"})
    assert req.full_url == "https://api.github.com/repos/kylep/agent-platform/pulls"
    assert req.method == "POST"
    assert req.get_header("Authorization") == "Bearer ghtok"
    assert req.get_header("Accept") == "application/vnd.github+json"
    assert req.get_header("X-github-api-version") == API_VERSION
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"head": "coder/x", "base": "main", "title": "t", "body": "b"}


def test_get_request_has_no_body():
    req = client().build_request("GET", "/pulls?state=open")
    assert req.method == "GET"
    assert req.data is None
    assert req.get_header("Content-type") is None
    assert req.full_url.endswith("/pulls?state=open")


def test_merge_uses_put_and_method():
    req = client().build_request("PUT", "/pulls/7/merge", {"merge_method": "squash"})
    assert req.method == "PUT"
    assert req.full_url.endswith("/pulls/7/merge")
    assert json.loads(req.data) == {"merge_method": "squash"}


def test_close_uses_patch():
    req = client().build_request("PATCH", "/pulls/7", {"state": "closed"})
    assert req.method == "PATCH"
    assert json.loads(req.data) == {"state": "closed"}
