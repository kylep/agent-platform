"""Deterministic secret verification (docs/design/10). Two forms, both code
not LLM: a declarative HTTP probe (GET url with headers, 2xx = valid) and a
sandboxed verify script (subprocess with ONLY that secret's data in its env —
a script can't reach other secrets or the DB). The registry's `verify:` block
picks the form; claude-credentials (`verify: run`) is verified by run outcomes
in the recorder and has nothing runnable here."""
import asyncio
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from agentplatform.secretregistry import ProbeSpec, SecretInfo

PROBE_TIMEOUT_SECONDS = 8
SCRIPT_TIMEOUT_SECONDS = 20

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.-]+)\}")


@dataclass
class VerifyResult:
    status: str  # "valid" | "invalid"
    code: int | None  # HTTP status for probes; None for scripts/network errors
    detail: str


def interpolate(template: str, data: dict[str, str]) -> str:
    """Substitute `{key}` placeholders with the secret's data. Raises KeyError
    on a missing key. Tolerates a pasted auth-scheme prefix: with the template
    `Bot {token}` and a value already starting with `Bot `, the duplicate
    scheme is dropped (generalizes the old discord-bot special case)."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in data:
            raise KeyError(key)
        val = data[key].strip()
        before = template[: m.start()]
        prefix = re.search(r"(\S+ )$", before)
        if prefix and val.lower().startswith(prefix.group(1).lower()):
            val = val[len(prefix.group(1)):].strip()
        return val

    return _PLACEHOLDER.sub(repl, template)


def http_probe(url: str, headers: dict) -> tuple[int | None, str]:
    """GET the url; return (http_status, detail). status is None on a network
    error (couldn't reach the host at all)."""
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as r:
            return r.status, "ok"
    except urllib.error.HTTPError as e:
        return e.code, (e.reason or "")
    except urllib.error.URLError as e:
        return None, f"unreachable: {e.reason}"
    except Exception as e:
        return None, f"error: {e}"


def _run_probe(probe: ProbeSpec, data: dict[str, str]) -> VerifyResult:
    try:
        url = interpolate(probe.url, data)
        headers = {k: interpolate(v, data) for k, v in probe.headers.items()}
    except KeyError as e:
        return VerifyResult("invalid", None, f"missing key `{e.args[0]}` in secret data")
    if not url.startswith(("http://", "https://")):
        return VerifyResult("invalid", None, "probe url is not http(s)")
    code, detail = http_probe(url, headers)
    ok = code is not None and 200 <= code < 300
    return VerifyResult("valid" if ok else "invalid", code, detail)


def _run_script(info: SecretInfo, data: dict[str, str]) -> VerifyResult:
    script = (info.dir / info.spec.verify.script).resolve()
    root = info.dir.resolve()
    if not script.is_file() or root not in script.parents:
        return VerifyResult("invalid", None, f"verify script `{info.spec.verify.script}` not found")
    # Sandbox: a fresh env holding ONLY this secret's data (plus PATH so the
    # interpreter works); -I ignores PYTHONPATH/user-site. The script keeps the
    # backend image's installed packages (it needs e.g. PyJWT) but sees no other
    # secret, no DB URL, no platform config.
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), **data}
    try:
        p = subprocess.run([sys.executable, "-I", str(script)], env=env,
                           capture_output=True, text=True,
                           timeout=SCRIPT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return VerifyResult("invalid", None, "verify script timed out")
    detail = (p.stdout.strip() or p.stderr.strip())[-500:]
    return VerifyResult("valid" if p.returncode == 0 else "invalid", None, detail)


async def verify_secret(info: SecretInfo, data: dict[str, str]) -> VerifyResult | None:
    """Run the secret's declared verification. None when there is nothing the
    platform can run (no spec, `verify: run`, or no verify at all)."""
    spec = info.spec
    if spec is None or not spec.verifiable:
        return None
    if spec.verify.probe is not None:
        return await asyncio.to_thread(_run_probe, spec.verify.probe, data)
    return await asyncio.to_thread(_run_script, info, data)
