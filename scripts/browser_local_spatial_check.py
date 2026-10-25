"""Local spatial GPU/CPU parity, real NEF hot sliders, native crop zoom and lifecycle."""
import argparse
import json
import sys
from io import BytesIO
from pathlib import Path
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from darkroom.imaging import Adjustments,render
from darkroom.masking import bake_masks,packed_weights,SPATIAL_KEYS
from darkroom.structure import packed,packed_sharpness


def parity(browser,args):
    page=browser.new_page();page.goto(args.url)
    y,x=np.mgrid[:144,:192];v=.27+.10*np.sin(x*.15)+.045*np.sin(x*1.2)+.025*np.cos(y*.9)
    raw=(np.stack([v,v*.85,v*.7],axis=-1)*65535).astype(np.uint16)
    max_error=0
    for count in [1,8]:
        for key in SPATIAL_KEYS:
            for sign in [-80,80]:
                settings=Adjustments(exposure=.2,contrast=8,texture=20,masks=[{'kind':'gradient','opacity':.7,'adjustments':{'exposure':.1,key:sign if i%2==0 else -sign/2}} for i in range(count)])
                color=[m.model_copy(update={'adjustments':m.adjustments.model_copy(update={k:0 for k in SPATIAL_KEYS})}) for m in settings.masks]
                source=bake_masks(raw,color)
                data={'rgb':raw.ravel().tolist(),'masked':source.ravel().tolist(),'fields':packed(raw,full_size=(192,144)).ravel().tolist(),'sharp':packed_sharpness(raw).ravel().tolist(),'weights':packed_weights(raw,settings.masks,full_size=(192,144)).ravel().tolist(),'planes':(count+3)//4,'settings':settings.model_dump()}
                actual=page.evaluate('''async data=>{
                  const {GpuRenderer}=await import('/static/gpu.js');const c=document.createElement('canvas');c.width=192;c.height=144;
                  const g=new GpuRenderer(c),meta={width:192,height:144},u=pixels=>({...meta,pixels:new Uint16Array(pixels)});
                  g.load(u(data.rgb),meta);g.loadMasked(u(data.masked));g.loadStructure(u(data.fields));g.loadSharpness(u(data.sharp));
                  g.loadLocal({...meta,planes:data.planes,pixels:new Uint8Array(data.weights)});g.localEnabled=true;
                  data.settings.masks.forEach((m,i)=>['dehaze','clarity','texture','sharpness'].forEach((k,j)=>g.localAmounts[i*4+j]=m.adjustments[k]*m.opacity));
                  g.draw(data.settings,{origin:[0,0],offset:[0,0],scale:1});const p=new Uint8Array(192*144*4);g.gl.readPixels(0,0,192,144,g.gl.RGBA,g.gl.UNSIGNED_BYTE,p);
                  const error=g.gl.getError();g.dispose();if(error)throw Error('GL '+error);return Array.from(p);
                }''',data)
                actual=np.array(actual,dtype=np.uint8).reshape(144,192,4)[::-1,:,:3].astype(int)
                expected=np.asarray(render(raw,settings)).astype(int);error=np.abs(actual-expected).max();max_error=max(max_error,int(error))
                assert error<=2,(count,key,sign,error)
    page.close();print(f'Local spatial GPU/CPU parity: 16 combinations, max {max_error}/255',flush=True)


def check(browser,args,cpu=False):
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1.5)
    page.add_init_script('''window.__glCounts={textures:0,framebuffers:0,buffers:0,programs:0};
          for(const [name,key] of [['Texture','textures'],['Framebuffer','framebuffers'],['Buffer','buffers'],['Program','programs']]){
            const p=WebGL2RenderingContext.prototype,create=p['create'+name],remove=p['delete'+name];
            p['create'+name]=function(...a){const r=create.apply(this,a);if(r)window.__glCounts[key]++;return r;};
            p['delete'+name]=function(r){if(r)window.__glCounts[key]--;return remove.call(this,r);};
          }''')
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];requests=[];values=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def request(r):
        if '/api/photos/' in r.url:requests.append(r)
        if r.method=='POST' and '/api/photos/' in r.url:values.append(r.post_data_json)
    page.on('request',request);page.goto(args.url);page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    with page.expect_response(lambda r:r.url.endswith('/api/photos') and r.request.method=='POST') as upload:page.locator('#file').set_input_files(args.nef)
    photo=upload.value.json();page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def adjust(key,value):
        e=page.locator('#'+key);e.fill(str(value));e.dispatch_event('input');e.dispatch_event('change')
    def settle():page.wait_for_load_state('networkidle',timeout=120000);page.wait_for_timeout(350)
    def prepared():
        settle()
        if not cpu:
            page.wait_for_function("document.getElementById('preview').dataset.localReady==='true'",timeout=120000)
    page.locator('#mask-kind').select_option('radial');page.locator('#mask-add').click();page.locator('#mask-show').uncheck()
    for key in SPATIAL_KEYS:
        assert page.locator('#local-'+key).input_value()=='0'
        adjust('local-'+key,55)
    prepared()
    if not cpu:
        before=len(requests)
        for value in [-100,-40,0,40,100]:
            for key in SPATIAL_KEYS:adjust('local-'+key,value)
        settle();assert len(requests)==before,[r.url for r in requests[before:]]
    for key in SPATIAL_KEYS:
        page.locator('#local-'+key).dispatch_event('dblclick');assert page.locator('#local-'+key).input_value()=='0'
    adjust('local-texture',60);adjust('local-sharpness',75)
    page.locator('#preset-name').fill('local spatial');page.locator('#save-preset').click()
    saved=page.evaluate("JSON.parse(localStorage.getItem('mydarkroom.presets.v1')).at(-1).values")
    mask=next(v for v in reversed(values) if v.get('masks'))['masks'][0]
    assert 'masks' not in saved  # Global presets preserve photo-specific masks.
    page.locator('#preset').select_option('mono')
    assert page.locator('#local-texture').input_value()=='60' and page.locator('#local-sharpness').input_value()=='75'
    adjust('local-sharpness',30);page.locator('#undo').click();assert page.locator('#local-sharpness').input_value()=='75'
    adjust('geo-rotation',5);adjust('geo-vertical',.1)
    page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
    page.locator('#zoom').click();page.wait_for_function("Boolean(document.getElementById('preview').dataset.detail)",timeout=120000)
    prepared()
    if not cpu:
        page.wait_for_function("document.getElementById('preview').dataset.localDetailReady==='true'",timeout=120000)
        settle();before=len(requests)
        for value in [-75,0,45,100]:adjust('local-texture',value);adjust('local-sharpness',value)
        settle();assert len(requests)==before,[r.url for r in requests[before:]]
    # Native output pixel after crop/rotation matches the CPU/export pipeline.
    state=next(v for v in reversed(values) if 'geometry' in v)
    local={**mask['adjustments'],**{k:float(page.locator('#local-'+k).input_value()) for k in SPATIAL_KEYS}}
    state={**state,'masks':[{**mask,'adjustments':local}]}
    v=json.loads(page.locator('#preview').get_attribute('data-view'));pixels=page.locator('#preview').evaluate('c=>[c.width,c.height]');mx,my=pixels[0]//2,pixels[1]//2
    ox=int(v['origin'][0]+mx+.5-v['offset'][0]);oy=int(v['origin'][1]+my+.5-v['offset'][1])
    response=page.request.post(f"{args.url}/api/photos/{photo['id']}/viewport?x={ox}&y={oy}&width=1&height=1",data=state);assert response.status==200
    expected=np.asarray(Image.open(BytesIO(response.body())))[0,0].astype(int)
    actual=page.locator('#geometry-view' if cpu else '#preview').evaluate("""(c,{x,y,cpu})=>{if(cpu)return [...c.getContext('2d').getImageData(x,y,1,1).data].slice(0,3);const gl=c.getContext('webgl2'),p=new Uint8Array(4);gl.readPixels(x,c.height-y-1,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return [...p].slice(0,3)}""",{'x':mx,'y':my,'cpu':cpu})
    assert np.max(np.abs(np.array(actual)-expected))<=5,(actual,expected)
    adjust('zoom-scale',200);assert json.loads(page.locator('#preview').get_attribute('data-view'))['scale']==2
    with page.expect_download(timeout=120000) as download:
        page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-start').click()
    image=Image.open(BytesIO(Path(download.value.path()).read_bytes()));assert max(image.size)==2000
    # Exercise repeated weight/detail replacement; monitor live GL resource counts.
    if not cpu:
        counts=[]
        for _ in range(3):
            page.locator('#fit').click();page.locator('#mask-invert').check();prepared();page.locator('#mask-invert').uncheck();prepared()
            page.locator('#zoom').click();page.wait_for_function("document.getElementById('preview').dataset.localDetailReady==='true'",timeout=120000);settle()
            counts.append(page.evaluate('window.__glCounts'))
        assert counts[0]==counts[1]==counts[2],counts
        print('Steady GL resources after repeated fit/invert/native detail:',counts,flush=True)
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': local spatial sliders, reset, undo, preset, crop 200%, JPEG and hot path passed',flush=True)
    page.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nef',required=True);p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--chromium',default='/usr/bin/chromium');args=p.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=args.chromium,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader'])

        parity(browser,args);check(browser,args);check(browser,args,True);browser.close()
