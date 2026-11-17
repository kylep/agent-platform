"""SVG native-effect retries upload only authorized document SHA assets."""
import httpx
import pytest
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.errors import UiDesignerError

SHA='a'*64
DOC={'schema':1,'pages':[{'id':'page','children':[{'id':'pic','type':'image','asset':SHA,'w':10,'h':10}]}]}


async def test_svg_missing_upload_is_bounded_and_retried_once():
    client=RenderClient('http://renderer');calls=[];uploads=[]
    async def call(method,path,**kw):
        calls.append((method,path,kw['json']))
        return (409,{'missing':[SHA,SHA]}) if len(calls)==1 else (200,{'svg':'<svg/>','box':[0,0,10,10]})
    async def send(session,shas): uploads.append((session,shas))
    client._call=call;client._send_assets=send
    try:
        assert (await client.svg('session',doc=DOC,mode=None))['svg']=='<svg/>'
        assert uploads==[('session',[SHA])] and len(calls)==2
        assert all(c[1]=='/svg' for c in calls)
    finally:await client.aclose()


@pytest.mark.parametrize('missing', [['b'*64],['../../etc/passwd'],['https://example.com/x'],[{}],[SHA]*5001,'not-a-list'])
async def test_svg_unrelated_or_malformed_missing_never_uploads(missing):
    client=RenderClient('http://renderer')
    async def call(*args,**kwargs):return 409,{'missing':missing}
    async def send(*args):raise AssertionError('must not upload')
    client._call=call;client._send_assets=send
    try:
        with pytest.raises(UiDesignerError):await client.svg('session',doc=DOC,mode=None)
    finally:await client.aclose()


async def test_svg_repeated_missing_and_runless_session_refused():
    client=RenderClient('http://renderer');uploads=[]
    async def call(*args,**kwargs):return 409,{'missing':[SHA]}
    async def send(*args):uploads.append(args)
    client._call=call;client._send_assets=send
    try:
        with pytest.raises(UiDesignerError):await client.svg(doc=DOC,mode=None)
        assert not uploads
        with pytest.raises(UiDesignerError):await client.svg('s',doc=DOC,mode=None)
        assert len(uploads)==1
    finally:await client.aclose()
