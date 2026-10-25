import numpy as np
import pytest
from pydantic import ValidationError
from darkroom.imaging import Adjustments,render
from darkroom.masking import Mask,mask_weight,brush_grid
from darkroom.structure import prepare,fields
from darkroom.geometry import Geometry,forward_matrix


def fixture():
    y,x=np.mgrid[:128,:192];v=.35+.12*np.sin(x*.12)+.03*np.sin(x*1.1)+.02*np.cos(y*.7)
    return np.repeat((v*65535).astype(np.uint16)[...,None],3,axis=2)


def test_spatial_zero_identity_and_signed_behavior():
    raw=fixture();base=np.asarray(render(raw,Adjustments()))
    assert np.array_equal(base,np.asarray(render(raw,Adjustments(dehaze=0,clarity=0,texture=0))))
    for key in ['clarity','texture']:
        plus=np.asarray(render(raw,Adjustments(**{key:100}))).astype(float)
        minus=np.asarray(render(raw,Adjustments(**{key:-100}))).astype(float)
        assert plus.std()>base.std()>minus.std()
    assert np.asarray(render(raw,Adjustments(dehaze=-100))).mean()>base.mean()
    assert np.asarray(render(raw,Adjustments(dehaze=100))).mean()<base.mean()
    for key in ['dehaze','clarity','texture']:
        with pytest.raises(ValidationError):Adjustments(**{key:101})


def test_spatial_detail_matches_full_without_crop_seams():
    raw=fixture();ctx=prepare(raw,full_size=(192,128));f=fields(ctx,30,192,40)
    detail=raw[30:70,40:100];ctx2=prepare(detail,full_size=(192,128),origin=(40,30),source=raw)
    assert np.allclose(f[:,40:100],fields(ctx2,0,60,40,origin=(40,30)),atol=2e-7)
    settings=Adjustments(dehaze=40,texture=65,clarity=-20)
    full=np.asarray(render(raw,settings))
    crop=np.asarray(render(detail,settings,origin=(40,30),full_size=(192,128),structure_source=raw))
    assert np.max(np.abs(full[30:70,40:100].astype(int)-crop.astype(int)))<=1


def test_uniform_noise_and_extremes_are_bounded():
    for value in [0,12000,65535]:
        raw=np.full((32,48,3),value,dtype=np.uint16)
        base=np.asarray(render(raw,Adjustments()))
        assert np.array_equal(base,np.asarray(render(raw,Adjustments(clarity=100,texture=-100))))
        for sign in [-100,100]:
            result=np.asarray(render(raw,Adjustments(dehaze=sign,clarity=sign,texture=sign)))
            assert np.isfinite(result).all()


def test_projected_brush_circle_and_eraser_follow_source():
    width,height=800,600;g=Geometry(rotation=8,vertical=.2)
    d=np.diag([width,height,1.]);m=np.linalg.inv(d)@forward_matrix((width,height),g)@d
    stroke={'points':[{'x':.55,'y':.5}],'radius':.08,'feather':0,'transform':m.ravel().tolist()}
    mask=Mask(kind='brush',strokes=[stroke]);inv=np.linalg.inv(m)
    points=[]
    for t in np.linspace(0,2*np.pi,12,endpoint=False):
        for radius in [.04,.11]:
            q=inv@np.array([.55+np.cos(t)*radius*height/width,.5+np.sin(t)*radius,1]);q/=q[2];points.append(q)
    points=np.array(points);weight=mask_weight(mask,np.zeros((len(points),1,3)),points[:,0,None],points[:,1,None],(width,height)).ravel()
    assert np.all(weight[::2]>.9) and np.all(weight[1::2]<.1)
    erased=Mask(kind='brush',strokes=[stroke,{**stroke,'radius':.03,'erase':True}])
    q=inv@np.array([.55,.5,1]);q/=q[2]
    assert mask_weight(erased,np.zeros((1,1,3)),np.array([[q[0]]]),np.array([[q[1]]]),(width,height))[0,0]<.1
    for matrix in [[0]*9,[float('nan')]*9]:
        with pytest.raises(ValidationError):Mask(kind='brush',strokes=[{**stroke,'transform':matrix}])


def test_projected_radial_and_linear_are_consistent():
    m=[1,0,.1,0,1,0,0,0,1]
    raw=np.zeros((1,1,3));x=np.array([[.4]]);y=np.array([[.5]])
    radial=Mask(kind='radial',transform=m,start={'x':.5,'y':.5},end={'x':.7,'y':.5})
    assert mask_weight(radial,raw,x,y,(800,600))[0,0]==pytest.approx(1)
    linear=Mask(kind='gradient',transform=m,start={'x':.3,'y':.5},end={'x':.7,'y':.5})
    assert mask_weight(linear,raw,x,y,(800,600))[0,0]==pytest.approx(.5)


def test_structure_api_detail_and_overlay(monkeypatch):
    from fastapi.testclient import TestClient
    from darkroom.app import app
    from darkroom.structure import packed
    raw=fixture()
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=192,height=128)
        return raw.copy()
    monkeypatch.setattr('darkroom.app.develop',develop)
    with TestClient(app) as client:
        photo=client.post('/api/photos',files={'file':('sample.nef',b'fixture')}).json();url=f"/api/photos/{photo['id']}"
        result=client.get(url+'/structure');assert result.status_code==200
        actual=np.frombuffer(result.content,dtype='<u2').reshape(128,192,4)
        detail=client.get(url+'/structure?x=40&y=30&width=60&height=40');assert detail.status_code==200
        actual_detail=np.frombuffer(detail.content,dtype='<u2').reshape(40,60,4)
        assert np.max(np.abs(actual[30:70,40:100].astype(int)-actual_detail.astype(int)))<=1
        assert client.get(url+'/structure?x=999').status_code==422
        assert client.get('/api/photos/missing/structure').status_code==404
        settings={'dehaze':30,'clarity':-20,'texture':50,'masks':[{'kind':'radial','transform':[1,0,.1,0,1,0,0,0,1],'adjustments':{'exposure':.3}}]}
        assert client.post(url+'/preview',json=settings).status_code==200
        assert client.post(url+'/export?long_edge=100',json=settings).status_code==200
