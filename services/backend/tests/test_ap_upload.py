"""bin/ap-upload — a run's files become artifacts (docs/design/25 T4).

The script has no .py suffix and no package, so it is loaded here by path,
like `test_ap_verify.py` loads its sibling. Nothing here touches the network:
`urllib.request.urlopen` is replaced by a recorder that answers as the
artifacts route would, so what is pinned is the request the script builds —
the multipart body, the identity headers, and what it prints — and that a
refusal comes out as the API's own words on stderr, never the token.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import re
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "bin" / "ap-upload"
TOKEN = "eyJhbGciOiJSUzI1NiJ9.sa-token-body.signature"
RUN_TOKEN = "eyJhbGciOiJSUzI1NiJ9.run-token-body.signature"


def _load():
    loader = importlib.machinery.SourceFileLoader("ap_upload", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ap_upload", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def apu():
    return _load()


class _Reply(io.BytesIO):
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def api(apu, monkeypatch):
    """The fake artifacts route: records each `urllib.request.Request`, answers
    a fresh id per upload (or raises what `replies` holds)."""
    sent = []
    ids = iter(f"{i:02x}" * 16 for i in range(1, 10))

    def urlopen(req, timeout=None):
        sent.append(req)
        reply = getattr(urlopen, "error", None)
        if reply is not None:
            raise reply
        return _Reply(json.dumps({"id": next(ids), "name": "x"}).encode())

    monkeypatch.setattr(apu.urllib.request, "urlopen", urlopen)
    return sent, urlopen


@pytest.fixture
def pod_env(monkeypatch, tmp_path):
    """The env a run pod has: the API's URL, a projected SA token FILE (the
    design/13 identity) and the run JWT beside it — never AP_API_TOKEN."""
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    monkeypatch.delenv("AP_API_TOKEN", raising=False)
    monkeypatch.setenv("AP_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("AP_API_URL", "http://agent-platform-api:8000/")
    monkeypatch.setenv("AP_RUN_TOKEN", RUN_TOKEN)


def _parts(req) -> dict:
    """The multipart body as {field: (filename, content-type, bytes)}."""
    ctype = req.get_header("Content-type")
    boundary = re.search(r'boundary="?([^";]+)"?', ctype).group(1).encode()
    parts = {}
    for chunk in req.data.split(b"--" + boundary)[1:-1]:
        head, body = chunk.lstrip(b"\r\n").split(b"\r\n\r\n", 1)
        head = head.decode()
        name = re.search(r'name="([^"]+)"', head).group(1)
        filename = re.search(r'filename="([^"]*)"', head)
        mime = re.search(r"Content-Type: (\S+)", head)
        parts[name] = (filename.group(1) if filename else None,
                       mime.group(1) if mime else None, body[:-2])
    return parts


# ---------------------------------------------------------------- the upload


def test_each_file_is_one_multipart_post_and_prints_its_id(apu, api, pod_env, tmp_path, capsys):
    junit = tmp_path / "reports" / "junit.xml"
    junit.parent.mkdir()
    junit.write_bytes(b"<testsuites/>")
    pw = tmp_path / "playwright.json"
    pw.write_bytes(b'{"suites": []}')
    assert apu.main([str(junit), str(pw)]) == 0
    sent, _ = api
    assert [r.full_url for r in sent] == ["http://agent-platform-api:8000/api/artifacts"] * 2
    assert [r.get_method() for r in sent] == ["POST", "POST"]
    assert capsys.readouterr().out == "01" * 16 + "\n" + "02" * 16 + "\n"

    parts = _parts(sent[0])
    # The mime is a claim the store re-sniffs; XML's guess differs by platform.
    assert (parts["file"][0], parts["file"][2]) == ("junit.xml", b"<testsuites/>")
    assert parts["file"][1] in ("text/xml", "application/xml")
    assert parts["name"] == (None, None, b"junit.xml")
    assert json.loads(parts["tags"][2]) == ["tcms"]
    assert _parts(sent[1])["file"][0] == "playwright.json"
    assert _parts(sent[1])["file"][1] == "application/json"


def test_the_identity_is_the_token_file_and_the_run_jwt(apu, api, pod_env, tmp_path):
    f = tmp_path / "coverage.xml"
    f.write_bytes(b"<coverage/>")
    assert apu.main([str(f)]) == 0
    req = api[0][0]
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert req.get_header("X-ap-run-token") == RUN_TOKEN


def test_an_inline_token_wins_and_no_run_jwt_means_no_header(apu, api, pod_env, tmp_path,
                                                             monkeypatch):
    monkeypatch.setenv("AP_API_TOKEN", "ap_inline")
    monkeypatch.delenv("AP_RUN_TOKEN")
    f = tmp_path / "a.txt"
    f.write_bytes(b"hi")
    assert apu.main([str(f)]) == 0
    req = api[0][0]
    assert req.get_header("Authorization") == "Bearer ap_inline"
    assert req.get_header("X-ap-run-token") is None


def test_tags_can_be_chosen(apu, api, pod_env, tmp_path):
    f = tmp_path / "shot.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 8)
    assert apu.main(["--tag", "qa", "--tag", "screenshot", str(f)]) == 0
    parts = _parts(api[0][0])
    assert json.loads(parts["tags"][2]) == ["qa", "screenshot"]
    assert parts["file"][1] == "image/png"


# ---------------------------------------------------------------- refusals


def test_the_apis_refusal_is_the_message_and_exit_1(apu, api, pod_env, tmp_path, capsys):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 10)
    _, urlopen = api
    urlopen.error = urllib.error.HTTPError(
        "http://agent-platform-api:8000/api/artifacts", 413, "Payload Too Large", {},
        io.BytesIO(b'{"detail": "an artifact is at most 8388608 bytes"}'))
    assert apu.main([str(f)]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "an artifact is at most 8388608 bytes" in err and "big.bin" in err
    assert TOKEN not in err and RUN_TOKEN not in err


def test_an_unreachable_api_is_exit_1_without_the_token(apu, api, pod_env, tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hi")
    _, urlopen = api
    urlopen.error = urllib.error.URLError("[Errno 111] Connection refused")
    assert apu.main([str(f)]) == 1
    err = capsys.readouterr().err
    assert "Connection refused" in err and TOKEN not in err


def test_ids_already_printed_stay_when_a_later_file_fails(apu, api, pod_env, tmp_path, capsys):
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    a.write_bytes(b"<a/>")
    b.write_bytes(b"<b/>")
    sent, urlopen = api
    real = urlopen

    def flaky(req, timeout=None):
        if len(sent) == 1:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b'{"detail": "not granted"}'))
        return real(req, timeout=timeout)

    apu.urllib.request.urlopen = flaky
    assert apu.main([str(a), str(b)]) == 1
    out, err = capsys.readouterr()
    assert out == "01" * 16 + "\n"
    assert "b.xml" in err and "not granted" in err


def test_no_identity_is_a_clear_exit_1_before_any_request(apu, api, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AP_API_TOKEN", raising=False)
    monkeypatch.delenv("AP_API_TOKEN_FILE", raising=False)
    f = tmp_path / "a.txt"
    f.write_bytes(b"hi")
    assert apu.main([str(f)]) == 1
    assert "AP_API_TOKEN" in capsys.readouterr().err
    assert api[0] == []


def test_a_missing_file_is_exit_1_before_any_request(apu, api, pod_env, tmp_path, capsys):
    assert apu.main([str(tmp_path / "nope.xml")]) == 1
    assert "nope.xml" in capsys.readouterr().err
    assert api[0] == []


def test_help_prints_usage(apu, capsys):
    with pytest.raises(SystemExit) as e:
        apu.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage:") and "AP_API_TOKEN_FILE" in out


# ---------------------------------------------------------------- the real route


async def test_the_body_it_builds_is_one_the_artifacts_route_parses(apu, admin_client):
    """No fake here: the exact bytes the script would send, through the real
    multipart parser, land a row with the basename and the tag."""
    body, ctype = apu.multipart({"name": "junit.xml", "tags": json.dumps(["tcms"])},
                                "junit.xml", "application/xml", b"<testsuites/>")
    r = await admin_client.post("/api/artifacts", content=body,
                                headers={"Content-Type": ctype})
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["name"], a["tags"], a["size"]) == ("junit.xml", ["tcms"], len(b"<testsuites/>"))
