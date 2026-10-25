from io import BytesIO
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from fastapi.testclient import TestClient
from darkroom.app import app
from darkroom.imaging import Adjustments,render
from darkroom.masking import Mask,LocalAdjustments,bake_masks,packed_weights,SPATIAL_KEYS
from darkroom.structure import packed_sharpness
from darkroom.viewport import viewport_image


def fixture():
    y,x=np.mgrid[:288,:384]
    lum=.28+.09*np.sin(x*.08)+.035*np.sin(x*1.1)+.025*np.cos(y*.9)
    return np.stack([lum,lum*.85,lum*.7],axis=-1).__mul__(65535).astype(np.uint16)


@pytest.mark.parametrize('key',SPATIAL_KEYS)
def test_signed_controls_only_change_masked_pixels(key):
    raw=fixture();base=bake_masks(raw,[])
    def mask(value,**kw):return Mask(kind='luminance',low=0,high=1,invert=False,**kw,adjustments={key:value})
    assert np.array_equal(bake_masks(raw,[mask(0)]),raw)
    assert np.array_equal(bake_masks(raw,[mask(100,enabled=False)]),raw)
    assert np.array_equal(bake_masks(raw,[mask(100,opacity=0)]),raw)
    plus=bake_masks(raw,[mask(100)]);minus=bake_masks(raw,[mask(-100)])
    assert np.any(plus!=base) and np.any(minus!=base)
    if key=='dehaze':assert plus.mean()<base.mean()<minus.mean()
    else:assert plus.std()>base.std()>minus.std()
    radial=Mask(kind='radial',start={'x':.5,'y':.5},end={'x':.7,'y':.5},adjustments={key:90})
    actual=bake_masks(raw,[radial]);assert np.array_equal(actual[:30],raw[:30])
    with pytest.raises(ValidationError):LocalAdjustments(**{key:101})


def test_overlap_cancel_opacity_invert_and_uniform_edges():
    raw=fixture()
    masks=[Mask(kind='luminance',low=0,high=1,adjustments={k:v for k in SPATIAL_KEYS}) for v in [80,-80]]
    assert np.array_equal(bake_masks(raw,masks),raw)
    a=Mask(kind='radial',opacity=.5,enabled=False,adjustments={'sharpness':100})
    weights=packed_weights(raw,[a],full_size=(384,288))
    assert weights.shape==(1,288,384,4) and weights.max()==255
    assert np.count_nonzero(weights[:,:,:,[1,2,3]])==0
    inverted=packed_weights(raw,[a.model_copy(update={'invert':True})],full_size=(384,288))
    assert np.max(np.abs(weights[0,:,:,0].astype(int)+inverted[0,:,:,0]-255))<=1
    for value in [0,24000,65535]:
        plain=np.full((3,1,3),value,dtype=np.uint16)
        assert np.array_equal(bake_masks(plain,[Mask(kind='luminance',low=0,high=1,adjustments={'sharpness':100,'texture':100,'clarity':100})]),plain)


def test_local_spatial_detail_and_viewport_match_export_without_strip_seams():
    raw=fixture();settings=Adjustments(exposure=.3,clarity=20,masks=[{'kind':'gradient','adjustments':{'exposure':.2,'dehaze':25,'clarity':40,'texture':60,'sharpness':80}}])
    full=np.asarray(render(raw,settings));roi=raw[100:245,90:240]
    detail=np.asarray(render(roi,settings,origin=(90,100),full_size=(384,288),structure_source=raw))
    assert np.max(np.abs(full[100:245,90:240].astype(int)-detail.astype(int)))<=1
    full_sharp=packed_sharpness(raw);detail_sharp=packed_sharpness(roi,origin=(90,100),source=raw)
    assert np.array_equal(full_sharp[100:245,90:240],detail_sharp)
    data,_,_=viewport_image(raw,settings,90,100,150,145)
    assert np.percentile(np.abs(np.asarray(Image.open(BytesIO(data))).astype(int)-full[100:245,90:240].astype(int)),99.9)<=3


def test_local_maps_api_and_eight_mask_planes(monkeypatch):
    raw=fixture()
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=384,height=288)
        return raw.copy()
    monkeypatch.setattr('darkroom.app.develop',develop)
    with TestClient(app) as client:
        photo=client.post('/api/photos',files={'file':('sample.nef',b'fixture')}).json();url=f"/api/photos/{photo['id']}"
        masks=[{'kind':'radial','adjustments':{'texture':70,'sharpness':30}} for _ in range(8)]
        response=client.post(url+'/mask-fields',json={'masks':masks})
        assert response.status_code==200 and response.headers['x-mask-planes']=='2' and len(response.content)==2*384*288*4
        detail=client.post(url+'/mask-fields?x=90&y=100&width=150&height=145',json={'masks':masks})
        expected=np.frombuffer(response.content,dtype=np.uint8).reshape(2,288,384,4)[:,100:245,90:240]
        assert np.array_equal(np.frombuffer(detail.content,dtype=np.uint8).reshape(2,145,150,4),expected)
        sharp=client.get(url+'/structure?kind=sharpness');assert sharp.status_code==200 and len(sharp.content)==384*288*2
        native=client.post(url+'/region?x=90&y=100&width=150&height=145&masked=true',json={'masks':masks})
        expected=bake_masks(raw,[Mask(**m) for m in masks])[100:245,90:240]
        assert native.status_code==200 and np.max(np.abs(np.frombuffer(native.content,dtype='<u2').reshape(145,150,3).astype(int)-expected.astype(int)))<=1
        assert client.get(url+'/structure?kind=invalid').status_code==422
        assert client.post(url+'/mask-fields?x=9999',json={'masks':masks}).status_code==422
        assert client.post('/api/photos/missing/mask-fields',json={}).status_code==404
        assert client.post(url+'/preview',json={'masks':masks}).status_code==200
        assert client.post(url+'/export?long_edge=200',json={'masks':masks}).status_code==200
