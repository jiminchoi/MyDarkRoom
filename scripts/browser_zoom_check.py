"""Native zoom, pointer anchoring, cropped geometry, ROI reuse and panning."""
import argparse
import json
from pathlib import Path
from io import BytesIO
from urllib.parse import parse_qs,urlparse
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright


def check(browser,args,cpu=False):
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1.5)
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];requests=[];settings=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def request(r):
        if '/region?' in r.url or '/viewport?' in r.url:requests.append(r);settings.append(r.post_data_json)
    page.on('request',request);page.goto(args.url)
    page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    with page.expect_response(lambda r:r.url.endswith('/api/photos') and r.request.method=='POST') as upload:page.locator('#file').set_input_files(args.nef)
    photo=upload.value.json();page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def tick():page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    def view():tick();return json.loads(page.locator('#preview').get_attribute('data-view'))
    def zoom(percent):page.locator('#zoom-scale').fill(str(percent));page.locator('#zoom-scale').dispatch_event('input');page.locator('#zoom-scale').dispatch_event('change');tick()
    def detail():page.wait_for_function("Boolean(document.getElementById('preview').dataset.detail)",timeout=120000)
    def ready():page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=120000)
    def adjust(key,value):page.locator('#'+key).fill(str(value));page.locator('#'+key).dispatch_event('input');page.locator('#'+key).dispatch_event('change')
    # The initial value reports native-pixel scale, not a multiplier of fit.
    v=view();assert 0<v['scale']<1 and page.locator('#zoom-scale').input_value()!='100'
    page.locator('#zoom').click();detail();assert view()['scale']==1
    page.wait_for_timeout(400);count=len(requests);zoom(200);page.wait_for_timeout(500)
    assert view()['scale']==2 and len(requests)==count,'200% should reuse the already loaded source window'
    # Wheel anchoring at an off-center cursor position.
    box=page.locator('#canvas').bounding_box();mouse=(box['x']+box['width']*.6,box['y']+box['height']*.42)
    before=view();pixels=page.locator('#preview').evaluate('c=>[c.width,c.height]');anchor=[(mouse[0]-box['x'])*pixels[0]/box['width'],(mouse[1]-box['y'])*pixels[1]/box['height']]
    old=[before['origin'][i]+(anchor[i]-before['offset'][i])/before['scale'] for i in range(2)]
    page.mouse.move(*mouse);page.mouse.wheel(0,100);page.wait_for_timeout(150);after=view()
    assert 1<after['scale']<2
    new=[after['origin'][i]+(anchor[i]-after['offset'][i])/after['scale'] for i in range(2)]
    assert max(abs(a-b) for a,b in zip(old,new))<2,(old,new)
    # Slider zoom keeps the viewport center fixed.
    before=view();old=[before['origin'][i]+(pixels[i]/2-before['offset'][i])/before['scale'] for i in range(2)];zoom(200);after=view()
    new=[after['origin'][i]+(pixels[i]/2-after['offset'][i])/after['scale'] for i in range(2)]
    assert np.allclose(old,new,atol=1e-6)
    previous=page.locator('#preview').get_attribute('data-detail')
    page.mouse.move(box['x']+box['width']*.6,box['y']+box['height']*.5);page.mouse.down();page.mouse.move(box['x']-100,box['y']+box['height']*.3,steps=12);page.mouse.up()
    page.wait_for_function("old=>document.getElementById('preview').dataset.detail!==old",arg=previous,timeout=120000)
    assert view()['origin']!=before['origin']
    # Crop, level and perspective all retain zoom controls.
    page.locator('#fit').click();page.locator('#crop-ratio').select_option('1');ready()
    assert page.locator('#zoom').is_disabled()
    page.locator('#crop-edit').click();ready();page.locator('#geometry-auto-crop').uncheck()
    adjust('geo-rotation',6);adjust('geo-vertical',.1);ready()
    assert not page.locator('#zoom').is_disabled()
    page.locator('#zoom').click();detail();v=view();assert v['scale']==1 and v['size'][0]==v['size'][1]
    # An output-center pixel matches the transformed original, not an uncropped source tile.
    state=settings[-1];mx,my=pixels[0]//2,pixels[1]//2
    ox=int(v['origin'][0]+(mx+.5-v['offset'][0]));oy=int(v['origin'][1]+(my+.5-v['offset'][1]))
    response=page.request.post(f"{args.url}/api/photos/{photo['id']}/viewport?x={ox}&y={oy}&width=1&height=1",data=state)
    assert response.status==200
    expected=np.asarray(Image.open(BytesIO(response.body())))[0,0]
    actual=page.locator('#preview' if not cpu else '#geometry-view').evaluate("""(c,{x,y,cpu})=>{if(cpu)return [...c.getContext('2d').getImageData(x,y,1,1).data].slice(0,3);const gl=c.getContext('webgl2'),p=new Uint8Array(4);gl.readPixels(x,c.height-y-1,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return [...p].slice(0,3)}""",{'x':mx,'y':my,'cpu':cpu})
    assert np.max(np.abs(np.array(actual,dtype=int)-expected.astype(int)))<=5,(actual,expected)
    count=len(requests);zoom(200);page.wait_for_timeout(500);assert view()['scale']==2 and len(requests)==count
    # Space-drag pans without painting, even when a brush is selected.
    page.locator('#mask-kind').select_option('brush');page.locator('#mask-add').click();page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    page.locator('#canvas').focus();page.keyboard.down('Space');p1=view()['origin']
    page.mouse.move(box['x']+box['width']*.5,box['y']+box['height']*.5);page.mouse.down();page.mouse.move(box['x']+box['width']*.5+90,box['y']+box['height']*.5+30,steps=4);page.mouse.up();page.keyboard.up('Space');tick()
    assert view()['origin']!=p1
    # Fit works at any point and slider bounds do not exceed 200%.
    page.locator('#fit').click();zoom(1);assert view()['scale']==.01;page.mouse.move(*mouse)
    for _ in range(15):page.mouse.wheel(0,-2000)
    page.wait_for_timeout(100);assert view()['scale']==2
    page.locator('#fit').click();assert page.locator('#fit').get_attribute('aria-pressed')=='true'
    # View changes do not change output geometry, and Space-drag did not paint.
    with page.expect_request(lambda r:'/export?' in r.url) as saved, page.expect_download(timeout=120000) as downloaded:
        page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-start').click()
    body=saved.value.post_data_json
    assert body['geometry']==state['geometry'] and body['masks'][0]['strokes']==[]
    with Image.open(BytesIO(Path(downloaded.value.path()).read_bytes())) as image:
        assert max(image.size)==2000 and abs(image.width-image.height)<=1
    # Painting after a cropped 200% pan maps back to the visible source content.
    zoom(200);detail();v=view();box=page.locator('#canvas').bounding_box()
    with page.expect_request(lambda r:('/masked-pixels' in r.url or r.url.endswith('/preview')) and r.method=='POST' and r.post_data_json.get('masks') and r.post_data_json['masks'][0]['strokes']) as painted:
        page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
    stroke=painted.value.post_data_json['masks'][0]['strokes'][0]
    cfg=page.evaluate("""async({size,g})=>{const {gpuGeometry}=await import('/static/geometry-math.js');return gpuGeometry(size,g)}""",{'size':[photo['width'],photo['height']],'g':state['geometry']})
    q=[v['origin'][i]+(pixels[i]/2-v['offset'][i])/v['scale']+cfg['cropOrigin'][i] for i in range(2)]
    assert abs(stroke['points'][0]['x']-q[0]/photo['width'])<.0005
    assert abs(stroke['points'][0]['y']-q[1]/photo['height'])<.0005
    page.locator('#fit').click()
    # Layout still fits.
    for width,height in [(1024,768),(390,844)]:
        page.set_viewport_size({'width':width,'height':height});tick();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': 1–200%, wheel/slider anchors, cache reuse, pan, cropped perspective native pixels, Space-drag and layout passed',flush=True)
    page.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nef',required=True);p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--chromium',default='/usr/bin/chromium');args=p.parse_args()
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True,executable_path=args.chromium,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader'])
        math_page=b.new_page();math_page.goto(args.url)
        assert math_page.evaluate("""async()=>{
          const m=await import('/static/geometry-math.js'),size=[4000,3000],viewport=[1200,800],old=m.viewAt(size,viewport,.3),point=[720,280];
          const pan=m.anchoredPan(old,1,point,size,viewport),next=m.viewAt(size,viewport,1,pan);
          const error=point.map((p,i)=>Math.abs((p-old.offset[i])/old.scale+old.origin[i]-(p-next.offset[i])/next.scale-next.origin[i]));
          const clamped=m.viewAt(size,viewport,2,[-99,99999]).origin;
          return error.every(v=>v<1e-8)&&clamped[0]===0&&clamped[1]===2600&&m.containsRegion({x:10,y:10,width:100,height:100},{x:20,y:30,width:20,height:30});
        }""")
        math_page.close();check(b,args);check(b,args,True);b.close()
