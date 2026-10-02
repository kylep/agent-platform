"""The views pool's chart (docs/design/39 "Tool views"; plan D6): a second
executor Deployment whose pods reach nothing but DNS and the platform API, and
which only the API reaches. Its deny policy renders whatever the global
`networkPolicy` switches say, and no global allow widens it.

Rendered with `helm template`, as services/claude-proxy/tests does; needs
`helm dependency update charts/agent-platform` first."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "charts" / "agent-platform"
SECRETS = ["--set", "env.AP_SESSION_SECRET=x", "--set", "env.AP_INTERNAL_SECRET=y"]
COMPONENT = "tool-executor-views"
VIEWS_POD = {"app.kubernetes.io/name": "agent-platform",
             "app.kubernetes.io/instance": "test",
             "app.kubernetes.io/component": COMPONENT}
API_POD = {**VIEWS_POD, "app.kubernetes.io/component": "api"}
DENY = f"test-{COMPONENT}"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def render(*overrides: str) -> list[dict]:
    out = subprocess.run(["helm", "template", "test", str(CHART), *SECRETS, *overrides],
                         capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def find(docs, kind, name):
    hits = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(hits) == 1, f"{kind}/{name}: {len(hits)} found"
    return hits[0]


def selects(selector: dict, labels: dict) -> bool:
    """Kubernetes label-selector semantics, enough for this chart."""
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        key, op, values = e["key"], e["operator"], e.get("values") or []
        if op == "In" and labels.get(key) not in values:
            return False
        if op == "NotIn" and key in labels and labels[key] in values:
            return False
        if op == "Exists" and key not in labels:
            return False
        if op == "DoesNotExist" and key in labels:
            return False
    return True


def policies(docs):
    return [d for d in docs if d["kind"] == "NetworkPolicy"]


@pytest.fixture(scope="module")
def no_egress():
    return render("--set", "networkPolicy.egress=false")


@pytest.fixture(scope="module")
def with_egress():
    return render("--set", "networkPolicy.egress=true")


def test_pool_deployment_runs_views_mode_under_its_own_identity(no_egress):
    dep = find(no_egress, "Deployment", "test-tool-executor-views")
    spec = dep["spec"]["template"]["spec"]
    assert dep["spec"]["template"]["metadata"]["labels"] == VIEWS_POD
    assert spec["serviceAccountName"] == "test-tool-executor-views"
    # No k8s API token: the pool fetches no secrets.
    assert spec["automountServiceAccountToken"] is False
    (c,) = spec["containers"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    assert env["AP_EXECUTOR_POOL"] == "views"
    assert "AP_KAFKA_BOOTSTRAP" not in env
    find(no_egress, "ServiceAccount", "test-tool-executor-views")
    svc = find(no_egress, "Service", "agent-platform-tool-executor-views")
    assert svc["spec"]["selector"] == VIEWS_POD
    # The pool's SA is bound to no Role: it reads no secrets.
    for b in (d for d in no_egress if d["kind"] in ("RoleBinding", "ClusterRoleBinding")):
        assert all(s["name"] != "test-tool-executor-views" for s in b.get("subjects") or [])


@pytest.mark.parametrize("overrides", [
    ("--set", "networkPolicy.egress=false"),
    ("--set", "networkPolicy.egress=true"),
    ("--set", "networkPolicy.enabled=false"),
])
def test_deny_policy_renders_whatever_the_global_switches_say(overrides):
    pol = find(render(*overrides), "NetworkPolicy", DENY)
    assert selects(pol["spec"]["podSelector"], VIEWS_POD)
    assert sorted(pol["spec"]["policyTypes"]) == ["Egress", "Ingress"]


def test_deny_policy_allows_only_dns_and_the_api(no_egress):
    spec = find(no_egress, "NetworkPolicy", DENY)["spec"]
    dns, api = spec["egress"]
    assert dns["to"] == [{"namespaceSelector": {"matchLabels": {
        "kubernetes.io/metadata.name": "kube-system"}}}]
    assert sorted((p["port"], p["protocol"]) for p in dns["ports"]) == [(53, "TCP"), (53, "UDP")]
    (peer,) = api["to"]
    assert set(peer) == {"podSelector"}
    assert selects(peer["podSelector"], API_POD)
    assert not selects(peer["podSelector"], {**API_POD, "app.kubernetes.io/component": "tool-executor"})
    assert api["ports"] == [{"port": 8000, "protocol": "TCP"}]
    (ingress,) = spec["ingress"]
    (src,) = ingress["from"]
    assert set(src) == {"podSelector"}
    assert selects(src["podSelector"], API_POD)
    for other in ("mcp-broker", "runner", "web", "tool-executor"):
        assert not selects(src["podSelector"], {**API_POD, "app.kubernetes.io/component": other})


def test_no_global_egress_allow_selects_the_pool(with_egress):
    """Policies are additive: one global allow that selected the pool would
    reopen its egress, so every other egress policy must miss it."""
    egress = [p for p in policies(with_egress)
              if "Egress" in p["spec"]["policyTypes"] and p["metadata"]["name"] != DENY]
    assert any(p["metadata"]["name"] == "test-allow-egress-internal" for p in egress)
    for p in egress:
        assert not (p["spec"].get("egress") and selects(p["spec"]["podSelector"], VIEWS_POD)), \
            p["metadata"]["name"]


def test_the_api_admits_the_pool(no_egress):
    pol = find(no_egress, "NetworkPolicy", "test-allow-api")
    assert any(selects(src["podSelector"], VIEWS_POD)
               for rule in pol["spec"]["ingress"] for src in rule["from"])


def test_values_files_size_the_pool():
    for name in ("values.yaml", "values-pai-nuc.yaml"):
        v = yaml.safe_load((CHART / name).read_text())["toolExecutorViews"]
        assert v["replicas"] == 1
        assert v["resources"]["requests"]
