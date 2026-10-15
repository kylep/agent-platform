"""Isolated real FastMCP runtime; existing broker unit tests stub that module."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import broker
import persona_edge


async def main():
    grants = ['mcp__platform__relay']
    observed = []
    async def identity():
        from fastmcp.server.dependencies import get_http_request
        request = get_http_request()
        observed.append(dict(request.headers))
        return {'agent':'alice', 'tools':grants, 'run_id':'r1'}
    broker._whoami = identity
    async def upstream(request):
        return httpx.Response(200 if request.headers.get('authorization') == 'Bearer lps_test' else 401)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url='http://api') as auth:
        app = persona_edge.app(auth_client=auth)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
                headers = {'Accept':'application/json, text/event-stream', 'Authorization':'Bearer lps_test',
                    'Cookie':'ap_session=admin', 'X-AP-Run-Token':'forged', 'X-AP-Tool-Call':'forged'}
                async def rpc(method, params=None, credentials=headers):
                    return await c.post('/persona/mcp', headers=credentials, json={
                        'jsonrpc':'2.0', 'id':1, 'method':method, 'params':params or {}})
                result = await rpc('tools/list', credentials={'Accept':'application/json, text/event-stream'})
                assert result.status_code == 401 and 'relay' not in result.text
                result = await rpc('tools/list')
                assert result.status_code == 200, result.text
                def data(response):
                    if response.headers.get('content-type','').startswith('application/json'):
                        return response.json()
                    return json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
                listed = data(result)['result']['tools']
                assert sorted(t['name'] for t in listed) == ['local_session_history', 'relay'], listed
                assert all('cookie' not in h and 'x-ap-run-token' not in h and 'x-ap-tool-call' not in h for h in observed)
                # Direct tools/call never relies on the discovery menu.
                result = await rpc('tools/call', {'name':'agents_grant', 'arguments':{'action':'get','name':'alice'}})
                assert data(result).get('error') or data(result)['result'].get('isError'), result.text
                grants.clear()
                assert [t['name'] for t in data(await rpc('tools/list'))['result']['tools']] == ['local_session_history']
                for bearer in ('ap_test','lpe_test','lpr_test'):
                    bad = {**headers, 'Authorization':'Bearer '+bearer}
                    assert (await rpc('tools/list', credentials=bad)).status_code == 401
    print('real FastMCP discovery/call/ingress boundaries passed')

asyncio.run(main())
