"""Embedded WebView2 editor; rendering stays in the existing WebGL2 canvas."""
import html
import logging
import os
import threading
from .native_export import NativeApi
from .desktop import read_json,write_json


def webview2_available():
    if os.name!='nt':return False
    import winreg
    client=r'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive,prefix in [(winreg.HKEY_CURRENT_USER,'SOFTWARE\\'),(winreg.HKEY_LOCAL_MACHINE,'SOFTWARE\\WOW6432Node\\'),(winreg.HKEY_LOCAL_MACHINE,'SOFTWARE\\')]:
        try:
            with winreg.OpenKey(hive,prefix+client) as key:
                if winreg.QueryValueEx(key,'pv')[0] not in ['','0','0.0.0.0']:return True
        except OSError:pass
    return False


class Launcher:
    def __init__(self,runtime,debug_port=None):
        if not webview2_available():
            raise RuntimeError('Microsoft Edge WebView2 Runtime이 필요합니다. https://go.microsoft.com/fwlink/p/?LinkId=2124703')
        import webview
        self.webview=webview;self.runtime=runtime;self.loaded=False;self.saving=False
        webview.settings['ALLOW_DOWNLOADS']=True
        webview.settings['ALLOW_FILE_URLS']=False
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG']=False
        if debug_port is not None:
            if not 1024<=debug_port<=65535:raise ValueError('Invalid diagnostic port')
            webview.settings['REMOTE_DEBUGGING_PORT']=debug_port
        state=read_json(runtime.directory/'window.json')
        width=max(960,min(2560,state.get('width',1440)));height=max(640,min(1600,state.get('height',960)))
        self.api=NativeApi(runtime)
        self.window=webview.create_window('MyDarkRoom',html='<html lang="ko"><body style="background:#101212;color:#eee;font:18px sans-serif;padding:40px">MyDarkRoom · 편집 화면을 준비하고 있어요…</body></html>',
            width=width,height=height,min_size=(960,640),background_color='#101212',js_api=self.api)
        self.api._window=self.window
        self.window.events.closing+=self.close
        self.window.events.closed+=runtime.stop

    def close(self):
        if self.runtime.finished.is_set():return True
        if self.saving or self.runtime.closing:return False
        if not self.loaded:self.runtime.stop();return False
        self.saving=True;self.window.set_title('MyDarkRoom · 보정정보 저장 중…')
        # Closing events run on the GUI thread. Wait for the JS promise on a
        # worker, so WebView2 and the local save endpoint can keep responding.
        def flush_and_stop():
            try:
                completed=threading.Event();result=[]
                def done(value):result.append(value);completed.set()
                self.window.evaluate_js('window.mydarkroomFlushEdits ? window.mydarkroomFlushEdits() : Promise.resolve({ok:true})',callback=done)
                if not completed.wait(30) or not result or not isinstance(result[0],dict) or not result[0].get('ok'):
                    raise RuntimeError('Photo edits were not saved; the editor remains open')
                self.runtime.stop();self.window.set_title('MyDarkRoom · 작업과 임시 파일 정리 중…')
            except Exception:
                logging.exception('Could not flush edits before close')
                self.window.set_title('MyDarkRoom · 보정정보 저장 실패 · 다시 저장해 주세요')
            finally:self.saving=False
        threading.Thread(target=flush_and_stop,name='MyDarkRoom-save-on-close',daemon=True).start()
        return False

    def monitor(self):
        try:
            if self.webview.renderer!='edgechromium':raise RuntimeError('WebView2 renderer did not initialize')
            while not self.runtime.finished.wait(.1):
                if self.runtime.ready and not self.loaded and not self.runtime.closing:
                    self.loaded=True;self.window.load_url(self.runtime.editor_url)
                while not self.runtime.commands.empty():
                    command=self.runtime.commands.get()
                    if command=='shutdown':self.close()
                    elif command=='activate' and not self.runtime.closing:self.window.restore();self.window.show()
            if self.runtime.failure and not self.runtime.closing:
                self.window.load_html('<body style="background:#101212;color:white;padding:30px">실행에 문제가 생겼습니다. 로그를 확인해 주세요.<p>'+html.escape(str(self.runtime.directory/'logs'))+'</p></body>')
                return
            write_json(self.runtime.directory/'window.json',{'width':int(self.window.width),'height':int(self.window.height)})
            self.window.destroy()
        except Exception as exc:
            logging.exception('Embedded editor failed');self.runtime.failure=exc;self.runtime.stop()
            self.runtime.finished.wait(200);self.window.destroy()

    def run(self):
        from .native_runtime import initialize
        initialize()
        self.webview.start(self.monitor,gui='edgechromium',debug=False,private_mode=False,storage_path=str(self.runtime.directory/'WebView2'))
