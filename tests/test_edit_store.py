import hashlib
import json
from types import SimpleNamespace
import threading
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from darkroom.app import app
from darkroom.desktop_ui import Launcher
from darkroom.edit_store import EditStore, EditError, EditConflict, EditUpdate, PhotoIdentity, MAX_EDIT_BYTES
from darkroom.imaging import Adjustments


@pytest.fixture
def fake_raw(monkeypatch):
    monkeypatch.setattr("darkroom.app.develop", lambda *a, **kw: np.full((24, 36, 3), 12000, dtype=np.uint16))


def upload(client, name="photo.NEF", data=b"fixture RAW"):
    response = client.post("/api/photos", files={"file": (name, data)})
    assert response.status_code == 200, response.text
    return response.json()


def recipe():
    return Adjustments(exposure=.73, grain=40, grain_size=3, monochrome=True, texture=27,
        curve_points=[[0, .02], [.3, .4], [.6, .5], [1, .98]],
        geometry={"rotation":3.2,"vertical":.04,"horizontal":-.02,"crop":[.1,.15,.7,.6]},
        masks=[{"kind":"brush","name":"얼굴","adjustments":{"exposure":.3,"texture":-35,"sharpness":25},
                "strokes":[{"points":[{"x":.4,"y":.2},{"x":.42,"y":.25}],"transform":[1,0,0,0,1,0,.01,0,1],"erase":True}]},
               {"kind":"radial","density":[1,.9,.3,.1,0],"invert":True,"adjustments":{"clarity":35}}])


def test_edits_restore_after_restart_rename_and_cache_cleanup(fake_raw):
    settings = recipe().model_dump(mode="json")
    with TestClient(app) as client:
        photo = upload(client)
        assert photo["edits"]["settings"] is None and photo["edits"]["revision"] == 0
        url = f"/api/photos/{photo['id']}/edits"
        response = client.put(url, json={"revision":0,"settings":settings})
        assert response.status_code == 200 and response.json()["revision"] == 1
        directory = app.state.studio.directory
        assert client.delete(f"/api/photos/{photo['id']}").status_code == 200
        assert list(directory.iterdir()) == []
    assert not directory.exists()
    with TestClient(app) as client:
        renamed = upload(client, "다른 이름.NEF")
        assert renamed["identity"] == photo["identity"]
        assert renamed["edits"]["settings"] == settings
        assert renamed["edits"]["revision"] == 1
        other = upload(client, "photo.NEF", b"different RAW")
        assert other["edits"]["settings"] is None
        assert app.state.studio.get(renamed["id"]).path.read_bytes() == b"fixture RAW"


def test_edit_export_import_validates_identity_without_applying_early(fake_raw):
    with TestClient(app) as client:
        photo = upload(client);url = f"/api/photos/{photo['id']}/edits"
        exported = client.post(url+"/export", json=recipe().model_dump(mode="json")).json()
        assert exported["format"] == "mydarkroom-edit" and exported["photo"] == photo["identity"]
        imported = client.post(url+"/import", json=exported)
        assert imported.status_code == 200 and imported.json()["settings"] == exported["settings"]
        assert client.get(url).json()["settings"] is None  # Preview/undo before actual save.
        other = upload(client, data=b"other RAW")
        assert client.post(f"/api/photos/{other['id']}/edits/import", json=exported).status_code == 422
        exported["version"] = 99
        assert client.post(url+"/import", json=exported).status_code == 422


def test_converted_nef_restores_even_when_temporary_dng_bytes_change(monkeypatch):
    from pathlib import Path
    from darkroom import raw_support
    converted=[]
    monkeypatch.setattr(raw_support,"camera_model",lambda path:"NIKON Z5_2")
    monkeypatch.setattr(raw_support,"converter_path",lambda:Path("converter.exe"))
    def convert(command,**kwargs):
        data=f"DNG generated on open {len(converted)}".encode();converted.append(data)
        (Path(command[3])/"converted.dng").write_bytes(data)
        return SimpleNamespace(returncode=0)
    def native(path,*,preview,metadata):
        assert path.suffix==".dng"
        if metadata is not None:metadata.update(width=36,height=24)
        return np.zeros((24,36,3),dtype=np.uint16)
    monkeypatch.setattr(raw_support.subprocess,"run",convert)
    monkeypatch.setattr("darkroom.imaging._develop_native",native)
    with TestClient(app) as client:
        first=upload(client)
        assert first['decoder']=='Adobe DNG → LibRaw'
        assert first['identity']['sha256']==hashlib.sha256(b'fixture RAW').hexdigest()
        response=client.put(f"/api/photos/{first['id']}/edits",json={"revision":0,"settings":recipe().model_dump(mode="json")})
        assert response.status_code==200
    with TestClient(app) as client:
        reopened=upload(client,"moved.NEF")
        assert converted[0]!=converted[1] and reopened['identity']==first['identity']
        assert reopened['edits']['settings']==recipe().model_dump(mode="json")


def test_stale_writer_cannot_overwrite_newer_edits_or_wait_on_raw_lock(fake_raw):
    with TestClient(app) as client:
        first = upload(client);second = upload(client)
        a = f"/api/photos/{first['id']}/edits";b = f"/api/photos/{second['id']}/edits"
        with app.state.studio.lock:  # RAW/JPEG thread may hold this lock for seconds.
            assert client.put(a, json={"revision":0,"settings":{"exposure":.8}}).status_code == 200
        assert client.put(b, json={"revision":0,"settings":{"exposure":-.5}}).status_code == 409
        assert client.get(b).json()["settings"]["exposure"] == .8
        assert client.put(a, json={"revision":1,"settings":{}}).json()["revision"] == 2
        assert client.get(b).json()["settings"]["exposure"] == 0  # Reset is also durable.


def test_invalid_and_oversized_edits_do_not_replace_saved_state(fake_raw):
    with TestClient(app) as client:
        photo = upload(client);url = f"/api/photos/{photo['id']}/edits"
        assert client.put(url, json={"revision":0,"settings":{"grain":30}}).status_code == 200
        for settings in [{"unknown":1},{"exposure":9},{"masks":[{"kind":"radial","density":[1,2,0,0,0]}]}]:
            assert client.put(url, json={"revision":1,"settings":settings}).status_code == 422
        assert client.put(url, content=b"x"*(MAX_EDIT_BYTES+1)).status_code == 413
        assert client.put(url, headers={"Origin":"https://example.com"}, json={"revision":1,"settings":{}}).status_code == 403
        assert client.get(url).json()["settings"]["grain"] == 30


def test_backup_recovery_and_damaged_files_are_preserved(fake_raw):
    with TestClient(app) as client:
        photo = upload(client);url = f"/api/photos/{photo['id']}/edits"
        for revision, exposure in [(0,.2),(1,.6)]:
            assert client.put(url,json={"revision":revision,"settings":{"exposure":exposure}}).status_code == 200
        target = app.state.edits.directory / f"{photo['identity']['sha256']}.json"
        target.write_text("damaged")
        recovered = client.get(url).json()
        assert recovered["settings"]["exposure"] == .2 and recovered["warning"] and recovered["writable"]
        assert client.put(url,json={"revision":1,"settings":{"exposure":.4}}).status_code == 200
        assert json.loads(target.read_text())["settings"]["exposure"] == .4
        target.write_text("damaged");target.with_suffix(".bak").write_text("also damaged")
        protected = upload(client)["edits"]
        assert not protected["writable"] and protected["warning"]
        assert client.put(url,json={"revision":0,"settings":{}}).status_code == 503
        assert target.read_text() == "damaged" and target.with_suffix(".bak").read_text() == "also damaged"


def test_disk_write_failure_keeps_previous_edits(tmp_path, monkeypatch):
    photo = PhotoIdentity(sha256=hashlib.sha256(b"raw").hexdigest(),size=3,width=36,height=24)
    store = EditStore(tmp_path/"edits")
    store.save(photo,EditUpdate(revision=0,settings=Adjustments(exposure=.2)))
    original = store._atomic
    def disk_full(target, data):
        if target.suffix == ".json":raise OSError("Disk full")
        original(target,data)
    monkeypatch.setattr(store,"_atomic",disk_full)
    with pytest.raises(OSError):store.save(photo,EditUpdate(revision=1,settings=Adjustments(exposure=.8)))
    assert store.load(photo)[0].settings.exposure == .2
    assert not list(store.directory.glob("*.tmp"))


@pytest.mark.parametrize("success",[True,False])
def test_native_close_waits_for_save_and_stays_open_on_failure(success):
    completed = threading.Event();stopped = threading.Event()
    runtime = SimpleNamespace(finished=threading.Event(),closing=False,stop=stopped.set)
    def evaluate(script,callback):
        assert "mydarkroomFlushEdits" in script
        assert not stopped.is_set()
        callback({"ok":success});completed.set()
    launcher = Launcher.__new__(Launcher)
    launcher.runtime=runtime;launcher.loaded=True;launcher.saving=False
    launcher.window=SimpleNamespace(evaluate_js=evaluate,set_title=lambda value:None)
    assert launcher.close() is False
    assert completed.wait(2)
    deadline=time.monotonic()+2
    while launcher.saving and time.monotonic()<deadline:time.sleep(.01)
    assert stopped.is_set() == success
