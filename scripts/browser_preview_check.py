"""GPU geometry parity, zero JPEG roundtrips, manual auto-crop and default panels."""
import argparse
from io import BytesIO
from pathlib import Path
import sys
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from darkroom.geometry import Geometry,forward_matrix,auto_crop,apply_geometry
from darkroom.imaging import Adjustments,render


def math_and_pixels(page):
    rng=np.random.default_rng(17)
    for i in range(16):
        size=(640,480) if i%2 else (480,720)
        g=Geometry(rotation=float(rng.uniform(-15,15)),vertical=float(rng.uniform(-.35,.35)),horizontal=float(rng.uniform(-.25,.25)))
        if i%3==0:g.upright=[1,.02,0,-.01,1,0,.03,-.04,1]
        result=page.evaluate("""async({size,g})=>{const m=await import('/static/geometry-math.js');return {matrix:m.forwardMatrix(size,g),crop:m.autoCrop(size,g)};}""",{'size':size,'g':g.model_dump()})
        assert np.allclose(result['matrix'],forward_matrix(size,g).ravel(),atol=1e-9)
        assert np.allclose(result['crop'],auto_crop(size,g),atol=1e-6)
    odd=page.evaluate("""async()=>{const {gpuGeometry}=await import('/static/geometry-math.js');return gpuGeometry([601,400],{rotation:0,vertical:0,horizontal:0,upright:[1,0,0,0,1,0,0,0,1],crop:[.5,0,.5,1]});}""")
    assert odd['cropOrigin']==[300,0] and odd['size']==[301,400]
    # A smooth sensor fixture isolates coordinate correctness from edge resampling differences.
    y,x=np.mgrid[:240,:320];rgb=np.stack([8000+x*70,7000+y*110,12000+x*30+y*35],axis=-1).astype(np.uint16)
    for g in [Geometry(),Geometry(rotation=7),Geometry(rotation=-4,vertical=.2,horizontal=-.1,upright=[1,.015,0,-.01,1,0,.02,-.03,1])]:
        g.crop=tuple(auto_crop((320,240),g)) if g.rotation else (0,0,1,1)
        settings=Adjustments(exposure=.3,curve_points=[[0,0],[.3,.25],[.6,.7],[1,1]],geometry=g)
        expected=np.asarray(apply_geometry(render(rgb,settings),g))
        result=page.evaluate("""async({rgb,settings})=>{
          const {GpuRenderer}=await import('/static/gpu.js'),{gpuGeometry}=await import('/static/geometry-math.js');
          const geometry=gpuGeometry([320,240],settings.geometry),c=document.createElement('canvas');[c.width,c.height]=geometry.size;
          const r=new GpuRenderer(c);r.load({width:320,height:240,pixels:new Uint16Array(rgb)},{width:320,height:240});
          r.draw(settings,{scale:1,offset:[0,0],origin:[0,0]},{geometry});const pixels=new Uint8Array(c.width*c.height*4);r.gl.readPixels(0,0,c.width,c.height,r.gl.RGBA,r.gl.UNSIGNED_BYTE,pixels);r.dispose();
          return {width:c.width,height:c.height,pixels:[...pixels]};
        }""",{'rgb':rgb.ravel().tolist(),'settings':settings.model_dump()})
        actual=np.array(result['pixels'],dtype=np.uint8).reshape(result['height'],result['width'],4)[::-1,:,:3]
        assert actual.shape==expected.shape
        delta=np.abs(actual[3:-3,3:-3].astype(int)-expected[3:-3,3:-3].astype(int))
        assert np.percentile(delta,99.9)<=2,(g,delta.max())
    print('GPU/source homographies and auto-crop match Python; geometry/color preview interior error <=2/255 at percentile 99.9',flush=True)


def workflow(browser,args,cpu=False):
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1.5)
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    # Old automatically saved panel states must not override the new default.
    page.add_init_script("for(const key of ['presets','빛','색','톤 커브','흑백 채널 믹서','색조','그레인','geometry','masks'])localStorage.setItem('mydarkroom.category.'+key,'open')")
    if not cpu:page.add_init_script("""window.liveGL={};for(const kind of ['Texture','Framebuffer','Buffer','Program']){const values=new Set();liveGL[kind]=values;const p=WebGL2RenderingContext.prototype,c=p['create'+kind],d=p['delete'+kind];p['create'+kind]=function(...a){const v=c.apply(this,a);if(v)values.add(v);return v};p['delete'+kind]=function(v){values.delete(v);return d.call(this,v)}}""")
    errors=[];requests=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r) if r.url.endswith('/preview') else None)
    page.goto(args.url)
    assert page.locator('details.category').evaluate_all('es=>es.filter(e=>e.open).map(e=>e.dataset.category)')==['빛']
    for key in ['geometry','톤 커브','masks']:page.locator(f'[data-category="{key}"]>summary').click()
    with page.expect_response(lambda r:r.url.endswith('/api/photos') and r.request.method=='POST') as upload:page.locator('#file').set_input_files(args.nef)
    photo=upload.value.json();page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def ready():page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
    def adjust(key,value):
        element=page.locator('#'+key);element.fill(str(value));element.dispatch_event('input');element.dispatch_event('change')
    start=len(requests);adjust('geo-rotation',7);ready()
    if not cpu:assert len(requests)==start,'GPU geometry must not request a server JPEG'
    if cpu:
        before=len(requests);adjust('exposure',.2);ready();assert len(requests)==before+1,'CPU geometry reuses unchanged before image and skips the raw source refresh'
    assert page.locator('#geometry-auto-crop').is_checked()
    assert page.locator('#crop-ratio').input_value()=='original'
    with page.expect_download(timeout=120000) as download:
        page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-start').click()
    image=Image.open(BytesIO(Path(download.value.path()).read_bytes()));image.load()
    assert abs(image.width/image.height-photo['width']/photo['height'])<.003
    assert max(image.size)==2000
    # Capture saved geometry through export, including undo and disabled-auto behavior.
    def saved_geometry():
        with page.expect_request(lambda r:'/export?' in r.url) as request:
            page.locator('#export').click();page.locator('#export-start').click()
        g=request.value.post_data_json['geometry'];page.wait_for_function("!document.getElementById('export').disabled",timeout=60000);return g
    first=saved_geometry();assert first['crop'][2]<.98
    adjust('geo-vertical',.12);ready();second=saved_geometry();assert second['crop']!=first['crop']
    page.locator('#undo').click();ready();assert saved_geometry()==first
    page.locator('#redo').click();ready();assert saved_geometry()==second
    page.locator('#geometry-auto-crop').uncheck();adjust('geo-rotation',10);ready();assert saved_geometry()['crop']==second['crop']
    page.locator('#geometry-auto-crop').check();ready();assert saved_geometry()['crop']!=second['crop']
    if not cpu:
        # Histogram does not read back while a pointer is dragging a control.
        page.wait_for_function("Number(document.getElementById('histogram').dataset.count)>0")
        before=len(requests);chart=page.locator('#tone-curve');chart.scroll_into_view_if_needed();box=chart.bounding_box();hist=page.locator('#histogram').get_attribute('data-revision')
        page.mouse.move(box['x']+box['width']*.5,box['y']+box['height']*.5);page.mouse.down()
        for i in range(8):page.mouse.move(box['x']+box['width']*(.45+i*.01),box['y']+box['height']*(.55-i*.015))
        page.wait_for_timeout(300)
        assert page.locator('#histogram').get_attribute('data-revision')==hist
        page.mouse.up();ready()
        page.wait_for_function("old=>Number(document.getElementById('histogram').dataset.revision)>Number(old)",arg=hist)
        assert len(requests)==before
        assert page.locator('#preview').get_attribute('data-geometry-renderer')=='webgl2'
        counts=page.evaluate("Object.fromEntries(Object.entries(liveGL).map(([k,v])=>[k,v.size]))")
        for angle in [3,-5,8,1]:adjust('geo-rotation',angle);ready()
        page.wait_for_timeout(600)
        assert page.evaluate("Object.fromEntries(Object.entries(liveGL).map(([k,v])=>[k,v.size]))")==counts
        print('GPU: 8 curve moves, deferred histogram and stable GPU objects; zero JPEG preview requests',flush=True)
    # Original/source mode is still required for mask editing in this release.
    assert not page.locator('#mask-add').is_disabled()
    page.locator('#geometry-source').check();assert not page.locator('#mask-add').is_disabled()
    page.locator('#geometry-source').uncheck();ready()
    # Each new page starts with only Light open.
    page.reload();assert not page.locator('[data-category="geometry"]').evaluate('e=>e.open')
    assert not page.locator('[data-category="presets"]').evaluate('e=>e.open')
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': defaults/persistence, manual auto-crop, toggle, undo/redo, JPEG and source-mode masks passed',flush=True)
    page.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nef',required=True);p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--chromium',default='/usr/bin/chromium');args=p.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=args.chromium,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader'])
        page=browser.new_page();page.goto(args.url);math_and_pixels(page);page.close()
        workflow(browser,args);workflow(browser,args,True);browser.close()
