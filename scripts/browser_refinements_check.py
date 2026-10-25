"""Real-NEF refinement workflow, layout bounds, custom-curve parity and presets."""
import argparse
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.browser_check import shader_checks


def check(browser,args,cpu=False):
    page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1.5)
    if cpu:page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.url)
    if not cpu:shader_checks(page)
    names=page.locator('details.category>summary').all_text_contents()
    assert names==['프리셋','빛','색','톤 커브','흑백 채널 믹서','색조','그레인','크롭 · 수평 · 업라이트','마스크'],names
    assert page.locator('details.category').evaluate_all("es=>es.filter(e=>e.open).map(e=>e.dataset.category)")==['빛']
    page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    for width,height in [(1920,1080),(1440,900),(1024,768)]:
        page.set_viewport_size({'width':width,'height':height})
        assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
        assert page.locator('footer').bounding_box()['y']<height
        assert page.locator('#canvas').bounding_box()['height']>300
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.set_viewport_size({'width':1440,'height':900})
    with page.expect_response(lambda r:r.url.endswith('/api/photos') and r.request.method=='POST') as uploaded:
        page.locator('#file').set_input_files(args.nef)
    metadata=uploaded.value.json()
    page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    def adjust(key,value):
        item=page.locator('#'+key);item.fill(str(value));item.dispatch_event('input');item.dispatch_event('change')
    adjust('saturation',37);adjust('temperature',18);adjust('tint',-11)
    adjust('exposure',.7)
    with page.expect_response(lambda r:'/auto-light' in r.url):page.locator('#auto-light').click()
    page.wait_for_function("document.getElementById('status').textContent.includes('자동 밝기 보정 완료')")
    for k,v in [('saturation','37'),('temperature','18'),('tint','-11')]:assert page.locator('#'+k).input_value()==v
    page.locator('#undo').click();assert page.locator('#exposure').input_value()=='0.7'
    # A real pointer double-click on the handle resets just this slider and is undoable.
    box=page.locator('#exposure').bounding_box();x=box['x']+10+(box['width']-20)*3.7/6
    page.mouse.dblclick(x,box['y']+box['height']/2)
    assert page.locator('#exposure').input_value()=='0'
    page.locator('#undo').click();assert page.locator('#exposure').input_value()=='0.7'
    adjust('grain_size',4);page.locator('#grain_size').dispatch_event('dblclick');assert page.locator('#grain_size').input_value()=='1'
    # Add >10 points using actual graph interaction; check saved preset and history.
    page.locator('#curve-reset').click();chart=page.locator('#tone-curve');chart.scroll_into_view_if_needed();box=chart.bounding_box()
    for i in range(1,11):
        x=i/11;y=x**.8
        page.mouse.click(box['x']+box['width']*(4+248*x)/256,box['y']+box['height']*(4+172*(1-y))/180)
    def snapshot():
        page.locator('#preset-name').fill('refinement')
        page.locator('#save-preset').click()
        return page.evaluate("JSON.parse(localStorage.getItem('mydarkroom.presets.v1')).at(-1).values")
    settings=snapshot();assert len(settings['curve_points'])==12,settings['curve_points']
    # New cubic points survive a real preset JSON backup and re-import.
    with page.expect_download() as backup:page.locator('#preset-export').click()
    backup_bytes=Path(backup.value.path()).read_bytes()
    page.locator('#preset-import-file').set_input_files({'name':'curves.json','mimeType':'application/json','buffer':backup_bytes})
    page.wait_for_function("JSON.parse(localStorage.getItem('mydarkroom.presets.v1')).length===2")
    assert snapshot()['curve_points']==settings['curve_points']
    # Delete an interior point with a real double-click; undo restores the graph.
    chart.scroll_into_view_if_needed();box=chart.bounding_box();x,y=settings['curve_points'][5]
    page.mouse.dblclick(box['x']+box['width']*(4+248*x)/256,box['y']+box['height']*(4+172*(1-y))/180)
    page.locator('#undo').click()
    assert snapshot()['curve_points']==settings['curve_points']
    assert settings['saturation']==37
    # Reloaded/imported presets use the identical 12-point curve.
    page.locator('#preset').select_option('base')
    page.locator('#preset').select_option(index=page.locator('#preset option').count()-1)
    assert snapshot()['curve_points']==settings['curve_points']
    page.locator('[data-category="색"]>summary').click();assert not page.locator('[data-category="색"]').evaluate('e=>e.open')
    page.reload();assert not page.locator('[data-category="색"]').evaluate('e=>e.open')
    # Upload again because photo sessions are intentionally not restored on reload.
    page.locator('#file').set_input_files(args.nef);page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    page.locator('#geometry-auto-crop').uncheck()
    page.locator('#crop-ratio').select_option('original')
    def ready():page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
    ready()
    # Get the request payload without relying on internal module variables.
    page.locator('#crop-full').click();ready()
    # Fit dimensions in CSS pixels; every drag updates actual crop payload.
    box=page.locator('#canvas').bounding_box();w,h=metadata['width'],metadata['height'];scale=min((box['width']-2)/w,(box['height']-2)/h)
    left=box['x']+(box['width']-w*scale)/2;top=box['y']+(box['height']-h*scale)/2;cw,ch=w*scale,h*scale
    # Start from an inset crop, then exercise all 8 handles in free and locked modes.
    page.locator('#crop-ratio').select_option('free')
    page.mouse.move(left+.2*cw,top+.2*ch);page.mouse.down();page.mouse.move(left+.8*cw,top+.8*ch,steps=5);page.mouse.up();ready()
    for ratio in ['free','original']:
        page.locator('#crop-ratio').select_option(ratio)
        if ratio!='free':ready()
        for index in range(8):
            # In edit mode requests are uncropped; export gives the true saved crop.
            with page.expect_request(lambda r:r.url.endswith('/export?long_edge=2000&dpi=300&quality=95')) as req:
                page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-start').click()
            crop=req.value.post_data_json['geometry']['crop'];page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
            x,y,a,b=crop;handles=[(x,y),(x+a/2,y),(x+a,y),(x+a,y+b/2),(x+a,y+b),(x+a/2,y+b),(x,y+b),(x,y+b/2)]
            hx,hy=handles[index];nx=hx+(.025 if hx<x+a/2 else -.025);ny=hy+(.025 if hy<y+b/2 else -.025)
            if index in [1,5]:nx=hx
            if index in [3,7]:ny=hy
            page.mouse.move(left+hx*cw,top+hy*ch);page.mouse.down();page.mouse.move(left+nx*cw,top+ny*ch,steps=4);page.mouse.up();ready()
            with page.expect_request(lambda r:'/export?' in r.url) as req:
                page.locator('#export').click();page.locator('#export-start').click()
            result=req.value.post_data_json['geometry']['crop'];assert result!=crop,(index,ratio,crop,result)
            if ratio=='original':assert abs(result[2]-result[3])<1e-6
            page.wait_for_function("!document.getElementById('export').disabled",timeout=60000)
    assert not errors,errors
    assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
    print(('CPU' if cpu else 'WebGL2')+': layout, categories, auto light/color preservation, handle reset, 12-point presets and 8 crop handles passed',flush=True)
    if not cpu and args.artifacts:
        page.locator('#crop-edit').click();ready();page.locator('aside').evaluate('e=>e.scrollTop=0')
        directory=Path(args.artifacts);directory.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(directory/'refinements.png'))
    page.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nef',required=True);p.add_argument('--chromium',default='/usr/bin/chromium');p.add_argument('--url',default='http://127.0.0.1:8765');p.add_argument('--artifacts');args=p.parse_args()
    with sync_playwright() as play:
        browser=play.chromium.launch(headless=True,executable_path=args.chromium,args=['--enable-unsafe-swiftshader','--use-angle=swiftshader']);check(browser,args);check(browser,args,True);browser.close()
