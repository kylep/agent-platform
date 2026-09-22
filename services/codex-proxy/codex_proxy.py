"""Streaming, refresh-aware credential broker for Codex subscription traffic."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

log = logging.getLogger("codex_proxy")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ROUTES = {("GET", "/models"), ("GET", "/responses"),
                ("POST", "/responses"), ("POST", "/images/generations"),
                # Codex 0.155+ executes its standalone web tool against the
                # configured provider base instead of embedding it in a
                # Responses turn. This maps to /backend-api/codex/alpha/search
                # upstream and remains a single-purpose, authenticated route.
                ("POST", "/alpha/search")}
# Codex's built-in subscription tools (including ImageGen) are exposed by the
# ChatGPT backend as an authenticated MCP endpoint. Keep this exact allowlist:
# runners get the capability, not a general-purpose proxy into backend-api.
CHATGPT_ROUTES = {("POST", "/backend-api/ps/mcp")}
CHATGPT_READ_ROUTES = {
    ("GET", "/backend-api/plugins/featured"),
    ("GET", "/backend-api/ps/plugins/installed"),
    ("GET", "/backend-api/ps/plugins/list"),
    ("GET", "/backend-api/ps/plugins/suggested/codex"),
    ("GET", "/backend-api/wham/settings/user"),
}
ALLOWED = CODEX_ROUTES | CHATGPT_ROUTES | CHATGPT_READ_ROUTES
DROP_REQUEST = {
    "authorization", "chatgpt-account-id", "connection", "content-length",
    "cookie", "host", "openai-organization", "openai-project", "proxy-authorization",
    "transfer-encoding", "upgrade", "x-api-key", "x-forwarded-for", "x-forwarded-host",
    "x-forwarded-proto",
}
DROP_RESPONSE = {
    "connection", "content-encoding", "content-length", "proxy-authenticate", "set-cookie",
    "transfer-encoding", "upgrade",
}


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    auth_file: Path = Path("/secrets/codex/auth.json")
    internal_secret_file: Path = Path("/secrets/internal/quota")
    api_url: str = "http://agent-platform-api:8000"
    upstream: str = "https://chatgpt.com/backend-api/codex"
    oauth_url: str = "https://auth.openai.com/oauth/token"
    refresh_window_seconds: int = 300
    # Four 8 MiB artifact references become roughly 43 MiB as base64 inside
    # an ImageGen request. Leave enough envelope without an unbounded buffer.
    max_request_bytes: int = 48 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            auth_file=Path(os.environ.get("CODEX_AUTH_FILE", str(cls.auth_file))),
            internal_secret_file=Path(os.environ.get(
                "AP_INTERNAL_SECRET_FILE", str(cls.internal_secret_file))),
            api_url=os.environ.get("AP_API_URL", cls.api_url).rstrip("/"),
            upstream=os.environ.get("CODEX_UPSTREAM", cls.upstream).rstrip("/"),
            oauth_url=os.environ.get("CODEX_OAUTH_URL", cls.oauth_url),
            refresh_window_seconds=int(os.environ.get(
                "CODEX_REFRESH_WINDOW_SECONDS", cls.refresh_window_seconds)),
            max_request_bytes=int(os.environ.get(
                "CODEX_MAX_REQUEST_BYTES", cls.max_request_bytes)),
        )


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        value = json.loads(base64.urlsafe_b64decode(part))
    except Exception as exc:
        raise CredentialError("Codex access token is not a JWT") from exc
    if not isinstance(value, dict):
        raise CredentialError("Codex access token has invalid claims")
    return value


def _validate_auth(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("auth_mode") != "chatgpt":
        raise CredentialError("Codex auth must use ChatGPT login")
    tokens = value.get("tokens")
    required = ("access_token", "refresh_token", "account_id")
    if not isinstance(tokens, dict) or any(
            not isinstance(tokens.get(k), str) or not tokens[k] for k in required):
        raise CredentialError("Codex auth is missing required token fields")
    _jwt_payload(tokens["access_token"])
    return deepcopy(value)


class Credentials:
    """Own the only in-memory OAuth copy and serialize refresh-token rotation."""

    def __init__(self, config: Config, session: ClientSession):
        self.config = config
        self.session = session
        self.value = _validate_auth(json.loads(config.auth_file.read_text()))
        self.lock = asyncio.Lock()
        self.dirty = False

    def _expires_at(self) -> int:
        exp = _jwt_payload(self.value["tokens"]["access_token"]).get("exp", 0)
        return int(exp) if isinstance(exp, (int, float)) else 0

    async def current(self, *, force_refresh: bool = False) -> tuple[str, str]:
        async with self.lock:
            if self.dirty:
                await self._persist()
            if (force_refresh or self._expires_at()
                    <= time.time() + self.config.refresh_window_seconds):
                await self._refresh()
            tokens = self.value["tokens"]
            return tokens["access_token"], tokens["account_id"]

    async def _refresh(self) -> None:
        old = self.value["tokens"]
        try:
            async with self.session.post(self.config.oauth_url, json={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": old["refresh_token"],
            }) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
                    raise CredentialError(
                        f"Codex OAuth refresh failed ({response.status}, {code or 'unknown'})")
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError("Codex OAuth refresh request failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
            raise CredentialError("Codex OAuth refresh returned invalid JSON")
        updated = deepcopy(self.value)
        for key in ("access_token", "refresh_token", "id_token"):
            if isinstance(payload.get(key), str) and payload[key]:
                updated["tokens"][key] = payload[key]
        updated["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.value = _validate_auth(updated)
        self.dirty = True
        await self._persist()
        log.info("Codex OAuth token refreshed and persisted")

    async def _persist(self) -> None:
        try:
            secret = self.config.internal_secret_file.read_text().strip()
        except OSError as exc:
            raise CredentialError("internal persistence credential is unavailable") from exc
        if not secret:
            raise CredentialError("internal persistence credential is empty")
        try:
            async with self.session.post(
                f"{self.config.api_url}/api/internal/codex-auth",
                headers={"X-AP-Internal-Secret": secret},
                json={"auth_json": json.dumps(self.value, separators=(",", ":"))},
            ) as response:
                await response.read()
                if response.status != 200:
                    raise CredentialError(
                        f"Codex credential persistence failed ({response.status})")
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError("Codex credential persistence request failed") from exc
        self.dirty = False


async def _stream(request: web.Request, response) -> web.StreamResponse:
    headers = {k: v for k, v in response.headers.items()
               if k.lower() not in DROP_RESPONSE}
    outgoing = web.StreamResponse(status=response.status, headers=headers)
    await outgoing.prepare(request)
    try:
        async for chunk in response.content.iter_any():
            await outgoing.write(chunk)
        await outgoing.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return outgoing


async def _websocket(request: web.Request, credentials: Credentials,
                     session: ClientSession) -> web.WebSocketResponse:
    """Tunnel Codex's first-party Responses WebSocket through the broker."""
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in DROP_REQUEST
               and not k.lower().startswith("sec-websocket-")}
    protocols = [part.strip() for part in
                 request.headers.get("Sec-WebSocket-Protocol", "").split(",")
                 if part.strip()]
    config: Config = request.app["config"]
    upstream_url = config.upstream.replace("https://", "wss://", 1).replace(
        "http://", "ws://", 1) + str(request.rel_url)

    async def connect(force_refresh: bool = False):
        token, account = await credentials.current(force_refresh=force_refresh)
        outgoing = {**headers, "Authorization": f"Bearer {token}",
                    "ChatGPT-Account-Id": account}
        return await session.ws_connect(upstream_url, headers=outgoing,
                                        protocols=protocols)

    try:
        try:
            upstream = await connect()
        except Exception as exc:
            if getattr(exc, "status", None) != 401:
                raise
            upstream = await connect(force_refresh=True)
        selected = [upstream.protocol] if upstream.protocol else protocols
        client = web.WebSocketResponse(protocols=selected)
        await client.prepare(request)

        async def relay(source, target):
            async for message in source:
                if message.type == web.WSMsgType.TEXT:
                    await target.send_str(message.data)
                elif message.type == web.WSMsgType.BINARY:
                    await target.send_bytes(message.data)
                elif message.type in (web.WSMsgType.CLOSE, web.WSMsgType.CLOSED,
                                      web.WSMsgType.ERROR):
                    break

        left = asyncio.create_task(relay(client, upstream))
        right = asyncio.create_task(relay(upstream, client))
        done, pending = await asyncio.wait({left, right},
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await upstream.close()
        await client.close()
        for task in done:
            task.result()
        return client
    except CredentialError as exc:
        log.error("credential broker failure: %s", exc)
        raise web.HTTPBadGateway(text="Codex credential broker unavailable") from None


async def proxy(request: web.Request) -> web.StreamResponse:
    if (request.method, request.path) not in ALLOWED:
        # Codex telemetry is intentionally outside the capability allowlist
        # and is expected once per turn; keep unexpected route drift visible
        # without filling the broker log with that known refusal.
        report = log.debug if request.path.endswith("/analytics-events/events") else log.warning
        report("rejected proxy route method=%s path=%s", request.method, request.path)
        raise web.HTTPNotFound()
    body = await request.read()
    credentials: Credentials = request.app["credentials"]
    session: ClientSession = request.app["session"]
    if request.method == "GET" and request.path == "/responses" \
            and request.headers.get("Upgrade", "").lower() == "websocket":
        return await _websocket(request, credentials, session)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in DROP_REQUEST}

    async def send(force_refresh: bool = False):
        token, account = await credentials.current(force_refresh=force_refresh)
        outgoing = {**headers, "Authorization": f"Bearer {token}",
                    "ChatGPT-Account-Id": account}
        upstream_root = request.app["config"].upstream
        upstream_path = str(request.rel_url)
        if ((request.method, request.path) in CHATGPT_ROUTES | CHATGPT_READ_ROUTES):
            # Codex's default chatgpt_base_url is /backend-api/. Its MCP
            # client appends /ps/mcp to that base, while model traffic uses
            # the sibling /backend-api/codex base.
            upstream_root = upstream_root.removesuffix("/codex")
            upstream_path = str(request.rel_url).removeprefix("/backend-api")
        return await session.request(
            request.method, upstream_root + upstream_path,
            headers=outgoing, data=body, allow_redirects=False)

    try:
        upstream = await send()
        if upstream.status == 401:
            await upstream.read()
            upstream.release()
            upstream = await send(force_refresh=True)
        if upstream.status >= 400:
            log.warning("upstream request failed method=%s path=%s status=%d",
                        request.method, request.path, upstream.status)
        try:
            return await _stream(request, upstream)
        finally:
            upstream.release()
    except CredentialError as exc:
        log.error("credential broker failure: %s", exc)
        raise web.HTTPBadGateway(text="Codex credential broker unavailable") from None


async def health(request: web.Request) -> web.Response:
    credentials: Credentials = request.app["credentials"]
    if credentials.dirty:
        raise web.HTTPServiceUnavailable(text="credential persistence pending")
    try:
        await credentials.current()
    except CredentialError:
        raise web.HTTPServiceUnavailable(text="credential unavailable") from None
    return web.json_response({"ok": True})


async def quota(request: web.Request) -> web.Response:
    """Private usage probe for the API; never exposed to runner workloads."""
    config: Config = request.app["config"]
    try:
        expected = config.internal_secret_file.read_text().strip()
    except OSError:
        raise web.HTTPServiceUnavailable(text="internal credential unavailable") from None
    presented = request.headers.get("X-AP-Internal-Secret", "")
    if not expected or not secrets.compare_digest(presented, expected):
        raise web.HTTPUnauthorized(text="unauthorized")
    credentials: Credentials = request.app["credentials"]
    session: ClientSession = request.app["session"]

    async def send(force_refresh: bool = False):
        token, account = await credentials.current(force_refresh=force_refresh)
        # ChatGPT's current Codex client uses the WHAM usage route while model
        # traffic remains under /codex. Keep both behind the same OAuth owner.
        usage_url = (config.upstream.removesuffix("/codex") + "/wham/usage"
                     if config.upstream.endswith("/codex")
                     else config.upstream + "/usage")
        return await session.get(usage_url, headers={
            "Authorization": f"Bearer {token}", "ChatGPT-Account-Id": account,
            "User-Agent": "codex-cli", "Accept": "application/json",
        }, allow_redirects=False)

    try:
        upstream = await send()
        if upstream.status == 401:
            await upstream.read()
            upstream.release()
            upstream = await send(force_refresh=True)
        try:
            body = await upstream.read()
            return web.Response(body=body, status=upstream.status,
                                content_type="application/json")
        finally:
            upstream.release()
    except CredentialError as exc:
        log.error("credential broker failure: %s", exc)
        raise web.HTTPBadGateway(text="Codex credential broker unavailable") from None


def create_app(config: Config | None = None) -> web.Application:
    config = config or Config.from_env()

    async def context(app):
        session = ClientSession(timeout=ClientTimeout(
            total=None, connect=30, sock_connect=30, sock_read=None))
        app["session"] = session
        app["credentials"] = Credentials(config, session)
        yield
        await session.close()

    app = web.Application(client_max_size=config.max_request_bytes)
    app["config"] = config
    app.cleanup_ctx.append(context)
    app.router.add_get("/healthz", health)
    app.router.add_get("/internal/quota", quota)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000, access_log=None)
