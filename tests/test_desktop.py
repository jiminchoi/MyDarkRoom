import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import httpx
import pytest

from darkroom.desktop import InstanceLock,bind_loopback,clean_cache,control,read_json,write_json


def test_atomic_state_and_untrusted_control_addresses(tmp_path):
    path=tmp_path/'한글 state.json';write_json(path,{'port':8765,'label':'암실'})
    assert read_json(path)['label']=='암실' and not path.with_suffix('.pending').exists()
    path.write_text('broken');assert read_json(path)=={}
    for state in [{},{'port':'https://example.com','token':'x'},{'port':-1,'token':'x'},{'port':8765}]:
        assert not control(state,'activate')


def test_single_instance_and_blocked_port(tmp_path):
    first=InstanceLock(tmp_path);second=InstanceLock(tmp_path)
    try:
        assert first.acquire() and not second.acquire()
        first.close();assert second.acquire()
    finally:first.close();second.close()
    with socket.socket() as busy:
        busy.bind(('127.0.0.1',0));busy.listen()
        fallback=bind_loopback(busy.getsockname()[1])
        try:assert fallback.getsockname()[0]=='127.0.0.1' and fallback.getsockname()[1]!=busy.getsockname()[1]
        finally:fallback.close()


def test_cache_cleanup_only_removes_owned_directories(tmp_path):
    stale=tmp_path/'mydarkroom-stale';stale.mkdir();(stale/'source.nef').write_bytes(b'cache')
    original=tmp_path/'photos';original.mkdir();(original/'source.nef').write_bytes(b'original')
    clean_cache(tmp_path)
    assert not stale.exists() and (original/'source.nef').read_bytes()==b'original'


def test_real_launcher_start_duplicate_and_clean_shutdown(tmp_path):
    profile=tmp_path/'한글 프로필 with spaces';root=Path(__file__).resolve().parents[1]
    command=[sys.executable,str(root/'desktop.py'),'--headless','--no-browser','--data-dir',str(profile),'--port','0']
    env=os.environ.copy();env.pop('MYDARKROOM_CACHE_DIR',None)
    process=subprocess.Popen(command,cwd=tmp_path,env=env)
    client=httpx.Client(trust_env=False,timeout=3)
    try:
        deadline=time.monotonic()+30;state={};url=''
        while time.monotonic()<deadline:
            assert process.poll() is None
            state=read_json(profile/'instance.json')
            try:
                url=f"http://127.0.0.1:{state['port']}"
                if client.get(url+'/api/health').status_code==200:break
            except (KeyError,httpx.HTTPError):pass
            time.sleep(.1)
        else:pytest.fail('Desktop readiness timed out')
        assert client.get(url+'/static/local-spatial.js').status_code==200
        assert client.post(url+'/desktop/shutdown').status_code==403
        assert client.post(url+'/desktop/shutdown',headers={'X-MyDarkRoom-Token':state['token'],'Origin':'https://example.com'}).status_code==403
        duplicate=subprocess.run(command,cwd=tmp_path,env=env,timeout=25)
        assert duplicate.returncode==0 and process.poll() is None
        assert read_json(profile/'instance.json')['pid']==process.pid
        assert control(state,'shutdown')
        assert process.wait(timeout=20)==0
        assert not (profile/'instance.json').exists()
        assert not list((profile/'cache').glob('mydarkroom-*'))
        assert (profile/'logs'/'MyDarkRoom.log').is_file()
    finally:
        client.close()
        if process.poll() is None:process.terminate();process.wait(timeout=10)
