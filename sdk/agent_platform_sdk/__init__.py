"""Tiny, dependency-free Python SDK for the agent-platform HTTP API.

Mirrors the platform's OpenAPI (served live at ``/openapi.json``) with a
hand-written client so it can run anywhere with only the standard library. Auth
is a single ``ap_`` API key; the caller's role decides what it may do.

    from agent_platform_sdk import Client
    ap = Client("http://agent-platform:8000", "ap_...")
    ap.list_agents()
    run = ap.create_run("echo", "hello")
    ap.get_run(run["id"])

The HTTP call is isolated in ``_fetch`` so tests can inject a transport; by
default it uses ``urllib`` (no third-party dependency).
"""
import json
import urllib.error
import urllib.request

__all__ = ["Client", "ApiError"]


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def _urllib_fetch(method: str, url: str, headers: dict, body: bytes | None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Client:
    def __init__(self, base_url: str, token: str, *, fetch=None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._fetch = fetch or _urllib_fetch

    def _call(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None):
        url = self.base_url + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self._fetch(method, url, headers, data)
        if status >= 400:
            raise ApiError(status, raw.decode(errors="replace"))
        return json.loads(raw) if raw else None

    # --- agents -----------------------------------------------------------
    def list_agents(self) -> list:
        return self._call("GET", "/api/agents")

    def get_agent(self, name: str) -> dict:
        return self._call("GET", f"/api/agents/{name}")

    # --- runs -------------------------------------------------------------
    def create_run(self, agent: str, prompt: str) -> dict:
        """Request a run. Requires an operator+ (or admin) key."""
        return self._call("POST", "/api/runs", body={"agent": agent, "prompt": prompt})

    def get_run(self, run_id: str) -> dict:
        return self._call("GET", f"/api/runs/{run_id}")

    def list_runs(self, *, limit: int = 50, tag: str | None = None) -> list:
        return self._call("GET", "/api/runs", params={"limit": limit, "tag": tag})

    # --- memory -----------------------------------------------------------
    def save_memory(self, content: str, *, key: str | None = None,
                    tags: list | None = None, agent: str | None = None) -> dict:
        return self._call("POST", "/api/memories",
                          body={"content": content, "key": key, "tags": tags, "agent": agent})

    def search_memories(self, *, q: str | None = None, agent: str | None = None,
                        limit: int = 50) -> list:
        return self._call("GET", "/api/memories", params={"q": q, "agent": agent, "limit": limit})

    # --- health -----------------------------------------------------------
    def kafka_health(self) -> dict:
        return self._call("GET", "/api/health/kafka")
