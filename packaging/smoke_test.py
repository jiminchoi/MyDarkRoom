"""Exercise the frozen Windows binary without Python on PATH, using a real NEF."""
import argparse
import ctypes
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import traceback
import urllib.request

import httpx
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from darkroom.version import VERSION

SAMPLE_URL="https://raw.githubusercontent.com/letmaik/rawpy/401a9e5e4cd2348b8d11bdddd567915de07c0a83/test/iss030e122639.NEF"
SAMPLE_SHA="5922721d13f11795557d97fdeb0a60b900086c402bc82a848ff280d15b99ffd4"


def sample(path):
    if not path.exists():
        for attempt in range(3):
            try:
                urllib.request.urlretrieve(SAMPLE_URL,path);break
            except OSError:
                if attempt==2:raise
                time.sleep(1)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==SAMPLE_SHA,"NEF sample checksum mismatch"


def wait_ready(process,profile,client):
    start=time.monotonic()
    while time.monotonic()-start<90:
        assert process.poll() is None,f"Launcher exited early: {process.returncode}"
        try:
            state=json.loads((profile/"instance.json").read_text(encoding="utf-8"))
            url=f"http://127.0.0.1:{state['port']}"
            health=client.get(url+"/api/health",timeout=1)
            if health.status_code==200 and health.json().get("app_version")==VERSION:
                return state,url,round(time.monotonic()-start,3)
        except (OSError,ValueError,KeyError,httpx.HTTPError):pass
        time.sleep(.1)
    raise AssertionError("Frozen app readiness timed out")


def windows_visible_window(pid):
    from ctypes import wintypes
    user=ctypes.WinDLL("user32",use_last_error=True);found=[]
    callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes=[wintypes.HWND]
    user.EnumWindows.argtypes=[callback,wintypes.LPARAM]
    def visit(window,_):
        owner=wintypes.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(owner))
        if owner.value==pid and user.IsWindowVisible(window):found.append(window)
        return True
    user.EnumWindows(callback(visit),0)
    return bool(found)


def run(args):
    sample(args.nef)
    report={"version":VERSION,"platform":sys.platform,"checks":[]}
    with TemporaryDirectory(prefix="mydarkroom-exe-smoke-",ignore_cleanup_errors=True) as work:
        work=Path(work);bundle=work/"배포 폴더 with spaces"/"MyDarkRoom"
        shutil.copytree(args.bundle,bundle)
        exe=bundle/"MyDarkRoom.exe";assert exe.is_file()
        assert list(bundle.rglob("python312.dll")),"Bundled Python missing"
        assert list(bundle.rglob("raw_r.dll")),"LibRaw DLL missing"
        assert list(bundle.rglob("vcruntime140.dll")),"VC runtime DLL missing"
        before={str(p.relative_to(bundle)):p.stat().st_size for p in bundle.rglob("*") if p.is_file()}
        profile=work/"사용자 프로필 user";env=os.environ.copy()
        for key in ["PYTHONPATH","PYTHONHOME","VIRTUAL_ENV"]:env.pop(key,None)
        env["PYTHONNOUSERSITE"]="1"
        env["PATH"]=str(Path(env.get("SystemRoot",r"C:\Windows"))/"System32")
        processes=[]
        # Explorer can propagate an Internet zone from a downloaded ZIP to DLLs.
        # Reproduce the old bootstrap failure, then keep the marks during real UI tests.
        managed=[]
        for name in ['Python.Runtime.dll','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll']:
            paths=list(bundle.rglob(name));assert paths,f'Missing managed assembly: {name}'
            managed+=paths
        for dll in managed:
            Path(str(dll)+':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n',encoding='ascii')
        config=bundle/'MyDarkRoom.exe.config';assert config.is_file()
        disabled=config.with_suffix('.config.disabled');config.rename(disabled)
        baseline_report=work/'blocked-baseline.json'
        try:
            baseline=subprocess.run([str(exe),'--check-ui-runtime',str(baseline_report),'--data-dir',str(profile)],env=env,cwd=work,timeout=30)
        finally:disabled.rename(config)
        baseline_result=json.loads(baseline_report.read_text(encoding='utf-8'))
        assert baseline.returncode==1 and 'Failed to resolve Python.Runtime.Loader.Initialize' in baseline_result.get('error',''),baseline_result
        fixed_report=work/'fixed-runtime.json'
        fixed=subprocess.run([str(exe),'--check-ui-runtime',str(fixed_report),'--data-dir',str(profile)],env=env,cwd=work,timeout=30)
        fixed_result=json.loads(fixed_report.read_text(encoding='utf-8'))
        assert fixed.returncode==0 and fixed_result['passed'],fixed_result
        assert fixed_result['assembly_zone']==3,'Test must retain the download mark'
        report['checks']+=['Internet-marked DLL bootstrap failure reproduced without app config','Internet-marked Python.NET and WebView2 assemblies load with app config','download marks retained without unblocking Windows files']
        report['native_runtime']=fixed_result
        with httpx.Client(trust_env=False,timeout=120) as client:
            def start(*extra,extra_env=None):
                process=subprocess.Popen([str(exe),"--no-browser","--data-dir",str(profile),*extra],cwd=work,env={**env,**(extra_env or {})})
                processes.append(process)
                state,url,elapsed=wait_ready(process,profile,client)
                return process,state,url,elapsed
            def stop(process,state,url):
                response=client.post(url+"/desktop/shutdown",headers={"X-MyDarkRoom-Token":state["token"]})
                assert response.status_code==200
                assert process.wait(timeout=30)==0
                assert not (profile/"instance.json").exists()
                assert not list((profile/"cache").glob("mydarkroom-*")),"Temporary RAW/mmap cache survived exit"
            try:
                process,state,url,elapsed=start("--headless","--port","0")
                report["first_ready_seconds"]=elapsed
                assert client.post(url+"/desktop/shutdown").status_code==403
                assert client.post(url+"/desktop/shutdown",headers={"X-MyDarkRoom-Token":state["token"],"Origin":"https://example.com"}).status_code==403
                assert client.get(url).status_code==200
                for asset in ["app.js","gpu.js","local-spatial.js","masks.js","histogram-worker.js"]:
                    assert client.get(url+"/static/"+asset).status_code==200
                opened=client.post(url+"/api/photos",files={"file":("한글 사진.NEF",args.nef.read_bytes(),"application/octet-stream")})
                assert opened.status_code==200,opened.text
                photo=opened.json();api=url+"/api/photos/"+photo["id"]
                assert photo['preview_width']*photo['preview_height']<=4_000_000
                values={"exposure":.25,"clarity":20,"texture":30,"curve_points":[[0,0],[.25,.2],[.5,.55],[.75,.8],[1,1]],
                    "geometry":{"rotation":5,"vertical":.1,"crop":[.1,.1,.8,.8]},
                    "masks":[{"kind":"radial","adjustments":{"exposure":.2,"dehaze":15,"clarity":25,"texture":40,"sharpness":65}}]}
                preview=client.post(api+"/preview",json=values);assert preview.status_code==200,preview.text
                Image.open(BytesIO(preview.content)).load()
                raw=client.get(api+"/pixels");assert raw.status_code==200 and len(raw.content)==photo['preview_width']*photo['preview_height']*6
                for suffix in ["/structure","/structure?kind=sharpness"]:assert client.get(api+suffix).status_code==200
                assert client.post(api+"/mask-fields",json=values).status_code==200
                assert client.post(api+"/region?x=60&y=70&width=300&height=200&masked=true",json=values).status_code==200
                viewport=client.post(api+"/viewport?x=60&y=70&width=300&height=200",json=values)
                assert viewport.status_code==200 and Image.open(BytesIO(viewport.content)).size==(300,200)
                exported=client.post(api+"/export?long_edge=2000&dpi=240&quality=95",json=values)
                assert exported.status_code==200,exported.text
                with Image.open(BytesIO(exported.content)) as image:
                    image.load();assert max(image.size)==2000 and image.info["icc_profile"] and image.info['dpi']==(240,240)
                assert client.post(url+"/api/photos",files={"file":("broken.nef",b"broken")}).status_code==422
                assert hashlib.sha256(args.nef.read_bytes()).hexdigest()==SAMPLE_SHA
                again=subprocess.run([str(exe),"--no-browser","--data-dir",str(profile)],env=env,cwd=work,timeout=30)
                assert again.returncode==0 and json.loads((profile/"instance.json").read_text())["pid"]==process.pid
                port=state['port'];stop(process,state,url)
                report['checks'] += ['no Python on PATH','Unicode install and profile paths','static files','real NEF and LibRaw DLL','4MP preview','local/global controls and curve','crop/perspective native ROI','JPEG DPI and ICC','single instance','authenticated control','source unchanged','Windows mmap and cache cleanup']
                if args.gui:
                    with socket.socket() as debug_socket:
                        debug_socket.bind(('127.0.0.1',0));debug_port=debug_socket.getsockname()[1]
                    process,state,url,elapsed=start('--webview-debug-port',str(debug_port),extra_env={'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS':'--enable-unsafe-swiftshader --use-angle=swiftshader'})
                    assert state['port']==port,"Browser origin changed on restart"
                    deadline=time.monotonic()+45
                    while not windows_visible_window(process.pid) and time.monotonic()<deadline:
                        assert process.poll() is None,'Embedded window process exited early'
                        time.sleep(.1)
                    assert windows_visible_window(process.pid),"Native editor window missing"
                    from embedded_smoke import verify
                    report['restart_ready_seconds']=elapsed
                    report['checks'] += verify(process,profile,url,debug_port,args.nef,work)
                    report['checks'] += ['restart and stable origin']
                with socket.socket() as occupied:
                    occupied.bind(("127.0.0.1",0));occupied.listen();busy=occupied.getsockname()[1]
                    process,state,url,_=start("--headless","--port",str(busy))
                    assert state['port']!=busy;stop(process,state,url)
                    report['checks'].append('occupied port fallback')
                after={str(p.relative_to(bundle)):p.stat().st_size for p in bundle.rglob("*") if p.is_file()}
                assert before==after,"Application wrote inside the installation folder"
                report['checks'].append('no installation-folder writes')
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=10)
                        except subprocess.TimeoutExpired:process.kill()
                if args.report:
                    args.report.parent.mkdir(parents=True,exist_ok=True)
                    if (profile/"logs").is_dir():shutil.copytree(profile/"logs",args.report.parent/"smoke-logs",dirs_exist_ok=True)
        report['passed']=True
        if args.report:args.report.write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument('--bundle',type=Path,default=ROOT/'dist/MyDarkRoom');parser.add_argument('--nef',type=Path,required=True);parser.add_argument('--gui',action='store_true');parser.add_argument('--report',type=Path);parser.add_argument('--download-only',action='store_true');args=parser.parse_args()
    if args.download_only:sample(args.nef)
    else:
        try:run(args)
        except BaseException:
            def annotate(title,value):
                value=value[-18000:].replace('%','%25').replace('\r','%0D').replace('\n','%0A')
                print(f'::error title={title}::{value}',flush=True)
            annotate('Frozen EXE smoke failure',traceback.format_exc())
            if args.report:
                for log in (args.report.parent/'smoke-logs').glob('*.log'):
                    annotate('Frozen EXE runtime log',log.read_text(encoding='utf-8',errors='replace'))
            raise
