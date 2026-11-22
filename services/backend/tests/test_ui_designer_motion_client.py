import base64
import hashlib
import io
import copy
import pytest
from PIL import Image
from agentplatform.ui_designer import motion,svg
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.errors import UiDesignerError


def manifest():
    buf=io.BytesIO();Image.new('RGBA',(64,64),'red').save(buf,'PNG');raw=buf.getvalue();key=hashlib.sha256(raw).hexdigest();encoded=base64.b64encode(raw).decode()
    pose={'x':0,'y':0,'w':64,'h':64,'rotate':0,'opacity':1,'asset':key}
    return {'ok':True,'motion_version':'motion-1','renderer_version':svg.renderer_version(),'width':64,'height':64,'box':[0,0,64,64],'duration_ms':800,'fps':24,'frames':19,'easing':'linear','mode':None,'from_image':encoded,'to_image':encoded,'assets':{key:encoded},'layers':[{'id':'root','from':pose,'to':pose}],'warnings':[]}


async def test_motion_preview_forwards_pinned_options_and_strips_internal_ok():
    client=RenderClient('http://renderer');seen=[]
    async def call(*args,**kw):seen.append((args,kw));return 200,manifest()
    client._call=call
    try:
        out=await client.motion_preview(None,doc={},from_node='a',to_node='b',easing='linear')
        assert 'ok' not in out and out['motion_version']=='motion-1'
        assert seen[0][0]==('POST','/motion/preview') and seen[0][1]['timeout_s']==70
    finally:await client.aclose()


@pytest.mark.parametrize('mutate',[
 lambda m:m.update(renderer_version='old'),lambda m:m.update(width=True),lambda m:m.update(frames=True),
 lambda m:m['layers'][0]['from'].update(asset='https://example.com'),lambda m:m['layers'][0]['to'].update(opacity=2),
 lambda m:m.update(from_image='data:image/png;base64,x'),lambda m:m.update(layers=m['layers']*65),
 lambda m:m['assets'].update({'b'*64:next(iter(m['assets'].values()))}),
])
async def test_motion_client_rejects_untrusted_or_overbudget_manifest(mutate):
    m=manifest();mutate(m);client=RenderClient('http://renderer')
    async def call(*args,**kwargs):return 200,m
    client._call=call
    try:
        with pytest.raises(UiDesignerError):await client.motion_preview(None,doc={},from_node='a',to_node='b')
    finally:await client.aclose()


async def test_motion_client_never_uploads_unrelated_assets():
    client=RenderClient('http://renderer')
    async def call(*args,**kwargs):return 409,{'missing':['b'*64]}
    async def send(*args):raise AssertionError('unrelated upload')
    client._call=call;client._send_assets=send
    try:
        with pytest.raises(UiDesignerError):await client.motion_preview(None,doc={},from_node='a',to_node='b')
    finally:await client.aclose()
