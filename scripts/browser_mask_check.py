"""Stage 4 interaction/preview/export checks against a running local server."""
import argparse
from io import BytesIO
from pathlib import Path
import sys
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from darkroom.imaging import Adjustments,render


def workflow(browser,args,cpu=False):
    page=browser.new_page(viewport={"width":1440,"height":1100},device_scale_factor=args.dpr)
    if cpu:
        page.add_init_script("""const original=HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl2'?null:original.call(this,type,...args)};""")
    errors=[];states=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    def request(r):
        if any(x in r.url for x in ["/masked-pixels","/preview","/export"]) and r.method=="POST":states.append(r.post_data_json)
    page.on("request",request)
    page.goto(args.url)
    with page.expect_response(lambda r:r.url.endswith("/api/photos") and r.request.method=="POST") as response:
        page.locator("#file").set_input_files(args.nef)
    photo=response.value.json();page.wait_for_function("!document.getElementById('controls').disabled",timeout=60000)
    page.wait_for_function("Number(document.getElementById('histogram').dataset.count)>0",timeout=60000)
    def idle():
        page.wait_for_function("!document.getElementById('export').disabled && (document.getElementById('status').textContent.includes('완료') || document.getElementById('status').textContent.includes('픽셀'))",timeout=60000)
    def add(kind):
        page.locator("#mask-kind").select_option(kind);page.locator("#mask-add").click()
    def slider(id,value):
        page.locator("#"+id).fill(str(value));page.locator("#"+id).dispatch_event("input");page.locator("#"+id).dispatch_event("change")
    def stroke(a,b):
        box=page.locator("#canvas").bounding_box()
        def pos(p):return (box["x"]+box["width"]*p[0],box["y"]+box["height"]*p[1])
        page.mouse.move(*pos(a));page.mouse.down();page.mouse.move(*pos(b),steps=12);page.mouse.up()
    add("brush");slider("brush-radius",.12)
    stroke((.4,.5),(.6,.5));slider("local-exposure",1);idle()
    page.wait_for_function("""()=>{const c=document.getElementById('mask-overlay'),ctx=c.getContext('2d');return ctx.getImageData(Math.floor(c.width/2),Math.floor(c.height/2),1,1).data[3]>0}""",timeout=60000)
    assert states[-1]["masks"][0]["strokes"]
    page.locator("#zoom").click();page.wait_for_function("Boolean(document.getElementById('preview').dataset.detail)",timeout=60000)
    if not cpu:
        x,y,w,h=map(int,page.locator("#preview").get_attribute("data-detail").split(","))
        size=page.locator("#preview").evaluate("c=>[c.width,c.height]")
        mx,my=size[0]//2,size[1]//2
        settings=states[-1]
        result=page.request.post(f"{args.url}/api/photos/{photo['id']}/masked-detail?x={x+mx}&y={y+my}&width=1&height=1",data=settings)
        assert result.status==200
        raw=np.frombuffer(result.body(),dtype="<u2").reshape(1,1,3)
        expected=np.asarray(render(raw,Adjustments(**{**settings,"masks":[]})))[0,0]
        actual=page.locator("#preview").evaluate("""(c,{x,y})=>{const gl=c.getContext('webgl2'),p=new Uint8Array(4);gl.readPixels(x,c.height-y-1,1,1,gl.RGBA,gl.UNSIGNED_BYTE,p);return [...p].slice(0,3)}""",{"x":mx,"y":my})
        assert np.max(np.abs(np.array(actual,dtype=int)-expected.astype(int)))<=1
    page.locator("#fit").click()
    page.locator("#brush-erase").check();stroke((.49,.5),(.51,.5));idle()
    assert states[-1]["masks"][0]["strokes"][-1]["erase"]
    add("gradient");stroke((.5,.35),(.5,.65));slider("local-shadows",35);page.locator("#mask-invert").check();idle()
    add("color")
    page.locator("#mask-pick").click()
    box=page.locator("#canvas").bounding_box()
    with page.expect_response(lambda r:"/sample-color?" in r.url) as picked:
        page.mouse.click(box["x"]+box["width"]*.5,box["y"]+box["height"]*.5)
    sampled=picked.value.json()["color"]
    expected_hex="#"+"".join(f"{round(v*255):02x}" for v in sampled)
    page.wait_for_function("value=>document.getElementById('mask-color').value===value",arg=expected_hex)
    page.locator("#mask-color").evaluate("e=>{e.value='#00ff00';e.dispatchEvent(new Event('input'));e.dispatchEvent(new Event('change'));}")
    slider("mask-tolerance",.45);slider("local-temperature",25);idle()
    add("luminance");slider("mask-low",0);slider("mask-high",.35);slider("local-contrast",20);idle()
    assert page.locator("#mask-select option").count()==4
    page.locator("#mask-delete").click();idle()
    assert page.locator("#mask-select option").count()==3
    page.locator("#undo").click();idle()
    assert page.locator("#mask-select option").count()==4
    page.locator("#redo").click();idle()
    assert page.locator("#mask-select option").count()==3
    page.locator("#undo").click();idle()
    # A global preset must not remove photo-specific masks.
    page.locator("#preset").select_option("mono");idle()
    assert page.locator("#mask-select option").count()==4
    page.locator("#compare").dispatch_event("keydown",{"key":"Enter"})
    assert page.locator("#compare-label").is_visible()
    assert page.locator("#mask-overlay").evaluate("c=>!c.getContext('2d').getImageData(Math.floor(c.width/2),Math.floor(c.height/2),1,1).data[3]")
    page.locator("#compare").dispatch_event("keyup",{"key":"Enter"})
    if args.artifacts:
        page.screenshot(path=str(Path(args.artifacts)/f"stage4-{'cpu' if cpu else 'gpu'}.png"),full_page=True)
    with page.expect_download(timeout=120000) as downloaded:
        page.locator("#export").click();page.locator("#export-start").click()
    with Image.open(BytesIO(Path(downloaded.value.path()).read_bytes())) as image:
        image.load();assert image.size==(photo["width"],photo["height"]) and image.info["icc_profile"]
    assert len(states[-1]["masks"])==4
    page.locator("#reset").click()
    assert page.locator("#mask-select option").count()==0
    assert not errors,errors
    assert page.locator("#error").is_hidden(),page.locator("#error").inner_text()
    print(f"{'CPU' if cpu else 'WebGL2'}: brush, eraser, gradient, color/luma range, local controls, overlay, undo/redo, preset preservation and full-size JPEG passed",flush=True)
    page.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--nef",required=True);parser.add_argument("--url",default="http://127.0.0.1:8765")
    parser.add_argument("--chromium");parser.add_argument("--software-gl",action="store_true");parser.add_argument("--dpr",type=float,default=1);parser.add_argument("--artifacts")
    args=parser.parse_args();args.nef=str(Path(args.nef).resolve())
    if args.artifacts:Path(args.artifacts).mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        launch={"headless":True}
        if args.chromium:launch["executable_path"]=args.chromium
        if args.software_gl:launch["args"]=["--enable-unsafe-swiftshader","--use-angle=swiftshader"]
        browser=p.chromium.launch(**launch)
        workflow(browser,args);workflow(browser,args,cpu=True);browser.close()
