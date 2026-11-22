"""Focused network-free security and sink contract tests."""
import importlib.util
import io
import json
from pathlib import Path
import urllib.error

import pytest

spec = importlib.util.spec_from_file_location("figma_fetch", Path(__file__).with_name("run.py"))
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FIGMA_TOKEN", "secret-canary")
    monkeypatch.setenv("TOOL_OUT_DIR", str(tmp_path))
    return tmp_path


def test_tree_goes_to_sink_not_stdout(configured, monkeypatch):
    calls = []
    data = {"version": "123", "document": {"children": [{"id": "1:2", "name": "Page", "children": []}]}}
    def api(path, params, token):
        calls.append((path, params, token))
        return data
    monkeypatch.setattr(fetch, "api", api)
    result = fetch.run({"file_key": "abc", "mode": "tree_page", "page_ids": ["1:2"], "version": "123"})
    assert result == {"ok": True, "tree_file": "tree.json", "version": "123"}
    assert json.loads((configured / "tree.json").read_text()) == data
    assert calls == [("/files/abc", {"version": "123", "ids": "1:2", "geometry": "paths"}, "secret-canary")]


def test_asset_batch_sink_and_no_url_or_token(configured, monkeypatch):
    monkeypatch.setattr(fetch, "api", lambda *a: {"meta": {"images": {"hash": "https://s3-alpha.figma.com/image?signature=private"}}})
    monkeypatch.setattr(fetch, "download", lambda url: b"\x89PNG\r\n\x1a\nimage")
    result = fetch.run({"file_key": "abc", "mode": "asset_batch", "image_refs": ["hash", "absent"]})
    assert result["missing"] == ["absent"]
    assert result["files"] == [{"name": "asset-0.bin", "image_ref": "hash"}]
    assert json.loads((configured / "asset-0.bin.meta.json").read_text()) == {"image_ref": "hash"}
    assert "private" not in json.dumps(result)
    assert "secret-canary" not in json.dumps(result)


@pytest.mark.parametrize("url", ["http://s3-alpha.figma.com/a", "https://s3-alpha.figma.com.evil.test/a", "https://127.0.0.1/a", "https://s3-alpha.figma.com:444/a", "https://u:p@s3-alpha.figma.com/a", "https://api.figma.com/a", "https://evil.s3.amazonaws.com/a"])
def test_cdn_ssrf_refused(url):
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download(url)
    assert exc.value.result["error"] == "UD-IMPORT-URL-REFUSED"


def test_redirects_refused_without_credential_forward():
    with pytest.raises(fetch.FetchError):
        fetch.NoRedirect().redirect_request(None, None, 302, "", {}, "https://127.0.0.1/")


@pytest.mark.parametrize("header,expected", [("7200", 7200), ("bad", 60), ("0", 1)])
def test_429_returns_durable_retry_without_sleep(monkeypatch, header, expected):
    class Opener:
        def open(self, req, timeout):
            assert req.get_header("X-figma-token") == "canary"
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {"Retry-After": header}, io.BytesIO(b"secret upstream body"))
    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download("https://api.figma.com/v1/files/abc", token="canary")
    assert exc.value.result == {"ok": False, "error": "UD-IMPORT-RATE-LIMITED", "retry_after_seconds": expected}


def test_stream_read_cap(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size == 11
            return b"x" * size
    class Opener:
        def open(self, *a, **k): return Response()
    monkeypatch.setattr(fetch.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download("https://s3-alpha.figma.com/a", cap=10)
    assert exc.value.result["error"] == "UD-IMPORT-FILE-TOO-BIG"


def test_batch_never_exceeds_eight(configured):
    with pytest.raises(fetch.FetchError):
        fetch.run({"file_key": "abc", "mode": "asset_batch", "image_refs": ["hash"] * 9})
    assert not list(configured.iterdir())


def test_failed_batch_publishes_no_partial_sink(configured, monkeypatch):
    monkeypatch.setattr(fetch, "api", lambda *a: {"images": {"1:1": "first", "1:2": "second"}})
    def download(url):
        if url == "second": raise fetch.FetchError("UD-IMPORT-RATE-LIMITED", retry_after_seconds=30)
        return b"png"
    monkeypatch.setattr(fetch, "download", download)
    with pytest.raises(fetch.FetchError):
        fetch.run({"file_key": "abc", "mode": "references", "node_ids": ["1:1", "1:2"]})
    assert not list(configured.iterdir())


def test_oversize_page_has_frame_fallback(configured, monkeypatch):
    def api(path, params, token):
        if params.get("depth") != 2:
            raise fetch.FetchError("UD-IMPORT-FILE-TOO-BIG")
        return {"document": {"children": [{"id": "1:1", "children": [{"id": "2:1", "name": "Frame"}]}, {"id": "unrelated", "children": [{"id": "9:9", "name": "Other page"}]}]}}
    monkeypatch.setattr(fetch, "api", api)
    with pytest.raises(fetch.FetchError) as exc:
        fetch.run({"file_key": "abc", "mode": "tree_page", "page_ids": ["1:1"]})
    assert exc.value.result == {"ok": False, "error": "UD-IMPORT-PAGE-TOO-BIG", "frames": [{"id": "2:1", "name": "Frame"}]}


def test_api_token_only_goes_to_api_origin():
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download("https://s3-alpha.figma.com/image", token="never-forward")
    assert exc.value.result["error"] == "UD-IMPORT-URL-REFUSED"


def test_eight_assets_use_exactly_eight_sink_slots(configured, monkeypatch):
    refs = [f"hash-{n}" for n in range(8)]
    monkeypatch.setattr(fetch, "api", lambda *a: {"meta": {"images": dict.fromkeys(refs, "https://s3-alpha.figma.com/image")}})
    monkeypatch.setattr(fetch, "download", lambda url: b"png")
    result = fetch.run({"file_key": "abc", "mode": "asset_batch", "image_refs": refs})
    assert len(result["files"]) == 8
    assert len(list(configured.glob("*.bin"))) == 8
    assert len(list(configured.glob("*.meta.json"))) == 8


@pytest.mark.parametrize('url', ['https://s3-alpha.figma.com/\nsecret', 'https://s3-alpha.figma.com:bad/a', 'https://[invalid/a', 'https://s3-alpha.figma.com/' + 'x' * 16384])
def test_malformed_urls_are_refused_with_fixed_error(url):
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download(url)
    assert exc.value.result['error'] == 'UD-IMPORT-URL-REFUSED'


def test_image_fills_accepts_documented_top_level_envelope(configured, monkeypatch):
    monkeypatch.setattr(fetch, 'api', lambda *a: {'images': {'hash': 'https://s3-alpha.figma.com/image'}})
    monkeypatch.setattr(fetch, 'download', lambda url: b'png')
    result = fetch.run({'file_key': 'abc', 'mode': 'asset_batch', 'image_refs': ['hash']})
    assert result['files'][0]['image_ref'] == 'hash'


def test_bad_image_fills_envelope_is_not_silently_treated_as_missing(configured, monkeypatch):
    monkeypatch.setattr(fetch, 'api', lambda *a: {'unexpected': 'upstream error'})
    with pytest.raises(fetch.FetchError) as exc:
        fetch.run({'file_key': 'abc', 'mode': 'asset_batch', 'image_refs': ['hash']})
    assert exc.value.result['error'] == 'UD-IMPORT-BAD-RESPONSE'


@pytest.mark.parametrize('body', [b'[]', b'null', b'{"error":true,"status":500}', b'{"err":"private upstream detail"}'])
def test_api_error_envelopes_are_redacted(monkeypatch, body):
    monkeypatch.setattr(fetch, 'download', lambda *a, **k: body)
    with pytest.raises(fetch.FetchError) as exc:
        fetch.api('/files/abc', {}, 'canary')
    assert exc.value.result == {'ok': False, 'error': 'UD-IMPORT-BAD-RESPONSE'}
