"""bin/ap-web-login — the pod's QA credential becomes a browser storage state
(docs/design/25 T7).

Loaded by path like its siblings. `urllib.request.urlopen` is a recorder that
answers as `/api/login` would, so what is pinned is the request the script
builds (the JSON body, the principal), the file it writes (Playwright's
storage-state shape, mode 0600, the cookie's domain = the web host) and what
it prints: `ok` or the status, and never the cookie or the password.
"""

from __future__ import annotations

import email.message
import importlib.machinery
import importlib.util
import io
import json
import stat
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "bin" / "ap-web-login"
COOKIE = "eyJwcmluY2lwYWwiOiAicWEifQ.aBcDeF.signature-bytes"
PASSWORD = "correct-horse-battery-staple"


def _load():
    loader = importlib.machinery.SourceFileLoader("ap_web_login", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ap_web_login", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def awl():
    return _load()


class _Reply(io.BytesIO):
    status = 200

    def __init__(self, body: bytes, set_cookie: list[str]):
        super().__init__(body)
        self.headers = email.message.Message()
        for value in set_cookie:
            self.headers["Set-Cookie"] = value

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def api(awl, monkeypatch):
    """The fake login route: records the request; answers 200 + the cookie the
    real route sets (HttpOnly, SameSite=lax, Path=/) unless `error` is set."""
    sent = []

    def urlopen(req, timeout=None):
        sent.append(req)
        err = getattr(urlopen, "error", None)
        if err is not None:
            raise err
        return _Reply(b'{"ok": true}', [
            "csrf_probe=ignored; Path=/",
            f"ap_session={COOKIE}; HttpOnly; Path=/; SameSite=lax"])
    monkeypatch.setattr(awl.urllib.request, "urlopen", urlopen)
    return sent, urlopen


@pytest.fixture
def pod_env(monkeypatch):
    monkeypatch.setenv("AP_WEB_URL", "http://ap-web:8090/")
    monkeypatch.setenv("QA_WEB_USER", "qa")
    monkeypatch.setenv("QA_WEB_PASSWORD", PASSWORD)


def test_login_writes_the_storage_state_and_prints_ok(awl, api, pod_env, tmp_path, capsys):
    sent, _ = api
    out = tmp_path / "qa" / "state.json"
    assert awl.main(["--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "ok\n" and captured.err == ""
    # The request: the login route, JSON, the principal from the env.
    (req,) = sent
    assert req.full_url == "http://ap-web:8090/api/login" and req.get_method() == "POST"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"principal": "qa", "password": PASSWORD}
    # The file: Playwright's storage-state shape, the session cookie only,
    # scoped to the web host, and readable by the owner alone.
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    state = json.loads(out.read_text())
    assert state == {"cookies": [{
        "name": "ap_session", "value": COOKIE, "domain": "ap-web", "path": "/",
        "expires": -1, "httpOnly": True, "secure": False, "sameSite": "Lax"}],
        "origins": []}


def test_a_refused_login_prints_the_status_and_exits_one(awl, api, pod_env, tmp_path, capsys):
    sent, urlopen = api
    urlopen.error = urllib.error.HTTPError("http://ap-web:8090/api/login", 401, "Unauthorized",
                                           {}, io.BytesIO(b'{"detail":"Unauthorized"}'))
    out = tmp_path / "state.json"
    assert awl.main(["--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "401\n"
    assert not out.exists()
    assert PASSWORD not in captured.out + captured.err and COOKIE not in captured.out + captured.err


def test_a_reply_without_the_cookie_is_a_failure(awl, pod_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(awl.urllib.request, "urlopen",
                        lambda req, timeout=None: _Reply(b'{"ok": true}', ["other=1; Path=/"]))
    assert awl.main(["--out", str(tmp_path / "state.json")]) == 1
    assert capsys.readouterr().out == "200\n"
    assert not (tmp_path / "state.json").exists()


def test_an_unreachable_web_is_a_status_word_not_a_traceback(awl, pod_env, tmp_path, monkeypatch, capsys):
    def urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(awl.urllib.request, "urlopen", urlopen)
    assert awl.main(["--out", str(tmp_path / "state.json")]) == 1
    assert capsys.readouterr().out == "unreachable\n"


def test_no_credential_is_exit_two_unless_the_state_already_exists(awl, tmp_path, monkeypatch, capsys):
    """The model may run the script itself (the walk starts with it) in an
    environment the runner has already stripped the password from: with the
    state file the runner's login wrote in place that is still `ok`; with
    nothing to log in with and no state, it says so."""
    monkeypatch.setenv("AP_WEB_URL", "http://ap-web:8090")
    monkeypatch.delenv("QA_WEB_USER", raising=False)
    monkeypatch.delenv("QA_WEB_PASSWORD", raising=False)
    out = tmp_path / "state.json"
    assert awl.main(["--out", str(out)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "QA_WEB_PASSWORD" in captured.err
    out.write_text("{}")
    assert awl.main(["--out", str(out)]) == 0
    assert capsys.readouterr().out == "ok\n"


def test_the_password_never_reaches_argv_or_the_url(awl, api, pod_env, tmp_path):
    """The credential travels in the JSON body only: no flag takes it, so it
    cannot land in a process listing, and the URL is the web URL plus the
    route."""
    sent, _ = api
    assert awl.main(["--out", str(tmp_path / "s.json"), "--user", "qa"]) == 0
    (req,) = sent
    assert PASSWORD not in req.full_url
    with pytest.raises(SystemExit):
        awl.main(["--out", str(tmp_path / "s.json"), "--password", PASSWORD])


def test_script_is_executable_and_stdlib_only():
    assert SCRIPT.stat().st_mode & 0o111
    assert SCRIPT.read_text().splitlines()[0] == "#!/usr/bin/env python3"
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import ast,sys;t=ast.parse(open(sys.argv[1]).read());"
                        "mods={n.name.split('.')[0] for s in t.body if isinstance(s,ast.Import) for n in s.names}"
                        "|{s.module.split('.')[0] for s in t.body if isinstance(s,ast.ImportFrom)};"
                        "bad=[m for m in mods if m not in sys.stdlib_module_names];print(bad);sys.exit(bool(bad))",
                        str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
