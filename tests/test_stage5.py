from io import BytesIO
import os
from pathlib import Path
import subprocess
import struct

import numpy as np
from PIL import Image
import pytest
import rawpy
from fastapi.testclient import TestClient
from pydantic import ValidationError

from darkroom.app import app
from darkroom.imaging import Adjustments,jpeg,render
from darkroom.raw_support import compatible_develop,RawCompatibilityError,camera_model


def test_curve_identity_mapping_and_validation():
    rgb=np.linspace(0,65535,80*3,dtype=np.uint16).reshape(1,80,3)
    original=np.asarray(render(rgb,Adjustments()))
    raised=np.asarray(render(rgb,Adjustments(curve_mid=65,curve_light=85)))
    assert np.array_equal(original[:,0],raised[:,0])
    assert raised[0,35,1]>original[0,35,1]
    with pytest.raises(ValidationError):Adjustments(curve_mid=90,curve_light=60)
    with pytest.raises(ValidationError):Adjustments(curve_white=101)


def test_export_dimensions_dpi_and_no_upscale():
    rgb=np.full((200,300,3),15000,dtype=np.uint16)
    data=jpeg(rgb,Adjustments(),quality=91,long_edge=150,dpi=240)
    image=Image.open(BytesIO(data));assert image.size==(150,100)
    assert image.info["dpi"]==(240,240) and image.info["icc_profile"]
    image=Image.open(BytesIO(jpeg(rgb,Adjustments(),quality=95,long_edge=2000,dpi=72)))
    assert image.size==(300,200) and image.info["dpi"]==(72,72)


def test_dng_fallback_commands_and_verified_cache(tmp_path,monkeypatch):
    from darkroom import raw_support
    path=tmp_path/"camera.nef";path.write_bytes(b"original-nef")
    calls=[]
    def native(source,*,preview,metadata):
        calls.append(source.suffix)
        if source.suffix==".nef":raise rawpy.LibRawFileUnsupportedError("unsupported HE")
        assert source.read_bytes()==b"converted-dng"
        return np.ones((4,6,3),dtype=np.uint16)
    monkeypatch.setattr(raw_support,"converter_path",lambda:Path("converter.exe"))
    def convert(command,**kwargs):
        assert command[1:3]==["-c","-d"]
        (Path(command[3])/"camera.dng").write_bytes(b"converted-dng")
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(raw_support.subprocess,"run",convert)
    metadata={}
    result=compatible_develop(path,native,preview=True,metadata=metadata)
    assert result.shape==(4,6,3) and metadata["decoder"]=="Adobe DNG → LibRaw"
    compatible_develop(path,native,preview=False,metadata=None)
    assert calls==[".nef",".dng",".dng"]
    assert path.read_bytes()==b"original-nef" and path.with_suffix(".dng").exists()
    assert not list(tmp_path.glob("dng-*"))


def test_unsupported_raw_has_actionable_error_and_preserves_corruption(tmp_path,monkeypatch):
    from darkroom import raw_support
    path=tmp_path/"camera.nef";path.write_bytes(b"bad")
    monkeypatch.setattr(raw_support,"converter_path",lambda:None)
    def unsupported(*args,**kwargs):raise rawpy.LibRawFileUnsupportedError("unsupported")
    with pytest.raises(RawCompatibilityError,match="Adobe DNG Converter"):
        compatible_develop(path,unsupported,preview=True,metadata=None)
    def corrupt(*args,**kwargs):raise rawpy.LibRawDataError("damaged")
    with pytest.raises(rawpy.LibRawDataError):compatible_develop(path,corrupt,preview=True,metadata=None)
    assert not path.with_suffix(".dng").exists()


def test_bounded_camera_model_reader(tmp_path):
    path=tmp_path/"camera.nef"
    model=b"NIKON Z5_2\0"
    data=b"II*\0"+struct.pack("<I",8)+struct.pack("<H",1)+struct.pack("<HHII",272,2,len(model),26)+struct.pack("<I",0)+model
    path.write_bytes(data)
    assert camera_model(path)=="NIKON Z5_2"


def test_export_api_and_photo_close_release_files(monkeypatch):
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=300,height=200)
        return np.full((200,300,3),12000,dtype=np.uint16)
    monkeypatch.setattr("darkroom.app.develop",develop)
    with TestClient(app) as client:
        photo=client.post("/api/photos",files={"file":("camera.nef",b"fixture")}).json()
        path=app.state.studio.photos[photo["id"]].path
        path.with_suffix(".dng").write_bytes(b"converter-cache")
        url=f"/api/photos/{photo['id']}"
        response=client.post(url+"/export?long_edge=120&dpi=180&quality=90",json={"curve_mid":60})
        assert response.status_code==200
        image=Image.open(BytesIO(response.content));assert image.size==(120,80) and image.info["dpi"]==(180,180)
        assert client.post(url+"/export?dpi=0",json={}).status_code==422
        assert client.delete(url).status_code==200
        assert not path.exists() and not path.with_suffix(".dng").exists() and not path.with_suffix(".npy").exists()
        assert client.get(url+"/pixels").status_code==404
