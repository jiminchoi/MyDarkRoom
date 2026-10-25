from io import BytesIO
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from darkroom.app import app
from darkroom.imaging import Adjustments,render
from darkroom.geometry import apply_geometry
from darkroom.viewport import viewport_image,source_region,bounded_size


def fixture():
    y,x=np.mgrid[:240,:360];return np.stack([10000+x*80,8000+y*130,12000+x*40+y*40],axis=-1).astype(np.uint16)


@pytest.mark.parametrize('geometry',[{}, {'crop':[.15,.1,.7,.8]}, {'rotation':7,'vertical':.15,'horizontal':-.08,'crop':[.1,.12,.75,.7]}])
def test_viewport_matches_whole_export_pixels(geometry):
    raw=fixture();settings=Adjustments(exposure=.3,clarity=25,texture=-30,geometry=geometry,masks=[{'kind':'radial','adjustments':{'exposure':.2}}])
    expected=apply_geometry(render(raw,settings),settings.geometry)
    data,span,size=viewport_image(raw,settings,31,27,80,70)
    actual=np.asarray(Image.open(BytesIO(data))).astype(int)
    reference=np.asarray(expected.crop((31,27,111,97))).astype(int)
    assert span==(80,70) and size==span
    assert np.percentile(np.abs(actual-reference),99.9)<=3
    expected.close()


def test_region_bounds_and_bounded_work():
    for w,h in [(100,200),(10000,9000),(9000,500)]:
        x,y=bounded_size(w,h);assert x<=4096 and y<=4096 and x*y<=8_000_000 and x<=w and y<=h
    raw=fixture();data,span=source_region(raw,350,230,500,500)
    assert data.shape==(10,10,3) and span==(10,10)
    with pytest.raises(ValueError):source_region(raw,999,0,2,2)
    with pytest.raises(ValueError):viewport_image(raw,Adjustments(),999,0,20,20)


def test_region_and_viewport_api(monkeypatch):
    raw=fixture()
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=360,height=240)
        return raw.copy()
    monkeypatch.setattr('darkroom.app.develop',develop)
    with TestClient(app) as client:
        photo=client.post('/api/photos',files={'file':('sample.nef',b'fixture')}).json();url=f"/api/photos/{photo['id']}"
        values={'geometry':{'rotation':5,'crop':[.1,.1,.8,.8]},'masks':[{'kind':'gradient','adjustments':{'exposure':.6}}]}
        region=client.post(url+'/region?x=30&y=20&width=80&height=60',json=values)
        assert region.status_code==200 and len(region.content)==80*60*6
        assert region.headers['x-region-width']=='80'
        assert client.post(url+'/region?x=30&y=20&width=80&height=60&masked=true',json=values).content!=region.content
        viewport=client.post(url+'/viewport?x=20&y=25&width=50&height=40',json=values)
        assert viewport.status_code==200 and Image.open(BytesIO(viewport.content)).size==(50,40)
        assert client.post(url+'/viewport?x=9999&y=0&width=50&height=40',json=values).status_code==422
        assert client.post(url+'/region?x=-1&y=0&width=50&height=40',json=values).status_code==422
        assert client.post('/api/photos/missing/region?x=0&y=0&width=1&height=1',json=values).status_code==404


def test_large_source_region_is_bounded_without_changing_coverage():
    source=np.full((3000,4000,3),12000,dtype=np.uint16)
    rgb,span=source_region(source,0,0,4000,3000)
    assert span==(4000,3000) and rgb.shape[1::-1]==bounded_size(4000,3000)
    assert rgb.shape[0]*rgb.shape[1]<=8_000_000 and rgb.min()==rgb.max()==12000
