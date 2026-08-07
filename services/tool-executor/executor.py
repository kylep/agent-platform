"""Tool executor (docs/design/12) — the ONLY place custom platform tools run.

The MCP broker forwards verified calls here; this service runs the tool's
PR-reviewed `run.py` as a subprocess with a minimal environment:

  - the JSON arguments on stdin (already schema-validated, and validated
    again here — the executor trusts the broker's *identity* verification,
    never its input hygiene),
  - TOOL_CALLER_AGENT / TOOL_RUN_ID (broker-verified, never model-supplied),
  - the tool's declared secrets, fetched from k8s AT CALL TIME (nothing is
    baked into this pod's env),
  - TOOL_DB_URL when the tool declares `database: true` (from the
    provisioner-managed `tool-<name>-db` secret).

The subprocess never sees this process's environment. Timeout and output cap
are enforced; a non-zero exit becomes a structured error for the model.

Netpol makes this pod the single internet-egress point for agent-driven code
and the broker its only client.
"""
import asyncio
import base64
import json
import logging
import os
import ssl
from pathlib import Path

import httpx
import jsonschema
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tool-executor")

TOOLS_ROOT = Path(os.environ.get("AP_TOOLS_ROOT", "/agents/tools"))
OUTPUT_CAP = 256 * 1024
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")

app = FastAPI(title="tool-executor")


class Caller(BaseModel):
    agent: str = ""
    run_id: str = ""


class RunIn(BaseModel):
    tool: str
    args: dict = {}
    caller: Caller = Caller()


def load_manifest(name: str) -> dict:
    """Re-read tool.yaml per call: the checkout syncs under us and a stale
    cache would run yesterday's schema against today's script."""
    if not name.replace("_", "").isalnum() or "/" in name or name.startswith("."):
        raise HTTPException(400, "invalid tool name")
    d = TOOLS_ROOT / name
    yml = d / "tool.yaml"
    if not yml.is_file() or not (d / "run.py").is_file():
        raise HTTPException(404, f"unknown tool {name!r}")
    m = yaml.safe_load(yml.read_text()) or {}
    m.setdefault("name", name)
    m.setdefault("params", {"type": "object"})
    m.setdefault("timeout_seconds", 30)
    m.setdefault("infra", {})
    return m


async def fetch_secret_env(secret_name: str) -> dict[str, str]:
    """Read one k8s Secret's key/values via the pod ServiceAccount. Missing
    secrets degrade (empty dict) — same contract as skill envFrom `optional`."""
    token_file = SA_DIR / "token"
    if not token_file.is_file():
        log.warning("no serviceaccount token; cannot fetch secret %s", secret_name)
        return {}
    ns = (SA_DIR / "namespace").read_text().strip()
    ctx = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
    url = f"https://kubernetes.default.svc/api/v1/namespaces/{ns}/secrets/{secret_name}"
    headers = {"Authorization": f"Bearer {token_file.read_text().strip()}"}
    async with httpx.AsyncClient(verify=ctx, timeout=5) as c:
        r = await c.get(url, headers=headers)
    if r.status_code != 200:
        log.warning("secret %s fetch: %s", secret_name, r.status_code)
        return {}
    data = r.json().get("data") or {}
    return {k: base64.b64decode(v).decode() for k, v in data.items()}


async def build_env(manifest: dict, caller: Caller) -> dict[str, str]:
    infra = manifest.get("infra") or {}
    env = {
        # Minimal, explicit base — never os.environ.
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "TOOL_NAME": manifest["name"],
        "TOOL_CALLER_AGENT": caller.agent,
        "TOOL_RUN_ID": caller.run_id,
    }
    for secret in infra.get("secrets") or []:
        name = secret["name"] if isinstance(secret, dict) else secret
        env.update(await fetch_secret_env(name))
    if infra.get("database"):
        db = await fetch_secret_env(f"tool-{manifest['name']}-db")
        if "TOOL_DB_URL" in db:
            env["TOOL_DB_URL"] = db["TOOL_DB_URL"]
        elif "APP_DB_URL" in db:  # provisioner reuses the app secret shape
            env["TOOL_DB_URL"] = db["APP_DB_URL"]
    return env


@app.get("/healthz")
async def healthz():
    return {"ok": True, "tools_root": str(TOOLS_ROOT), "found": sorted(
        p.name for p in TOOLS_ROOT.iterdir() if (p / "tool.yaml").is_file()
    ) if TOOLS_ROOT.is_dir() else []}


@app.post("/run")
async def run_tool(body: RunIn):
    manifest = load_manifest(body.tool)
    try:
        jsonschema.validate(body.args, manifest["params"])
    except jsonschema.ValidationError as e:
        # A schema miss is the model's mistake — return it as a plain error
        # message the model can correct from, not a 4xx the broker mangles.
        return {"ok": False, "error": f"arguments do not match the tool's schema: {e.message}"}

    env = await build_env(manifest, body.caller)
    timeout = min(int(manifest.get("timeout_seconds", 30)), 120)
    proc = await asyncio.create_subprocess_exec(
        "python3", str(TOOLS_ROOT / body.tool / "run.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(TOOLS_ROOT / body.tool),
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(body.args).encode()), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "error": f"tool timed out after {timeout}s"}

    if proc.returncode != 0:
        detail = (err or out or b"").decode(errors="replace")[-2000:]
        log.warning("tool %s exited %s: %s", body.tool, proc.returncode, detail[:500])
        return {"ok": False, "error": f"tool exited {proc.returncode}: {detail}"}
    text = out.decode(errors="replace")
    if len(text) > OUTPUT_CAP:
        text = text[:OUTPUT_CAP] + f"\n…[truncated at {OUTPUT_CAP} bytes]"
    return {"ok": True, "output": text}


if __name__ == "__main__":
    import uvicorn
    # design/13 B: with SPIRE mTLS on, bind localhost behind the ghostunnel
    # server sidecar (8443, broker-SVID clients only).
    uvicorn.run(app, host=os.environ.get("AP_BIND_HOST", "0.0.0.0"), port=8000)
