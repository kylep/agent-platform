"""The proxy's quota capture (docs/design/22).

Every model call in the platform goes through this proxy, so the capture is
held to one rule above all others: it may not change the response the runner
sees, or when it sees it.
"""

from __future__ import annotations

import json
import subprocess

from conftest import (
    FLOOD_HEADERS,
    run_njs,
    RATELIMIT_HEADERS,
    UPSTREAM_BODY,
    CHART,
    Proxy,
    helm_template,
    wait_for,
)


# --- chart rendering (no docker) --------------------------------------------
def _conf(*overrides: str) -> str:
    docs = helm_template(*overrides, show_only="templates/claude-proxy-config.yaml")
    return docs[0]["data"]["claude-proxy.conf.template"]


def test_internal_secret_carries_the_quota_key():
    docs = helm_template(show_only="templates/internal-secret.yaml")
    secret = docs[0]
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "test-internal"
    assert secret["stringData"] == {"quota": "test-internal-secret"}


def test_a_proxyless_install_needs_no_internal_secret():
    """The legacy fallback (token mounted into every runner) must stay usable."""
    for off in ("claudeProxy.enabled=false", "claudeProxy.quota.enabled=false"):
        result = subprocess.run(
            ["helm", "template", "test", str(CHART),
             "--set", "env.AP_SESSION_SECRET=x", "--set", off],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "secretName: test-internal" not in result.stdout
        assert "mountPath: /secrets/internal" not in result.stdout


def test_internal_secret_is_required():
    cmd = ["helm", "template", "test", str(CHART),
           "--set", "env.AP_SESSION_SECRET=x",
           "--show-only", "templates/internal-secret.yaml"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode != 0
    assert "env.AP_INTERNAL_SECRET is required" in result.stderr


def test_proxy_mounts_the_internal_secret_read_only_and_rolls_on_config_change():
    docs = helm_template(show_only="templates/claude-proxy.yaml")
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    pod = deployment["spec"]["template"]
    assert pod["metadata"]["annotations"]["checksum/config"]
    container = pod["spec"]["containers"][0]
    mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/secrets/internal")
    assert mount["readOnly"] is True
    volume = next(v for v in pod["spec"]["volumes"] if v["name"] == mount["name"])
    assert volume["secret"]["secretName"] == "test-internal"


def test_config_change_moves_the_checksum():
    def checksum(*overrides):
        docs = helm_template(*overrides, show_only="templates/claude-proxy.yaml")
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        return deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"]

    assert checksum() != checksum("--set=claudeProxy.quota.apiUrl=http://elsewhere:8000")


def test_network_policies_allow_the_quota_hop_both_ways():
    docs = helm_template(show_only="templates/networkpolicy.yaml")
    by_name = {d["metadata"]["name"]: d for d in docs}

    def components(policy):
        found = set()
        for rule in policy["spec"]["ingress"]:
            for source in rule.get("from", []):
                for expr in source.get("podSelector", {}).get("matchExpressions", []):
                    if expr["key"] == "app.kubernetes.io/component":
                        found |= set(expr["values"])
        return found

    assert "claude-proxy" in components(by_name["test-allow-api"])
    assert "api" in components(by_name["test-allow-claude-proxy"])


def test_production_defaults_keep_anthropic_tls_and_blank_the_api_key():
    conf = _conf()
    assert "set $upstream https://api.anthropic.com;" in conf
    assert "proxy_set_header Host api.anthropic.com;" in conf
    assert "proxy_ssl_verify on;" in conf
    assert "proxy_ssl_name api.anthropic.com;" in conf
    assert 'proxy_set_header x-api-key "";' in conf
    assert "js_header_filter claude.quotaCapture;" in conf


def test_tls_verification_follows_the_scheme():
    """No combination of values sends the token over https unverified."""
    plain = _conf("--set=claudeProxy.upstream=http://fake.test:1234")
    assert "proxy_ssl" not in plain
    assert "set $upstream http://fake.test:1234;" in plain
    assert "proxy_set_header Host fake.test:1234;" in plain

    other = _conf("--set=claudeProxy.upstream=https://relay.example.com")
    assert "proxy_ssl_verify on;" in other
    # The verified name tracks the upstream rather than a hardcoded constant,
    # so pointing elsewhere cannot silently verify against api.anthropic.com.
    assert "proxy_ssl_name relay.example.com;" in other
    assert "api.anthropic.com" not in other


def test_the_push_cannot_outlive_its_own_tick():
    conf = _conf()
    assert "js_fetch_timeout 1500ms;" in conf
    assert "js_periodic claude.quotaPush interval=5s;" in conf
    # Without evict, a full zone throws out of the header filter.
    assert "js_shared_dict_zone zone=quota:32k timeout=60s evict;" in conf


def test_quota_can_be_switched_off():
    conf = _conf("--set=claudeProxy.quota.enabled=false")
    assert "js_header_filter" not in conf
    assert "js_periodic" not in conf
    assert "js_shared_dict_zone" not in conf



# --- the real image over the rendered config --------------------------------

def test_ratelimit_headers_reach_the_api_untouched(proxy, fakes):
    _, receiver, _ = fakes
    receiver.received.clear()

    status, body, headers = proxy.request("/v1/messages")

    assert (status, body) == (200, UPSTREAM_BODY)
    for name, value in RATELIMIT_HEADERS.items():
        assert headers[name] == value

    assert wait_for(lambda: len(receiver.received) == 1), proxy.logs()
    post = receiver.received[0]
    assert post["path"] == "/api/internal/quota"
    assert post["secret"] == "test-internal-secret"
    assert post["content_type"] == "application/json"
    assert post["body"]["headers"] == RATELIMIT_HEADERS
    assert post["body"]["status"] == 200
    assert post["body"]["observed_at"].endswith("Z")
    # One response, one post — the timer must not re-send what it already sent.
    assert not wait_for(lambda: len(receiver.received) > 1, timeout=7)


def test_a_response_without_the_headers_posts_nothing(proxy, fakes):
    _, receiver, _ = fakes
    receiver.received.clear()

    status, body, headers = proxy.request("/health")

    assert (status, body) == (200, UPSTREAM_BODY)
    assert not any(h.startswith("anthropic-ratelimit") for h in headers)
    assert not wait_for(lambda: receiver.received, timeout=8)


# Exercises quotaCapture in the interpreter that runs it in production, with a
# stubbed dict, because nginx's own 4k header budget stops a live proxy from
# reaching the guards below.
CAPTURE_HARNESS = """
import claude from '/njs/claude.js';
function capture(headersOut, status, failStore) {
  var stored = null, warnings = [];
  globalThis.ngx = {shared: {quota: {set: function (k, v) {
    if (failStore) { throw Error('SharedMemoryError'); }
    stored = v;
  }}}};
  claude.quotaCapture({headersOut: headersOut, status: status,
                       warn: function (m) { warnings.push(m); }});
  return {stored: stored, warnings: warnings};
}
var flood = {};
for (var i = 0; i < 200; i++) {
  flood['anthropic-ratelimit-unified-pad-' + i] = '9'.repeat(40);
}
flood['anthropic-ratelimit-unified-status'] = 'allowed';
flood['anthropic-ratelimit-unified-5h-utilization'] = '0.22';
console.log(JSON.stringify({
  trimmed: capture(flood, 200),
  repeated: capture({'Anthropic-RateLimit-Unified-Status': ['first', 'last']}, 200),
  throwing: capture({'anthropic-ratelimit-unified-status': 'allowed'}, 200, true),
  irrelevant: capture({'content-type': 'application/json'}, 200)
}));
"""


def test_the_capture_never_throws_and_bounds_what_it_stores(tmp_path, docker_host):
    out = run_njs(tmp_path, CAPTURE_HARNESS)

    # An unbounded header set falls back to the fields the platform reads.
    trimmed = json.loads(out["trimmed"]["stored"])
    assert trimmed["headers"] == {
        "anthropic-ratelimit-unified-5h-utilization": "0.22",
        "anthropic-ratelimit-unified-status": "allowed",
    }
    assert out["trimmed"]["warnings"] == []

    # A repeated header is the value nginx sends, not a comma-joined array.
    assert json.loads(out["repeated"]["stored"])["headers"] == {
        "anthropic-ratelimit-unified-status": "last"}

    # A full zone must be a log line, not a destroyed response.
    assert out["throwing"]["stored"] is None
    assert out["throwing"]["warnings"] == ["quota capture failed: SharedMemoryError"]

    assert out["irrelevant"] == {"stored": None, "warnings": []}


def test_a_flood_of_headers_does_not_reach_the_client(proxy, fakes):
    """The capture may drop a snapshot under pressure; it may not drop a reply."""
    _, receiver, _ = fakes
    receiver.received.clear()

    status, body, headers = proxy.request("/v1/flood")

    assert (status, body) == (200, UPSTREAM_BODY)
    assert len([h for h in headers if h.startswith("anthropic-ratelimit-unified-pad-")]) \
        == FLOOD_HEADERS
    assert wait_for(lambda: receiver.received), proxy.logs()
    # Whatever else it kept, the fields the platform reads survived intact.
    posted = receiver.received[0]["body"]["headers"]
    for name, value in RATELIMIT_HEADERS.items():
        assert posted[name] == value


def test_a_slow_receiver_loses_its_post_and_nothing_else(proxy, fakes):
    """A hung API must not wedge the timer: the next tick has to win."""
    _, receiver, _ = fakes
    receiver.received.clear()
    receiver.delay = 3.0
    try:
        status, body, _ = proxy.request("/v1/messages")
        assert (status, body) == (200, UPSTREAM_BODY)
        assert wait_for(lambda: receiver.received), proxy.logs()
        assert wait_for(lambda: "quota post failed" in proxy.logs()), proxy.logs()
    finally:
        receiver.delay = 0.0

    receiver.received.clear()
    proxy.request("/v1/messages")
    assert wait_for(lambda: receiver.received), proxy.logs()
    assert receiver.received[0]["body"]["headers"] == RATELIMIT_HEADERS


def test_a_dead_receiver_does_not_change_what_the_client_gets(
        tmp_path, docker_host, fakes):
    host_ip, resolver = docker_host
    upstream, _, _ = fakes
    from conftest import _free_port

    dead = Proxy(tmp_path, host_ip, resolver,
                 f"http://{host_ip}:{upstream.port}",
                 f"http://{host_ip}:{_free_port()}")
    try:
        status, body, headers = dead.request("/v1/messages")
        assert (status, body) == (200, UPSTREAM_BODY)
        assert headers["anthropic-ratelimit-unified-status"] == "allowed"
        # And the failure is logged, not raised.
        assert wait_for(lambda: "quota post failed" in dead.logs(), timeout=8), dead.logs()
    finally:
        dead.stop()


def test_a_missing_secret_file_posts_nothing(tmp_path, docker_host, fakes):
    host_ip, resolver = docker_host
    upstream, receiver, _ = fakes
    receiver.received.clear()

    bare = Proxy(tmp_path, host_ip, resolver,
                 f"http://{host_ip}:{upstream.port}",
                 f"http://{host_ip}:{receiver.port}", secret=None)
    try:
        status, body, _ = bare.request("/v1/messages")
        assert (status, body) == (200, UPSTREAM_BODY)
        assert not wait_for(lambda: receiver.received, timeout=8)
        assert "quota secret unreadable" in bare.logs()
    finally:
        bare.stop()


def test_the_default_push_target_is_the_api_service_full_name():
    # nginx's resolver applies no search domains: a bare service name fails with
    # "Host not found" at push time (seen live, design 22 R1).
    docs = helm_template(show_only="templates/claude-proxy-config.yaml")
    config = next(d for d in docs if d["kind"] == "ConfigMap")
    assert "http://agent-platform-api.default.svc.cluster.local:8000/api/internal/quota" in config["data"]["claude.js"]
