import asyncio, json, os, shutil, stat, subprocess, sys, tempfile, uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from aiokafka import AIOKafkaProducer

TOPIC_TRANSCRIPT, TOPIC_EVENTS = "run.transcript", "run.events"
SCHEMA_VERSION = 1

def _envelope(type_: str, key: str, data: dict) -> dict:
    # Must match agentplatform.events.Envelope so the recorder can unwrap.
    return {"type": type_, "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).isoformat(), "key": key,
            "source": "runner", "data": data}

class KafkaProducerWrapper:
    # AIOKafkaProducer must be constructed inside a running event loop, so
    # construction is deferred to start() (run() calls us from sync code).
    def __init__(self, bootstrap):
        self._bootstrap = bootstrap
        self._p = None
    async def start(self):
        self._p = AIOKafkaProducer(bootstrap_servers=self._bootstrap,
                                   enable_idempotence=True, acks="all",
                                   compression_type="gzip")
        await self._p.start()
    async def stop(self): await self._p.stop()
    async def publish(self, topic, key, value, type="run.transcript"):
        env = _envelope(type, key, value)
        await self._p.send_and_wait(topic, json.dumps(env).encode(), key=key.encode())

def _install_credentials() -> dict:
    """Returns extra env for the claude subprocess. Preferred: a long-lived
    `claude setup-token` under the secret's `token` key (nothing rotates it).
    Fallback: a session credentials.json snapshot (goes stale fast — the
    laptop's own claude rotates the refresh token; kept for completeness)."""
    secrets = Path(os.environ.get("AP_SECRETS_DIR", "/secrets/claude"))
    token_file = secrets / "token"
    if token_file.is_file():
        return {"CLAUDE_CODE_OAUTH_TOKEN": token_file.read_text().strip()}
    src = secrets / "credentials.json"
    dst = Path.home() / ".claude" / ".credentials.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # copy: never write back to the mount
    return {}

def _install_agent(agent: str) -> None:
    # `claude --agent <name>` resolves agents from ~/.claude/agents/, so the
    # synced definition is copied there under the agent's name.
    src = Path(os.environ.get("AP_AGENTS_DIR", "/agents/agents")) / agent / "agent.md"
    dst = Path.home() / ".claude" / "agents" / f"{agent}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)

def _install_skills() -> None:
    # `claude` resolves skills from ~/.claude/skills/<name>/SKILL.md. Copy each
    # skill named in AP_SKILLS (set by the launcher from the agent's manifest)
    # from the synced skills tree into place. Unknown names are skipped.
    names = [n.strip() for n in os.environ.get("AP_SKILLS", "").split(",") if n.strip()]
    if not names:
        return
    src_root = Path(os.environ.get("AP_SKILLS_DIR", "/agents/skills"))
    dst_root = Path.home() / ".claude" / "skills"
    for name in names:
        src = src_root / name
        if src.is_dir():
            shutil.copytree(src, dst_root / name, dirs_exist_ok=True)

# --- self-edit (coder) support -------------------------------------------

def _git_env() -> dict:
    """Env that feeds the App token to git via GIT_ASKPASS — the token never
    appears in a URL, argv, or subprocess error/log."""
    d = Path(tempfile.mkdtemp())
    askpass = d / "askpass.sh"
    askpass.write_text('#!/bin/sh\nprintf "%s" "$AP_GIT_TOKEN"\n')
    askpass.chmod(stat.S_IRWXU)
    return {**os.environ, "AP_GIT_TOKEN": os.environ["AP_GITHUB_TOKEN"],
            "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0"}

def _clone_url() -> str:
    # username-only https URL; the password (token) comes from GIT_ASKPASS.
    return os.environ["AP_GIT_REMOTE_URL"].replace("https://", "https://x-access-token@", 1)

def _title(prompt: str) -> str:
    first = next((l for l in prompt.strip().splitlines() if l.strip()), "edit")
    return first.strip()[:60]

def self_edit_clone(repo_dir: Path, env: dict) -> None:
    subprocess.run(["git", "clone", "--depth", "1", _clone_url(), str(repo_dir)],
                   check=True, env=env, capture_output=True, text=True)

def _gh(method: str, path: str, body: dict | None = None):
    repo = os.environ["AP_GITHUB_REPO"]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}{path}",
                                 method=method, data=data)
    req.add_header("Authorization", f"Bearer {os.environ['AP_GITHUB_TOKEN']}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def _open_or_find_pr(branch: str, run_id: str, prompt: str) -> dict:
    base = os.environ.get("AP_DEFAULT_BRANCH", "main")
    try:
        d = _gh("POST", "/pulls", {
            "head": branch, "base": base, "title": f"platform-coder: {_title(prompt)}",
            "body": f"Automated edit by platform-coder (run `{run_id}`).\n\nInstruction:\n\n> {prompt}"})
    except urllib.error.HTTPError as e:
        if e.code != 422:  # 422 = a PR already exists for this (force-updated) branch
            raise
        owner = os.environ["AP_GITHUB_REPO"].split("/")[0]
        found = _gh("GET", f"/pulls?state=open&head={owner}:{branch}")
        if not found:
            raise
        d = found[0]
    return {"number": d["number"], "url": d["html_url"]}

def _target_agent(status: str) -> str | None:
    """The edited agent's name, from the first changed agents/<name>/ path."""
    for line in status.splitlines():
        parts = line[3:].strip().split("/")
        if len(parts) >= 2 and parts[0] == "agents":
            return parts[1]
    return None

def self_edit_publish(repo_dir: Path, env: dict, run_id: str, agent: str, prompt: str) -> dict:
    """Commit the agent's edits to the target agent's deterministic branch,
    force-push, and open (or update) its PR. Freeform edits always go through a
    PR; one open PR per agent."""
    def git(*a):
        return subprocess.run(["git", "-C", str(repo_dir), *a],
                              check=True, env=env, capture_output=True, text=True).stdout
    status = git("status", "--porcelain")
    if not status.strip():
        return {"changed": False}
    target = _target_agent(status) or agent
    branch = f"coder/agent-{target}"
    git("checkout", "-b", branch)
    git("add", "-A")
    git("-c", "user.name=platform-coder", "-c",
        "user.email=platform-coder@agent-platform.local", "commit", "-m",
        f"platform-coder: {_title(prompt)}")
    git("push", "origin", f"+HEAD:{branch}")   # force → overwrite the per-agent branch
    return {"changed": True, "branch": branch, "target": target,
            "pr": _open_or_find_pr(branch, run_id, prompt)}

# -------------------------------------------------------------------------

def run(producer=None) -> int:
    run_id, agent = os.environ["AP_RUN_ID"], os.environ["AP_AGENT"]
    prompt = os.environ["AP_PROMPT"]
    producer = producer or KafkaProducerWrapper(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"))
    return asyncio.run(_run(producer, run_id, agent, prompt))

async def _run(producer, run_id: str, agent: str, prompt: str) -> int:
    extra_env = _install_credentials()
    _install_agent(agent)
    _install_skills()
    await producer.start()

    self_edit = os.environ.get("AP_SELF_EDIT") == "1"
    cwd = None
    git_env = None
    if self_edit:
        git_env = _git_env()
        repo_dir = Path("/workspace/repo")
        await asyncio.to_thread(self_edit_clone, repo_dir, git_env)
        cwd = str(repo_dir)

    claude = os.environ.get("CLAUDE_BIN", "claude")
    args = [claude, "--agent", agent, "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if os.environ.get("AP_MODEL"):
        args += ["--model", os.environ["AP_MODEL"]]
    if self_edit:
        # Headless runs can't approve tool use interactively; auto-accept file
        # edits so the agent can actually modify the clone. Safe because the
        # work is an ephemeral sandbox and every change lands as a reviewable
        # PR — nothing reaches the default branch without a human merge.
        args += ["--permission-mode", "acceptEdits"]
    elif os.environ.get("AP_API_TOKEN"):
        # A trusted system agent (API access injected) needs its tools to run
        # unattended; its token is operator-scoped and it runs in a sandbox.
        args += ["--permission-mode", "bypassPermissions"]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd,
        env={**os.environ, **extra_env})
    seq = 0
    while True:
        line = await asyncio.to_thread(proc.stdout.readline)
        if line == "":
            break
        line = line.strip()
        if not line: continue
        seq += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"type": "raw", "text": line}
        payload["seq"] = seq
        await producer.publish(TOPIC_TRANSCRIPT, run_id, payload)
    rc = await asyncio.to_thread(proc.wait)
    state = "succeeded" if rc == 0 else "failed"

    # On a successful self-edit run, open a PR for whatever the agent changed.
    if self_edit and rc == 0:
        try:
            result = await asyncio.to_thread(self_edit_publish, Path(cwd), git_env, run_id, agent, prompt)
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "self_edit", **result})
        except Exception as e:
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "self_edit", "error": str(e)})
            state = "failed"

    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": seq + 1, "type": "lifecycle", "terminal": True, "state": state})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": state,
                            "exit_code": rc, "terminal": True}, type="run.state")
    await producer.stop()
    return rc

if __name__ == "__main__":
    sys.exit(run())
