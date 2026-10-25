"""Run against a running app: python scripts/browser_check.py --nef path.NEF.

Install requirements-browser.txt and `python -m playwright install chromium` first,
or pass --chromium /path/to/an/existing/chromium. --software-gl is for CI only.
"""
import argparse
from pathlib import Path
import sys

import numpy as np
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from darkroom.imaging import Adjustments, render
from darkroom.structure import packed


def shader_checks(page):
    rgb = np.random.default_rng(42).integers(0, 65536, size=(4, 5, 3), dtype=np.uint16)
    rgb[0, 0] = 0
    rgb[-1, -1] = 65535
    cases = [{}, {"exposure": 1.2, "temperature": 50, "tint": -40},
             {"contrast": 70, "highlights": -80, "shadows": 60, "saturation": 30},
             {"saturation": -100}, {"exposure": -2, "temperature": -75, "tint": 60},
             {"monochrome":True,"mix_red":80,"mix_green":15,"mix_blue":5},
             {"monochrome":True,"mix_red":0,"mix_green":0,"mix_blue":0,"tone_hue":210,"tone_amount":45},
             {"grain":80,"grain_size":2,"tone_hue":35,"tone_amount":70},
             {"monochrome":True,"grain":100,"grain_size":1},
             {"curve_black":5,"curve_shadow":18,"curve_mid":60,"curve_light":85,"curve_white":95,"saturation":20},
             {"curve_mid":65,"curve_light":85,"monochrome":True,"grain":35},
             {"curve_points":[[0,.03],[.1,.05],[.2,.15],[.3,.26],[.4,.43],[.5,.62],[.6,.73],[.7,.8],[.8,.85],[.9,.92],[1,.98]],"saturation":15},
             {"curve_points":[[0,0],[.3,.7],[.6,.4],[1,1]],"monochrome":True},
             {"dehaze":100,"clarity":100,"texture":100},
             {"dehaze":-100,"clarity":-100,"texture":-100},
             {"dehaze":40,"clarity":-30,"texture":65,"exposure":.4,"temperature":-20}]
    for case in cases:
        settings = Adjustments(**case)
        result = page.evaluate("""async ({rgb,settings,fields}) => {
          const {GpuRenderer}=await import('/static/gpu.js');
          if (!window.checkRenderer) {
            const canvas=document.createElement('canvas'); canvas.width=5;canvas.height=4;
            window.checkRenderer=new GpuRenderer(canvas);
          }
          const renderer=window.checkRenderer;
          renderer.load({width:5,height:4,pixels:new Uint16Array(rgb)},{width:5,height:4});
          renderer.loadStructure({width:5,height:4,pixels:new Uint16Array(fields)});
          const pixels=renderer.histogram(settings,{scale:1,offset:[0,0],origin:[0,0]});
          return [...pixels];
        }""", {"rgb": rgb.ravel().tolist(), "settings": settings.model_dump(),"fields":packed(rgb,full_size=(5,4)).ravel().tolist()})
        # readPixels starts at the bottom; ndarray RGB starts at the top.
        actual = np.array(result, dtype=np.uint8).reshape(4, 5, 4)[::-1, :, :3]
        expected = np.asarray(render(rgb, settings))
        delta = np.abs(actual.astype(int) - expected.astype(int))
        assert delta.max() <= 1, (case, delta.max())
    bins = page.evaluate("""() => new Promise((resolve,reject) => {
      const worker=new Worker('/static/histogram-worker.js');
      worker.onmessage=({data})=>{worker.terminate();resolve({count:data.count,bins:[...data.bins]});};
      worker.onerror=reject;
      const pixels=new Uint8Array([0,10,255,255, 0,20,255,255, 100,20,0,255]);
      worker.postMessage({revision:1,pixels:pixels.buffer},[pixels.buffer]);
    })""")
    assert bins["count"] == 3
    assert bins["bins"][0] == 2 and bins["bins"][100] == 1
    assert bins["bins"][256+20] == 2 and bins["bins"][512+255] == 2
    assert sum(bins["bins"]) == 9
    print("GPU vs CPU: base/creative/curve cases, max error <= 1/255; histogram bins verified")


def workflow(browser, args, *, cpu=False):
    page = browser.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=args.dpr)
    if cpu:
        page.add_init_script("""const original=HTMLCanvasElement.prototype.getContext;
          HTMLCanvasElement.prototype.getContext=function(type,...args) {
            return type==='webgl2' ? null : original.call(this,type,...args);
          };""")
    errors, preview_requests = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: preview_requests.append(r.url) if "/preview" in r.url else None)
    page.goto(args.url)
    page.evaluate("document.querySelectorAll('details.category').forEach(e=>e.open=true)")
    page.wait_for_function("!document.getElementById('renderer-mode').textContent.includes('확인 중')")
    if not cpu:
        assert "WebGL2" in page.locator("#renderer-mode").inner_text()
        shader_checks(page)
    else:
        assert "CPU" in page.locator("#renderer-mode").inner_text()
    with page.expect_response(lambda r: r.url.endswith("/api/photos") and r.request.method == "POST") as response:
        page.locator("#file").set_input_files(args.nef)
    metadata = response.value.json()
    page.wait_for_function("!document.getElementById('controls').disabled", timeout=60000)
    page.wait_for_function("Number(document.getElementById('histogram').dataset.count)>0", timeout=60000)
    assert int(page.locator("#histogram").get_attribute("data-count")) == metadata["preview_width"]*metadata["preview_height"]
    assert metadata["preview_width"]*metadata["preview_height"] <= 4_000_000
    count = len(preview_requests)
    start_revision = int(page.locator("#histogram").get_attribute("data-revision"))
    page.evaluate("window.editEvents=0;document.getElementById('controls').addEventListener('input',()=>window.editEvents++)")
    for exposure in [0.4, -0.2, 0.8]:
        page.locator("#exposure").fill(str(exposure))
        page.locator("#exposure").dispatch_event("input")
    expected_revision = start_revision + page.evaluate("window.editEvents")
    page.wait_for_function("expected=>Number(document.getElementById('histogram').dataset.revision) === expected", arg=expected_revision, timeout=60000)
    if not cpu:
        assert len(preview_requests) == count, "GPU slider changes must not call server preview"
    edited = page.locator("#preview").evaluate("c=>c.toDataURL()")
    page.locator("#compare").dispatch_event("keydown", {"key": "Enter"})
    assert page.locator("#compare-label").is_visible()
    original = page.locator("#preview").evaluate("c=>c.toDataURL()")
    assert edited != original
    page.locator("#compare").dispatch_event("keyup", {"key": "Enter"})
    page.locator("#split").click()
    assert page.locator("#split-line").is_visible()
    page.locator("#split-position").fill("35")
    page.locator("#split-position").dispatch_event("input")
    page.wait_for_function("document.getElementById('split-line').style.left === '35%'")
    divided = page.locator("#preview").evaluate("c=>c.toDataURL()")
    assert divided != edited and divided != original
    if args.artifacts:
        page.screenshot(path=str(Path(args.artifacts)/f"stage2-{'cpu' if cpu else 'gpu'}.png"), full_page=True)
    page.locator("#zoom").click()
    page.wait_for_function("Boolean(document.getElementById('preview').dataset.detail)", timeout=60000)
    first = page.locator("#preview").get_attribute("data-detail")
    x,y,width,height = map(int,first.split(","))
    size = page.locator("#preview").evaluate("c=>[c.width,c.height]")
    assert width >= min(size[0],metadata["width"]-x) and height >= min(size[1],metadata["height"]-y)
    if not cpu and metadata["width"] > size[0] and metadata["height"] > size[1]:
        mx, my = size[0]//2, size[1]//2
        v=page.locator("#preview").evaluate("c=>JSON.parse(c.dataset.view)")
        source_x=int(v["origin"][0]+mx+.5-v["offset"][0]);source_y=int(v["origin"][1]+my+.5-v["offset"][1])
        sample = page.request.get(f"{args.url}/api/photos/{metadata['id']}/detail?x={source_x}&y={source_y}&width=1&height=1")
        assert sample.status == 200
        rgb = np.frombuffer(sample.body(),dtype="<u2").reshape(1,1,3)
        expected = np.asarray(render(rgb,Adjustments(exposure=.8)))[0,0]
        actual = page.locator("#preview").evaluate("""(c,{x,y})=>{
          const gl=c.getContext('webgl2'),p=new Uint8Array(4);
          gl.readPixels(x,c.height-y-1,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return [...p].slice(0,3);
        }""", {"x":mx,"y":my})
        assert np.max(np.abs(np.array(actual,dtype=int)-expected.astype(int))) <= 1
    box=page.locator("#canvas").bounding_box()
    page.mouse.move(box["x"]+box["width"]*.5,box["y"]+box["height"]*.5)
    page.mouse.down();page.mouse.move(box["x"]+box["width"]*.5+90,box["y"]+box["height"]*.5+50,steps=5);page.mouse.up()
    page.wait_for_function("old=>document.getElementById('preview').dataset.detail !== old", arg=first, timeout=60000)
    page.locator("#fit").click()
    assert page.locator("#fit").get_attribute("aria-pressed") == "true"
    with page.expect_download(timeout=60000) as downloaded:
        page.locator("#export").click()
        page.locator("#export-start").click()
    assert downloaded.value.suggested_filename.endswith("-edited.jpg")
    page.locator("#reset").click()
    assert page.locator("#exposure").input_value() == "0"
    page.locator("#preset").select_option("mono")
    assert page.locator("#monochrome").is_checked()
    assert page.locator("#contrast").input_value()=="20"
    page.locator("#undo").click()
    assert not page.locator("#monochrome").is_checked()
    page.locator("#redo").click()
    assert page.locator("#monochrome").is_checked()
    page.locator("#grain").fill("55")
    page.locator("#grain").dispatch_event("change")
    page.locator("#grain").focus()
    page.keyboard.press("Control+z")
    assert page.locator("#grain").input_value()=="0"
    page.keyboard.press("Control+Shift+z")
    assert page.locator("#grain").input_value()=="55"
    page.locator("#preset-name").fill("테스트 프리셋 <>&")
    page.locator("#save-preset").click()
    saved_id=page.locator("#preset").input_value()
    assert saved_id.startswith("user-")
    with page.expect_download() as backup:
        page.locator("#preset-export").click()
    backup_bytes=Path(backup.value.path()).read_bytes()
    page.reload()
    assert page.locator(f'#preset option[value="{saved_id}"]').inner_text()=="테스트 프리셋 <>&"
    page.locator("#file").set_input_files(args.nef)
    page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    page.locator("#preset").select_option(saved_id)
    assert page.locator("#monochrome").is_checked() and page.locator("#grain").input_value()=="55"
    page.locator("#delete-preset").click()
    assert page.locator(f'#preset option[value="{saved_id}"]').count()==0
    page.locator("#preset-import-file").set_input_files({"name":"presets.json","mimeType":"application/json","buffer":backup_bytes})
    page.wait_for_function("document.getElementById('preset').value.startsWith('user-')")
    imported=page.locator("#preset").input_value()
    page.locator("#reset").click()
    page.locator("#preset").select_option(imported)
    assert page.locator("#grain").input_value()=="55" and page.locator("#monochrome").is_checked()
    if args.artifacts:
        page.screenshot(path=str(Path(args.artifacts)/f"stage3-{'cpu' if cpu else 'gpu'}.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    assert page.locator("#error").is_hidden(), page.locator("#error").inner_text()
    print(f"{'CPU fallback' if cpu else 'WebGL2'}: 4 MP preview, histogram, compare, split, 100%, pan, export, mobile passed")
    print("Stage 3: monochrome preset, undo/redo, user preset persistence, JSON backup/restore passed")
    page.close()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--nef", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--chromium")
    parser.add_argument("--software-gl", action="store_true")
    parser.add_argument("--dpr", type=float, default=1, help="Device pixel ratio, e.g. 1.5 for a 150%% display")
    parser.add_argument("--artifacts")
    args=parser.parse_args()
    args.nef=str(Path(args.nef).resolve())
    if args.artifacts: Path(args.artifacts).mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        launch={"headless":True}
        if args.chromium: launch["executable_path"]=args.chromium
        if args.software_gl: launch["args"]=["--enable-unsafe-swiftshader","--use-angle=swiftshader"]
        browser=p.chromium.launch(**launch)
        workflow(browser,args)
        workflow(browser,args,cpu=True)
        browser.close()
