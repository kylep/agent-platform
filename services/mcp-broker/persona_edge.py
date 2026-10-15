"""Separate web-only listener; the internal SPIRE listener stays unchanged."""
from __future__ import annotations
import os
import threading

import httpx
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import Response

import broker


# The edge authenticates before MCP initialization. Discovery and execution
# also re-check each request: cached MCP session ids carry no authority.
from fastmcp.server.middleware import Middleware as MCPMiddleware
from fastmcp.exceptions import ToolError


class LocalPersonaPolicy(MCPMiddleware):
    async def on_request(self, context, call_next):
        headers = broker._caller_headers()
        if headers.get('Authorization', '').startswith('Bearer lps_'):
            ident = await broker._whoami()
            if ident is None:
                raise ToolError('persona authority unavailable; synchronize or resume')
            grants = set(ident.get('tools') or []) | {'mcp__platform__local_session_history'}
            if context.method == 'tools/call':
                if f'mcp__platform__{context.message.name}' not in grants:
                    raise ToolError('tool is not granted to this persona session')
            if context.method in ('resources/read', 'prompts/get'):
                raise ToolError('resource is unavailable to local personas')
            result = await call_next(context)
            if context.method == 'tools/list':
                return [t for t in result if f'mcp__platform__{t.name}' in grants]
            if context.method in ('resources/list', 'resources/templates/list', 'prompts/list'):
                return []
            return result
        return await call_next(context)


broker.mcp.add_middleware(LocalPersonaPolicy())


@broker.mcp.tool
async def local_session_history(after: int = 0) -> str:
    """Read up to 50 recorded events from this local session only, after a
    sequence number. Device-reported history, including tool activity; no
    other session or persona may be selected. Use memory for durable identity.
    """
    return await broker._call('GET', '/api/local-persona/current/history', {'after': max(0, after)})


class PersonaIngress:
    def __init__(self, app, auth_client=None):
        self.app = app
        self.auth_client = auth_client or httpx.AsyncClient(base_url=broker._API, timeout=5)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        bearer = Headers(scope=scope).get('authorization', '')
        if not bearer.startswith('Bearer lps_'):
            await Response(status_code=401)(scope, receive, send)
            return
        try:
            response = await self.auth_client.get('/api/whoami', headers={'Authorization': bearer})
        except httpx.HTTPError:
            await Response(status_code=503)(scope, receive, send)
            return
        if response.status_code != 200:
            await Response(status_code=response.status_code)(scope, receive, send)
            return
        clean = dict(scope)
        # Transport headers only. Cookies, workload/run credentials and all
        # caller-controlled identity headers are discarded before FastMCP.
        allowed = {b'authorization', b'accept', b'content-type', b'content-length',
                   b'mcp-session-id', b'mcp-protocol-version', b'last-event-id', b'host'}
        clean['headers'] = [(k, v) for k, v in scope.get('headers', []) if k.lower() in allowed]
        await self.app(clean, receive, send)


def app(auth_client=None):
    return broker.mcp.http_app(path='/persona/mcp',
        middleware=[Middleware(PersonaIngress, auth_client=auth_client)], stateless_http=True)


if __name__ == '__main__':
    import uvicorn
    broker.refresh_custom_tools()
    threading.Thread(target=broker._refresh_forever, daemon=True).start()
    uvicorn.run(app(), host=os.environ.get('AP_BIND_HOST', '0.0.0.0'), port=8001)
