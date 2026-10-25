"""Drive the actual embedded WebView2 through CDP and its real Windows save dialog."""
import json
from pathlib import Path
import time

from PIL import Image
from playwright.sync_api import sync_playwright


def verify(process,profile,url,port,nef,work):
    from pywinauto import Desktop
    errors=[];requests=[]
    with sync_playwright() as pw:
        deadline=time.monotonic()+60;browser=None
        while time.monotonic()<deadline:
            try:browser=pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}',timeout=2000);break
            except Exception:time.sleep(.2)
        assert browser is not None,'WebView2 CDP did not start'
        context=browser.contexts[0]
        while not context.pages and time.monotonic()<deadline:time.sleep(.1)
        page=context.pages[0];page.wait_for_url(url+'/',timeout=60000)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url) if '/api/photos/' in r.url else None)
        page.wait_for_function("window.pywebview?.api?.export_jpeg && document.querySelectorAll('details.category').length===9",timeout=60000)
        def open_panels():return page.locator('details.category').evaluate_all("es=>es.filter(e=>e.open).map(e=>e.dataset.category)")
        assert open_panels()==['빛']
        page.evaluate("for(const e of document.querySelectorAll('details.category'))localStorage.setItem('mydarkroom.category.v2.'+e.dataset.category,'open');localStorage.setItem('mydarkroom.embedded.test','persistent')")
        page.locator('[data-category="masks"]>summary').click();page.reload()
        page.wait_for_function("window.pywebview?.api?.export_jpeg && document.querySelectorAll('details.category').length===9",timeout=60000)
        assert open_panels()==['빛'] and page.evaluate("localStorage.getItem('mydarkroom.embedded.test')")=='persistent'
        page.locator('#file').set_input_files(nef)
        page.wait_for_function("!document.getElementById('controls').disabled",timeout=90000)
        assert 'WebGL2' in page.locator('#renderer-mode').inner_text(),'Embedded GPU renderer fell back to CPU'
        def adjust(key,value):
            e=page.locator('#'+key);e.fill(str(value));e.dispatch_event('input');e.dispatch_event('change')
        page.locator('[data-category="geometry"]>summary').click();adjust('geo-rotation',5)
        page.wait_for_function("document.getElementById('status').textContent.includes('기하 보정 미리보기 완료')",timeout=60000)
        page.locator('[data-category="masks"]>summary').click();page.locator('#mask-kind').select_option('radial');page.locator('#mask-add').click()
        page.locator('#mask-show').uncheck();adjust('local-texture',60);adjust('local-sharpness',70)
        page.wait_for_function("document.getElementById('preview').dataset.localReady==='true'",timeout=90000)
        page.wait_for_load_state('networkidle',timeout=60000);before=len(requests)
        for value in [-70,0,40,70]:adjust('local-texture',value);adjust('local-sharpness',value)
        page.wait_for_timeout(500);assert len(requests)==before,'Local strength changes sent server requests'
        page.locator('#zoom').click();page.wait_for_function("document.getElementById('preview').dataset.localDetailReady==='true'",timeout=90000)
        adjust('zoom-scale',200);page.wait_for_timeout(300)
        assert json.loads(page.locator('#preview').get_attribute('data-view'))['scale']==2
        box=page.locator('#canvas').bounding_box();old=page.locator('#preview').get_attribute('data-view')
        page.locator('#canvas').focus();page.keyboard.down('Space');page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+70,box['y']+box['height']/2+20,steps=4);page.mouse.up();page.keyboard.up('Space');page.wait_for_timeout(300)
        assert old!=page.locator('#preview').get_attribute('data-view')
        # Save As is a real WinForms file dialog. No JPEG bytes cross the JS bridge.
        output=Path(work)/'내장 창 저장.jpg'
        page.locator('#export').click();page.locator('#size-small').click();page.locator('#export-dpi').fill('240');page.locator('#export-name').fill(output.name);page.locator('#export-start').click()
        dialog=Desktop(backend='uia').window(process=process.pid,class_name='#32770')
        dialog.wait('visible',timeout=30)
        dialog.child_window(auto_id='1001',control_type='Edit').set_edit_text(str(output))
        dialog.child_window(auto_id='1',control_type='Button').click()
        page.wait_for_function("document.getElementById('status').textContent.includes('저장 완료')",timeout=120000)
        with Image.open(output) as image:
            image.load();assert max(image.size)==2000 and image.info['dpi']==(240,240) and image.info['icc_profile']
        assert not errors,errors
        assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
        # Actual native close button path, including asynchronous engine/cache cleanup.
        Desktop(backend='win32').window(process=process.pid,title_re='MyDarkRoom.*').close()
        assert process.wait(timeout=45)==0
        assert not (profile/'instance.json').exists() and not list((profile/'cache').glob('mydarkroom-*'))
    return ['embedded WebView2 page and JS bridge','Light-only startup with stale saved panels','WebGL2 renderer','native file input NEF upload','crop and 200% zoom/pan','cached local spatial sliders without requests','real native Save As JPEG/DPI/ICC','native window close and engine/cache cleanup','persistent embedded profile']
