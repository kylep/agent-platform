"""End-to-end exercise of the generated SDK against a real API.

The SDK under `sdk/` is generated from the OpenAPI spec (see sdk/regenerate.py);
CI regenerates and diffs it, so it can't drift. This test proves the generated
client actually works against the running app: the real `AuthenticatedClient`
drives the real ASGI app over httpx with a genuine `ap_` key — routing, auth,
RBAC, and typed (de)serialization for real. The async path is used because the
in-process app is served via httpx's ASGI transport.

The platform skill (`skills/agent-platform/SKILL.md`) documents this same API,
so `test_skill_documented_paths_exist_in_openapi` holds it to the live OpenAPI.
"""
import re
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "sdk"))
from agent_platform_sdk import AuthenticatedClient  # noqa: E402
from agent_platform_sdk.api.default import (create_run, get_run, kafka_health,  # noqa: E402
                                            list_agents, list_runs, save_memory,
                                            list_memories)
from agent_platform_sdk.models import MemoryIn, RunIn  # noqa: E402

from agentplatform.apikeys import generate_token, hash_token, token_prefix  # noqa: E402
from agentplatform.db import ApiKey  # noqa: E402


async def _mint(sf, role: str) -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=f"itest:{role}", role=role, key_hash=hash_token(token),
                     prefix=token_prefix(token)))
        await s.commit()
    return token


def _sdk(app, token: str) -> AuthenticatedClient:
    """A real generated client whose async httpx routes through the ASGI app."""
    return AuthenticatedClient(
        base_url="http://itest", token=token,
        httpx_args={"transport": httpx.ASGITransport(app=app)})


@pytest.fixture
async def app(sf, producer, secret_store, agent_store):
    from agentplatform.api.app import create_app
    from agentplatform.config import Settings
    return create_app(Settings(agents_root=str(agent_store.root)), sf, producer,
                      secret_store=secret_store, agent_store=agent_store)


async def test_operator_key_drives_the_documented_flow(app, sf):
    """The sequence the skill teaches: list agents → trigger a run → fetch it →
    list recent runs → check health, through the generated typed client."""
    c = _sdk(app, await _mint(sf, "operator"))

    agents = await list_agents.asyncio(client=c)
    assert any(a.name == "hello-world" for a in agents)          # typed AgentSummary

    run = await create_run.asyncio(client=c, body=RunIn(agent="hello-world", prompt="hi"))
    assert run.state == "queued" and run.id

    fetched = await get_run.asyncio(client=c, run_id=run.id)
    assert fetched.id == run.id and fetched.agent == "hello-world"

    assert any(r.id == run.id for r in await list_runs.asyncio(client=c))

    assert (await kafka_health.asyncio(client=c)).reachable is False   # typed KafkaHealth


async def test_memory_round_trips_through_the_sdk(app, sf):
    c = _sdk(app, await _mint(sf, "operator"))
    # A human key isn't bound to an agent, so it names the namespace it acts on.
    await save_memory.asyncio(client=c, body=MemoryIn(content="the sky is blue",
                                                      key="sky", agent="hello-world"))
    hits = await list_memories.asyncio(client=c, q="sky", agent="hello-world")
    assert any("sky is blue" in m.content for m in hits)


async def test_reader_key_cannot_trigger_runs(app, sf):
    """RBAC is real: a reader key lists runs but is refused a run trigger (403).
    `_detailed` exposes the status code the parsed helper hides."""
    c = _sdk(app, await _mint(sf, "reader"))
    assert (await list_runs.asyncio_detailed(client=c)).status_code == 200
    denied = await create_run.asyncio_detailed(client=c, body=RunIn(agent="hello-world", prompt="no"))
    assert denied.status_code == 403


async def test_bad_key_is_rejected(app, sf):
    c = _sdk(app, "ap_not_a_real_key")
    assert (await list_runs.asyncio_detailed(client=c)).status_code == 401


# --- the skill must not drift from the API either -----------------------------

def _skill_paths() -> set[str]:
    text = (REPO / "skills" / "agent-platform" / "SKILL.md").read_text()
    paths = set()
    for m in re.finditer(r"/api/[A-Za-z0-9/_<>{}-]+", text):
        p = m.group(0).rstrip("/`\"'")
        norm = "/".join("{}" if re.fullmatch(r"[<{].*[>}]", seg) else seg
                        for seg in p.split("/"))
        paths.add(norm)
    return paths


def test_skill_documented_paths_exist_in_openapi(app):
    documented = _skill_paths()
    assert documented, "no /api paths parsed from SKILL.md — parser drift?"
    spec = app.openapi()["paths"]
    live = {"/".join("{}" if seg.startswith("{") else seg
                     for seg in tmpl.split("/")) for tmpl in spec}
    missing = {p for p in documented if p not in live}
    assert not missing, f"SKILL.md documents endpoints absent from the API: {missing}"
