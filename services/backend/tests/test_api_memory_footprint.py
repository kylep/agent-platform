import time

from agentplatform.api import auth


def test_sa_cache_drops_expired_entries(monkeypatch):
    cache = {}
    now = time.monotonic()
    monkeypatch.setattr(auth, "_SA_CACHE_TTL", 60.0)
    cache["old"] = (now - 1, "x")
    auth._sa_cache_put(cache, "new", "y")
    assert list(cache) == ["new"]
