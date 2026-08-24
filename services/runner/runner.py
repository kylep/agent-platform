import asyncio, base64, json, os, re, shutil, stat, subprocess, sys, tempfile, uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from aiokafka import AIOKafkaProducer

# Tools that can read the pod's mounted Claude token (/secrets/claude/token),
# read other secrets, or run arbitrary code. These are the tools that could
# turn any agent into a token-exfil vector, so they are **self-edit-only**.
_SENSITIVE_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit"]

def _permission_args(self_edit: bool, has_api_token: bool, agent: str) -> list[str]:
    """The claude permission flags for a run. Self-edit auto-accepts edits; every
    other agent — trusted or not — gets ONLY its declared tools unattended
    (`--allowedTools`), and the sensitive/token-reading tools are ALWAYS denied
    (`--disallowedTools`), even if the manifest declares them.

    Denying them unconditionally (rather than only when undeclared) is what
    hard-enforces the trifecta break: the shared Claude token is mounted in
    every runner pod, so if a manifest — mis-configured, or altered by a
    prompt-injected self-edit — could grant Bash/Read to a web-facing agent,
    that agent (untrusted input + open egress) could read and exfiltrate the
    token. Making Bash/Read/Edit/Write self-edit-only removes that path by
    construction, no matter what the tool list says. No blanket
    `bypassPermissions` anywhere."""
    if self_edit:
        # Headless runs can't approve tool use interactively; auto-accept file
        # edits so the agent can actually modify the clone. Safe because the
        # work is an ephemeral sandbox and every change lands as a reviewable PR.
        return ["--permission-mode", "acceptEdits"]
    tools = [t for t in _agent_tools(agent) if t not in _SENSITIVE_TOOLS]
    out: list[str] = []
    if tools:
        out += ["--allowedTools", *tools]
    out += ["--disallowedTools", *_SENSITIVE_TOOLS]
    return out


def _identity_token() -> str:
    """This run's platform identity: a dispatcher-minted API key (AP_API_TOKEN,
    system/can_invoke agents) or a kubelet-projected, audience-bound
    ServiceAccount token (AP_API_TOKEN_FILE — design/13: identity, not
    secrets; auto-rotated, useless off-cluster, never minted or stored)."""
    tok = os.environ.get("AP_API_TOKEN", "")
    if tok:
        return tok
    path = os.environ.get("AP_API_TOKEN_FILE", "")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return ""
    return ""


def _write_mcp_config() -> str:
    """Write a claude --mcp-config pointing at the platform MCP broker (an HTTP
    service), carrying this run's identity as the auth header the broker
    forwards/verifies. Returns the config path, or "" if no broker URL is
    configured."""
    url = os.environ.get("AP_MCP_URL")
    if not url:
        return ""
    headers = {"Authorization": f"Bearer {_identity_token()}"}
    if os.environ.get("AP_RUN_TOKEN"):
        # Sender-constrained run JWT (design/13 C) — the API requires it to
        # match the workload identity above.
        headers["X-AP-Run-Token"] = os.environ["AP_RUN_TOKEN"]
    cfg = {"mcpServers": {"platform": {
        "type": "http", "url": url, "headers": headers}}}
    fd, path = tempfile.mkstemp(prefix="mcp-", suffix=".json")
    os.write(fd, json.dumps(cfg).encode())
    os.close(fd)
    return path


def _agent_path(agent: str) -> Path:
    # `claude --agent <name>` resolves agents from ~/.claude/agents/.
    return Path.home() / ".claude" / "agents" / f"{agent}.md"


def _agent_tools(agent: str) -> list[str]:
    """The tools the INSTALLED definition declares (its `tools:` line), or [].

    Reads what `_install_agent` just wrote, not the source it came from, so the
    permission flags describe the definition `claude` is actually about to run
    — identical whether it arrived from the API or the /agents mount."""
    try:
        text = _agent_path(agent).read_text()
    except OSError:
        return []
    if not text.startswith("---"):
        return []
    parts = text.split("---", 2)
    if len(parts) != 3:
        return []
    for line in parts[1].splitlines():
        if re.match(r"\s*tools:", line, re.I):
            return [t.strip() for t in re.split(r"[,\s]+", line.split(":", 1)[1]) if t.strip()]
    return []

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
    """Returns extra env for the claude subprocess. Preferred: token brokering
    (docs/design/09) — the real subscription token lives only in the
    claude-proxy pod, which swaps in the Authorization header; this pod holds
    no credential. The CLI refuses to start with no token at all ("Not logged
    in", spiked 2026-07-30), so it gets a placeholder — any non-empty value
    keeps it in subscription-OAuth mode, and the proxy discards it.
    Legacy fallbacks: a long-lived `claude setup-token` mounted under the
    secret's `token` key, then a session credentials.json snapshot."""
    proxy_url = os.environ.get("AP_CLAUDE_PROXY_URL")
    if proxy_url:
        return {"ANTHROPIC_BASE_URL": proxy_url,
                "CLAUDE_CODE_OAUTH_TOKEN": "placeholder-token-lives-in-claude-proxy"}
    secrets = Path(os.environ.get("AP_SECRETS_DIR", "/secrets/claude"))
    token_file = secrets / "token"
    if token_file.is_file():
        return {"CLAUDE_CODE_OAUTH_TOKEN": token_file.read_text().strip()}
    src = secrets / "credentials.json"
    dst = Path.home() / ".claude" / ".credentials.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # copy: never write back to the mount
    return {}

# --- the platform API, as this run --------------------------------------
# AP_SESSION_TOKEN is a per-run key that reaches exactly two run-scoped
# endpoints: this run's agent definition (docs/design/15) and, for conversation
# turns, its session blob (docs/design/14). It authorizes nothing else.

def _api_req(method: str, path: str, body: dict | None = None) -> dict:
    url = os.environ["AP_API_URL"].rstrip("/") + path
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": "Bearer " + os.environ["AP_SESSION_TOKEN"],
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _agentdef() -> dict | None:
    """This run's agent definition from the platform, or None when the pod has
    no session token/API URL or the fetch fails — the caller then falls back to
    the mount."""
    if not (os.environ.get("AP_SESSION_TOKEN") and os.environ.get("AP_API_URL")):
        return None
    try:
        return _api_req("GET", f"/api/runs/{os.environ['AP_RUN_ID']}/agentdef")
    except Exception as e:
        print(f"agentdef fetch failed, falling back to the mount: {e}", flush=True)
        return None


def _render_agent_md(d: dict) -> str:
    """A fetched definition as the file `claude --agent` reads: frontmatter
    naming the agent and its granted tools, then the prompt as the body.

    The `tools:` line is deliberately the SAME shape the git-synced agent.md
    carried, because `_agent_tools` parses it back out for --allowedTools — the
    delivery channel changed, the contract didn't. With nothing granted there is
    no line at all, which reads back as [] and pre-approves nothing; the
    sensitive set stays denied either way."""
    tools = [*(d.get("harness_tools") or []), *(d.get("platform_tools") or [])]
    front = [f"name: {d['name']}"]
    if tools:
        front.append("tools: " + ", ".join(tools))
    return "---\n" + "\n".join(front) + "\n---\n\n" + (d.get("prompt") or "")


def _install_agent(agent: str) -> None:
    """Put this run's definition where `claude --agent <name>` finds it.

    Preferred (docs/design/15): fetch it from the platform — definitions are
    rows, and the pod gets exactly the one it is running, as of launch.
    Fallback: copy the git-synced /agents tree, kept for one release so a pod
    launched by an older dispatcher (no session token) or one whose API call
    fails still runs."""
    dst = _agent_path(agent)
    dst.parent.mkdir(parents=True, exist_ok=True)
    definition = _agentdef()
    if definition is not None:
        dst.write_text(_render_agent_md(definition))
        return
    src = Path(os.environ.get("AP_AGENTS_DIR", "/agents/agents")) / agent / "agent.md"
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

_BLOCK_KINDS = {"agents": "agent", "skills": "skill", "secrets": "secret",
                "reports": "report", "tools": "tool"}


def _target_block(status: str) -> tuple[str, str] | None:
    """(kind, name) of the building block the change targets, from the changed
    paths. A change spanning kinds (a new skill + the secret it declares)
    belongs to the highest-precedence kind — agent > skill > secret > report —
    NOT the first path in the status, which git sorts alphabetically (secrets/
    would beat skills/)."""
    found: dict[str, str] = {}
    for line in status.splitlines():
        parts = line[3:].strip().split("/")
        if len(parts) >= 2 and parts[0] in _BLOCK_KINDS and parts[1]:
            found.setdefault(_BLOCK_KINDS[parts[0]], parts[1])
    for kind in ("agent", "skill", "tool", "secret", "report"):
        if kind in found:
            return kind, found[kind]
    return None

def self_edit_publish(repo_dir: Path, env: dict, run_id: str, agent: str, prompt: str) -> dict:
    """Commit the agent's edits to the target block's deterministic branch
    (coder/agent-<name>, coder/skill-<name>, coder/tool-<name>, …), force-push,
    and open (or update) its PR. Freeform edits always go through a PR; one
    open PR per block. Edits outside the blocks fall back to the running
    agent's own branch."""
    def git(*a):
        return subprocess.run(["git", "-C", str(repo_dir), *a],
                              check=True, env=env, capture_output=True, text=True).stdout
    # -uall lists untracked FILES; the default collapses a brand-new directory
    # to `?? skills/`, hiding the block name the branch is derived from.
    status = git("status", "--porcelain", "-uall")
    if not status.strip():
        return {"changed": False}
    kind, target = _target_block(status) or ("agent", agent)
    branch = f"coder/{kind}-{target}"
    git("checkout", "-b", branch)
    git("add", "-A")
    git("-c", "user.name=platform-coder", "-c",
        "user.email=platform-coder@agent-platform.local", "commit", "-m",
        f"platform-coder: {_title(prompt)}")
    git("push", "origin", f"+HEAD:{branch}")   # force → overwrite the per-agent branch
    return {"changed": True, "branch": branch, "target": target,
            "pr": _open_or_find_pr(branch, run_id, prompt)}

# -------------------------------------------------------------------------

# --- conversation session resume (docs/design/14) --------------------------
# A conversation turn restores the Claude CLI session blob from the platform,
# resumes it (full fidelity + prompt-cache hits), and uploads the updated blob.
# Everything degrades to the flattened text-replay prompt (AP_PROMPT) on any
# failure, so a corrupt or version-incompatible session never kills a turn.
# (`_api_req` — the run-scoped platform call — lives up with the definition
# fetch that also uses it.)

def _project_dir(cwd: str) -> Path:
    # Mirror the CLI's project slug (non-alphanumerics -> '-') so the session
    # file lands exactly where `claude --resume` looks for it.
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)

def _restore_session(cwd: str) -> str | None:
    """Fetch this conversation's session blob and place it for --resume.
    Returns the session id, or None -> the caller uses the text-replay fallback."""
    if not (os.environ.get("AP_SESSION_TOKEN") and os.environ.get("AP_API_URL")):
        return None
    try:
        data = _api_req("GET", f"/api/runs/{os.environ['AP_RUN_ID']}/session")
    except Exception as e:
        print(f"session restore failed, falling back: {e}", flush=True)
        return None
    sid, blob = data.get("session_id"), data.get("blob_b64")
    if not sid or not blob:
        return None
    d = _project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_bytes(base64.b64decode(blob))
    return sid

def _upload_session(cwd: str, run_id: str, session_id: str) -> None:
    p = _project_dir(cwd) / f"{session_id}.jsonl"
    if not p.exists():
        return
    _api_req("PUT", f"/api/runs/{run_id}/session",
             {"session_id": session_id,
              "blob_b64": base64.b64encode(p.read_bytes()).decode()})


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
    common = ["--output-format", "stream-json", "--verbose"]
    if os.environ.get("AP_MODEL"):
        common += ["--model", os.environ["AP_MODEL"]]
    common += _permission_args(self_edit, bool(os.environ.get("AP_API_TOKEN")), agent)
    # Broker the platform API as MCP tools (mcp__platform__*) for token-bearing
    # agents, so they can read/annotate runs, check health, use memory and post
    # notifications WITHOUT a shell. The agent opts in by declaring the tools.
    if _identity_token():
        mcp_cfg = _write_mcp_config()
        if mcp_cfg:
            common += ["--mcp-config", mcp_cfg]
            # Load the broker's MCP tools UPFRONT into the model's context.
            # Default tool-search defers them behind a search tool, so an agent
            # that calls a tool by name (e.g. run-summarizer → runs_read) never
            # sees it and emits the call as plain text. Upfront loading fixes it.
            os.environ["ENABLE_TOOL_SEARCH"] = "false"

    # Conversation session resume (docs/design/14): with a restorable session
    # the prompt is JUST the new user message (prior turns live in the resumed
    # session); otherwise fall back to the flattened text-replay prompt.
    claude_cwd = cwd or os.getcwd()
    user_message = os.environ.get("AP_USER_MESSAGE", "")
    resume_sid = _restore_session(claude_cwd) if user_message else None

    def _args(resume: str | None) -> list[str]:
        if resume:
            return [claude, "--agent", agent, "--resume", resume, "-p", user_message, *common]
        return [claude, "--agent", agent, "-p", prompt, *common]

    seq = 0
    final_sid = None

    async def _invoke(args: list[str]) -> int:
        nonlocal seq, final_sid
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd,
            env={**os.environ, **extra_env})
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
            if payload.get("type") == "result" and payload.get("session_id"):
                final_sid = payload["session_id"]
            await producer.publish(TOPIC_TRANSCRIPT, run_id, payload)
        return await asyncio.to_thread(proc.wait)

    rc = await _invoke(_args(resume_sid))
    if rc != 0 and resume_sid:
        # A corrupt or version-incompatible session must not kill the turn:
        # retry once with the replayed-history fallback and a fresh session.
        seq += 1
        await producer.publish(TOPIC_TRANSCRIPT, run_id,
                               {"seq": seq, "type": "session_fallback",
                                "detail": "resume failed; retrying with replayed history"})
        final_sid = None
        rc = await _invoke(_args(None))
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

    # Persist the (possibly new) session so the next turn can resume it. Best
    # effort — an upload failure just means the next turn uses the fallback.
    if rc == 0 and final_sid and os.environ.get("AP_SESSION_TOKEN"):
        try:
            await asyncio.to_thread(_upload_session, claude_cwd, run_id, final_sid)
        except Exception as e:
            print(f"session upload failed (non-fatal): {e}", flush=True)

    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": seq + 1, "type": "lifecycle", "terminal": True, "state": state})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": state,
                            "exit_code": rc, "terminal": True}, type="run.state")
    await producer.stop()
    return rc

if __name__ == "__main__":
    sys.exit(run())
