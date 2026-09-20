import base64
import gzip
import json
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from codex_proxy import CLIENT_ID, Config, create_app


def _jwt(exp: int) -> str:
    def part(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return f"{part({'alg': 'none'})}.{part({'exp': exp})}.signature"


def _write_config(tmp_path, server, *, expired=False):
    auth = {"auth_mode": "chatgpt", "tokens": {
        "access_token": _jwt(1 if expired else int(time.time()) + 3600),
        "refresh_token": "refresh-old", "account_id": "acct-1"}}
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(auth))
    internal = tmp_path / "internal"
    internal.write_text("internal-secret")
    return Config(auth_file=auth_file, internal_secret_file=internal,
                  api_url=str(server.make_url("" )).rstrip("/"),
                  upstream=str(server.make_url("/codex")).rstrip("/"),
                  oauth_url=str(server.make_url("/oauth")))


async def test_proxy_replaces_placeholder_headers_and_streams(tmp_path):
    observed = {}

    async def responses(request):
        observed.update(request.headers)
        return web.Response(text='data: {"ok":true}\n\n',
                            content_type="text/event-stream")

    upstream = web.Application()
    upstream.router.add_post("/codex/responses", responses)
    server = TestServer(upstream)
    await server.start_server()
    client = TestClient(TestServer(create_app(_write_config(tmp_path, server))))
    await client.start_server()
    try:
        response = await client.post("/responses", data=b"{}", headers={
            "Authorization": "Bearer agent-platform-placeholder",
            "ChatGPT-Account-Id": "fake", "Content-Type": "application/json"})
        assert response.status == 200
        assert await response.text() == 'data: {"ok":true}\n\n'
        assert observed["Authorization"].startswith("Bearer ey")
        assert observed["ChatGPT-Account-Id"] == "acct-1"
    finally:
        await client.close()
        await server.close()


async def test_proxy_decodes_compressed_model_catalog(tmp_path):
    payload = b'{"data":[{"id":"gpt-test"}]}'

    async def models(request):
        return web.Response(body=gzip.compress(payload), headers={
            "Content-Type": "application/json", "Content-Encoding": "gzip"})

    upstream = web.Application()
    upstream.router.add_get("/codex/models", models)
    server = TestServer(upstream)
    await server.start_server()
    client = TestClient(TestServer(create_app(_write_config(tmp_path, server))))
    await client.start_server()
    try:
        response = await client.get("/models?client_version=0.155.1")
        assert response.status == 200
        assert await response.read() == payload
        assert "Content-Encoding" not in response.headers
    finally:
        await client.close()
        await server.close()


async def test_expired_token_refreshes_once_and_persists(tmp_path):
    calls = {"oauth": 0, "persist": 0}
    fresh = _jwt(int(time.time()) + 3600)

    async def oauth(request):
        calls["oauth"] += 1
        assert await request.json() == {"client_id": CLIENT_ID,
                                        "grant_type": "refresh_token",
                                        "refresh_token": "refresh-old"}
        return web.json_response({"access_token": fresh, "refresh_token": "refresh-new"})

    async def persist(request):
        calls["persist"] += 1
        assert request.headers["X-AP-Internal-Secret"] == "internal-secret"
        saved = json.loads((await request.json())["auth_json"])
        assert saved["tokens"]["access_token"] == fresh
        assert saved["tokens"]["refresh_token"] == "refresh-new"
        return web.json_response({"ok": True})

    async def responses(request):
        assert request.headers["Authorization"] == f"Bearer {fresh}"
        return web.json_response({"ok": True})

    upstream = web.Application()
    upstream.router.add_post("/oauth", oauth)
    upstream.router.add_post("/api/internal/codex-auth", persist)
    upstream.router.add_post("/codex/responses", responses)
    server = TestServer(upstream)
    await server.start_server()
    client = TestClient(TestServer(create_app(
        _write_config(tmp_path, server, expired=True))))
    await client.start_server()
    try:
        assert (await client.post("/responses", data=b"{}")).status == 200
        assert calls == {"oauth": 1, "persist": 1}
    finally:
        await client.close()
        await server.close()


async def test_proxy_rejects_unneeded_paths(tmp_path):
    upstream = TestServer(web.Application())
    await upstream.start_server()
    client = TestClient(TestServer(create_app(_write_config(tmp_path, upstream))))
    await client.start_server()
    try:
        assert (await client.get("/v1/chat/completions")).status == 404
    finally:
        await client.close()
        await upstream.close()


async def test_internal_quota_requires_secret_and_uses_oauth(tmp_path):
    observed = {}

    async def usage(request):
        observed.update(request.headers)
        return web.json_response({"rate_limit": {"allowed": True}})

    upstream = web.Application()
    upstream.router.add_get("/wham/usage", usage)
    server = TestServer(upstream)
    await server.start_server()
    client = TestClient(TestServer(create_app(_write_config(tmp_path, server))))
    await client.start_server()
    try:
        assert (await client.get("/internal/quota")).status == 401
        response = await client.get("/internal/quota", headers={
            "X-AP-Internal-Secret": "internal-secret"})
        assert response.status == 200
        assert (await response.json())["rate_limit"]["allowed"] is True
        assert observed["Authorization"].startswith("Bearer ey")
        assert observed["ChatGPT-Account-Id"] == "acct-1"
    finally:
        await client.close()
        await server.close()
