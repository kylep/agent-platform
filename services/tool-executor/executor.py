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
    provisioner-managed `tool-<name>-db` secret),
  - TOOL_IN_DIR / TOOL_OUT_DIR (docs/design/23): a per-call scratch pair. The
    caller's `files_in` land in the first by name; whatever the tool writes to
    the second comes back as `files` — the way a tool returns something that
    is not text, since stdout stays a capped text channel.

The subprocess never sees this process's environment. Timeout and output cap
are enforced; a non-zero exit becomes a structured error for the model.

Netpol makes this pod the single internet-egress point for agent-driven code;
its clients are the broker and (for `internal` tools) the platform API.
"""
import asyncio
import base64
import json
import logging
import os
import shutil
import signal
import ssl
import tempfile
from pathlib import Path

import httpx
import jsonschema
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tool-executor")

TOOLS_ROOT = Path(os.environ.get("AP_TOOLS_ROOT", "/agents/tools"))
SCRATCH_DIR = Path(os.environ.get("TOOL_SCRATCH_DIR", "/tmp"))
OUTPUT_CAP = 256 * 1024
# The manifest may ask for less; image generation polls and needs the room.
TIMEOUT_CEILING = 300
FILE_CAP = 8 * 1024 * 1024
FILES_IN_MAX = 4
FILES_OUT_MAX = 8
# base64 of one full file, plus padding.
B64_CAP = FILE_CAP * 4 // 3 + 4
# Every files_in slot full, plus room for args and the envelope. Enforced on
# the raw request before any JSON is parsed or base64 decoded.
BODY_CAP = FILES_IN_MAX * B64_CAP + 1024 * 1024
NAME_MAX = 200
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")


class BodyCap:
    """ASGI guard: refuse a request body over BODY_CAP with 413 before the
    app sees it — by Content-Length when the client sends one, else by
    counting the chunks as they arrive. The body the app then reads is the
    buffered copy, so nothing is parsed twice."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        declared = dict(scope.get("headers") or {}).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > BODY_CAP:
            return await self._reject(send)
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > BODY_CAP:
                return await self._reject(send)
            chunks.append(chunk)
            if not message.get("more_body"):
                message = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return message

        return await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({"detail": f"request body exceeds {BODY_CAP} bytes"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


app = FastAPI(title="tool-executor")
app.add_middleware(BodyCap)


class Caller(BaseModel):
    agent: str = ""
    run_id: str = ""


class FileIn(BaseModel):
    name: str
    mime: str = ""
    b64: str


class RunIn(BaseModel):
    tool: str
    args: dict = {}
    caller: Caller = Caller()
    files_in: list[FileIn] = []


def effective_timeout(manifest: dict) -> int:
    return min(int(manifest.get("timeout_seconds", 30)), TIMEOUT_CEILING)


def sniff_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _safe_name(name: str) -> bool:
    # Names are basenames: the in/ dir is the only place a caller can put a
    # file, and the out/ listing is the only place a tool can name one.
    return (bool(name) and name not in (".", "..") and len(name.encode()) <= NAME_MAX
            and not any(c in name for c in "/\\\0"))


def stage_files_in(in_dir: Path, files: list[FileIn]) -> None:
    if len(files) > FILES_IN_MAX:
        raise HTTPException(400, f"at most {FILES_IN_MAX} files_in")
    for f in files:
        if not _safe_name(f.name):
            raise HTTPException(400, f"files_in name must be a plain filename, got {f.name!r}")
        if len(f.b64) > B64_CAP:
            raise HTTPException(400, f"files_in {f.name!r} exceeds {FILE_CAP} bytes")
        try:
            data = base64.b64decode(f.b64, validate=True)
        except (ValueError, TypeError):
            raise HTTPException(400, f"files_in {f.name!r}: b64 is not valid base64")
        if len(data) > FILE_CAP:
            raise HTTPException(400, f"files_in {f.name!r} exceeds {FILE_CAP} bytes")
        (in_dir / f.name).write_bytes(data)


def _read_capped(p: Path) -> bytes | None:
    """The file's bytes, or None if it is over FILE_CAP. Bounded by the read
    itself, not a preceding stat: the tool's process group is dead by now, but
    a size that is only ever checked before the read is a habit worth not
    having."""
    with p.open("rb") as fh:
        data = fh.read(FILE_CAP + 1)
    return None if len(data) > FILE_CAP else data


def collect_files_out(out_dir: Path) -> tuple[list[dict], list[str]]:
    """Everything the tool left in out/, with a `<name>.meta.json` sidecar
    folded into its file's `meta`. Over-cap files are skipped with a warning
    rather than failing the call — the text result is still worth returning.
    Symlinks are never followed, in either role: out/ is the tool's to fill,
    not its window onto the rest of the filesystem."""
    files: list[dict] = []
    warnings: list[str] = []
    entries = sorted(out_dir.iterdir())
    sidecars = {p.name[:-len(".meta.json")]: p for p in entries
                if p.name.endswith(".meta.json")}
    sidecar_names = {p.name for p in sidecars.values()}
    for p in entries:
        if p.name in sidecar_names:
            continue
        if p.is_symlink() or not p.is_file():
            warnings.append(f"out/{p.name} skipped: not a regular file")
            continue
        if len(files) >= FILES_OUT_MAX:
            warnings.append(f"out/{p.name} skipped: more than {FILES_OUT_MAX} output files")
            continue
        data = _read_capped(p)
        if data is None:
            warnings.append(f"out/{p.name} skipped: exceeds {FILE_CAP} bytes")
            continue
        meta: dict = {}
        sidecar = sidecars.pop(p.name, None)
        if sidecar is not None:
            meta, warning = _read_sidecar(sidecar)
            if warning:
                warnings.append(warning)
        files.append({"name": p.name, "mime": sniff_mime(data[:16]),
                      "b64": base64.b64encode(data).decode(), "meta": meta})
    for orphan in sidecars.values():
        warnings.append(f"out/{orphan.name} ignored: no matching file")
    return files, warnings


def _read_sidecar(sidecar: Path) -> tuple[dict, str | None]:
    if sidecar.is_symlink() or not sidecar.is_file():
        return {}, f"out/{sidecar.name} ignored: not a regular file"
    raw = _read_capped(sidecar)
    if raw is None:
        return {}, f"out/{sidecar.name} ignored: exceeds {FILE_CAP} bytes"
    try:
        parsed = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return {}, f"out/{sidecar.name} ignored: invalid JSON"
    if not isinstance(parsed, dict):
        return {}, f"out/{sidecar.name} ignored: not a JSON object"
    return parsed, None


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


async def build_env(manifest: dict, caller: Caller, in_dir: Path, out_dir: Path) -> dict[str, str]:
    infra = manifest.get("infra") or {}
    env = {
        # Minimal, explicit base — never os.environ.
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "TOOL_NAME": manifest["name"],
        "TOOL_CALLER_AGENT": caller.agent,
        "TOOL_RUN_ID": caller.run_id,
        "TOOL_IN_DIR": str(in_dir),
        "TOOL_OUT_DIR": str(out_dir),
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


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # The group is not ours to signal (a setsid-less platform); the direct
        # child is the best that can be done.
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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

    scratch = Path(tempfile.mkdtemp(prefix="tool-", dir=SCRATCH_DIR))
    try:
        in_dir, out_dir = scratch / "in", scratch / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        stage_files_in(in_dir, body.files_in)
        env = await build_env(manifest, body.caller, in_dir, out_dir)
        timeout = effective_timeout(manifest)
        # Its own session, so the whole process group — anything run.py forks
        # included — can be killed as one. A surviving grandchild would keep
        # writing into out/ (and keep stdout open, which stalls proc.wait()).
        proc = await asyncio.create_subprocess_exec(
            "python3", str(TOOLS_ROOT / body.tool / "run.py"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(TOOLS_ROOT / body.tool),
            start_new_session=True,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(json.dumps(body.args).encode()), timeout=timeout)
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return {"ok": False, "error": f"tool timed out after {timeout}s"}
        # run.py exited; nothing it left behind gets to touch out/ after this.
        _kill_group(proc)

        if proc.returncode != 0:
            detail = (err or out or b"").decode(errors="replace")[-2000:]
            log.warning("tool %s exited %s: %s", body.tool, proc.returncode, detail[:500])
            return {"ok": False, "error": f"tool exited {proc.returncode}: {detail}"}
        text = out.decode(errors="replace")
        if len(text) > OUTPUT_CAP:
            text = text[:OUTPUT_CAP] + f"\n…[truncated at {OUTPUT_CAP} bytes]"
        files, warnings = collect_files_out(out_dir)
        return {"ok": True, "output": text, "files": files, "warnings": warnings}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    # design/13 B: with SPIRE mTLS on, bind localhost behind the ghostunnel
    # server sidecar (8443, broker-SVID clients only).
    uvicorn.run(app, host=os.environ.get("AP_BIND_HOST", "0.0.0.0"), port=8000)
