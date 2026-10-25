"""Real NEF: transformed brush/radial masks, spatial controls and hot GPU path."""
import argparse
from io import BytesIO
from pathlib import Path
import sys
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from darkroom.masking import Mask,mask_weight


def check(browser,args,cpu=False):
    page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1.5)
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];requests=[];payloads=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def request(r):
        if '/api/photos/' in r.url:requests.append(r)
        if ('/masked-pixels' in r.url or r.url.endswith('/preview')) and r.method=='POST':payloads.append(r.post_data_json)
    page.on('request',request)
    page.goto(args.url);page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    with page.expect_response(lambda r:r.url.endswith('/api/photos') and r.request.method=='POST') as uploaded:page.locator('#file').set_input_files(args.nef)
    meta=uploaded.value.json();page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def adjust(key,v):
        e=page.locator('#'+key);e.fill(str(v));e.dispatch_event('input');e.dispatch_event('change')
    def ready():page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
    adjust('geo-rotation',6);adjust('geo-vertical',.12);ready()
    assert not page.locator('#mask-add').is_disabled()
    page.locator('#mask-kind').select_option('brush');page.locator('#mask-add').click();adjust('local-exposure',.8)
    page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    # Auto-crop preserves source aspect; derive fit rectangle in CSS pixels.
    box=page.locator('#canvas').bounding_box();w,h=meta['width'],meta['height'];scale=min((box['width']-2)/w,(box['height']-2)/h)
    left=box['x']+(box['width']-w*scale)/2;top=box['y']+(box['height']-h*scale)/2
    x=left+w*scale*.6;y=top+h*scale*.48
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+18,y+6,steps=5);page.mouse.up()
    page.wait_for_function("!document.getElementById('export').disabled",timeout=60000);page.wait_for_timeout(800)
    settings=next(v for v in reversed(payloads) if v['masks'] and v['masks'][0]['strokes'])
    stroke=settings['masks'][0]['strokes'][0];assert stroke['transform'] and len(stroke['points'])>=2
    mask=Mask(**settings['masks'][0]);point=stroke['points'][0];matrix=np.array(stroke['transform']).reshape(3,3);raw=np.linalg.inv(matrix)@np.array([point['x'],point['y'],1]);raw/=raw[2]
    assert mask_weight(mask,np.zeros((1,1,3)),np.array([[raw[0]]]),np.array([[raw[1]]]),(w,h))[0,0]>.9
    overlay=page.request.post(f"{args.url}/api/photos/{meta['id']}/mask-overlay?selected=0",data=settings)
    assert overlay.status==200
    image=Image.open(BytesIO(overlay.body()));assert image.getpixel((round(raw[0]*image.width),round(raw[1]*image.height)))[3]>90
    # Eraser and undo retain the captured painting transform.
    page.locator('#brush-erase').check();page.mouse.click(x,y);page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    page.locator('#undo').click();page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    page.locator('#mask-kind').select_option('radial');page.locator('#mask-add').click();adjust('local-exposure',-.4)
    page.mouse.move(left+w*scale*.4,top+h*scale*.5);page.mouse.down();page.mouse.move(left+w*scale*.5,top+h*scale*.5,steps=5);page.mouse.up()
    page.wait_for_function("!document.getElementById('export').disabled",timeout=60000);page.wait_for_timeout(400)
    assert next(v for v in reversed(payloads) if len(v['masks'])==2)['masks'][1]['transform']
    # Picking a color in the transformed view samples its inverse-mapped RAW location.
    from urllib.parse import urlparse,parse_qs
    page.locator('#mask-kind').select_option('color');page.locator('#mask-add').click()
    with page.expect_request(lambda r:'/sample-color?' in r.url) as sampled, page.expect_response(lambda r:'/sample-color?' in r.url) as picked:
        page.locator('#mask-pick').click();page.mouse.click(x,y)
    query=parse_qs(urlparse(sampled.value.url).query)
    assert abs(float(query['x'][0])-raw[0])<.002 and abs(float(query['y'][0])-raw[1])<.002
    expected_hex='#'+''.join(f'{round(v*255):02x}' for v in picked.value.json()['color'])
    page.wait_for_function("value=>document.getElementById('mask-color').value===value",arg=expected_hex,timeout=60000)
    page.locator('#mask-delete').click();page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    # Keep spatial controls independent of server mask baking.
    if cpu:page.locator('#mask-show').uncheck()
    adjust('dehaze',35);adjust('clarity',45);adjust('texture',60)
    if not cpu:
        page.wait_for_function("document.getElementById('preview').dataset.structureReady==='true'",timeout=60000)
        page.wait_for_function("document.getElementById('mask-overlay').dataset.ready==='true'",timeout=60000)
        page.wait_for_load_state("networkidle",timeout=60000);before=len(requests)
        for v in [-100,-40,0,30,100]:adjust('texture',v);adjust('clarity',-v);adjust('dehaze',v//2)
        page.wait_for_timeout(600)
        assert len(requests)==before,[r.url for r in requests[before:]]
    else:ready()
    for key in ['dehaze','clarity','texture']:page.locator('#'+key).dispatch_event('dblclick');assert page.locator('#'+key).input_value()=='0'
    adjust('clarity',25);adjust('texture',40)
    page.locator('#preset-name').fill('spatial');page.locator('#save-preset').click()
    values=page.evaluate("JSON.parse(localStorage.getItem('mydarkroom.presets.v1')).at(-1).values")
    assert values['clarity']==25 and values['texture']==40
    with page.expect_download(timeout=120000) as downloaded:
        page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-start').click()
    output=Image.open(BytesIO(Path(downloaded.value.path()).read_bytes()));output.load();assert max(output.size)==2000 and output.info['icc_profile']
    # Geometry changes and crop reset keep masks attached to original content.
    adjust('geo-rotation',-4);ready();page.locator('#geometry-reset').click()
    assert page.locator('#mask-select option').count()==2
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': transformed brush/eraser/radial, overlay alignment, signed spatial sliders, presets, JPEG and geometry reset passed',flush=True)
    page.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nef',required=True);p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--chromium',default='/usr/bin/chromium');args=p.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=args.chromium,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader']);check(browser,args);check(browser,args,True);browser.close()
