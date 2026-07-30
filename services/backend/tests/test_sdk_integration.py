"""End-to-end exercise of the shipped SDK and the platform skill against a real
API, not a fake transport.

`test_sdk.py` unit-tests request construction with a `FakeFetch`, and the drift
guard proves the paths *exist* in the OpenAPI — but nothing there actually calls
the running app. Here the real `agent_platform_sdk.Client` drives the real ASGI
app (routing, auth middleware, RBAC, DB) over httpx, with a genuine `ap_` key.
The only thing missing vs. a `helm install` is the TCP socket and uvicorn's HTTP
parsing; everything the SDK and skill depend on — auth, roles, serialization,
status codes — is exercised for real.

These tests are synchronous on purpose: they own one event loop so the SDK's
synchronous `fetch` can drive the async ASGI app (via `run_until_complete`)
without loop nesting, and the in-memory engine stays on a single loop.

The platform skill (`skills/agent-platform/SKILL.md`) documents this same API,
so `test_skill_documented_paths_exist_in_openapi` holds it to the live OpenAPI
the same way the SDK is held — the skill can't silently drift either.
"""
import asyncio
import re
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sdk"))
from agent_platform_sdk import ApiError, Client  # noqa: E402

from agentplatform.agents import AgentStore  # noqa: E402
from agentplatform.api.app import create_app  # noqa: E402
from agentplatform.apikeys import generate_token, hash_token, token_prefix  # noqa: E402
from agentplatform.config import Settings  # noqa: E402
from agentplatform.db import ApiKey, init_db, make_engine, make_session_factory  # noqa: E402
from agentplatform.events import FakeProducer  # noqa: E402
from agentplatform.secrets import InMemorySecretStore  # noqa: E402


class Live:
    """A real app + engine on one owned loop, with SDK clients that route to it."""

    def __init__(self, tmp_path: Path):
        self.loop = asyncio.new_event_loop()
        d = tmp_path / "hello-world"
        d.mkdir(parents=True)
        (d / "agent.md").write_text("# hello-world\nYou are hello-world.")
        (d / "manifest.yaml").write_text("description: test\n")
        self.engine = make_engine("sqlite+aiosqlite:///:memory:")
        self.loop.run_until_complete(init_db(self.engine))
        self.sf = make_session_factory(self.engine)
        self.app = create_app(Settings(agents_root=str(tmp_path)), self.sf,
                              FakeProducer(), secret_store=InMemorySecretStore(),
                              agent_store=AgentStore(tmp_path))

    def close(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def mint(self, role: str) -> str:
        token = generate_token()

        async def _add():
            async with self.sf() as s:
                s.add(ApiKey(name=f"itest:{role}", role=role, key_hash=hash_token(token),
                             prefix=token_prefix(token)))
                await s.commit()
        self.loop.run_until_complete(_add())
        return token

    def sdk(self, token: str) -> Client:
        """A real SDK Client whose fetch drives the ASGI app on the owned loop."""
        async def _once(method, url, headers, body):
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://itest") as c:
                r = await c.request(method, url, headers=headers, content=body)
            return r.status_code, r.content

        def fetch(method, url, headers, body):
            return self.loop.run_until_complete(_once(method, url, headers, body))
        return Client("http://itest", token, fetch=fetch)


@pytest.fixture
def live(tmp_path):
    lv = Live(tmp_path)
    yield lv
    lv.close()


def test_operator_key_drives_the_documented_flow(live):
    """The exact sequence the skill teaches: list agents → trigger a run →
    fetch it → list recent runs → check health, through the real SDK."""
    ap = live.sdk(live.mint("operator"))

    agents = ap.list_agents()
    assert any(a["name"] == "hello-world" for a in agents)

    assert ap.get_agent("hello-world")["name"] == "hello-world"

    run = ap.create_run("hello-world", "say hi")
    assert run["state"] == "queued" and run["id"]

    fetched = ap.get_run(run["id"])
    assert fetched["id"] == run["id"] and fetched["agent"] == "hello-world"

    assert any(r["id"] == run["id"] for r in ap.list_runs(limit=20))

    # Health payload shape the skill relies on (broker liveness + backlog).
    assert "reachable" in ap.kafka_health()


def test_memory_round_trips_through_the_sdk(live):
    # A human key isn't bound to an agent, so it must name the namespace it acts
    # on (namespace isolation — an agent key is instead locked to its own).
    ap = live.sdk(live.mint("operator"))
    ap.save_memory("the sky is blue", key="sky", agent="hello-world")
    hits = ap.search_memories(q="sky", agent="hello-world")
    assert any("sky is blue" in m["content"] for m in hits)


def test_human_memory_without_namespace_is_refused(live):
    """The other half of the namespace rule: a human key that names no agent is
    told so (400), rather than silently writing to a global bucket."""
    ap = live.sdk(live.mint("operator"))
    with pytest.raises(ApiError) as ei:
        ap.save_memory("orphan", key="x")
    assert ei.value.status == 400


def test_reader_key_cannot_trigger_runs(live):
    """RBAC is real, not mocked: a reader key sees runs but is refused a run
    trigger with 403 — the skill's role note, enforced end to end."""
    ap = live.sdk(live.mint("reader"))
    ap.list_runs()  # allowed
    with pytest.raises(ApiError) as ei:
        ap.create_run("hello-world", "nope")
    assert ei.value.status == 403


def test_bad_key_is_rejected(live):
    ap = live.sdk("ap_not_a_real_key")
    with pytest.raises(ApiError) as ei:
        ap.list_runs()
    assert ei.value.status == 401


# --- the skill must not drift from the API either -----------------------------

def _skill_paths() -> set[str]:
    """The /api/... paths the platform skill documents, templated to match the
    OpenAPI (concrete ids like <id> / {name} → a single wildcard segment)."""
    text = (REPO / "skills" / "agent-platform" / "SKILL.md").read_text()
    paths = set()
    for m in re.finditer(r"/api/[A-Za-z0-9/_<>{}-]+", text):
        p = m.group(0).rstrip("/`\"'")
        norm = "/".join("{}" if re.fullmatch(r"[<{].*[>}]", seg) else seg
                        for seg in p.split("/"))
        paths.add(norm)
    return paths


def test_skill_documented_paths_exist_in_openapi(live):
    documented = _skill_paths()
    assert documented, "no /api paths parsed from SKILL.md — parser drift?"
    spec = live.app.openapi()["paths"]
    live_templates = {"/".join("{}" if seg.startswith("{") else seg
                               for seg in tmpl.split("/")) for tmpl in spec}
    missing = {p for p in documented if p not in live_templates}
    assert not missing, f"SKILL.md documents endpoints absent from the API: {missing}"
