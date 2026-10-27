"""Exercise an upgrade in an actual browser with a fresh legacy HTTP cache."""
from contextlib import AbstractContextManager
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener


class UpgradeServer(AbstractContextManager):
    def __init__(self, target, assets=None):
        self.target=target;self.legacy=True;self.hits=[]
        # The old screen asks the current API for its version, just as 0.12 did.
        self.assets=assets or {
            '/':b'<html><head><link rel="stylesheet" href="/static/style.css"><script type="module" src="/static/app.js"></script></head><body><p id="legacy-editor">Old cached editor</p><p id="engine"></p></body></html>',
            '/static/app.js':b'import {legacy} from "./gpu.js";window.legacyEditor=legacy;fetch("/api/health").then(r=>r.json()).then(d=>document.getElementById("engine").textContent=d.app_version);',
            '/static/gpu.js':b'export const legacy=true;',
            '/static/style.css':b'body{background:#101212;color:white;}',
        }
        owner=self;opener=build_opener(ProxyHandler({}))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                owner.hits.append(self.path)
                if owner.legacy and self.path in owner.assets:
                    data=owner.assets[self.path]
                    self.send_response(200)
                    self.send_header('Content-Type','text/html' if self.path=='/' else 'text/css' if self.path.endswith('.css') else 'text/javascript')
                    self.send_header('Cache-Control','max-age=86400')
                    self.send_header('Last-Modified',formatdate(time.time()-172800,usegmt=True))
                    self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                    return
                try:
                    with opener.open(owner.target+self.path,timeout=15) as response:
                        data=response.read();self.send_response(response.status)
                        for key,value in response.headers.items():
                            if key.lower() not in ('date','server','connection','transfer-encoding'):self.send_header(key,value)
                        self.end_headers();self.wfile.write(data)
                except HTTPError as exc:self.send_error(exc.code)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):self.thread.start();return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(5)


def verify_upgrade(page, target, version):
    with UpgradeServer(target) as server:
        page.goto(server.url+'/');page.wait_for_function('window.legacyEditor===true')
        page.wait_for_function('(v)=>document.getElementById("engine").textContent===v',arg=version)
        page.evaluate("localStorage.setItem('mydarkroom.upgrade-test','preserved')")
        assert page.locator('#edit-save').count()==0
        server.legacy=False;server.hits.clear()
        page.goto(server.url+'/');page.wait_for_function('window.legacyEditor===true')
        assert page.locator('#edit-save').count()==0 and '/' not in server.hits,'Legacy cache was not reproduced'
        server.hits.clear();page.goto(server.url+'/?v='+version)
        page.wait_for_function("typeof window.mydarkroomFlushEdits==='function' && document.querySelectorAll('details.category').length===9",timeout=60000)
        assert page.locator('#edit-save').count()==1 and page.locator('#edit-export').count()==1
        assert page.evaluate("localStorage.getItem('mydarkroom.upgrade-test')")=='preserved'
        assert page.locator('script[type="module"]').get_attribute('src')==f'/static/{version}/app.js'
        assert any(f'/static/{version}/edit-save.js' in path for path in server.hits)
        assert any(f'/static/{version}/gpu.js' in path for path in server.hits)
        assert any(f'/static/{version}/histogram-worker.js' in path for path in server.hits)
        assert '/static/app.js' not in server.hits and '/static/gpu.js' not in server.hits
    return ['legacy cached screen with current API version reproduced','versioned HTML/modules/worker bypass fresh legacy cache','upgrade preserves browser localStorage without deleting profile']
