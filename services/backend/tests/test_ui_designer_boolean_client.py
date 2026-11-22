import json

import httpx
import pytest

from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.svg import renderer_version
from agentplatform.ui_designer.errors import UiDesignerError


OPERANDS=[{'type':'rect','x':0,'y':0,'w':10,'h':10},{'type':'rect','x':5,'y':0,'w':10,'h':10}]


def response():
    return {'ok':True,'renderer_version':renderer_version(),'geometry':{'x':0,'y':0,'w':15,'h':10,
        'd':'M0 0H15V10H0Z','empty':False},'warnings':[]}


async def test_boolean_client_forwards_geometry_and_checks_version():
    seen=[]
    def handle(request):
        seen.append((request.url.path,json.loads(request.content)))
        return httpx.Response(200,json=response())
    client=RenderClient('http://renderer',client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    try:
        result=await client.boolean_geometry('union',OPERANDS)
        assert result['geometry']['w']==15
        assert seen==[('/boolean',{'kind':'union','operands':OPERANDS})]
    finally: await client.aclose()


@pytest.mark.parametrize('mutate',[
    lambda r:r.update(renderer_version='old'),
    lambda r:r.update(ok=False),
    lambda r:r['geometry'].update(w=float('nan')),
    lambda r:r['geometry'].update(w=0),
    lambda r:r['geometry'].update(d='M0 0junk'),
    lambda r:r['geometry'].update(href='https://example.com'),
    lambda r:r.update(warnings=['界'*200]),
])
async def test_boolean_client_rejects_malformed_worker_result(mutate):
    data=response();mutate(data)
    async def call(*args,**kwargs): return 200,data
    client=RenderClient('http://renderer')
    client._call=call
    try:
        with pytest.raises(UiDesignerError) as exc: await client.boolean_geometry('union',OPERANDS)
        assert exc.value.code=='UD-RENDER-FAILED'
    finally: await client.aclose()
