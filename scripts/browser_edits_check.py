"""Real NEF edit/restore, portable edit files, and idle-save responsiveness."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def verify(browser, url, nef, cpu=False):
    page=browser.new_page(viewport={"width":1440,"height":1000},accept_downloads=True)
    if cpu:
        page.add_init_script("const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}")
    errors=[];writes=[];photos=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.on("request",lambda request:writes.append(request.post_data_json) if request.method=="PUT" and request.url.endswith("/edits") else None)
    page.on("response",lambda response:photos.append(response.json()) if response.url.endswith("/api/photos") and response.request.method=="POST" and response.ok else None)
    page.goto(url);page.wait_for_selector("details.category")
    def upload(name=None):
        page.locator("#file").set_input_files(nef if not name else {"name":name,"mimeType":"application/octet-stream","buffer":nef.read_bytes()})
        page.wait_for_function("!document.getElementById('controls').disabled",timeout=90000)
    def panels():page.locator("details.category").evaluate_all("es=>es.forEach(e=>e.open=true)")
    def adjust(key,value):
        page.locator("#"+key).evaluate("(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}",value)
    upload();panels()
    page.locator("#reset").click();page.locator("#edit-save").click()
    page.wait_for_function("document.getElementById('edit-save-status').dataset.state==='saved'")
    writes.clear()
    # Every movement refreshes the idle deadline. No persistence HTTP on drag.
    for value in range(10):adjust("exposure",value/10);page.wait_for_timeout(70)
    assert not writes,"Saved while a slider was being moved"
    page.wait_for_function("document.getElementById('edit-save-status').dataset.state==='saved'",timeout=15000)
    assert len(writes)==1 and writes[0]["settings"]["exposure"]==.9
    adjust("grain",37);adjust("geo-rotation",3)
    page.locator("#mask-kind").select_option("brush");page.locator("#mask-add").click();adjust("local-exposure",.4)
    box=page.locator("#canvas").bounding_box()
    page.mouse.move(box["x"]+box["width"]*.5,box["y"]+box["height"]*.5);page.mouse.down();page.mouse.move(box["x"]+box["width"]*.55,box["y"]+box["height"]*.55,steps=4);page.mouse.up()
    page.locator("#mask-kind").select_option("radial");page.locator("#mask-add").click();adjust("local-texture",50);adjust("density-2",37)
    page.locator("#mask-show").uncheck()
    page.locator("#tone-curve").click(position={"x":128,"y":70})
    # Opening the next photo flushes immediately, before the 1.5 s idle timer.
    upload("이름 변경.NEF");panels()
    restored=photos[-1]["edits"]["settings"]
    assert restored["exposure"]==.9 and restored["grain"]==37 and restored["geometry"]["rotation"]==3
    assert len(restored["masks"])==2 and restored["masks"][0]["strokes"] and restored["masks"][1]["adjustments"]["texture"]==50
    assert restored["masks"][1]["density"][2]==.37 and len(restored["curve_points"])>=6
    assert page.locator("#exposure").input_value()=="0.9" and page.locator("#geo-rotation").input_value()=="3"
    assert not page.locator("#mask-draw").is_checked()
    page.wait_for_function("document.getElementById('preview').dataset.geometryRenderer",timeout=60000)
    if not cpu:page.wait_for_function("document.getElementById('preview').dataset.localReady==='true'",timeout=60000)
    with page.expect_download() as download:page.locator("#edit-export").click()
    exported=json.loads(Path(download.value.path()).read_text())
    assert exported["settings"]==restored
    page.locator("#reset").click();page.locator("#edit-import-file").set_input_files({"name":"restore.mydarkroom.json","mimeType":"application/json","buffer":json.dumps(exported).encode()})
    page.wait_for_function("document.getElementById('status').textContent.includes('보정정보를 불러와 저장')",timeout=60000)
    assert page.locator("#grain").input_value()=="37"
    page.locator("#undo").click();assert page.locator("#exposure").input_value()=="0"
    page.locator("#redo").click();assert page.locator("#exposure").input_value()=="0.9"
    assert page.evaluate("window.mydarkroomFlushEdits()") == {"ok":True}
    page.reload();page.wait_for_selector("details.category")
    assert page.locator("details.category").evaluate_all("es=>es.filter(e=>e.open).map(e=>e.dataset.category)")==["빛"]
    upload("별도 폴더에서 재개.NEF")
    assert photos[-1]["edits"]["settings"]==restored
    assert not errors,errors
    assert page.locator("#error").is_hidden(),page.locator("#error").inner_text()
    print(("CPU" if cpu else "WebGL2")+": idle save coalesced; rename/reload restored all edits, brush/radial masks and curve; JSON export/import, undo/redo and close flush passed")
    page.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--url",default="http://127.0.0.1:8765");parser.add_argument("--nef",type=Path,required=True);args=parser.parse_args()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path="/usr/bin/chromium",args=["--enable-unsafe-swiftshader"])
        verify(browser,args.url,args.nef);verify(browser,args.url,args.nef,cpu=True);browser.close()
