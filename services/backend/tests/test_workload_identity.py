"""Workload identity (docs/design/13 A): projected SA tokens replace minted
bearer secrets for ladder agents. Identity comes from the cluster
(TokenReview), authorization from the agent's declared tools in git."""
import pytest
from kubernetes.client.rest import ApiException

from agentplatform.config import Settings
from agentplatform.joblauncher import K8sJobLauncher

from .test_toolregistry import tool_client  # noqa: F401 — shared fixture

# A syntactically JWT-shaped bearer (content irrelevant — the fake validator
# decides; the real one is TokenReview).
FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"


def _fake_validator(username: str | None):
    async def validate(token: str) -> str | None:
        return username
    return validate


def _auth():
    return {"Authorization": f"Bearer {FAKE_JWT}"}


async def test_sa_token_resolves_agent_and_ladder_role(tool_client):
    app_state = tool_client._transport.app.state
    app_state.sa_validator = _fake_validator("system:serviceaccount:ap:agent-echo-user")
    tool_client.cookies.clear()
    r = await tool_client.get("/api/whoami", headers=_auth())
    assert r.status_code == 200, r.text
    d = r.json()
    # echo-user declares only the custom echo tool → whoami-only tools role.
    assert d["principal"] == "sa:echo-user" and d["role"] == "tools"
    assert d["agent"] == "echo-user" and d["run_id"] is None
    assert d["tools"] == ["mcp__platform__echo"]


async def test_sa_token_core_tools_earn_annotator(tool_client, agent_store):
    d = agent_store.root / "corey"
    d.mkdir()
    (d / "agent.md").write_text("---\nname: corey\ntools: mcp__platform__runs_read\n---\nbody")
    (d / "manifest.yaml").write_text("description: t\n")
    app_state = tool_client._transport.app.state
    app_state.sa_validator = _fake_validator("system:serviceaccount:ap:agent-corey")
    tool_client.cookies.clear()
    d = (await tool_client.get("/api/whoami", headers=_auth())).json()
    assert d["role"] == "annotator"
    # And an annotator SA identity can actually read runs.
    assert (await tool_client.get("/api/runs", headers=_auth())).status_code == 200


@pytest.mark.parametrize("username", [
    None,                                          # TokenReview rejected
    "system:serviceaccount:ap:not-an-agent-sa",    # foreign SA
    "system:serviceaccount:ap:agent-ghost",        # unknown agent
])
async def test_sa_token_rejections(tool_client, username):
    app_state = tool_client._transport.app.state
    app_state.sa_validator = _fake_validator(username)
    tool_client.cookies.clear()
    assert (await tool_client.get("/api/whoami", headers=_auth())).status_code == 401


async def test_sa_token_without_validator_rejected(tool_client):
    # No validator configured (outside a cluster) → SA-shaped bearers fail.
    tool_client.cookies.clear()
    assert (await tool_client.get("/api/whoami", headers=_auth())).status_code == 401


# --- launcher side -----------------------------------------------------------

class _FakeCore:
    def __init__(self):
        self.created = []
        self.existing = set()

    def read_namespaced_service_account(self, name, ns):
        if name not in self.existing:
            raise ApiException(status=404)

    def create_namespaced_service_account(self, ns, body):
        self.created.append(body.metadata.name)
        self.existing.add(body.metadata.name)


def test_ensure_service_account_idempotent():
    core = _FakeCore()
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"),
                              core=core)
    assert launcher._ensure_service_account("pai") == "agent-pai"
    assert launcher._ensure_service_account("pai") == "agent-pai"
    assert core.created == ["agent-pai"]   # cached after first ensure


def test_build_job_with_sa_identity_mounts_projection():
    from agentplatform.agents import Manifest
    from agentplatform.db import Run
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    job = launcher.build_job(Run(id="r" * 32, agent="pai", prompt="p"),
                             Manifest(description="d"), sa_identity="agent-pai")
    spec = job.spec.template.spec
    assert spec.service_account_name == "agent-pai"
    assert spec.automount_service_account_token is False
    env = {e.name: e.value for e in spec.containers[0].env}
    assert env["AP_API_TOKEN_FILE"] == "/var/run/ap-identity/token"
    assert "AP_API_TOKEN" not in env   # identity, not secrets
    vol = next(v for v in spec.volumes if v.name == "ap-identity")
    proj = vol.projected.sources[0].service_account_token
    assert proj.audience == "agent-platform" and proj.expiration_seconds == 7200
    mount = next(m for m in spec.containers[0].volume_mounts if m.name == "ap-identity")
    assert mount.mount_path == "/var/run/ap-identity" and mount.read_only


def test_build_job_without_identity_has_no_projection():
    from agentplatform.agents import Manifest
    from agentplatform.db import Run
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    job = launcher.build_job(Run(id="r" * 32, agent="x", prompt="p"), Manifest(description="d"))
    spec = job.spec.template.spec
    assert spec.service_account_name is None
    assert all(v.name != "ap-identity" for v in spec.volumes)


# --- principal stub (docs/design/13 D) ---------------------------------------

async def test_initiated_by_set_and_chained(client, sf):
    """Admin-started runs record the principal; an agent-invoked child
    inherits the ROOT initiator, not the intermediate agent."""
    from sqlalchemy import select
    from agentplatform.apikeys import generate_token, hash_token, token_prefix
    from agentplatform.db import ApiKey, Run
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})
    root_id = r.json()["id"]
    async with sf() as s:
        root = await s.get(Run, root_id)
        assert root.initiated_by == "admin" and root.requested_by == "admin"
        # Mint an operator per-run key as if hello-world can invoke.
        token = generate_token()
        s.add(ApiKey(name="invoke:hello-world", role="operator", agent="hello-world",
                     run_id=root_id, key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    client.cookies.clear()
    r = await client.post("/api/runs", json={"agent": "hello-world", "prompt": "child"},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    async with sf() as s:
        child = await s.get(Run, r.json()["id"])
    assert child.trigger == "agent" and child.parent_run_id == root_id
    assert child.initiated_by == "admin"          # root principal survives the chain
    assert child.requested_by == "invoke:hello-world"


# --- run JWTs (docs/design/13 C) ---------------------------------------------

from agentplatform import runjwt


def test_runjwt_roundtrip_and_cnf_binding():
    keys = runjwt.generate_keypair()
    tok = runjwt.mint(keys["private_key"], run_id="r1", agent="pai",
                      initiated_by="admin", tools=["mcp__platform__stocks"],
                      sa_name="agent-pai", timeout_seconds=60)
    claims = runjwt.verify(keys["public_key"], tok, expected_sa="agent-pai")
    assert claims["run_id"] == "r1" and claims["tools"] == ["mcp__platform__stocks"]
    assert claims["initiated_by"] == "admin"
    # cnf mismatch: the same token presented by a different workload dies.
    assert runjwt.verify(keys["public_key"], tok, expected_sa="agent-evil") is None
    # wrong key dies.
    other = runjwt.generate_keypair()
    assert runjwt.verify(other["public_key"], tok, expected_sa="agent-pai") is None


def test_runjwt_expiry():
    keys = runjwt.generate_keypair()
    tok = runjwt.mint(keys["private_key"], run_id="r1", agent="pai",
                      initiated_by="admin", tools=[], sa_name="agent-pai",
                      timeout_seconds=-(runjwt.EXP_SLACK_SECONDS + 120))
    assert runjwt.verify(keys["public_key"], tok, expected_sa="agent-pai") is None


async def test_run_token_freezes_grants_and_carries_run_id(tool_client, secret_store):
    keys = runjwt.generate_keypair()
    await secret_store.set(runjwt.SECRET_NAME, keys)
    app_state = tool_client._transport.app.state
    app_state.sa_validator = _fake_validator("system:serviceaccount:ap:agent-echo-user")
    tool_client.cookies.clear()
    tok = runjwt.mint(keys["private_key"], run_id="run-42", agent="echo-user",
                      initiated_by="admin", tools=["mcp__platform__frozen_tool"],
                      sa_name="agent-echo-user", timeout_seconds=300)
    r = await tool_client.get("/api/whoami", headers={
        **_auth(), "X-AP-Run-Token": tok})
    assert r.status_code == 200, r.text
    d = r.json()
    # Frozen set wins over the live agent.md (which declares echo, not frozen_tool).
    assert d["tools"] == ["mcp__platform__frozen_tool"]
    assert d["run_id"] == "run-42" and d["role"] == "tools"


async def test_invalid_run_token_rejects_entirely(tool_client, secret_store):
    keys = runjwt.generate_keypair()
    await secret_store.set(runjwt.SECRET_NAME, keys)
    app_state = tool_client._transport.app.state
    app_state.sa_validator = _fake_validator("system:serviceaccount:ap:agent-echo-user")
    tool_client.cookies.clear()
    # cnf bound to a DIFFERENT agent's SA → presenting it here must 401.
    tok = runjwt.mint(keys["private_key"], run_id="run-42", agent="other",
                      initiated_by="admin", tools=[], sa_name="agent-other",
                      timeout_seconds=300)
    r = await tool_client.get("/api/whoami", headers={**_auth(), "X-AP-Run-Token": tok})
    assert r.status_code == 401
    r = await tool_client.get("/api/whoami", headers={**_auth(), "X-AP-Run-Token": "garbage"})
    assert r.status_code == 401


# --- audit trail (docs/design/13 E) ------------------------------------------

async def test_tool_audit_ingest_and_surfaces(client, sf, producer):
    from agentplatform.ingest import ToolAuditIngestor
    ing = ToolAuditIngestor(None, sf, producer)
    for decision, tool in [("allow", "stocks"), ("allow", "stocks"),
                           ("deny:undeclared", "linear"), ("error:tool", "stocks")]:
        await ing._record({"agent": "pai", "run_id": "r1", "initiated_by": "admin",
                           "tool": tool, "args_digest": "d" * 64,
                           "decision": decision, "latency_ms": 120, "result_bytes": 10})
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    m = {r["tool"]: r for r in (await client.get("/api/metrics/tools")).json()}
    assert m["stocks"]["calls"] == 3 and m["stocks"]["errors"] == 1 and m["stocks"]["denials"] == 0
    assert m["linear"]["denials"] == 1
    rows = (await client.get("/api/audit/tools?decision=deny")).json()
    assert len(rows) == 1 and rows[0]["tool"] == "linear"
    assert rows[0]["initiated_by"] == "admin" and rows[0]["args_digest"] == "d" * 64


# --- SPIRE mTLS sidecar (docs/design/13 B) -----------------------------------

def test_spire_enabled_adds_mcp_tunnel_sidecar():
    from agentplatform.agents import Manifest
    from agentplatform.db import Run
    launcher = K8sJobLauncher(batch=None, settings=Settings(
        runner_image="r:1", k8s_namespace="agent-platform", spire_enabled=True))
    job = launcher.build_job(Run(id="r" * 32, agent="pai", prompt="p"),
                             Manifest(description="d"), sa_identity="agent-pai",
                             pod_sa="agent-pai")
    spec = job.spec.template.spec
    tunnel = spec.init_containers[0]
    assert tunnel.name == "mcp-tunnel" and tunnel.restart_policy == "Always"
    assert "--verify-uri" in tunnel.args
    assert "spiffe://pai/ns/agent-platform/sa/ap-mcp-broker" in tunnel.args
    env = {e.name: e.value for e in spec.containers[0].env}
    assert env["AP_MCP_URL"] == "http://127.0.0.1:8300/mcp"
    assert any(v.name == "spiffe-workload-api" and v.csi.driver == "csi.spiffe.io"
               for v in spec.volumes)
    # The RUNNER container must not see the workload API socket.
    assert all(m.name != "spiffe-workload-api"
               for m in spec.containers[0].volume_mounts)


def test_spire_disabled_keeps_plain_path():
    from agentplatform.agents import Manifest
    from agentplatform.db import Run
    launcher = K8sJobLauncher(batch=None, settings=Settings(
        runner_image="r:1", k8s_namespace="agent-platform"))
    job = launcher.build_job(Run(id="r" * 32, agent="pai", prompt="p"),
                             Manifest(description="d"), sa_identity="agent-pai")
    spec = job.spec.template.spec
    assert spec.init_containers is None
    env = {e.name: e.value for e in spec.containers[0].env}
    assert env["AP_MCP_URL"].startswith("http://agent-platform-mcp-broker")
