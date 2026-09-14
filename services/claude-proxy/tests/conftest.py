"""Test fixtures for the claude-proxy.

The proxy ships no code of its own: it is stock nginx plus the njs and the
config that live in the chart's ConfigMap. So these tests render the chart with
`helm template` and, for the behavioural ones, run the real image over the
rendered config with fake servers standing in for Anthropic and the platform API.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "charts" / "agent-platform"

# Both are `required` in the templates, so every render has to supply them.
DUMMY_SECRETS = [
    "--set", "env.AP_SESSION_SECRET=test-session-secret",
    "--set", "env.AP_INTERNAL_SECRET=test-internal-secret",
]

# The snapshot Anthropic returns on a real /v1/messages call (docs/design/22).
RATELIMIT_HEADERS = {
    "anthropic-ratelimit-unified-5h-utilization": "0.22",
    "anthropic-ratelimit-unified-5h-reset": "1757880000",
    "anthropic-ratelimit-unified-7d-utilization": "0.81",
    "anthropic-ratelimit-unified-7d-reset": "1758150000",
    "anthropic-ratelimit-unified-status": "allowed",
}

UPSTREAM_BODY = b'{"content":"hello from anthropic"}'

# How many padding headers /v1/flood adds on top of the real five. Sized to stay
# inside nginx's proxy_buffer_size (one page): a response nginx itself rejects
# would prove nothing about the header filter.
FLOOD_HEADERS = 70
FLOOD_VALUE = "9" * 8


def helm_template(*overrides: str, show_only: str | None = None) -> list[dict]:
    """Render the chart and return the parsed documents."""
    cmd = ["helm", "template", "test", str(CHART), *DUMMY_SECRETS, *overrides]
    if show_only:
        cmd += ["--show-only", show_only]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def chart_values() -> dict:
    return yaml.safe_load((CHART / "values.yaml").read_text())


def _docker_ready() -> str | None:
    """Reason the docker tests cannot run, or None when they can."""
    if shutil.which("docker") is None:
        return "docker CLI not installed"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return f"docker daemon unreachable: {probe.stderr.strip().splitlines()[-1:]}"
    return None


@pytest.fixture(scope="session")
def docker_host() -> tuple[str, str]:
    """(host address reachable from a container, DNS resolver to hand nginx).

    nginx's `resolver` bypasses /etc/hosts, so the fakes are addressed by the IP
    behind host.docker.internal rather than by that name.
    """
    reason = _docker_ready()
    if reason:
        pytest.skip(reason)
    image = _proxy_image()
    probe = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
         "grep -m1 nameserver /etc/resolv.conf; getent hosts host.docker.internal"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        pytest.skip(f"cannot probe the container network: {probe.stderr.strip()}")
    lines = probe.stdout.split()
    try:
        resolver = lines[lines.index("nameserver") + 1]
        host_ip = probe.stdout.strip().splitlines()[-1].split()[0]
    except (ValueError, IndexError):  # pragma: no cover - host specific
        pytest.skip(f"unexpected container network probe output: {probe.stdout!r}")
    return host_ip, resolver


def _proxy_image() -> str:
    image = chart_values()["claudeProxy"]["image"]
    return f"{image['repository']}:{image['tag']}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _Server:
    """A stdlib HTTP server on its own thread, restartable on the same port."""

    handler: type[BaseHTTPRequestHandler]

    def __init__(self) -> None:
        self.port = _free_port()
        self.received: list[dict] = []
        self.delay = 0.0
        self._httpd: ThreadingHTTPServer | None = None
        self.start()

    def start(self) -> None:
        httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self.handler)
        httpd.received = self.received
        httpd.owner = self
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class _UpstreamHandler(BaseHTTPRequestHandler):
    """Stands in for api.anthropic.com; only /v1/ paths carry the headers."""

    protocol_version = "HTTP/1.1"

    def _respond(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.send_response(200)
        if self.path.startswith("/v1/"):
            for name, value in RATELIMIT_HEADERS.items():
                self.send_header(name, value)
        if self.path == "/v1/flood":
            for i in range(FLOOD_HEADERS):
                self.send_header(f"anthropic-ratelimit-unified-pad-{i:03d}", FLOOD_VALUE)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(UPSTREAM_BODY)))
        self.end_headers()
        self.wfile.write(UPSTREAM_BODY)

    do_GET = do_POST = _respond

    def log_message(self, *args) -> None:
        pass


class _ReceiverHandler(BaseHTTPRequestHandler):
    """Stands in for the platform API's /api/internal/quota route."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        self.server.received.append({
            "path": self.path,
            "secret": self.headers.get("X-AP-Internal-Secret"),
            "content_type": self.headers.get("Content-Type"),
            "body": json.loads(raw.decode()),
        })
        time.sleep(self.server.owner.delay)
        try:
            # A body, like the real endpoint: njs 1.0.0 never settles the fetch
            # promise for a bare 204, which would look like a permanent failure.
            reply = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)
        except OSError:
            # The point of the slow case: nginx gave up on us first.
            pass

    def log_message(self, *args) -> None:
        pass


class FakeUpstream(_Server):
    handler = _UpstreamHandler


class FakeReceiver(_Server):
    handler = _ReceiverHandler


class Proxy:
    """The real nginx image running the chart's rendered config."""

    def __init__(self, workdir: Path, host_ip: str, resolver: str,
                 upstream_url: str, quota_url: str, *, secret: str | None = "test-internal-secret") -> None:
        self.workdir = workdir
        docs = helm_template(
            f"--set=claudeProxy.upstream={upstream_url}",
            f"--set=claudeProxy.quota.apiUrl={quota_url}",
            show_only="templates/claude-proxy-config.yaml",
        )
        data = docs[0]["data"]
        (workdir / "njs").mkdir(parents=True, exist_ok=True)
        (workdir / "templates").mkdir(parents=True, exist_ok=True)
        (workdir / "nginx.conf").write_text(data["nginx.conf"])
        (workdir / "njs" / "claude.js").write_text(data["claude.js"])
        (workdir / "templates" / "claude-proxy.conf.template").write_text(
            data["claude-proxy.conf.template"])
        (workdir / "claude").mkdir(exist_ok=True)
        (workdir / "claude" / "token").write_text("dummy-oauth-token\n")
        (workdir / "internal").mkdir(exist_ok=True)
        if secret is not None:
            (workdir / "internal" / "quota").write_text(secret + "\n")

        self.port = _free_port()
        run = subprocess.run(
            ["docker", "run", "-d", "-p", f"{self.port}:8000",
             "-e", f"DNS_RESOLVER={resolver}",
             "-v", f"{workdir}/nginx.conf:/etc/nginx/nginx.conf:ro",
             "-v", f"{workdir}/njs:/etc/nginx/njs:ro",
             "-v", f"{workdir}/templates:/etc/nginx/templates:ro",
             "-v", f"{workdir}/claude:/secrets/claude:ro",
             "-v", f"{workdir}/internal:/secrets/internal:ro",
             _proxy_image()],
            capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stderr
        self.container = run.stdout.strip()
        self._wait_ready()

    def _wait_ready(self) -> None:
        # A published port accepts before nginx does (docker binds it first), so
        # readiness is a real answered request, not a successful connect().
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                self.request("/ready-probe")
                return
            except OSError:
                time.sleep(0.2)
        raise AssertionError(f"proxy never answered:\n{self.logs()}")

    def request(self, path: str) -> tuple[int, bytes, dict]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:  # pragma: no cover - failure path
            return exc.code, exc.read(), dict(exc.headers)

    def logs(self) -> str:
        return subprocess.run(["docker", "logs", self.container],
                              capture_output=True, text=True).stderr

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.container],
                       capture_output=True, text=True)


@pytest.fixture(scope="module")
def fakes(docker_host):
    host_ip, _ = docker_host
    upstream, receiver = FakeUpstream(), FakeReceiver()
    try:
        yield upstream, receiver, host_ip
    finally:
        upstream.stop()
        receiver.stop()


@pytest.fixture(scope="module")
def proxy(tmp_path_factory, docker_host, fakes):
    host_ip, resolver = docker_host
    upstream, receiver, _ = fakes
    p = Proxy(tmp_path_factory.mktemp("proxy"), host_ip, resolver,
              f"http://{host_ip}:{upstream.port}", f"http://{host_ip}:{receiver.port}")
    try:
        yield p
    finally:
        p.stop()


def run_njs(workdir: Path, harness: str) -> dict:
    """Run `harness` against the rendered claude.js in the image's njs CLI.

    nginx caps an upstream's header block at proxy_buffer_size (4k), so the
    filter's own guards cannot be reached through a live proxy. The interpreter
    that runs them in production can, and it is in the same image.
    """
    docs = helm_template(show_only="templates/claude-proxy-config.yaml")
    (workdir / "claude.js").write_text(docs[0]["data"]["claude.js"])
    (workdir / "harness.js").write_text(harness)
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{workdir}:/njs:ro",
         "--entrypoint", "njs", _proxy_image(), "/njs/harness.js"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def wait_for(predicate, timeout: float = 12.0) -> bool:
    """The push is a 5 s timer, so every assertion about it has to wait."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()
