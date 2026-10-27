"""Drive the actual embedded WebView2 through CDP and its real Windows save dialog."""
import json
from pathlib import Path
import time

from PIL import Image
from playwright.sync_api import sync_playwright
from darkroom.version import VERSION,editor_url
from upgrade_smoke import verify_upgrade


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
        page=context.pages[0] if context.pages else context.wait_for_event('page',timeout=60000)
        page.wait_for_url(editor_url(url),timeout=60000)
        upgrade_checks=verify_upgrade(page,url,VERSION)
        page.goto(editor_url(url))
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
        dialog=Desktop(backend='win32').window(process=process.pid,class_name='#32770')
        try:dialog.wait('visible',timeout=30)
        except Exception as exc:
            windows=[(w.process_id(),w.class_name(),w.window_text()) for w in Desktop(backend='win32').windows()]
            raise AssertionError(f"Save dialog missing. Status={page.locator('#status').inner_text()}; error={page.locator('#error').inner_text()}; windows={windows}") from exc
        # Keep the Save button on the CI desktop even when the editor is larger
        # than its display. This changes the test dialog position only.
        dialog.move_window(x=0,y=0,width=900,height=650)
        edits=dialog.descendants(class_name='Edit')
        filename=next((e for e in edits if e.control_id() in (1001,1148)),None)
        assert filename is not None,[(e.control_id(),e.window_text()) for e in edits]
        dialog.set_focus();filename.set_edit_text(str(output))
        # A synthesized WM_CLICK can leave the common dialog's autocomplete
        # popup active. Use real pointer input and verify that Save As closes.
        for attempt in range(3):
            dialog.child_window(control_id=1).click_input()
            try:dialog.wait_not('visible',timeout=3);break
            except Exception:
                if attempt==2:raise AssertionError('Native Save As did not close after real Save clicks')
        # Detail/zoom progress can replace the shared status text after export.
        # Check the actual export completion and image, not a transient caption.
        try:page.wait_for_function("document.getElementById('busy').hidden",timeout=120000)
        except Exception as exc:
            windows=[]
            for win in Desktop(backend='win32').windows(process=process.pid):
                controls=[(c.class_name(),c.control_id(),c.window_text()) for c in win.descendants() if c.class_name() in ('Edit','Button','Static')]
                windows.append((win.class_name(),win.window_text(),controls))
            raise AssertionError(f"JPEG save did not complete. Output={output.exists()}; status={page.locator('#status').inner_text()}; error={page.locator('#error').inner_text()}; busy={page.locator('#busy').is_visible()}; js_errors={errors}; windows={windows}") from exc
        with Image.open(output) as image:
            image.load();assert max(image.size)==2000 and image.info['dpi']==(240,240) and image.info['icc_profile']
        page.wait_for_function("document.getElementById('edit-save-status').dataset.state==='saved'",timeout=30000)
        stored=list((profile/'edits').glob('*.json'));assert len(stored)==1
        saved=json.loads(stored[0].read_text(encoding='utf-8'))
        assert saved['settings']['geometry']['rotation']==5
        assert saved['settings']['masks'][0]['adjustments']['sharpness']==70
        page.reload();page.wait_for_function("window.pywebview?.api?.export_jpeg && document.querySelectorAll('details.category').length===9",timeout=60000)
        assert open_panels()==['빛']
        page.locator('#file').set_input_files({'name':'renamed.NEF','mimeType':'application/octet-stream','buffer':Path(nef).read_bytes()})
        page.wait_for_function("!document.getElementById('controls').disabled",timeout=90000)
        assert page.locator('#geo-rotation').input_value()=='5'
        assert page.locator('#local-sharpness').input_value()=='70'
        assert not page.locator('#mask-draw').is_checked()
        page.wait_for_function("document.getElementById('preview').dataset.localReady==='true'",timeout=90000)
        assert not errors,errors
        assert page.locator('#error').is_hidden(),page.locator('#error').inner_text()
        # Close immediately after a new adjustment, before the idle-save deadline.
        adjust('exposure',.57)
        Desktop(backend='win32').window(process=process.pid,title_re='MyDarkRoom.*').close()
        assert process.wait(timeout=45)==0
        assert not (profile/'instance.json').exists() and not list((profile/'cache').glob('mydarkroom-*'))
        closed=json.loads(stored[0].read_text(encoding='utf-8'))
        assert closed['settings']['exposure']==.57 and closed['settings']['masks']==saved['settings']['masks']
    return upgrade_checks+['embedded WebView2 page and JS bridge','Light-only startup with stale saved panels','WebGL2 renderer','native file input NEF upload','crop and 200% zoom/pan','cached local spatial sliders without requests','real native Save As JPEG/DPI/ICC','native window close and engine/cache cleanup','persistent embedded profile','photo edit auto-save outside disposable cache','renamed NEF restores geometry and local spatial masks','dirty native close flushes edits before engine shutdown']
