"""The dev run's git seam (docs/design/24): an anonymous clone before `claude`
starts, a bundle to the platform after it exits.

The pod holds no repository credential. The clone is anonymous, `git push`
fails at the remote by construction, and the only way code leaves the pod is
`POST /api/runs/{id}/publish` — the API re-derives everything from the bundle
and owns the push. The runner decides when that happens (after a clean exit,
if the branch is ahead of its base); the model cannot skip, forge or bypass
it. Nothing here reads a token, so nothing here can leak one."""
import base64, html, json, re, shutil, subprocess, time
import urllib.error
from pathlib import Path

WORKSPACE = Path("/workspace")
NPM_CACHE = Path("/opt/npm-cache")
VENV_BIN = Path("/opt/venv/bin")

NOTES_MAX_BYTES = 32 * 1024
DEFAULT_PUBLISH_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_VERIFY_TIMEOUT = 2400
# What the runner waits beyond ap-verify's per-suite clock: room for ap-verify's
# own overhead (the kill, the report) once ONE suite has hit its cap. Several
# slow suites can still exhaust the outer budget together; that is recorded as
# "verify timed out" and the publish still goes out.
VERIFY_GRACE = 120
GIT_TIMEOUT = 600
NPM_TIMEOUT = 900
DEEPEN_STEP, DEEPEN_ROUNDS = 100, 5
PUBLISH_RETRY_DELAY = 2

# What a dependency's postinstall script gets to see. It runs before the model
# does, in the same pod, so the run's tokens and git's env are not for it.
NPM_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "NODE_ENV", "PLAYWRIGHT_BROWSERS_PATH")
# What `bin/ap-web-login` gets to see (docs/design/25): the credential the
# `qa-web-login` secret bound, the web URL it posts to, and a PATH/HOME to run
# with. Not the session token, not the run JWT — a login is not a run.
LOGIN_ENV_KEYS = ("PATH", "HOME", "QA_WEB_USER", "QA_WEB_PASSWORD", "AP_WEB_URL")
LOGIN_TIMEOUT = 60

# The API validates the branch before it is used anywhere; this is the same
# rule re-checked where the name becomes git argv, so a malformed response can
# never turn into an option (`-c core.sshCommand=…`) or a traversal.
BRANCH_RE = re.compile(r"^(coder|qa)/[a-z0-9][a-z0-9-]{0,40}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$")
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# The clone is anonymous over https (file:// is the tests' local remote). A
# value of any other shape — an ssh URL, a bare path, a `-o…` flag — is refused
# before it can reach argv.
REMOTE_RE = re.compile(r"^(https://|file://)[^\s]+$")


class WorkbenchError(RuntimeError):
    """A prepare/finalize step that cannot proceed. Its text names the step,
    never a command's output — output is where a remote's error page or a
    stray credential would otherwise end up in a transcript frame."""


def fetch_workbench(api_req, run_id: str) -> dict:
    """`GET /api/runs/{id}/workbench` with the run-scoped session token, shape-
    checked before any value reaches git: branch, base, existing, open_pr and
    ticket_key are the platform's facts, and everything else in the body is
    ignored so a surprise field can never reach the prompt or a command.

    `publish_nonce` is the exception that proves it: the platform mints it on
    a run's FIRST call — this one, made before `claude` is spawned — and the
    publish must present it. It is returned here as a value in a dict and
    must stay one: the caller keeps it in Python memory and hands it straight
    to `finalize`. Never the environment (a child process inherits that, and
    the model's shell is a child process), never a file in the workspace,
    never the prompt, never a frame."""
    d = api_req("GET", f"/api/runs/{run_id}/workbench")
    if not isinstance(d, dict):
        raise WorkbenchError("workbench response is not an object")
    branch, base = d.get("branch"), d.get("base")
    if not isinstance(branch, str) or not BRANCH_RE.match(branch):
        raise WorkbenchError("workbench response carries an invalid branch")
    if not isinstance(base, str) or not REF_RE.match(base) or ".." in base:
        raise WorkbenchError("workbench response carries an invalid base")
    if not isinstance(d.get("existing"), bool):
        raise WorkbenchError("workbench response carries an invalid `existing`")
    ticket = d.get("ticket_key")
    if ticket is not None and not isinstance(ticket, str):
        raise WorkbenchError("workbench response carries an invalid ticket_key")
    pr = d.get("open_pr")
    if pr is not None and not (isinstance(pr, dict) and isinstance(pr.get("number"), int)
                               and not isinstance(pr.get("number"), bool)
                               and isinstance(pr.get("url"), str)):
        raise WorkbenchError("workbench response carries an invalid open_pr")
    remote = d.get("remote_url")
    if remote is not None and not (isinstance(remote, str) and REMOTE_RE.match(remote)):
        raise WorkbenchError("workbench response carries an invalid remote_url")
    nonce = d.get("publish_nonce")
    if nonce is not None and not isinstance(nonce, str):
        raise WorkbenchError("workbench response carries an invalid publish_nonce")
    return {"branch": branch, "base": base, "existing": d["existing"], "ticket_key": ticket,
            "open_pr": None if pr is None else {"number": pr["number"], "url": pr["url"]},
            "remote_url": remote, "publish_nonce": nonce or None}


def dev_env(environ: dict) -> dict:
    """The subprocess env for a dev run: git never prompts (there is nothing to
    answer with), and the image's venv leads PATH so `python3`, `pytest` and
    `bin/ap-verify` all resolve to the interpreter that has the repo's
    requirements. No askpass, no token — that is the point."""
    env = {k: v for k, v in environ.items() if k not in ("AP_GITHUB_TOKEN", "GIT_ASKPASS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if VENV_BIN.is_dir():
        env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
    return env


def _git(repo_dir: Path | None, env: dict, *a: str, timeout: int = GIT_TIMEOUT) -> str:
    cmd = ["git", *(["-C", str(repo_dir)] if repo_dir else []), *a]
    try:
        return subprocess.run(cmd, check=True, env=env, capture_output=True, text=True,
                              timeout=timeout).stdout
    except subprocess.CalledProcessError as e:
        raise WorkbenchError(f"git {a[0]} failed (exit {e.returncode})") from None
    except subprocess.TimeoutExpired:
        raise WorkbenchError(f"git {a[0]} timed out after {timeout}s") from None


def _agent(wb: dict, env: dict) -> str:
    name = wb.get("agent") or env.get("AP_AGENT") or ""
    return name if AGENT_RE.match(name) else "agent"


def _ahead(repo_dir: Path, wb: dict, env: dict) -> int:
    return int(_git(repo_dir, env, "rev-list", "--count",
                    f"origin/{wb['base']}..refs/heads/{wb['branch']}").strip() or 0)


def qa_state_path() -> Path:
    """Where `ap-web-login` writes the browser's storage state and where the
    runner points the Playwright MCP server; its parent is also the server's
    output directory. A function, not a constant, so the workspace root stays
    the one thing to move."""
    return WORKSPACE / "qa" / "state.json"


def _web_login(repo_dir: Path, env: dict, frames: list | None) -> None:
    """Turn the pod's `QA_WEB_USER`/`QA_WEB_PASSWORD` into the storage-state
    file, with the checkout's own `bin/ap-web-login`, before the model exists.
    Nothing is done without the credential (an engineer's pod has none). The
    script's stdout is never echoed: a failure is a frame with the exit code
    and the ONE word the script prints last (`ok`, or the HTTP status; its
    stderr's last line when stdout is empty), capped short, so no line of it
    can carry a cookie into a transcript."""
    if not (env.get("QA_WEB_USER") and env.get("QA_WEB_PASSWORD")):
        return
    state = qa_state_path()
    (state.parent / "mcp").mkdir(parents=True, exist_ok=True)
    login_env = {k: v for k, v in env.items() if k in LOGIN_ENV_KEYS}
    cmd = ["python3", "bin/ap-web-login", "--out", str(state)]
    try:
        r = subprocess.run(cmd, cwd=repo_dir, env=login_env, capture_output=True, text=True,
                           timeout=LOGIN_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        if frames is not None:
            frames.append({"step": "web login", "ok": False, "exit": None,
                           "tail": type(e).__name__})
        return
    if r.returncode != 0 and frames is not None:
        # The status word is on stdout, capped to a word; a script that could
        # not even try (no web URL) says why on stderr and nothing on stdout,
        # and that reason is a sentence of its own, never a reply's.
        out_lines, err_lines = r.stdout.strip().splitlines(), r.stderr.strip().splitlines()
        tail = out_lines[-1][:16] if out_lines else (err_lines[-1][:80] if err_lines else "")
        frames.append({"step": "web login", "ok": False, "exit": r.returncode, "tail": tail})


def _npm_env(env: dict) -> dict:
    out = {k: v for k, v in env.items()
           if k in NPM_ENV_KEYS or k.startswith("npm_config_")}
    out["CI"] = "1"
    out["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    return out


def _connect_histories(repo_dir: Path, wb: dict, env: dict) -> None:
    """A 50-deep clone of the base and a 50-deep fetch of the branch are two
    islands when the fork is further back than that: `merge-base` fails and
    every `origin/<base>..<branch>` range is wrong. Deepen both until they
    meet, within a bound that covers any sane branch."""
    branch, base = wb["branch"], wb["base"]
    for _ in range(DEEPEN_ROUNDS):
        try:
            _git(repo_dir, env, "merge-base", f"origin/{base}", f"refs/heads/{branch}")
            return
        except WorkbenchError:
            _git(repo_dir, env, "fetch", f"--deepen={DEEPEN_STEP}", "origin", base, branch)
    try:
        _git(repo_dir, env, "merge-base", f"origin/{base}", f"refs/heads/{branch}")
    except WorkbenchError:
        raise WorkbenchError(f"cannot find the fork point of {branch} within "
                             f"{50 + DEEPEN_STEP * DEEPEN_ROUNDS} commits") from None


def _tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def block(repo_dir: Path, wb: dict, ahead: int) -> str:
    """The `<workbench>` block: the platform's own voice at the very end of the
    prompt, built only from facts the runner computed. Every value is escaped
    on the way in, and nothing from the ticket, the thread or any free-text
    field of the API response is allowed here — that is what keeps the block
    trusted while the `<ticket>` above it is not."""
    e = html.escape
    branch, base = e(wb["branch"]), e(wb["base"])
    attrs = [f'branch="{branch}"', f'base="{base}"', f'commits_ahead="{int(ahead)}"']
    if wb.get("ticket_key"):
        attrs.append(f'ticket="{e(wb["ticket_key"])}"')
    pr = wb.get("open_pr")
    if pr:
        attrs.append(f'pr="{int(pr["number"])}"')
        pr_line = (f"Pull request #{int(pr['number'])} ({e(pr['url'])}) is open for this branch; "
                   "the platform updates it when this run ends.")
    else:
        pr_line = "No pull request is open for this branch yet; the platform opens one when this run ends."
    return "\n".join([
        f"<workbench {' '.join(attrs)}>",
        (f"Your clone of the repository is at {e(str(repo_dir))}, on branch `{branch}` "
         f"({ahead} commit(s) ahead of `origin/{base}`). {pr_line}"),
        "Rules:",
        "1. Commit as you go: after each working step, with a message a stranger can act on.",
        ("2. Never push. This pod holds no repository credential; the platform publishes your "
         "branch as a pull request when the run ends."),
        ("3. Run `python3 bin/ap-verify --changed` before you finish. Paste nothing: the runner "
         "records the real result."),
        ("4. Write `.ap/pr.md`: what and why, what was verified, what the reviewer should look "
         "at, what is deferred. It becomes the pull request's notes."),
        "</workbench>",
    ]) + "\n"


def prepare(repo_dir: Path, wb: dict, env: dict, frames: list | None = None) -> str:
    """Clone anonymously, check out the branch, give git an identity, warm the
    toolchain, and return the `<workbench>` block for the prompt.

    An `npm ci` failure is appended to `frames` (a transcript note the run page
    shows) rather than raised: the agent can still work in a tree whose web
    dependencies are missing, and the verify step will say what did not run."""
    remote = env.get("AP_GIT_REMOTE_URL") or wb.get("remote_url")
    if not remote:
        raise WorkbenchError("no git remote (AP_GIT_REMOTE_URL unset)")
    if not REMOTE_RE.match(remote):
        raise WorkbenchError("AP_GIT_REMOTE_URL is not an https:// or file:// URL")
    branch, base = wb["branch"], wb["base"]
    _git(None, env, "clone", "--depth", "50", "--branch", base, "--", remote, str(repo_dir))
    if wb.get("existing"):
        # A --branch clone tracks only the base, so the branch needs an explicit
        # refspec to land as origin/<branch> (no `+`: the ref does not exist yet).
        _git(repo_dir, env, "fetch", "--depth", "50", "origin",
             f"refs/heads/{branch}:refs/remotes/origin/{branch}")
        _git(repo_dir, env, "checkout", "-B", branch, f"origin/{branch}")
        _connect_histories(repo_dir, wb, env)
    else:
        _git(repo_dir, env, "checkout", "-b", branch)

    agent = _agent(wb, env)
    home = Path(env.get("HOME") or Path.home())
    (home / ".gitconfig").write_text(
        f"[user]\n\tname = {agent}\n\temail = {agent}@agent-platform.local\n")

    _web_login(repo_dir, env, frames)

    if NPM_CACHE.is_dir():
        shutil.copytree(NPM_CACHE, home / ".npm", dirs_exist_ok=True)
    if (repo_dir / "package-lock.json").is_file():
        try:
            r = subprocess.run(["npm", "ci", "--prefer-offline", "--no-audit", "--no-fund"],
                               cwd=repo_dir, env=_npm_env(env), capture_output=True, text=True,
                               timeout=NPM_TIMEOUT, check=False)
            if r.returncode != 0 and frames is not None:
                frames.append({"step": "npm ci", "ok": False, "exit": r.returncode,
                               "tail": _tail(r.stdout + r.stderr)})
        except (subprocess.TimeoutExpired, OSError) as e:
            if frames is not None:
                frames.append({"step": "npm ci", "ok": False, "exit": None,
                               "tail": type(e).__name__})
    return block(repo_dir, wb, _ahead(repo_dir, wb, env))


def _verify(repo_dir: Path, wb: dict, env: dict) -> dict:
    """Run `bin/ap-verify --changed` after the model's turn and capture what it
    recorded. A timeout or a crash is recorded AS that — the PR's verification
    section is evidence, and "it did not run" is evidence too.

    `AP_VERIFY_TIMEOUT` is handed down as ap-verify's `--timeout`: the suite
    clock is the platform's setting, not the script's default (a coverage run
    of the backend suite outlives that default on the NUC)."""
    out = WORKSPACE / "verify"
    timeout = int(env.get("AP_VERIFY_TIMEOUT") or DEFAULT_VERIFY_TIMEOUT)
    cmd = ["python3", "bin/ap-verify", "--changed", "--base", f"origin/{wb['base']}",
           "--out", str(out), "--timeout", str(timeout)]
    try:
        r = subprocess.run(cmd, cwd=repo_dir, env=env, capture_output=True, text=True,
                           timeout=timeout + VERIFY_GRACE, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "verify timed out"}
    try:
        data = json.loads((out / "verify.json").read_text())
    except (OSError, ValueError):
        return {"ok": False, "error": f"verify crashed: {r.returncode}"}
    return data if isinstance(data, dict) else {"ok": False, "error": f"verify crashed: {r.returncode}"}


def _notes(repo_dir: Path) -> str:
    p = repo_dir / ".ap" / "pr.md"
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    if len(raw) <= NOTES_MAX_BYTES:
        return raw.decode("utf-8", "replace")
    return (raw[:NOTES_MAX_BYTES].decode("utf-8", "replace")
            + f"\n\n[truncated: .ap/pr.md was {len(raw)} bytes, the cap is {NOTES_MAX_BYTES}]")


PUBLISH_NONCE_HEADER = "X-AP-Publish-Nonce"


def _post_publish(api_req, run_id: str, body: dict, nonce: str | None = None):
    """One retry on a connection-level failure (the API pod restarting under a
    deploy is the case); an HTTP status is an answer and is never retried.

    The nonce travels as a header and nowhere else. It is what tells the API
    this POST is the runner's: the session token is in the pod's environment,
    where the model's shell can read it, but this process's memory is not —
    a descendant process inherits the env, never the parent's variables."""
    headers = {PUBLISH_NONCE_HEADER: nonce} if nonce else None
    for attempt in (1, 2):
        try:
            return api_req("POST", f"/api/runs/{run_id}/publish", body, headers=headers)
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(PUBLISH_RETRY_DELAY)


REASON_MAX_CHARS = 2048


def _refusal_reason(e: urllib.error.HTTPError) -> str:
    """The API's own sentence out of a refusal: FastAPI wraps it as
    `{"detail": "..."}`, and the run page shows `reason` as it is, so the
    wrapper comes off. Anything else — a proxy's error page, a validation
    error list — stays as text, and an empty body falls back to the status
    reason. Capped either way."""
    try:
        text = e.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        text = ""
    try:
        detail = json.loads(text).get("detail")
        if isinstance(detail, str):
            text = detail
    except (ValueError, AttributeError):
        pass
    return text[:REASON_MAX_CHARS] or str(e.reason)


def finalize(repo_dir: Path, wb: dict, env: dict, run_id: str, api_req, *,
             nonce: str | None = None) -> dict:
    """After a clean exit: checkpoint whatever is uncommitted, verify, bundle,
    and hand the bundle to the platform. Returns the API's body (with
    `published: True`) or a `published: False` record naming why not.
    `nonce` is the publish nonce `fetch_workbench` was given, kept in the
    runner's memory since — see `_post_publish`."""
    branch, base = wb["branch"], wb["base"]
    if _git(repo_dir, env, "status", "--porcelain", "-uall").strip():
        _git(repo_dir, env, "add", "-A")
        _git(repo_dir, env, "commit", "-q", "-m", f"{_agent(wb, env)}: checkpoint at run end")
    if _ahead(repo_dir, wb, env) == 0:
        return {"published": False, "reason": "no changes"}

    verify = _verify(repo_dir, wb, env)

    bundle = WORKSPACE / "publish.bundle"
    bundle.unlink(missing_ok=True)
    # refs/heads/<branch>, not HEAD: the bundle then names the branch, which is
    # what the API fetches from it (and all it will accept).
    _git(repo_dir, env, "bundle", "create", str(bundle), f"origin/{base}..refs/heads/{branch}")
    cap = int(env.get("AP_PUBLISH_MAX_BYTES") or DEFAULT_PUBLISH_MAX_BYTES)
    size = bundle.stat().st_size
    if size > cap:
        return {"published": False, "verify": verify,
                "reason": f"bundle is {size} bytes, over the {cap} byte cap; nothing was published"}

    body = {"bundle_b64": base64.b64encode(bundle.read_bytes()).decode(),
            "head_sha": _git(repo_dir, env, "rev-parse", f"refs/heads/{branch}").strip(),
            "base_sha": _git(repo_dir, env, "rev-parse", f"origin/{base}").strip(),
            "verify": verify, "notes_md": _notes(repo_dir)}
    try:
        result = _post_publish(api_req, run_id, body, nonce)
    except urllib.error.HTTPError as e:
        return {"published": False, "status": e.code, "reason": _refusal_reason(e)}
    return {"published": True, **(result if isinstance(result, dict) else {"response": result})}
