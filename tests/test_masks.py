from io import BytesIO
import numpy as np
from PIL import Image
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from darkroom.app import app
from darkroom.imaging import Adjustments, render
from darkroom.masking import Mask, bake_masks, mask_weight


def coords(w,h):
    return (np.arange(w,dtype=np.float32)[None,:]+.5)/w,(np.arange(h,dtype=np.float32)[:,None]+.5)/h


def test_gradient_localizes_and_inverts_before_global_effects():
    rgb=np.full((40,100,3),10000,dtype=np.uint16)
    mask=Mask(kind="gradient",start={"x":.2,"y":.5},end={"x":.8,"y":.5},adjustments={"exposure":1})
    result=bake_masks(rgb,[mask])
    assert np.array_equal(result[:,:20],rgb[:,:20])
    assert np.all(result[:,80:]==20000)
    mask.invert=True
    reverse=bake_masks(rgb,[mask])
    assert np.all(reverse[:,:20]==20000)
    assert np.array_equal(reverse[:,80:],rgb[:,80:])
    mask.enabled=False
    assert bake_masks(rgb,[mask]) is rgb


def test_color_and_luminance_selection_uses_original_color():
    raw=np.array([[[65535,0,0],[0,65535,0],[65535,65535,65535],[0,0,0]]],dtype=np.uint16)
    xx,yy=coords(4,1)
    color=Mask(kind="color",color=(1,0,0),tolerance=.1)
    weight=mask_weight(color,raw.astype(np.float32)/65535,xx,yy,(4,1))
    assert weight[0,0]==1 and np.all(weight[0,1:]==0)
    lum=Mask(kind="luminance",low=.9,high=1,feather=0)
    weight=mask_weight(lum,raw.astype(np.float32)/65535,xx,yy,(4,1))
    assert weight.tolist()==[[0,0,1,0]]


def test_brush_feather_erase_and_original_crop_coordinates():
    rgb=np.full((160,240,3),9000,dtype=np.uint16)
    paint={"points":[{"x":.2,"y":.5},{"x":.8,"y":.5}],"radius":.15,"feather":.4}
    mask=Mask(kind="brush",strokes=[paint],adjustments={"exposure":1})
    painted=bake_masks(rgb,[mask],full_size=(240,160))
    assert painted[80,120,0]==18000 and painted[0,0,0]==9000
    assert np.any((painted[...,0]>9000)&(painted[...,0]<18000))
    crop=bake_masks(rgb[45:120,35:200],[mask],origin=(35,45),full_size=(240,160))
    assert np.array_equal(crop,painted[45:120,35:200])
    mask.strokes.append(type(mask.strokes[0])(**{**paint,"erase":True,"radius":.04}))
    erased=bake_masks(rgb,[mask],full_size=(240,160))
    assert erased[80,120,0]==9000


def test_masks_order_opacity_and_global_grain():
    rgb=np.full((50,80,3),10000,dtype=np.uint16)
    first=Mask(kind="gradient",invert=True,start={"x":1,"y":0},end={"x":1,"y":1},opacity=.5,adjustments={"exposure":1})
    settings=Adjustments(masks=[first],monochrome=True,grain=30)
    full=np.asarray(render(rgb,settings,full_size=(80,50)))
    crop=np.asarray(render(rgb[10:30,20:60],settings,origin=(20,10),full_size=(80,50)))
    assert np.array_equal(crop,full[10:30,20:60])
    a=Mask(kind="luminance",low=0,high=1,feather=0,opacity=.5,adjustments={"exposure":1})
    b=Mask(kind="luminance",low=0,high=1,feather=0,adjustments={"contrast":50})
    assert np.all(bake_masks(rgb,[a])==15000)
    assert not np.array_equal(bake_masks(rgb,[a,b]),bake_masks(rgb,[b,a]))


def test_invalid_masks_are_rejected():
    for value in [{"kind":"unknown"},{"kind":"luminance","low":.8,"high":.1},
                  {"kind":"color","color":[2,0,0]}, {"kind":"gradient","start":{"x":-1,"y":0}},
                  {"kind":"brush","strokes":[{"points":[],"radius":.1}]}]:
        with pytest.raises(ValidationError):Mask(**value)
    with pytest.raises(ValidationError):Adjustments(masks=[{"kind":"color"}]*9)


def test_mask_api_linear_detail_overlay_and_export(monkeypatch):
    raw=np.full((60,100,3),10000,dtype=np.uint16)
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=100,height=60)
        return raw.copy()
    monkeypatch.setattr("darkroom.app.develop",develop)
    with TestClient(app) as client:
        photo=client.post("/api/photos",files={"file":("fixture.nef",b"fixture")}).json()
        root=f"/api/photos/{photo['id']}"
        sampled=client.get(root+"/sample-color?x=1&y=1")
        assert sampled.status_code==200 and len(sampled.json()["color"])==3
        assert client.get(root+"/sample-color?x=2&y=0").status_code==422
        settings={"masks":[{"kind":"gradient","start":{"x":.2,"y":.5},"end":{"x":.8,"y":.5},"adjustments":{"exposure":1}}]}
        response=client.post(root+"/masked-pixels",json=settings)
        assert response.status_code==200
        pixels=np.frombuffer(response.content,dtype="<u2").reshape(raw.shape)
        assert np.all(pixels[:,80:]==20000)
        detail=client.post(root+"/masked-detail?x=75&y=10&width=20&height=30",json=settings)
        assert np.array_equal(np.frombuffer(detail.content,dtype="<u2").reshape(30,20,3),pixels[10:40,75:95])
        overlay=client.post(root+"/mask-overlay?selected=0",json=settings)
        image=np.asarray(Image.open(BytesIO(overlay.content)))
        assert image[20,5,3]==0 and image[20,95,3]>0
        assert client.post(root+"/mask-overlay?selected=1",json=settings).status_code==422
        exported=client.post(root+"/export",json=settings)
        assert exported.status_code==200
        image=Image.open(BytesIO(exported.content));image.load()
        assert image.size==(100,60) and image.info["icc_profile"]
        exported_pixels=np.asarray(image)
        baseline=np.asarray(render(raw,Adjustments()))
        assert np.max(abs(exported_pixels[20,5].astype(int)-baseline[20,5].astype(int)))<=3
        assert np.all(exported_pixels[20,95]>exported_pixels[20,5]+10)
