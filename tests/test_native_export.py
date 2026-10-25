from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time

import pytest
from darkroom.native_export import NativeApi,export_options
from darkroom.desktop import Runtime


@pytest.mark.parametrize('name',['../x.jpg','a/b.jpg','NUL.jpg','com1.txt','x.jpg.','bad?.jpg',''])
def test_reject_unsafe_native_names(name):
    with pytest.raises(ValueError):export_options({'filename':name})


def test_native_export_folder_collision_cancel_and_origin(tmp_path,monkeypatch):
    calls=[]
    studio=SimpleNamespace(image=lambda *a,**k:(calls.append((a,k)) or b'jpeg result'))
    runtime=SimpleNamespace(url='http://127.0.0.1:9876',closing=False,native_lock=threading.Lock(),app=SimpleNamespace(state=SimpleNamespace(studio=studio)))
    selected=[str(tmp_path)]
    window=SimpleNamespace(get_current_url=lambda:runtime.url,create_file_dialog=lambda *a,**k:selected)
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(FileDialog=SimpleNamespace(FOLDER=20,SAVE=30)))
    api=NativeApi(runtime);api._window=window;folder=api.choose_output()
    options={'filename':'한글.jpg','directory_token':folder['native_token'],'longEdge':2000,'dpi':240,'quality':95}
    assert api.export_jpeg('photo',{},options)['filename']=='한글.jpg'
    assert api.export_jpeg('photo',{},options)['filename']=='한글 (2).jpg'
    assert (tmp_path/'한글.jpg').read_bytes()==b'jpeg result' and calls[-1][1]['dpi']==240
    with pytest.raises(ValueError):api.export_jpeg('photo',{},dict(options,directory_token='unselected'))
    selected.clear();assert api.export_jpeg('photo',{},dict(options,directory_token=None))=={'cancelled':True}
    window.get_current_url=lambda:'https://example.com'
    with pytest.raises(PermissionError):api.choose_output()


def test_save_as_uses_atomic_replace_and_preserves_file_on_failure(tmp_path,monkeypatch):
    target=tmp_path/'existing.jpg';target.write_bytes(b'original')
    def fail(*a,**k):raise ValueError('render failed')
    studio=SimpleNamespace(image=fail)
    runtime=SimpleNamespace(url='http://127.0.0.1:1234',closing=False,native_lock=threading.Lock(),app=SimpleNamespace(state=SimpleNamespace(studio=studio)))
    api=NativeApi(runtime);api._window=SimpleNamespace(get_current_url=lambda:runtime.url,create_file_dialog=lambda *a,**k:[str(target)])
    monkeypatch.setitem(sys.modules,'webview',SimpleNamespace(FileDialog=SimpleNamespace(SAVE=30)))
    with pytest.raises(ValueError):api.export_jpeg('photo',{},dict(filename='existing.jpg'))
    assert target.read_bytes()==b'original'
    studio.image=lambda *a,**k:b'new result';api.export_jpeg('photo',{},dict(filename='existing.jpg'))
    assert target.read_bytes()==b'new result' and not list(tmp_path.glob('*.tmp'))


def test_shutdown_waits_for_native_export_without_blocking_window(tmp_path):
    runtime=Runtime(tmp_path,0);runtime.server=SimpleNamespace(should_exit=False)
    try:
        with runtime.native_lock:
            before=time.monotonic();runtime.stop()
            assert time.monotonic()-before<.1 and not runtime.server.should_exit
        runtime._stop_thread.join(2);assert runtime.server.should_exit
    finally:runtime.cleanup()
