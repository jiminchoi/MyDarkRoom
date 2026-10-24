"""Export/curve/keyboard and bounded-resource stress checks (Linux RSS optional)."""
import argparse
from io import BytesIO
from pathlib import Path
import sys
import json
import time

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.browser_check import shader_checks


def rss(pid):
    if not pid:return None
    path=Path(f"/proc/{pid}/status")
    if not path.exists():return None
    return int(next(line.split()[1] for line in path.read_text().splitlines() if line.startswith("VmRSS:")))*1024


def load(page,nef):
    with page.expect_response(lambda r:r.url.endswith("/api/photos") and r.request.method=="POST") as photo:
        page.locator("#file").set_input_files(nef)
    metadata=photo.value.json()
    page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    return metadata


def workflow(browser,args,cpu=False):
    page=browser.new_page(viewport={"width":1440,"height":1100})
    if cpu:page.add_init_script("""const get=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t,...a){return t==='webgl2'?null:get.call(this,t,...a)}""")
    errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto(args.url);metadata=load(page,args.nef)
    if not cpu:shader_checks(page)
    exposure=page.locator("#exposure");exposure.focus();page.keyboard.press("ArrowRight")
    assert exposure.input_value()=="0.01"
    page.keyboard.press("ArrowLeft");assert exposure.input_value()=="0"
    page.locator("#curve_mid").fill("65");page.locator("#curve_mid").dispatch_event("input");page.locator("#curve_mid").dispatch_event("change")
    page.locator("#curve_light").fill("85");page.locator("#curve_light").dispatch_event("input");page.locator("#curve_light").dispatch_event("change")
    page.locator("#undo").click();assert page.locator("#curve_light").input_value()=="75"
    page.locator("#redo").click();assert page.locator("#curve_light").input_value()=="85"
    # The real FileSystemDirectoryHandle/writer runs in OPFS; only the native chooser is stubbed.
    page.evaluate("""window.showDirectoryPicker=async options=>{
      window.pickerOptions=options;
      return (await navigator.storage.getDirectory()).getDirectoryHandle('mydarkroom-test',{create:true});
    }""")
    page.locator("#export").click();page.locator("#choose-output").click()
    page.wait_for_function("document.getElementById('output-folder').textContent==='mydarkroom-test'")
    assert page.evaluate("window.pickerOptions.mode")=="readwrite"
    page.locator("#size-custom").click();page.locator("#export-edge").fill("960")
    page.locator("#export-dpi").fill("240");page.locator("#export-quality").fill("90");page.locator("#export-name").fill("stage5.jpg")
    page.locator("#export-start").click();page.wait_for_function("document.getElementById('status').textContent.includes('저장 완료')",timeout=120000)
    data=page.evaluate("""async()=>{
      const dir=await(await navigator.storage.getDirectory()).getDirectoryHandle('mydarkroom-test');
      return [...new Uint8Array(await(await(await dir.getFileHandle('stage5.jpg')).getFile()).arrayBuffer())];
    }""")
    with Image.open(BytesIO(bytes(data))) as image:
        assert max(image.size)==960 and image.info["dpi"]==(240,240) and image.info["icc_profile"]
    page.locator("#export").click();page.locator("#export-name").fill("stage5.jpg");page.locator("#size-small").click()
    page.locator("#export-start").click();page.wait_for_function("document.getElementById('status').textContent.includes('stage5 (2).jpg')",timeout=120000)
    size=page.evaluate("""async()=>{
      const dir=await(await navigator.storage.getDirectory()).getDirectoryHandle('mydarkroom-test');
      const file=await(await dir.getFileHandle('stage5 (2).jpg')).getFile();const bitmap=await createImageBitmap(file);
      const size=[bitmap.width,bitmap.height];bitmap.close();return size;
    }""");assert max(size)==min(2000,max(metadata['width'],metadata['height']))
    assert not errors,errors
    assert page.locator("#error").is_hidden(),page.locator("#error").inner_text()
    print(f"{'CPU' if cpu else 'WebGL2'}: curve/undo, 0.01 keyboard, folder API/writer, 960px/240DPI, 2000px and collision preservation passed",flush=True)
    page.close()


def stress(browser,args):
    page=browser.new_page(viewport={"width":1200,"height":900})
    page.add_init_script("""window.resources={};
      for(const kind of ['Texture','Framebuffer','Buffer','Program']){
        const values=new Set();resources[kind]=values;
        const p=WebGL2RenderingContext.prototype,c=p['create'+kind],d=p['delete'+kind];
        p['create'+kind]=function(...a){const v=c.apply(this,a);if(v)values.add(v);return v};
        p['delete'+kind]=function(v){values.delete(v);return d.call(this,v)};
      }""")
    page.goto(args.url);cdp=page.context.new_cdp_session(page);measurements=[]
    for i in range(args.iterations+3):
        photo=load(page,args.nef)
        page.locator("#mask-kind").select_option("gradient");page.locator("#mask-add").click()
        page.locator("#local-exposure").fill("0.5");page.locator("#local-exposure").dispatch_event("input");page.locator("#local-exposure").dispatch_event("change")
        page.wait_for_function("!document.getElementById('export').disabled && document.getElementById('status').textContent.includes('완료')",timeout=60000)
        page.wait_for_function("Number(document.getElementById('histogram').dataset.count)>0",timeout=60000)
        response=page.request.post(f"{args.url}/api/photos/{photo['id']}/export?long_edge=2000&dpi=300",data={"masks":[{"kind":"gradient","adjustments":{"exposure":.5}}]})
        assert response.status==200;response.dispose()
        cdp.send("HeapProfiler.collectGarbage")
        memory=cdp.send("Runtime.getHeapUsage")
        counts=page.evaluate("Object.fromEntries(Object.entries(resources).map(([k,v])=>[k,v.size]))")
        nodes=cdp.send("Memory.getDOMCounters")
        row={"iteration":i,"rss":rss(args.pid),"js":memory['usedSize']+memory.get('backingStorageSize',0),"resources":counts,"nodes":nodes['nodes']}
        print(json.dumps(row),flush=True)
        if i>=3:measurements.append(row)
    assert len({json.dumps(r['resources'],sort_keys=True) for r in measurements})==1,"GPU resources grew across replacements"
    assert measurements[-1]['js']-measurements[0]['js']<8*1024*1024,"JS retained storage grew unexpectedly"
    assert measurements[-1]['nodes']-measurements[0]['nodes']<100,"DOM nodes grew unexpectedly"
    if measurements[0]['rss'] is not None:
        assert measurements[-1]['rss']-measurements[0]['rss']<48*1024*1024,"Server RSS did not stabilize after warm-up"
    print("Bounded-resource stress passed (finite test; not a proof of zero leaks on every platform)",flush=True)
    page.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--nef",required=True);parser.add_argument("--url",default="http://127.0.0.1:8765")
    parser.add_argument("--chromium");parser.add_argument("--software-gl",action="store_true");parser.add_argument("--pid",type=int);parser.add_argument("--iterations",type=int,default=6)
    args=parser.parse_args();args.nef=str(Path(args.nef).resolve())
    with sync_playwright() as p:
        options={"headless":True}
        if args.chromium:options["executable_path"]=args.chromium
        if args.software_gl:options["args"]=["--enable-unsafe-swiftshader","--use-angle=swiftshader"]
        browser=p.chromium.launch(**options);workflow(browser,args);workflow(browser,args,cpu=True)
        stress(browser,args);browser.close()
