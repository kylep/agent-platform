import pytest
from agentplatform.api.pulls import _view


def _pr(n, ref):
    return {"number": n, "title": f"t{n}", "html_url": f"http://x/{n}",
            "head": {"ref": ref}, "user": {"login": "app/pericakai"}, "created_at": "2026-07-20"}


def test_view_shape():
    v = _view(_pr(5, "coder/hello-abc"))
    assert v == {"number": 5, "title": "t5", "url": "http://x/5",
                 "branch": "coder/hello-abc", "author": "app/pericakai", "created_at": "2026-07-20"}


async def test_list_requires_github_app(admin_client):
    # default test app has no github-app secret / github_repo → 409
    assert (await admin_client.get("/api/pull-requests")).status_code == 409


async def test_list_filters_to_coder_branches(admin_client, monkeypatch):
    class FakeGH:
        def list_pull_requests(self):
            return [_pr(1, "coder/x"), _pr(2, "feature/y"), _pr(3, "coder/z")]
    async def fake_client(request):
        return FakeGH()
    monkeypatch.setattr("agentplatform.api.pulls._client", fake_client)
    r = await admin_client.get("/api/pull-requests")
    assert r.status_code == 200
    assert [p["number"] for p in r.json()] == [1, 3]   # non-coder/ filtered out
