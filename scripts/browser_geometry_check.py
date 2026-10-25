"""Real NEF crop/manual geometry and source-coordinate mask workflow."""
import argparse
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright


def check(browser,args,cpu=False):
    page=browser.new_page(viewport={"width":1440,"height":1100},device_scale_factor=1.5)
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.url);page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)");page.locator('#file').set_input_files(args.nef)
    page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def ready():page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
    page.locator('#geometry-auto-crop').uncheck()
    page.locator('#crop-edit').click();ready()
    # The source sample is landscape; derive the fitted image rectangle from metadata.
    text=page.locator('#dimensions').inner_text();w,h=map(int,text.split(' · ')[0].split(' × '))
    box=page.locator('#canvas').bounding_box();factor=min((box['width']-2)/w,(box['height']-2)/h)
    left=box['x']+(box['width']-w*factor)/2;top=box['y']+(box['height']-h*factor)/2
    page.mouse.move(left+w*factor*.2,top+h*factor*.2);page.mouse.down();page.mouse.move(left+w*factor*.8,top+h*factor*.8,steps=10);page.mouse.up();ready()
    page.locator('#crop-ratio').select_option('1');ready()
    # Move the fixed-ratio crop without changing its dimensions.
    page.mouse.move(left+w*factor*.5,top+h*factor*.5);page.mouse.down();page.mouse.move(left+w*factor*.55,top+h*factor*.5,steps=5);page.mouse.up();ready()
    page.locator('#crop-edit').click();ready()
    page.locator('#geo-rotation').fill('2');page.locator('#geo-rotation').dispatch_event('input');page.locator('#geo-rotation').dispatch_event('change');ready()
    page.locator('#undo').click();ready();assert page.locator('#geo-rotation').input_value()=='0'
    page.locator('#redo').click();ready();assert page.locator('#geo-rotation').input_value()=='2'
    assert page.locator('#mask-add').is_disabled()
    page.locator('#geometry-source').check();assert not page.locator('#mask-add').is_disabled()
    page.locator('#mask-kind').select_option('luminance');page.locator('#mask-add').click()
    page.locator('#local-exposure').fill('0.1');page.locator('#local-exposure').dispatch_event('input');page.locator('#local-exposure').dispatch_event('change')
    page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    page.locator('#geometry-source').uncheck();ready();assert page.locator('#mask-select option').count()==1
    with page.expect_download(timeout=120000) as downloaded:
        page.locator('#export').click();page.locator('#export-start').click()
    with Image.open(BytesIO(Path(downloaded.value.path()).read_bytes())) as image:
        image.load();assert abs(image.width-image.height)<=1 and image.width<min(w,h)
    page.locator('#geometry-reset').click();assert page.locator('#geo-rotation').input_value()=='0'
    assert page.locator('#mask-select option').count()==1
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': drag crop, 1:1, rotation, undo/redo, mask source mode and cropped JPEG passed',flush=True)
    page.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--nef',required=True);parser.add_argument('--url',default='http://127.0.0.1:8765');parser.add_argument('--chromium');args=parser.parse_args();args.nef=str(Path(args.nef).resolve())
    with sync_playwright() as p:
        options={'headless':True,'args':['--enable-unsafe-swiftshader','--use-angle=swiftshader']}
        if args.chromium:options['executable_path']=args.chromium
        browser=p.chromium.launch(**options);check(browser,args);check(browser,args,cpu=True);browser.close()
