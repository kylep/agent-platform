"""Tests for the image_gen tool. No network: urllib.request.urlopen is
monkeypatched with canned responses, and every provider test asserts the exact
URL / headers / body it sends, so a future port cannot silently drift from the
request shapes verified against the providers' docs (see run.py's header).
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

import pytest

import run

# A valid, minimal 1x1 transparent PNG.
TINY_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII=")
TINY_PNG = base64.b64decode(TINY_PNG_B64)
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._body, self.status, self.headers = body, status, headers or {}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(req, code: int, body: bytes):
    return urllib.error.HTTPError(req.full_url, code, "err", None, io.BytesIO(body))


def capture(monkeypatch, responder):
    """Route the tool's opener through `responder(req)` and collect every
    Request seen. The opener (not urllib.request.urlopen) is what run.py
    calls, so its no-redirect policy is exercised by the real-server test."""
    seen = []

    def fake(req, timeout=None):
        seen.append(req)
        return responder(req)

    monkeypatch.setattr(run._OPENER, "open", fake)
    return seen


@pytest.fixture(autouse=True)
def fresh_budget():
    run._deadline = None
    yield
    run._deadline = None


def parse_multipart(body: bytes, content_type: str) -> list[dict]:
    m = re.match(r'multipart/form-data; boundary=(.+)$', content_type)
    assert m, content_type
    boundary = m.group(1).encode()
    parts = []
    for chunk in body.split(b"--" + boundary):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        headers = dict(line.split(b": ", 1) for line in head.split(b"\r\n"))
        disp = headers[b"Content-Disposition"].decode()
        parts.append({"name": re.search(r'name="([^"]+)"', disp).group(1),
                      "filename": (re.search(r'filename="([^"]+)"', disp) or [None, None])[1],
                      "content_type": headers.get(b"Content-Type", b"").decode(),
                      "data": data})
    return parts


@pytest.fixture
def sink(tmp_path, monkeypatch):
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setenv("TOOL_IN_DIR", str(in_dir))
    monkeypatch.setenv("TOOL_OUT_DIR", str(out_dir))
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "BFL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return in_dir, out_dir


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def test_registry_shape_and_single_default():
    models = run.load_models()
    ids = [m["id"] for m in models]
    assert len(ids) == len(set(ids))
    defaults = [m["id"] for m in models if m.get("default")]
    assert defaults == ["gpt-image-2.5-flare"]
    for m in models:
        assert m["provider"] in run.KEY_ENV, m["id"]
        assert isinstance(m["label"], str) and m["label"]
        assert isinstance(m["price_usd"], (int, float)) and m["price_usd"] > 0
        assert ("sizes" in m) != ("aspects" in m), m["id"]
        assert m["qualities"] is None or isinstance(m["qualities"], list)
        assert isinstance(m["edits"], bool)


def test_registry_covers_every_provider_family():
    reg = run.registry()
    for mid, provider in [("gpt-image-2", "openai"), ("gpt-image-1-mini", "openai"),
                          ("gemini-3-pro-image", "gemini"), ("gemini-2.5-flash-image", "gemini"),
                          ("flux-2-klein-4b", "bfl"), ("flux-kontext-pro", "bfl"),
                          ("flux-pro-1.1", "bfl")]:
        assert reg[mid]["provider"] == provider


# --------------------------------------------------------------------------
# size <-> aspect bridge
# --------------------------------------------------------------------------

def test_parse_size():
    assert run.parse_size("1536x1024") == (1536, 1024)
    assert run.parse_size("1024X1024") == (1024, 1024)
    for bad in ("big", "0x100", "10x", "-5x5"):
        with pytest.raises(run.ImageGenError):
            run.parse_size(bad)


def test_nearest_aspect():
    assert run.nearest_aspect(1024, 1024) == "1:1"
    assert run.nearest_aspect(1536, 1024) == "4:3"
    assert run.nearest_aspect(1920, 1080) == "16:9"
    assert run.nearest_aspect(1024, 1536) == "3:4"
    # A model with a richer list gets the closer match.
    assert run.nearest_aspect(1536, 1024, ["1:1", "3:2", "4:3"]) == "3:2"


def test_aspect_to_size_is_about_one_megapixel_in_multiples_of_16():
    for aspect in ("1:1", "16:9", "9:16", "3:2", "21:9"):
        w, h = run.aspect_to_size(aspect)
        assert w % 16 == 0 and h % 16 == 0
        assert 0.85e6 <= w * h <= 1.15e6, (aspect, w, h)
        a, b = (int(x) for x in aspect.split(":"))
        assert abs(w / h - a / b) < 0.05
    assert run.aspect_to_size("1:1") == (1024, 1024)
    with pytest.raises(run.ImageGenError):
        run.aspect_to_size("wide")


def test_geometry_openai_fixed_sizes_and_custom():
    reg = run.registry()
    assert run.geometry(reg["gpt-image-1-mini"], size=None, aspect=None)["size"] == "1024x1024"
    assert run.geometry(reg["gpt-image-1-mini"], size="1536x1024", aspect=None)["size"] == "1536x1024"
    # No custom sizes on gpt-image-1-mini: an off-list size is refused, and
    # an aspect snaps to the nearest listed size.
    with pytest.raises(run.ImageGenError, match="size"):
        run.geometry(reg["gpt-image-1-mini"], size="1536x864", aspect=None)
    assert run.geometry(reg["gpt-image-1-mini"], size=None, aspect="16:9")["size"] == "1536x1024"
    # gpt-image-2+ takes arbitrary WxH in multiples of 16 within the doc'd bounds.
    assert run.geometry(reg["gpt-image-2.5-flare"], size="1536x864", aspect=None)["size"] == "1536x864"
    with pytest.raises(run.ImageGenError, match="multiple of 16"):
        run.geometry(reg["gpt-image-2.5-flare"], size="1000x1000", aspect=None)
    g = run.geometry(reg["gpt-image-2.5-flare"], size=None, aspect="16:9")
    w, h = run.parse_size(g["size"])
    assert w % 16 == 0 and h % 16 == 0 and abs(w / h - 16 / 9) < 0.05


def test_geometry_gemini_and_kontext_bridge_size_to_aspect():
    reg = run.registry()
    assert run.geometry(reg["gemini-3.1-flash-image"], size="1920x1080", aspect=None)["aspect"] == "16:9"
    assert run.geometry(reg["gemini-3.1-flash-image"], size=None, aspect="4:3")["aspect"] == "4:3"
    assert run.geometry(reg["gemini-3.1-flash-image"], size=None, aspect=None)["aspect"] == "1:1"
    with pytest.raises(run.ImageGenError, match="aspect"):
        run.geometry(reg["gemini-3.1-flash-image"], size=None, aspect="99:1")
    assert run.geometry(reg["flux-kontext-pro"], size="768x1024", aspect=None)["aspect"] == "3:4"
    assert run.geometry(reg["flux-kontext-pro"], size="1024x1536", aspect=None)["aspect"] == "2:3"


def test_geometry_flux2_bridges_aspect_to_pixels():
    reg = run.registry()
    g = run.geometry(reg["flux-2-klein-4b"], size=None, aspect="16:9")
    w, h = run.parse_size(g["size"])
    assert w % 16 == 0 and h % 16 == 0 and abs(w / h - 16 / 9) < 0.05
    # An off-grid custom size is snapped down to the 16-pixel grid, not refused.
    assert run.geometry(reg["flux-2-pro"], size="1000x700", aspect=None)["size"] == "992x688"


# --------------------------------------------------------------------------
# provider calls: exact request shapes
# --------------------------------------------------------------------------

def test_openai_generations_request_and_response(monkeypatch):
    seen = capture(monkeypatch, lambda req: FakeResponse(
        json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode()))
    data, ext = run.call_openai("gpt-image-2.5-flare", "a cat", "1024x1024", "high", "sk-test")
    assert (data, ext) == (TINY_PNG, "png")
    [req] = seen
    assert req.full_url == "https://api.openai.com/v1/images/generations"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer sk-test"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"model": "gpt-image-2.5-flare", "prompt": "a cat",
                                    "size": "1024x1024", "quality": "high", "n": 1,
                                    "output_format": "png"}


def test_openai_edits_is_multipart_with_image_array(monkeypatch):
    seen = capture(monkeypatch, lambda req: FakeResponse(
        json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode()))
    refs = [("ref1.png", TINY_PNG), ("ref2.jpg", TINY_JPEG)]
    data, ext = run.call_openai("gpt-image-2.5-sunburst", "make it blue", "1024x1024", "high",
                                "sk-test", references=refs)
    assert (data, ext) == (TINY_PNG, "png")
    [req] = seen
    assert req.full_url == "https://api.openai.com/v1/images/edits"
    assert req.get_header("Authorization") == "Bearer sk-test"
    parts = parse_multipart(req.data, req.get_header("Content-type"))
    fields = {p["name"]: p["data"].decode() for p in parts if p["filename"] is None}
    assert fields == {"model": "gpt-image-2.5-sunburst", "prompt": "make it blue",
                      "size": "1024x1024", "quality": "high", "n": "1", "output_format": "png"}
    images = [p for p in parts if p["filename"] is not None]
    assert [p["name"] for p in images] == ["image[]", "image[]"]
    assert [(p["filename"], p["content_type"], p["data"]) for p in images] == [
        ("ref1.png", "image/png", TINY_PNG), ("ref2.jpg", "image/jpeg", TINY_JPEG)]
    assert b"sk-test" not in req.data


def test_gemini_request_uses_header_key_and_inline_reference(monkeypatch):
    body = {"candidates": [{"content": {"parts": [
        {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": TINY_PNG_B64}}]}}]}
    seen = capture(monkeypatch, lambda req: FakeResponse(json.dumps(body).encode()))
    data, ext = run.call_gemini("gemini-3.1-flash-image", "a dog", "16:9", "g-key",
                                references=[("ref.png", TINY_PNG)], image_size="2K", seed=7)
    assert (data, ext) == (TINY_PNG, "png")
    [req] = seen
    assert req.full_url == ("https://generativelanguage.googleapis.com/v1beta/models/"
                            "gemini-3.1-flash-image:generateContent")
    assert "g-key" not in req.full_url
    assert req.get_header("X-goog-api-key") == "g-key"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {
        "contents": [{"parts": [
            {"text": "a dog"},
            {"inlineData": {"mimeType": "image/png", "data": TINY_PNG_B64}}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"},
                             "seed": 7},
    }


def test_gemini_minimal_request_omits_optional_fields(monkeypatch):
    body = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(TINY_JPEG).decode()}}]}}]}
    seen = capture(monkeypatch, lambda req: FakeResponse(json.dumps(body).encode()))
    data, ext = run.call_gemini("gemini-2.5-flash-image", "a dog", "1:1", "g-key")
    assert (data, ext) == (TINY_JPEG, "jpg")
    assert json.loads(seen[0].data) == {
        "contents": [{"parts": [{"text": "a dog"}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                             "imageConfig": {"aspectRatio": "1:1"}}}


def test_gemini_no_image_part_is_an_error(monkeypatch):
    body = {"candidates": [{"content": {"parts": [{"text": "I can't draw that"}]}}]}
    capture(monkeypatch, lambda req: FakeResponse(json.dumps(body).encode()))
    with pytest.raises(run.ImageGenError, match="did not contain an image"):
        run.call_gemini("gemini-2.5-flash-image", "x", "1:1", "g-key")


def test_bfl_flux2_submit_poll_download(monkeypatch):
    submit = {"id": "req-1", "polling_url": "https://api.us1.bfl.ai/v1/get_result?id=req-1"}
    ready = {"status": "Ready", "result": {"sample": "https://delivery.bfl.ai/img.png", "seed": 42}}
    pending = {"status": "Pending"}
    polls = iter([pending, ready])

    def responder(req):
        if req.get_method() == "POST":
            return FakeResponse(json.dumps(submit).encode())
        if "get_result" in req.full_url:
            return FakeResponse(json.dumps(next(polls)).encode())
        return FakeResponse(TINY_PNG, headers={"Content-Type": "image/png"})

    seen = capture(monkeypatch, responder)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    data, ext, seed = run.call_bfl("flux-2-pro", "a dragon", "bfl-key", width=1024, height=768,
                                   seed=42, references=[("a.png", TINY_PNG), ("b.jpg", TINY_JPEG)])
    assert (data, ext, seed) == (TINY_PNG, "png", 42)
    post, poll1, poll2, download = seen
    assert post.full_url == "https://api.bfl.ai/v1/flux-2-pro"
    assert post.get_header("X-key") == "bfl-key"
    assert post.get_header("Content-type") == "application/json"
    assert json.loads(post.data) == {
        "prompt": "a dragon", "width": 1024, "height": 768, "seed": 42, "output_format": "png",
        "input_image": TINY_PNG_B64, "input_image_2": base64.b64encode(TINY_JPEG).decode()}
    for poll in (poll1, poll2):
        assert poll.full_url == submit["polling_url"] and poll.get_method() == "GET"
        assert poll.get_header("X-key") == "bfl-key"
    assert download.full_url == ready["result"]["sample"]
    assert download.get_header("X-key") is None


def test_bfl_kontext_uses_aspect_ratio_and_input_image(monkeypatch):
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    ready = {"status": "Ready", "result": {"sample": "https://delivery.bfl.ai/img.jpg"}}

    def responder(req):
        if req.get_method() == "POST":
            return FakeResponse(json.dumps(submit).encode())
        if "get_result" in req.full_url:
            return FakeResponse(json.dumps(ready).encode())
        return FakeResponse(TINY_JPEG, headers={"Content-Type": "image/jpeg"})

    seen = capture(monkeypatch, responder)
    data, ext, seed = run.call_bfl("flux-kontext-max", "add a hat", "bfl-key", aspect="3:4",
                                   references=[("me.png", TINY_PNG)])
    assert (data, ext, seed) == (TINY_JPEG, "jpg", None)
    assert seen[0].full_url == "https://api.bfl.ai/v1/flux-kontext-max"
    assert json.loads(seen[0].data) == {"prompt": "add a hat", "aspect_ratio": "3:4",
                                        "output_format": "png", "input_image": TINY_PNG_B64}


@pytest.mark.parametrize("status", ["Content Moderated", "Request Moderated"])
def test_bfl_moderation_is_a_clean_error(monkeypatch, status):
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        submit if req.get_method() == "POST" else {"status": status}).encode()))
    with pytest.raises(run.ImageGenError, match="content moderation"):
        run.call_bfl("flux-2-pro", "bad", "bfl-key", width=1024, height=1024)


def test_bfl_error_status(monkeypatch):
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        submit if req.get_method() == "POST" else {"status": "Error", "details": "kaboom"}).encode()))
    with pytest.raises(run.ImageGenError, match="failed"):
        run.call_bfl("flux-2-pro", "x", "bfl-key", width=1024, height=1024)



def test_bfl_poll_loop_honours_a_pre_consumed_budget(monkeypatch):
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    seen = capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        submit if req.get_method() == "POST" else {"status": "Pending"}).encode()))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    # The budget is nearly spent before the submit; one poll fits, then the
    # loop must stop with a clean message rather than run into the executor's
    # SIGKILL.
    clock = iter([1.0, 2.0, 3.0, 11.0, 11.0])
    monkeypatch.setattr(run.time, "monotonic", lambda: next(clock))
    run._deadline = 10.0
    with pytest.raises(run.ImageGenError, match=f"still generating after {run.TOTAL_BUDGET_SECONDS} s"):
        run.call_bfl("flux-2-pro", "x", "bfl-key", width=1024, height=1024)
    assert [r.get_method() for r in seen] == ["POST", "GET"]


def test_budget_exhausted_before_a_request_is_a_clean_error(monkeypatch):
    seen = capture(monkeypatch, lambda req: FakeResponse(b"{}"))
    run._deadline = time.monotonic() - 1
    with pytest.raises(run.ImageGenError, match="budget"):
        run.call_openai("gpt-image-2", "x", "1024x1024", "high", "k")
    assert seen == []


def test_request_timeout_is_capped_by_the_remaining_budget(monkeypatch):
    timeouts = []

    def fake(req, timeout=None):
        timeouts.append(timeout)
        return FakeResponse(json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode())

    monkeypatch.setattr(run._OPENER, "open", fake)
    run._deadline = time.monotonic() + 1000
    run.call_openai("gpt-image-2", "x", "1024x1024", "high", "k")
    run._deadline = time.monotonic() + 5
    run.call_openai("gpt-image-2", "x", "1024x1024", "high", "k")
    assert timeouts[0] == 60 and 0 < timeouts[1] <= 5


# --------------------------------------------------------------------------
# provider-supplied URLs, redirects, response caps
# --------------------------------------------------------------------------

@pytest.mark.parametrize("polling_url", [
    "https://evil.example.com/v1/get_result?id=r",
    "https://api.bfl.ai.evil.example.com/get_result",
    "http://api.bfl.ai/v1/get_result?id=r",
    "https://notbfl.ai/get_result"])
def test_bfl_polling_url_must_be_https_on_bfl_ai(monkeypatch, polling_url):
    seen = capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        {"id": "r", "polling_url": polling_url}).encode()))
    with pytest.raises(run.ImageGenError, match="unexpected polling host"):
        run.call_bfl("flux-2-pro", "x", "bfl-key", width=1024, height=1024)
    assert len(seen) == 1


def test_bfl_sample_url_must_be_https(monkeypatch):
    submit = {"id": "r", "polling_url": "https://api.us1.bfl.ai/v1/get_result?id=r"}
    ready = {"status": "Ready", "result": {"sample": "http://delivery.bfl.ai/img.png"}}
    seen = capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        submit if req.get_method() == "POST" else ready).encode()))
    with pytest.raises(run.ImageGenError, match="sample url"):
        run.call_bfl("flux-2-pro", "x", "bfl-key", width=1024, height=1024)
    assert len(seen) == 2


def test_redirects_are_refused_with_credentials():
    """A real local server: the first hop answers 302 to a second path. The
    tool's opener must surface that as a clean error and never make the
    second request (the default opener would replay the auth headers to it)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/second")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/first"
        with pytest.raises(run.ImageGenError, match=r"redirected \(302\)") as e:
            run._request("GET", url, "test", headers={"x-key": "secret-k"})
        assert "secret-k" not in str(e.value)
    finally:
        srv.shutdown()
    assert hits == ["/first"]


def test_response_over_cap_is_an_error(monkeypatch):
    monkeypatch.setattr(run, "READ_CAP", 10)
    capture(monkeypatch, lambda req: FakeResponse(b"x" * 11))
    with pytest.raises(run.ImageGenError, match="exceeds"):
        run.call_openai("gpt-image-2", "x", "1024x1024", "high", "k")


def test_multipart_filename_is_sanitised(monkeypatch):
    seen = capture(monkeypatch, lambda req: FakeResponse(
        json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode()))
    hostile = 'a"; name="prompt"\r\nX-Injected: 1\x01.png'
    run.call_openai("gpt-image-2", "x", "1024x1024", "high", "k", references=[(hostile, TINY_PNG)])
    parts = parse_multipart(seen[0].data, seen[0].get_header("Content-type"))
    [img] = [p for p in parts if p["filename"] is not None]
    assert img["name"] == "image[]" and img["data"] == TINY_PNG
    assert img["filename"] == "a_; name=_prompt___X-Injected: 1_.png"
    assert b"X-Injected: 1\r\n" not in seen[0].data


# --------------------------------------------------------------------------
# HTTP error mapping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code,needle", [
    (401, "API key invalid"), (403, "API key invalid"), (404, "drift"),
    (429, "quota"), (500, "HTTP 500")])
def test_http_status_mapping(monkeypatch, code, needle):
    body = json.dumps({"error": {"message": "nope"}}).encode()
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(http_error(req, code, body)))
    with pytest.raises(run.ImageGenError) as e:
        run.call_openai("gpt-image-2", "x", "1024x1024", "high", "sk-secret-key")
    assert needle in str(e.value) and "nope" in str(e.value)
    assert "sk-secret-key" not in str(e.value)


def test_error_body_is_capped_and_never_echoes_the_key(monkeypatch):
    body = b"x" * 5000
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(http_error(req, 500, body)))
    with pytest.raises(run.ImageGenError) as e:
        run.call_gemini("gemini-3-pro-image", "x", "1:1", "g-key")
    assert len(str(e.value)) < 400 and "g-key" not in str(e.value)


def test_unreachable_provider(monkeypatch):
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(urllib.error.URLError("dns down")))
    with pytest.raises(run.ImageGenError, match="Could not reach bfl"):
        run.call_bfl("flux-2-pro", "x", "k", width=1024, height=1024)


# --------------------------------------------------------------------------
# generate: the sink files, the sidecar, the summary line
# --------------------------------------------------------------------------

def test_generate_writes_image_and_sidecar(sink, monkeypatch, capsys):
    in_dir, out_dir = sink
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    capture(monkeypatch, lambda req: FakeResponse(
        json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode()))
    rc = run.main({"action": "generate", "prompt": "a cat", "quality": "medium"})
    assert rc == 0
    assert (out_dir / "image.png").read_bytes() == TINY_PNG
    # The sidecar is named for the FULL filename — that is what the executor
    # merges into the file's `meta`; `image.meta.json` would be an orphan.
    meta = json.loads((out_dir / "image.png.meta.json").read_text())
    assert meta["provider"] == "openai" and meta["model"] == "gpt-image-2.5-flare"
    assert meta["seed"] is None and meta["cost_usd"] == run.registry()["gpt-image-2.5-flare"]["price_usd"]
    assert isinstance(meta["duration_ms"], int) and meta["duration_ms"] >= 0
    assert meta["params"] == {"prompt": "a cat", "size": "1024x1024", "quality": "medium",
                              "references": []}
    assert meta["warnings"] == []
    assert sorted(p.name for p in out_dir.iterdir()) == ["image.png", "image.png.meta.json"]
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"ok": True, "model": "gpt-image-2.5-flare",
                                    "cost_usd": meta["cost_usd"]}


def test_generate_reads_references_from_in_dir(sink, monkeypatch):
    in_dir, out_dir = sink
    (in_dir / "face.png").write_bytes(TINY_PNG)
    monkeypatch.setenv("BFL_API_KEY", "bfl-key")
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    ready = {"status": "Ready", "result": {"sample": "https://delivery.bfl.ai/x.jpg", "seed": 5}}

    def responder(req):
        if req.get_method() == "POST":
            return FakeResponse(json.dumps(submit).encode())
        if "get_result" in req.full_url:
            return FakeResponse(json.dumps(ready).encode())
        return FakeResponse(TINY_JPEG, headers={"Content-Type": "image/jpeg"})

    seen = capture(monkeypatch, responder)
    assert run.main({"action": "generate", "model": "flux-2-klein-4b", "prompt": "portrait",
                     "aspect": "3:4", "seed": 5, "references": ["face.png"]}) == 0
    assert json.loads(seen[0].data)["input_image"] == TINY_PNG_B64
    assert (out_dir / "image.jpg").read_bytes() == TINY_JPEG
    meta = json.loads((out_dir / "image.jpg.meta.json").read_text())
    assert meta["seed"] == 5 and meta["params"]["references"] == ["face.png"]
    assert meta["warnings"] == []
    assert meta["params"]["aspect"] == "3:4" and "size" in meta["params"]


def test_seed_on_openai_is_reported_as_ignored(sink, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = capture(monkeypatch, lambda req: FakeResponse(
        json.dumps({"data": [{"b64_json": TINY_PNG_B64}]}).encode()))
    assert run.main({"action": "generate", "prompt": "a cat", "seed": 9}) == 0
    assert "seed" not in json.loads(seen[0].data)
    meta = json.loads((sink[1] / "image.png.meta.json").read_text())
    assert meta["seed"] is None and meta["warnings"] == ["seed ignored by openai"]


def test_generate_rejects_bad_references(sink, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no network")))
    (sink[0] / "notes.txt").write_bytes(b"just text")
    assert run.main({"action": "generate", "prompt": "x", "references": ["notes.txt"]}) == 1
    assert "notes.txt is not a png/jpeg/gif/webp image" in capsys.readouterr().err
    assert run.main({"action": "generate", "prompt": "x", "references": ["../etc/passwd"]}) == 1
    assert "reference" in capsys.readouterr().err
    assert run.main({"action": "generate", "prompt": "x", "references": ["missing.png"]}) == 1
    assert "missing.png" in capsys.readouterr().err
    (sink[0] / "a.png").write_bytes(TINY_PNG)
    monkeypatch.setenv("BFL_API_KEY", "bfl-key")
    assert run.main({"action": "generate", "model": "flux-pro-1.1", "prompt": "x",
                     "references": ["a.png"]}) == 1
    assert "does not accept reference images" in capsys.readouterr().err


def test_generate_validates_model_prompt_quality(sink, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no network")))
    assert run.main({"action": "generate", "prompt": "x", "model": "dalle-99"}) == 1
    assert "unknown model" in capsys.readouterr().err
    assert run.main({"action": "generate", "prompt": "   "}) == 1
    assert "prompt" in capsys.readouterr().err
    assert run.main({"action": "generate", "prompt": "x", "quality": "ultra"}) == 1
    assert "quality" in capsys.readouterr().err
    assert run.main({"action": "bogus"}) == 1


def test_missing_provider_key_is_a_clean_exit(sink, monkeypatch, capsys):
    capture(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no network")))
    for model, provider, block in [("gpt-image-2", "openai", "openai-api-key"),
                                   ("gemini-3-pro-image", "gemini", "gemini-api-key"),
                                   ("flux-2-max", "bfl", "bfl-api-key")]:
        assert run.main({"action": "generate", "model": model, "prompt": "x"}) == 1
        err = capsys.readouterr().err
        assert f"provider {provider} is not configured (add the {block} secret)" in err
        assert "Traceback" not in err
    assert list(sink[1].iterdir()) == []


def test_moderation_refusal_is_a_clean_exit(sink, monkeypatch, capsys):
    monkeypatch.setenv("BFL_API_KEY", "bfl-key")
    submit = {"id": "r", "polling_url": "https://api.bfl.ai/v1/get_result?id=r"}
    capture(monkeypatch, lambda req: FakeResponse(json.dumps(
        submit if req.get_method() == "POST" else {"status": "Request Moderated"}).encode()))
    assert run.main({"action": "generate", "model": "flux-2-pro", "prompt": "x"}) == 1
    captured = capsys.readouterr()
    assert "content moderation" in captured.err and "Traceback" not in captured.err
    assert captured.out == ""
    assert list(sink[1].iterdir()) == []


def test_unexpected_exceptions_exit_cleanly(sink, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret")

    def boom(args, env=None):
        raise RuntimeError("boom " + "y" * 1000)

    monkeypatch.setattr(run, "generate", boom)
    assert run.main({"action": "generate", "prompt": "x"}) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("image_gen: unexpected RuntimeError: boom")
    assert len(captured.err) < 400 and "Traceback" not in captured.err
    assert "sk-very-secret" not in captured.err and captured.out == ""


def test_default_model_falls_back_to_the_first_entry(monkeypatch):
    monkeypatch.setattr(run, "load_models", lambda: [
        {"id": "first-model", "provider": "openai"}, {"id": "second", "provider": "bfl"}])
    assert run.default_model_id() == "first-model"


def test_main_reads_args_from_stdin(sink, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"action": "models"})))
    assert run.main() == 0
    assert json.loads(capsys.readouterr().out)["models"]


# --------------------------------------------------------------------------
# models action
# --------------------------------------------------------------------------

def test_models_action_reports_configured_providers(sink, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-value")
    monkeypatch.setenv("BFL_API_KEY", "   ")
    assert run.main({"action": "models"}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["providers"] == {"openai": {"configured": False, "secret": "openai-api-key"},
                                "gemini": {"configured": True, "secret": "gemini-api-key"},
                                "bfl": {"configured": False, "secret": "bfl-api-key"}}
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["gpt-image-2.5-flare"]["default"] is True
    assert by_id["gpt-image-2.5-flare"]["configured"] is False
    assert by_id["gemini-3-pro-image"]["configured"] is True
    assert "g-secret-value" not in json.dumps(out)
