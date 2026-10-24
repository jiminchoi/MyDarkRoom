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


def test_radial_is_circular_in_original_pixels_and_inverts():
    raw=np.zeros((100,200,3),dtype=np.float32)
    mask=Mask(kind="radial",start={"x":.5,"y":.5},end={"x":.7,"y":.5})
    # Both offsets are 20 source pixels on a rectangular image.
    weight=mask_weight(mask,raw,np.array([[.5,.6,.5,.9]],dtype=np.float32),np.array([[.5,.5,.7,.5]],dtype=np.float32),(200,100))
    assert weight[0,0]==1 and weight[0,3]==0
    assert weight[0,1]==pytest.approx(weight[0,2],abs=1e-6)
    assert weight[0,1]==pytest.approx(.5,abs=1e-6)
    mask.invert=True
    inverted=mask_weight(mask,raw,np.array([[.5,.9]]),np.array([[.5,.5]]),(200,100))
    assert np.allclose(inverted,[[0,1]])


@pytest.mark.parametrize("kind",["gradient","radial"])
def test_distance_density_stops_and_radial_crop_match(kind):
    mask=Mask(kind=kind,start={"x":.2,"y":.5},end={"x":.8,"y":.5},density=[.1,.8,.2,.9,.3],adjustments={"exposure":1})
    xx=np.array([[.2,.35,.5,.65,.8]],dtype=np.float32)
    weights=mask_weight(mask,np.zeros((1,5,3),dtype=np.float32),xx,np.full((1,5),.5),(200,100))
    assert np.allclose(weights,[mask.density],atol=1e-6)
    raw=np.full((100,200,3),10000,dtype=np.uint16)
    full=bake_masks(raw,[mask],full_size=(200,100))
    crop=bake_masks(raw[20:85,40:160],[mask],origin=(40,20),full_size=(200,100))
    assert np.array_equal(crop,full[20:85,40:160])
    for values in [[0,1],[0,0,0,0,1.1],[0,0,float('nan'),0,1]]:
        with pytest.raises(ValidationError):Mask(kind=kind,density=values)


def test_radial_profile_does_not_spill_outside_circle():
    mask=Mask(kind="radial",start={"x":.5,"y":.5},end={"x":.6,"y":.5},density=[1,1,1,1,1])
    weight=mask_weight(mask,np.zeros((1,2,3),dtype=np.float32),np.array([[.5,.9]]),np.array([[.5,.5]]),(200,100))
    assert np.array_equal(weight,[[1,0]])


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
        radial={"masks":[{"kind":"radial","start":{"x":.5,"y":.5},"end":{"x":.7,"y":.5},
            "density":[1,.8,.3,.1,0],"adjustments":{"exposure":1}}]}
        data=client.post(root+"/masked-pixels",json=radial)
        assert data.status_code==200
        pixels=np.frombuffer(data.content,dtype="<u2").reshape(raw.shape)
        assert pixels[30,50,0]>19500 and np.array_equal(pixels[0,0],raw[0,0])
        detail=client.post(root+"/masked-detail?x=40&y=20&width=20&height=20",json=radial)
        assert np.array_equal(np.frombuffer(detail.content,dtype="<u2").reshape(20,20,3),pixels[20:40,40:60])
        overlay=client.post(root+"/mask-overlay?selected=0",json=radial)
        alpha=np.asarray(Image.open(BytesIO(overlay.content)))[...,3]
        assert alpha[30,50]>100 and alpha[0,0]==0
        exported=client.post(root+"/export",json=radial)
        image=np.asarray(Image.open(BytesIO(exported.content)))
        assert np.all(image[30,50]>image[0,0]+15)
