"""One local editor process, stable browser origin, native launcher and clean exit."""
import argparse
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import Request,build_opener,ProxyHandler
import webbrowser

from .version import VERSION

LOG = logging.getLogger("mydarkroom")
# Local IPC must never pass through an HTTP proxy or send its token off-device.
LOCAL_HTTP = build_opener(ProxyHandler({}))


def read_json(path):
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def write_json(path,value):
    pending=path.with_suffix(".pending")
    pending.write_text(json.dumps(value,ensure_ascii=False),encoding="utf-8")
    pending.replace(path)


def profile_path():
    root=os.environ.get("LOCALAPPDATA") if os.name=="nt" else os.environ.get("XDG_DATA_HOME")
    return Path(root or Path.home()/".local"/"share")/"MyDarkRoom"


class InstanceLock:
    def __init__(self,directory):
        self.directory=directory;self.handle=None;self.file=None

    def acquire(self):
        if os.name=="nt":
            from ctypes import wintypes
            kernel=ctypes.WinDLL("kernel32",use_last_error=True)
            kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
            kernel.CreateMutexW.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            self.kernel=kernel
            digest=hashlib.sha256(str(self.directory.resolve()).casefold().encode()).hexdigest()[:24]
            ctypes.set_last_error(0)
            handle=kernel.CreateMutexW(None,False,"Local\\MyDarkRoom-"+digest)
            if not handle:raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error()==183:
                kernel.CloseHandle(handle);return False
            self.handle=handle;return True
        import fcntl
        self.file=(self.directory/"instance.lock").open("a+b")
        try:fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:self.close();return False
        return True

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None
        if self.file:self.file.close();self.file=None


def bind_loopback(preferred):
    for port in dict.fromkeys([preferred,0]):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        if os.name=="nt":sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:sock.bind(("127.0.0.1",port));return sock
        except OSError:sock.close()
    raise OSError("로컬 편집 화면에 사용할 포트를 열 수 없습니다.")


def control(state,command,timeout=2):
    port=state.get("port");token=state.get("token")
    if not isinstance(port,int) or not 0<port<65536 or not isinstance(token,str):return False
    request=Request(f"http://127.0.0.1:{port}/desktop/{command}",data=b"",headers={"X-MyDarkRoom-Token":token},method="POST")
    try:
        with LOCAL_HTTP.open(request,timeout=timeout) as response:return response.status==200
    except (URLError,OSError,ValueError):return False


def activate_existing(directory,timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if control(read_json(directory/"instance.json"),"activate",timeout=1):return True
        time.sleep(.2)
    return False


def open_editor(url):
    if os.name=="nt":
        # Edge/Chrome retain File System Access, WebGL2 and the user's normal GPU driver.
        preferred="msedge"
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
                if "chrome" in winreg.QueryValueEx(key,"ProgId")[0].lower():preferred="chrome"
        except OSError:pass
        browsers={"msedge":"Microsoft/Edge/Application/msedge.exe","chrome":"Google/Chrome/Application/chrome.exe"}
        for name in [preferred,"chrome" if preferred=="msedge" else "msedge"]:
            for root in ["ProgramFiles(x86)","ProgramFiles","LOCALAPPDATA"]:
                if not os.environ.get(root):continue
                exe=Path(os.environ[root])/browsers[name]
                if exe.is_file():
                    subprocess.Popen([str(exe),url],creationflags=subprocess.CREATE_NO_WINDOW)
                    return True
    return webbrowser.open(url)


def configure_logs(directory):
    logs=directory/"logs";logs.mkdir(exist_ok=True)
    handler=RotatingFileHandler(logs/"MyDarkRoom.log",maxBytes=2_000_000,backupCount=2,encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
    # Windowed PyInstaller has no console streams; native libraries must still have streams.
    if sys.stdout is None:sys.stdout=open(os.devnull,"w")
    if sys.stderr is None:sys.stderr=open(os.devnull,"w")


def clean_cache(directory):
    directory.mkdir(exist_ok=True)
    # Called only while holding this profile's exclusive instance lock.
    for child in directory.glob("mydarkroom-*"):
        if child.is_dir() and not child.is_symlink() and not child.is_junction():
            try:shutil.rmtree(child)
            except OSError:LOG.warning("Could not remove a previous temporary session")


class Runtime:
    def __init__(self,directory,port):
        self.directory=directory;self.socket=bind_loopback(port)
        self.port=self.socket.getsockname()[1];self.url=f"http://127.0.0.1:{self.port}"
        self.token=secrets.token_urlsafe(32);self.commands=queue.SimpleQueue()
        self.finished=threading.Event();self.closing=False;self.server=None;self.failure=None
        self.thread=threading.Thread(target=self._serve,name="MyDarkRoom-engine",daemon=True)

    def start(self):
        cache=self.directory/"cache";clean_cache(cache);os.environ["MYDARKROOM_CACHE_DIR"]=str(cache)
        write_json(self.directory/"instance.json",dict(port=self.port,token=self.token,pid=os.getpid(),version=VERSION))
        write_json(self.directory/"preferences.json",{"port":self.port})
        self.thread.start()

    @property
    def ready(self):return bool(self.server and self.server.started and not self.finished.is_set())

    def _serve(self):
        try:
            import uvicorn
            from fastapi import HTTPException,Request as ApiRequest
            from .app import app
            runtime=self

            def authorized(request):
                if not secrets.compare_digest(request.headers.get("X-MyDarkRoom-Token",""),runtime.token):
                    raise HTTPException(403,"Desktop control requires the local instance token")

            @app.post("/desktop/activate",include_in_schema=False)
            def activate(request:ApiRequest):
                authorized(request);runtime.commands.put("activate");return {"status":"ok"}

            @app.post("/desktop/shutdown",include_in_schema=False)
            def shutdown(request:ApiRequest):
                authorized(request);runtime.commands.put("shutdown");return {"status":"ok"}

            config=uvicorn.Config(app,host="127.0.0.1",port=self.port,log_config=None,access_log=False,loop="asyncio",http="h11",ws="none",lifespan="on",workers=1)
            self.server=uvicorn.Server(config)
            if self.closing:self.server.should_exit=True
            LOG.info("MyDarkRoom %s starting on loopback port %s",VERSION,self.port)
            self.server.run(sockets=[self.socket])
        except BaseException as exc:
            self.failure=exc;LOG.exception("Editor engine stopped unexpectedly")
        finally:self.socket.close();self.finished.set()

    def stop(self):
        self.closing=True
        if self.server:self.server.should_exit=True

    def cleanup(self):
        state=self.directory/"instance.json"
        if read_json(state).get("token")==self.token:state.unlink(missing_ok=True)
        self.socket.close()


def headless(runtime,auto_open):
    opened=False
    try:
        while not runtime.finished.wait(.1):
            if runtime.ready and auto_open and not opened:open_editor(runtime.url);opened=True
            while not runtime.commands.empty():
                command=runtime.commands.get()
                if command=="shutdown":runtime.stop()
                elif command=="activate" and auto_open:open_editor(runtime.url)
    except KeyboardInterrupt:
        runtime.stop();runtime.thread.join(200)
    return 1 if runtime.failure else 0


def show_error(message):
    LOG.error("%s",message)
    if os.name=="nt":ctypes.windll.user32.MessageBoxW(None,message,"MyDarkRoom",0x10)


def main(argv=None):
    parser=argparse.ArgumentParser(description="MyDarkRoom Windows launcher")
    parser.add_argument("--data-dir",type=Path,default=profile_path())
    parser.add_argument("--port",type=int,choices=range(0,65536),metavar="PORT")
    parser.add_argument("--headless",action="store_true",help="Diagnostic mode without the launcher window")
    parser.add_argument("--no-browser",action="store_true",help="Do not open a browser automatically")
    args=parser.parse_args(argv)
    for name in ["OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"]:os.environ.setdefault(name,"4")
    directory=args.data_dir.expanduser().resolve();directory.mkdir(parents=True,exist_ok=True)
    lock=InstanceLock(directory)
    if not lock.acquire():
        if activate_existing(directory):return 0
        show_error("이미 실행 중인 MyDarkRoom이 응답하지 않습니다. 실행 창을 확인해 주세요.");return 1
    runtime=None
    try:
        configure_logs(directory)
        preferred=args.port if args.port is not None else read_json(directory/"preferences.json").get("port",8765)
        if not isinstance(preferred,int) or not 0<=preferred<65536:preferred=8765
        runtime=Runtime(directory,preferred)
        if args.headless:
            runtime.start();return headless(runtime,not args.no_browser)
        from .desktop_ui import Launcher
        ui=Launcher(runtime,auto_open=not args.no_browser)
        runtime.start();ui.run();return 1 if runtime.failure else 0
    except BaseException:
        LOG.exception("Launcher failed")
        show_error(f"MyDarkRoom을 시작하지 못했습니다.\n로그: {directory/'logs'/'MyDarkRoom.log'}")
        return 1
    finally:
        if runtime:
            runtime.stop()
            if runtime.thread.is_alive():runtime.thread.join(200)
            runtime.cleanup()
        lock.close()
