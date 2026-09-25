import asyncio, base64, hashlib, json, mimetypes, os, re, shutil, subprocess, sys, tempfile, time, uuid
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from aiokafka import AIOKafkaProducer

# Tools that can read the pod's mounted Claude token (/secrets/claude/token),
# read other secrets, or run arbitrary code. These are the tools that could
# turn any standard agent into a token-exfil vector, so they are available
# only inside the credential-free Workbench profile.
_SENSITIVE_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit"]

# What a dev run's agent file enables besides its grants — the same fixed list
# `_permission_args`' dev case pre-approves. The CLI reads the file's `tools:`
# as the ENABLED set; a flag cannot enable what the file left out.
_DEV_SHELL_TOOLS = ["Bash", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep"]

# The Playwright MCP server (docs/design/25). `PlaywrightMCP` is the GRANT an
# admin puts on a `role: dev` row (agentspec.CLAUDE_TOOLS); what the CLI
# knows is the server's tools, `mcp__playwright__*`. The grant name is never
# written into a `tools:` line or an allow-list — the dev render turns it into
# the pattern, and every other run drops it with the sensitive set: the
# server is only ever started for a dev run, so elsewhere it enables nothing.
PLAYWRIGHT_GRANT = "PlaywrightMCP"
PLAYWRIGHT_TOOLS = "mcp__playwright__*"
_DEV_ONLY_TOOLS = [PLAYWRIGHT_GRANT, PLAYWRIGHT_TOOLS]
# The package's own bin, installed globally by Dockerfile.dev at the pinned
# version (test_runner pins the pair). The bin, not `npx <spec>`: npx resolves
# a spec against the registry, not the install, and the pod has no egress.
PLAYWRIGHT_MCP_BIN = "playwright-mcp"
CHROMIUM = "/opt/chromium/chrome"
NO_WEB_LOGIN = "no web login — browser tools off"
# One DNS name: what a Chromium host-resolver rule can EXCLUDE. The rule
# separators (`,` and ` `), a `;`, or an empty name would open a hole in
# the map, so a web URL whose host is anything else gets no browser.
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_LOOPBACK = ("localhost", "127.0.0.1")

def _permission_args(agent: str, dev: bool = False) -> list[str]:
    """The claude permission flags for a run. Every standard agent — trusted
    or not — gets ONLY its declared tools unattended
    (`--allowedTools`), and the sensitive/token-reading tools are ALWAYS denied
    (`--disallowedTools`), even if the manifest declares them.

    Denying them unconditionally (rather than only when undeclared) is what
    hard-enforces the trifecta break: the shared Claude token is mounted in
    every runner pod, so if a manifest — mis-configured, or altered by a
    prompt-injected editor — could grant Bash/Read to a web-facing agent,
    that agent (untrusted input + open egress) could read and exfiltrate the
    token. Making Bash/Read/Edit/Write Workbench-only removes that path by
    construction, no matter what the tool list says. No blanket
    `bypassPermissions` anywhere."""
    if dev:
        # A dev run (docs/design/24) is the other shell-capable profile, and
        # the reason it is safe is the POD, not the flags: no App token, no
        # AP_GITHUB_TOKEN, the Claude token behind the proxy, and the only way
        # out is a bundle the API re-derives and polices. So the file and shell
        # tools are allowed outright, the declared grants ride along, and
        # `--strict-mcp-config` keeps every MCP server but the runner's own out.
        # It sits BEFORE the variadic list so nothing can read it as a tool.
        installed = _agent_tools(agent)
        declared = [t for t in installed if t not in _DEV_SHELL_TOOLS + _DEV_ONLY_TOOLS]
        out = ["--permission-mode", "acceptEdits", "--strict-mcp-config",
               "--allowedTools", *_DEV_SHELL_TOOLS, *declared, "mcp__platform__*"]
        if PLAYWRIGHT_TOOLS in installed:
            # The browser's tools, pre-approved as a pattern like the broker's:
            # the render put the pattern in the file for the grant (and only
            # then), so this is the grant, read back the way every flag is.
            out.append(PLAYWRIGHT_TOOLS)
        return out
    tools = [t for t in _agent_tools(agent) if t not in _SENSITIVE_TOOLS + _DEV_ONLY_TOOLS]
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


def _web_host(url: str) -> str | None:
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return None
    return host if _HOST_RE.match(host) else None


def _browser_config(web_url: str) -> dict:
    """The MCP server's `--config` file: the boundary that holds. The server's
    `--allowed-origins` is documented as NOT a security boundary (redirects
    and in-page navigation ignore it), so Chromium itself is told to resolve
    the platform's web host and map every other name to NOTFOUND — a page
    that redirects the browser off the platform gets a DNS failure, not a
    request. A loopback web URL (a laptop) excludes both spellings, and only
    then. `--no-sandbox` rides in the same list so the sandbox setting does
    not depend on how the CLI merges its own flag with the file's args."""
    host = _web_host(web_url)
    exclude = list(_LOOPBACK) if host in _LOOPBACK else [host]
    rules = "MAP * ~NOTFOUND, " + ", ".join(f"EXCLUDE {h}" for h in exclude)
    return {"browser": {"launchOptions": {"args": [
        f"--host-resolver-rules={rules}", "--no-sandbox"]}}}


def _playwright_server(state: Path) -> dict:
    """The Playwright MCP server entry (docs/design/25), a stdio process the
    CLI spawns — never the model. Every argument is the runner's: the image's
    global bin, the image's Chromium (the package bundles a different
    playwright-core than the image's browsers, hence the explicit path), the
    storage state `ap-web-login` wrote, the platform's own origin as the
    advisory allow-list, and the config beside the state that makes the
    browser unable to resolve anything else. Nothing from the definition or
    the prompt is in this list, which is what makes it safe to pre-approve
    `mcp__playwright__*` wholesale."""
    return {"type": "stdio", "command": PLAYWRIGHT_MCP_BIN, "args": [
        "--headless", "--isolated", "--no-sandbox",
        "--executable-path", CHROMIUM,
        "--storage-state", str(state),
        "--allowed-origins", os.environ["AP_WEB_URL"],
        "--image-responses", "allow",
        "--output-dir", str(state.parent / "mcp"),
        "--viewport-size", "1280x800",
        "--config", str(state.parent / "mcp.json")]}


def _write_mcp_config(playwright_state: Path | None = None) -> str:
    """Write a claude --mcp-config pointing at the platform MCP broker (an HTTP
    service), carrying this run's identity as the auth header the broker
    forwards/verifies. Returns the config path, or "" if no broker URL is
    configured.

    `playwright_state` is the login state file of a dev run that holds the
    `PlaywrightMCP` grant: when it EXISTS (and the pod knows the web URL to
    lock the browser to) the config carries the second server. No cookie, no
    browser — the caller frames that."""
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
    web_url = os.environ.get("AP_WEB_URL", "")
    if playwright_state is not None and playwright_state.is_file() and _web_host(web_url):
        (playwright_state.parent / "mcp.json").write_text(json.dumps(_browser_config(web_url)))
        cfg["mcpServers"]["playwright"] = _playwright_server(playwright_state)
    fd, path = tempfile.mkstemp(prefix="mcp-", suffix=".json")
    os.write(fd, json.dumps(cfg).encode())
    os.close(fd)
    return path


def _agent_path(agent: str) -> Path:
    # `claude --agent <name>` resolves agents from ~/.claude/agents/.
    return Path.home() / ".claude" / "agents" / f"{agent}.md"


def _agent_tools(agent: str) -> list[str]:
    """The tools the INSTALLED definition declares (its `tools:` line), or [].

    Reads what `_install_agent` just wrote, not the payload it came from, so
    the permission flags describe the definition `claude` is actually about to
    run."""
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


def _install_codex_auth(run_id: str) -> tuple[Path, str]:
    """Install native, refreshable Codex OAuth state for this run."""
    data = _api_req("GET", f"/api/runs/{run_id}/codex-auth")
    raw = data["auth_json"]
    if not isinstance(json.loads(raw), dict):
        raise ValueError("codex auth.json is not a JSON object")
    home = Path.home() / ".codex"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    auth = home / "auth.json"
    auth.write_text(raw)
    auth.chmod(0o600)
    return auth, data.get("sha256", "")


def _upload_codex_auth(run_id: str, auth: Path, original_hash: str) -> None:
    try:
        raw = auth.read_text()
        if original_hash and hashlib.sha256(raw.encode()).hexdigest() == original_hash:
            return
        _api_req("PUT", f"/api/runs/{run_id}/codex-auth",
                 {"auth_json": raw, "sha256": original_hash})
    except Exception as e:
        # The run result is still useful. A concurrent run may have won the
        # optimistic update, in which case its newer credential remains stored.
        print(f"codex credential refresh upload skipped: {e}", flush=True)


def _write_codex_config(dev: bool, instructions: str = "",
                        playwright_state: Path | None = None) -> Path:
    """Write the noninteractive policy and broker config used by Codex."""
    home = Path.home() / ".codex"
    home.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("AP_CODEX_PROXY_URL", "").rstrip("/")
    lines = ['approval_policy = "never"',
             f'developer_instructions = {json.dumps(instructions)}']
    if proxy:
        # Kubernetes is the execution sandbox. The first-party provider keeps
        # its hosted tools, while the broker discards these placeholders and
        # injects OAuth.
        # A file-shaped ChatGPT login makes Codex register its hosted tools;
        # it contains no usable secret and is scoped to this disposable pod.
        def fake_jwt(claims: dict) -> str:
            def part(value: dict) -> str:
                return base64.urlsafe_b64encode(
                    json.dumps(value, separators=(",", ":")).encode()
                ).decode().rstrip("=")
            return f"{part({'alg': 'none', 'typ': 'JWT'})}.{part(claims)}.placeholder"

        now = int(time.time())
        claims = {"exp": now + 86400, "sub": "agent-platform-runner",
                  "email": "runner@agent-platform.invalid",
                  "https://api.openai.com/auth": {
                      "chatgpt_account_id": "agent-platform-placeholder",
                      "chatgpt_plan_type": "plus"}}
        (home / "auth.json").write_text(json.dumps({
            "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
            "tokens": {"id_token": fake_jwt(claims),
                       "access_token": fake_jwt(claims),
                       "refresh_token": "agent-platform-placeholder",
                       "account_id": "agent-platform-placeholder"},
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        }, separators=(",", ":")))
        (home / "auth.json").chmod(0o600)
        lines += ['sandbox_mode = "danger-full-access"',
                  # Keep the first-party provider identity so Codex exposes
                  # hosted subscription tools; only its network base moves.
                  'model_provider = "openai"',
                  f'openai_base_url = {json.dumps(proxy)}',
                  f'chatgpt_base_url = {json.dumps(proxy + "/backend-api/")}',
                  'cli_auth_credentials_store = "file"']
    else:
        # Legacy direct-auth mode. This retains Codex's own filesystem sandbox
        # because auth.json is present in the runner's home.
        lines += ['cli_auth_credentials_store = "file"',
                  'default_permissions = "agent-platform"', '',
                  '[permissions.agent-platform]',
                  '[permissions.agent-platform.filesystem]',
                  '":root" = "deny"', '":minimal" = "read"',
                  f'{json.dumps(str(home))} = "deny"',
                  f'{json.dumps(str(Path.home() / ".agents" / "skills"))} = "read"',
                  '', '[permissions.agent-platform.filesystem.":workspace_roots"]',
                  f'"." = {json.dumps("write" if dev else "read")}']
    lines += ['', '[shell_environment_policy]', 'inherit = "core"', '',
              '[shell_environment_policy.filters]', '"AP_*" = "exclude"',
              '"*TOKEN*" = "exclude"', '"*SECRET*" = "exclude"',
              '"*KEY*" = "exclude"']
    url = os.environ.get("AP_MCP_URL", "")
    if url and _identity_token():
        os.environ["AP_CODEX_MCP_BEARER"] = _identity_token()
        lines += ['', '[mcp_servers.platform]', f'url = {json.dumps(url)}',
                  'bearer_token_env_var = "AP_CODEX_MCP_BEARER"']
        if os.environ.get("AP_RUN_TOKEN"):
            lines.append('http_headers = { "X-AP-Run-Token" = '
                         + json.dumps(os.environ["AP_RUN_TOKEN"]) + ' }')
    web_url = os.environ.get("AP_WEB_URL", "")
    if (playwright_state is not None and playwright_state.is_file()
            and _web_host(web_url)):
        (playwright_state.parent / "mcp.json").write_text(
            json.dumps(_browser_config(web_url)))
        server = _playwright_server(playwright_state)
        lines += ['', '[mcp_servers.playwright]',
                  f'command = {json.dumps(server["command"])}',
                  f'args = {json.dumps(server["args"])}']
    path = home / "config.toml"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path

# --- the platform API, as this run --------------------------------------
# AP_SESSION_TOKEN is a per-run key that reaches only this run's run-scoped
# endpoints: its agent definition (docs/design/15), its session blob for
# conversation turns (docs/design/14), and the Workbench's `workbench` and
# `publish` routes (docs/design/24). It authorizes nothing else — and a publish
# needs the nonce on top, which this process holds and the model never sees.

def _api_req(method: str, path: str, body: dict | None = None,
             headers: dict | None = None) -> dict:
    url = os.environ["AP_API_URL"].rstrip("/") + path
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": "Bearer " + os.environ["AP_SESSION_TOKEN"],
                 "Content-Type": "application/json", **(headers or {})},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


class AgentUnavailable(RuntimeError):
    """The platform produced no definition, so there is nothing to run.

    Its own type because it is the one startup failure that must be REPORTED
    rather than merely raised: an uncaught traceback here kills the pod before
    the Kafka producer exists, and the run lands with an empty error — which is
    exactly what a Claude quota exhaustion looks like."""


def _agentdef() -> tuple[dict | None, str]:
    """This run's agent definition from the platform, plus the reason there
    isn't one: (definition, "") on success, (None, why) when the pod has no
    session token/API URL, the fetch fails, or the body isn't a definition.

    The reason is returned rather than only logged because the caller needs it:
    it is the whole error message the run reports, and a stdout line in a dead
    pod is not an error message.

    The shape check is not decoration: a 200 carrying something else (a proxy's
    error page, a truncated body) would otherwise reach the renderer and kill
    the run on a missing key — an uncaught traceback instead of a reported
    failure."""
    if not (os.environ.get("AP_SESSION_TOKEN") and os.environ.get("AP_API_URL")):
        return None, "not attempted (no AP_SESSION_TOKEN/AP_API_URL)"
    try:
        d = _api_req("GET", f"/api/runs/{os.environ['AP_RUN_ID']}/agentdef")
    except Exception as e:
        print(f"agentdef fetch failed: {e}", flush=True)
        return None, str(e) or repr(e)
    if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"]:
        print("agentdef response is not a definition", flush=True)
        return None, "the response was not a definition"
    return d, ""


def _agent_description(d: dict) -> str:
    """The frontmatter `description:` value for a fetched definition — always a
    non-empty ONE-LINE string.

    `name` and `description` are the only frontmatter fields the CLI requires,
    and a file with a name but no description is SKIPPED: the agent then shows
    up as "not found ... Available agents: <built-ins>", which is exactly how
    this was found in production.

    So the value is never allowed to be missing OR blank. A blank one is
    treated as absent here on purpose: the docs only promise that a present,
    populated description loads the file, and `description:` with nothing after
    it is YAML null — indistinguishable from the field being gone. An agent row
    whose description column is the schema default "" would otherwise fail the
    same way the missing field did. The fallback names the agent, which is all
    the description can usefully say when the row says nothing.

    Multi-line descriptions collapse to their first non-empty line: the whole
    value goes into a single frontmatter line, and a raw newline there would
    end the scalar and corrupt the block. `json.dumps` supplies the quoting —
    a JSON string is a valid YAML double-quoted scalar, so colons, quotes and
    backslashes in the text stay inside the value."""
    text = d.get("description") or ""
    first = next((ln.strip() for ln in str(text).splitlines() if ln.strip()), "")
    return json.dumps(first or f"The {d['name']} agent.")


def _render_agent_md(d: dict, dev: bool = False) -> str:
    """A fetched definition as the file `claude --agent` reads: frontmatter
    naming and describing the agent plus its granted tools, then the prompt as
    the body. Name and description are the CLI's required fields — see
    `_agent_description` for why the description is never left off.

    The `tools:` line is deliberately the SAME shape the git-synced agent.md
    carried, because `_agent_tools` parses it back out for --allowedTools — the
    delivery channel changed, the contract didn't. With nothing granted there is
    no line at all, which reads back as [] and pre-approves nothing; the
    sensitive set stays denied either way.

    A dev run is the exception: the CLI treats `tools:` as the enabled SET, and
    `--allowedTools` only pre-approves within it, so a file listing just the
    grants leaves Bash "not enabled in this context" whatever the flags say.
    There the line leads with `_DEV_SHELL_TOOLS`, then the grants that are not
    already in it — the parsed-back list still filters to the same flags.

    Only bare tool names get written. The API validates grants against the
    registries, so this is defense in depth — but it is the layer that matters
    for a permission SPECIFIER like `Bash(cat /secrets/...)`, which
    `_permission_args` strips the sensitive set by EXACT match and would
    therefore wave through into --allowedTools."""
    tools = [t for t in (*(d.get("harness_tools") or []),
                         *(d.get("platform_tools") or []))
             if isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9_]+", t)]
    # The Playwright grant is not a tool name the CLI knows. A dev file
    # enables the server's tools as the pattern instead, last; any other
    # file leaves it off, since no server will be there to enable.
    playwright = PLAYWRIGHT_GRANT in tools
    tools = [t for t in tools if t != PLAYWRIGHT_GRANT]
    if dev:
        tools = [*_DEV_SHELL_TOOLS, *(t for t in tools if t not in _DEV_SHELL_TOOLS)]
        if playwright:
            tools.append(PLAYWRIGHT_TOOLS)
    front = [f"name: {d['name']}", f"description: {_agent_description(d)}"]
    if tools:
        front.append("tools: " + ", ".join(tools))
    return "---\n" + "\n".join(front) + "\n---\n\n" + (d.get("prompt") or "")


def _install_agent(agent: str, dev: bool = False) -> dict:
    """Put this run's definition where `claude --agent <name>` finds it.

    One path (docs/design/15): fetch it from the platform — definitions are
    rows, and the pod gets exactly the one it is running, as of launch.

    Raises AgentUnavailable when the fetch doesn't produce one, naming why.
    Running anyway is not an option — `claude --agent <name>` with no such file
    is a different agent's idea of the job — so the only question is whether the
    run dies mutely or explains itself."""
    definition, api_error = _agentdef()
    if definition is None:
        raise AgentUnavailable(f"agent definition unavailable: api={api_error}")
    dst = _agent_path(agent)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_render_agent_md(definition, dev=dev))
    return definition

def _install_skills(runtime: str = "claude") -> None:
    # `claude` resolves skills from ~/.claude/skills/<name>/SKILL.md. Copy each
    # skill named in AP_SKILLS (set by the launcher from the agent's manifest)
    # from the synced skills tree into place. Unknown names are skipped.
    names = [n.strip() for n in os.environ.get("AP_SKILLS", "").split(",") if n.strip()]
    src_root = Path(os.environ.get("AP_SKILLS_DIR", "/agents/skills"))
    dst_root = (Path.home() / ".agents" / "skills" if runtime == "codex"
                else Path.home() / ".claude" / "skills")
    for name in names:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
            continue
        src = src_root / name
        plugin = src_root.parent / "plugins" / "agent-platform-coding"
        md = plugin / "skills" / name / "SKILL.md"
        if src.exists() and md.exists():
            continue  # an ambiguous name must not select either package
        if src.is_symlink():
            continue
        if src.is_dir():
            shutil.copytree(src, dst_root / name, dirs_exist_ok=True)
            continue
        # The only built-in plugin is a reviewed skills-only release. Copy the
        # pinned SKILL.md bytes, never a host manifest, hook, script or setting.
        release = plugin / "release.json"
        if (not md.is_file() or md.is_symlink() or not release.is_file()
                or release.is_symlink()):
            continue
        try:
            manifest = json.loads(release.read_text())
            if manifest.get("name") != "agent-platform-coding":
                continue
            expected = manifest["files"][f"skills/{name}/SKILL.md"]
            data = md.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                continue
        except (KeyError, ValueError, OSError):
            continue
        target = dst_root / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_bytes(data)


def _resume_work_context(prompt: str, user_message: str) -> str:
    """Restate run-scoped context on a resumed CLI turn, where the initial
    prompt is replaced by AP_USER_MESSAGE. Keep it out of stored chat history.
    """
    if not user_message or not prompt.startswith("<work-context>\n"):
        return user_message
    end = prompt.find("\n</work-context>")
    if end < 0:
        return user_message
    return prompt[:end + len("\n</work-context>")] + "\n\n" + user_message

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


def _restore_codex_thread(run_id: str) -> str | None:
    try:
        data = _api_req("GET", f"/api/runs/{run_id}/codex-session")
        return data.get("thread_id") or None
    except Exception as e:
        print(f"codex session restore failed, falling back: {e}", flush=True)
        return None


def _upload_codex_thread(run_id: str, thread_id: str) -> None:
    _api_req("PUT", f"/api/runs/{run_id}/codex-session", {"thread_id": thread_id})


_CODEX_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _codex_generated_files() -> list[Path]:
    """Built-in ImageGen's outputs, in stable creation order.

    A runner pod has a fresh HOME, so every file under this directory belongs
    to this run. Resolve and reject symlinks anyway: the model must not turn
    the trusted uploader into a reader for some other path in the pod.
    """
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "generated_images"
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in _CODEX_IMAGE_SUFFIXES:
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        out.append(path)
    return sorted(out, key=lambda p: (p.stat().st_mtime_ns, str(p)))[:4]


def _upload_codex_generated(run_id: str) -> list[dict]:
    """Move native Codex images across the run-scoped API seam."""
    uploaded = []
    for path in _codex_generated_files():
        data = path.read_bytes()
        if not data:
            continue
        uploaded.append(_api_req(
            "POST", f"/api/runs/{run_id}/generated-images",
            {"name": path.name,
             "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
             "content_b64": base64.b64encode(data).decode()}))
    return uploaded


def run(producer=None) -> int:
    run_id, agent = os.environ["AP_RUN_ID"], os.environ["AP_AGENT"]
    prompt = os.environ["AP_PROMPT"]
    producer = producer or KafkaProducerWrapper(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"))
    return asyncio.run(_run(producer, run_id, agent, prompt))

async def _abort(producer, run_id: str, detail: str) -> int:
    """End a run that cannot start, in words. Publishes the same terminal pair
    a finished run does, so the run is FAILED with a reason (`detail` becomes
    `run.error` in the recorder) and a live tail closes instead of hanging on a
    pod that is already gone."""
    print(detail, file=sys.stderr, flush=True)
    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": 1, "type": "agent_unavailable", "error": detail})
    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": 2, "type": "lifecycle", "terminal": True,
                            "state": "failed"})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": "failed",
                            "exit_code": 1, "terminal": True, "detail": detail},
                           type="run.state")
    await producer.stop()
    return 1


async def _run(producer, run_id: str, agent: str, prompt: str) -> int:
    runtime = os.environ.get("AP_RUNTIME", "claude")
    extra_env = _install_credentials() if runtime == "claude" else {}
    # The producer comes up BEFORE the definition is installed: a pod with no
    # definition has to report that, and it can only report over Kafka.
    await producer.start()
    dev = os.environ.get("AP_WORKSPACE") == "dev"
    try:
        definition = _install_agent(agent, dev=dev)
    except AgentUnavailable as e:
        return await _abort(producer, run_id, str(e))
    runtime = definition.get("runtime", runtime)
    _install_skills(runtime)

    cwd = None
    git_env = None
    seq = 0
    wb = None
    block = ""
    playwright_state = None
    if dev:
        # The dev run (docs/design/24): an anonymous clone the agent works in,
        # published as a bundle after a clean exit. Not being able to prepare
        # it is the same kind of failure as having no definition — nothing to
        # run — and is reported the same way.
        # Imported here, not at the top: the backend seam test loads this file
        # by path with no sibling on sys.path, so sibling imports stay inside
        # the dev branch.
        import workbench
        git_env = workbench.dev_env(os.environ)
        repo_dir = workbench.WORKSPACE / "repo"
        notes: list[dict] = []
        try:
            wb = workbench.fetch_workbench(_api_req, run_id)
            # The publish nonce stays in THIS process's memory, popped out of
            # the dict before anything else sees it: `claude` is spawned with
            # an environment, and the model's shell (and every `npm`/`git` it
            # runs) inherits that environment — but no child process can read
            # its parent's variables. That asymmetry is the whole reason the
            # API trusts a nonce over the session token, which IS in the env.
            publish_nonce = wb.pop("publish_nonce", None)
            block = await asyncio.to_thread(workbench.prepare, repo_dir, wb, git_env, notes)
        except Exception as e:
            return await _abort(producer, run_id, f"workbench prepare failed: {e}")
        # The login is done (or was never possible): the password has no
        # further reader in this pod. `claude`, the MCP server it spawns and
        # finalize's subprocesses all inherit an environment, so it comes out
        # of every one this process still holds — the state file is what the
        # browser signs in with, not the credential.
        os.environ.pop("QA_WEB_PASSWORD", None)
        git_env.pop("QA_WEB_PASSWORD", None)
        if PLAYWRIGHT_TOOLS in _agent_tools(agent):
            playwright_state = workbench.qa_state_path()
            if not playwright_state.is_file():
                notes.append({"step": "web login", "ok": False, "exit": None,
                              "tail": NO_WEB_LOGIN})
        for note in notes:
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "workbench", **note})
        # The block is the platform's voice and goes LAST, after the summons.
        prompt = prompt.rstrip("\n") + "\n\n" + block
        cwd = str(repo_dir)
        if "PATH" in git_env:
            extra_env = {**extra_env, "PATH": git_env["PATH"]}   # the venv leads, for claude too

    user_message = os.environ.get("AP_USER_MESSAGE", "")
    run_cwd = cwd or os.getcwd()
    user_message = _resume_work_context(prompt, user_message)
    if user_message and block:
        user_message = user_message.rstrip("\n") + "\n\n" + block

    final_sid = None
    final_text = ""
    final_error = ""
    codex_auth = None
    codex_auth_hash = ""

    if runtime == "codex":
        try:
            if not os.environ.get("AP_CODEX_PROXY_URL"):
                codex_auth, codex_auth_hash = _install_codex_auth(run_id)
            _write_codex_config(dev, definition.get("prompt") or "",
                                playwright_state)
        except Exception as e:
            return await _abort(producer, run_id, f"codex credential unavailable: {e}")
        resume_sid = _restore_codex_thread(run_id) if user_message else None
        codex = os.environ.get("CODEX_BIN", "codex")
        common = ["--json", "--skip-git-repo-check"]
        if os.environ.get("AP_MODEL"):
            common += ["--model", os.environ["AP_MODEL"]]
        initial = prompt

        def _args(resume: str | None) -> list[str]:
            if resume:
                return [codex, "exec", "resume", *common, resume, user_message]
            return [codex, "exec", *common, initial]
    else:
        claude = os.environ.get("CLAUDE_BIN", "claude")
        common = ["--output-format", "stream-json", "--verbose"]
        if os.environ.get("AP_MODEL"):
            common += ["--model", os.environ["AP_MODEL"]]
        common += _permission_args(agent, dev=dev)
        if dev:
            turns = os.environ.get("AP_MAX_TURNS", "")
            common += ["--max-turns", turns if turns.isdigit() else "200"]
        if _identity_token():
            mcp_cfg = _write_mcp_config(playwright_state)
            if mcp_cfg:
                common += ["--mcp-config", mcp_cfg]
                os.environ["ENABLE_TOOL_SEARCH"] = "false"
        resume_sid = _restore_session(run_cwd) if user_message else None

        def _args(resume: str | None) -> list[str]:
            if resume:
                return [claude, "--agent", agent, "--resume", resume,
                        "-p", user_message, *common]
            return [claude, "--agent", agent, "-p", prompt, *common]

    async def _invoke(args: list[str]) -> int:
        nonlocal seq, final_sid, final_text, final_error
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
            payload["runtime"] = runtime
            payload["seq"] = seq
            if payload.get("type") == "result" and payload.get("session_id"):
                final_sid = payload["session_id"]
            if runtime == "codex":
                if payload.get("type") == "thread.started":
                    final_sid = payload.get("thread_id")
                item = payload.get("item") or {}
                if (payload.get("type") == "item.completed"
                        and item.get("type") == "agent_message"):
                    final_text = item.get("text") or final_text
                if payload.get("type") in ("turn.failed", "error"):
                    error = payload.get("error") or payload.get("message") or ""
                    final_error = (error.get("message", "") if isinstance(error, dict)
                                   else str(error))
                    if any(word in final_error.lower()
                           for word in ("unauthorized", "authentication failed", "401")):
                        payload["error"] = "authentication_failed"
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
    if runtime == "codex" and rc == 0:
        try:
            generated = await asyncio.to_thread(_upload_codex_generated, run_id)
            if generated:
                markers = []
                for artifact in generated:
                    marker = f"[[artifact:{artifact.get('id')}]]"
                    markers.append(marker)
                    seq += 1
                    await producer.publish(
                        TOPIC_TRANSCRIPT, run_id,
                        {"seq": seq, "type": "generated_image", "artifact": artifact,
                         "runtime": "codex"})
                final_text = "\n".join(markers + ([final_text] if final_text else []))
        except Exception as e:
            # A generated file that cannot be kept is a failed run: otherwise
            # the model would claim success while its only deliverable dies
            # with the pod's emptyDir.
            final_error = f"generated image could not be stored: {e}"
            rc = 1
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "generated_image",
                                    "error": final_error, "runtime": "codex"})
    if runtime == "codex" and rc == 0 and final_text:
        seq += 1
        await producer.publish(TOPIC_TRANSCRIPT, run_id,
                               {"seq": seq, "type": "result", "result": final_text,
                                "session_id": final_sid, "runtime": "codex",
                                "is_error": False})
    state = "succeeded" if rc == 0 else "failed"

    # On a successful dev run, hand the branch to the platform as a bundle. The
    # result — published, refused, or nothing to publish — is a frame the run
    # page shows; a finalize that blows up fails the run.
    if dev and rc == 0:
        try:
            result = await asyncio.to_thread(workbench.finalize, Path(cwd), wb, git_env,
                                             run_id, _api_req, nonce=publish_nonce)
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "workbench", **result})
            # The API refusing the branch (policy, ancestry, cap) means nothing
            # landed; "no changes" means there was nothing to land. Only the
            # first is a failed run.
            status = result.get("status")
            if result.get("published") is False and isinstance(status, int) and status >= 400:
                state = "failed"
        except Exception as e:
            seq += 1
            await producer.publish(TOPIC_TRANSCRIPT, run_id,
                                   {"seq": seq, "type": "workbench", "error": str(e)})
            state = "failed"

    # Persist the (possibly new) session so the next turn can resume it. Best
    # effort — an upload failure just means the next turn uses the fallback.
    # Gated on the CONVERSATION (`user_message`), not on the session token: the
    # token is universal now (docs/design/15), and a plain run has no
    # conversation to store a blob against — it would base64 its whole jsonl
    # for the API to decode and 404, once per pod, drowning the real failures.
    if rc == 0 and final_sid and user_message:
        try:
            if runtime == "codex":
                await asyncio.to_thread(_upload_codex_thread, run_id, final_sid)
            else:
                await asyncio.to_thread(_upload_session, run_cwd, run_id, final_sid)
        except Exception as e:
            print(f"session upload failed (non-fatal): {e}", flush=True)

    if runtime == "codex" and codex_auth is not None:
        await asyncio.to_thread(_upload_codex_auth, run_id, codex_auth, codex_auth_hash)

    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": seq + 1, "type": "lifecycle", "terminal": True, "state": state})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": state,
                            "exit_code": rc, "terminal": True, "runtime": runtime,
                            "detail": final_error if rc else ""},
                           type="run.state")
    await producer.stop()
    return rc

if __name__ == "__main__":
    sys.exit(run())
