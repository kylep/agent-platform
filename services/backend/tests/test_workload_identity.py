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
