from io import BytesIO
import numpy as np
from PIL import Image,ImageDraw
import pytest
from pydantic import ValidationError
from darkroom.geometry import Geometry,apply_geometry,estimate_geometry,forward_matrix
from darkroom.imaging import Adjustments,jpeg
from fastapi.testclient import TestClient
from darkroom.app import app


def test_crop_pixels_dimensions_and_dpi():
    raw=np.full((200,300,3),15000,dtype=np.uint16)
    settings=Adjustments(geometry={"crop":[.1,.2,.5,.5]})
    im=Image.open(BytesIO(jpeg(raw,settings,quality=95,dpi=240)))
    assert im.size==(150,100) and im.info['dpi']==(240,240)
    im=Image.open(BytesIO(jpeg(raw,settings,quality=95,long_edge=75)))
    assert im.size==(75,50)


def test_geometry_identity_and_coordinate_mapping():
    image=Image.new('RGB',(300,200),'white')
    assert apply_geometry(image,Geometry()) is image
    matrix=forward_matrix(image.size,Geometry(rotation=8,vertical=.2))
    point=np.array([150,100,1.]);assert np.allclose(matrix@point,point)
    transformed=apply_geometry(image,Geometry(rotation=8,vertical=.2,crop=(.1,.1,.8,.8)))
    assert transformed.size==(240,160)


def test_auto_level_and_no_line_failure():
    image=Image.new('RGB',(800,600),'#202020');draw=ImageDraw.Draw(image)
    for y in [100,200,300,400,500]:draw.line((50,y,750,y),fill='white',width=3)
    tilted=image.rotate(-5,resample=Image.Resampling.BICUBIC)
    estimate=estimate_geometry(tilted,'level')
    assert estimate['geometry']['rotation']==pytest.approx(-5,abs=.8)
    with pytest.raises(ValueError):estimate_geometry(Image.new('RGB',(300,200)),'upright')


def facade():
    image=Image.new('RGB',(1000,800),'#202020');draw=ImageDraw.Draw(image)
    for x in [80,300,550,800,950]:draw.line((x,20,x,780),fill='white',width=3)
    for y in [50,270,510,750]:draw.line((20,y,980,y),fill='white',width=3)
    return image


@pytest.mark.parametrize('mode',['vertical','upright'])
def test_upright_corrects_mild_perspective_and_preserves_ratio(mode):
    image=facade();original_matrix=forward_matrix(image.size,Geometry(vertical=.15,rotation=2))
    distorted=apply_geometry(image,Geometry(vertical=.15,rotation=2))
    result=estimate_geometry(distorted,mode);g=Geometry(**result['geometry'])
    combined=forward_matrix(distorted.size,g)@original_matrix
    for x in [300,800]:
        a=combined@np.array([x,100,1.]);b=combined@np.array([x,700,1.]);a/=a[2];b/=b[2]
        assert abs(a[0]-b[0])<4
    assert g.crop[2]==pytest.approx(g.crop[3]) and g.crop[2]<1
    # The entire accepted crop must come from real source pixels, including corners.
    x,y,w,h=g.crop;inverse=np.linalg.inv(forward_matrix(distorted.size,g))
    for u,v in [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]:
        point=inverse@np.array([u*1000,v*800,1]);point/=point[2]
        assert 1<=point[0]<=999 and 1<=point[1]<=799


def test_ambiguous_and_excessive_perspective_is_rejected():
    # The old implementation accepted these unsupported, strongly converging lines.
    image=Image.new('RGB',(800,600),'#202020');draw=ImageDraw.Draw(image)
    for x in [80,200,320,480,600,720]:draw.line((400+(x-400)*.5,40,x,570),fill='white',width=3)
    with pytest.raises(ValueError):estimate_geometry(image,'upright')
    with pytest.raises(ValueError):estimate_geometry(apply_geometry(facade(),Geometry(vertical=.6)),'upright')
    # One line's two edges and scattered diagonal natural shapes aren't evidence.
    image=Image.new('RGB',(800,600));draw=ImageDraw.Draw(image)
    draw.line((10,200,790,215),fill='white',width=6)
    draw.line((20,500,210,240,460,430,730,180),fill='white',width=3)
    with pytest.raises(ValueError):estimate_geometry(image,'level')


def test_bad_geometry_rejected():
    for value in [{"crop":[0,0,0,1]},{"crop":[.9,0,.2,1]},{"rotation":30},{"upright":[0]*9}]:
        with pytest.raises(ValidationError):Geometry(**value)


def test_auto_geometry_api_and_failure_does_not_change_photo(monkeypatch):
    image=Image.new('RGB',(800,600),'#202020');draw=ImageDraw.Draw(image)
    for y in [100,200,300,400,500]:draw.line((50,y,750,y),fill='white',width=3)
    rgb=np.asarray(image.rotate(-5)).astype(np.uint16)*257
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=800,height=600)
        return rgb.copy()
    monkeypatch.setattr('darkroom.app.develop',develop)
    with TestClient(app) as client:
        photo=client.post('/api/photos',files={'file':('test.nef',b'fixture')}).json()
        url=f"/api/photos/{photo['id']}/auto-geometry"
        result=client.post(url+'?mode=level')
        assert result.status_code==200 and abs(result.json()['geometry']['rotation']+5)<.8
        assert client.post(url+'?mode=unknown').status_code==422
        app.state.studio.photos[photo['id']].preview[:]=0
        assert client.post(url+'?mode=upright').status_code==422
        assert np.all(app.state.studio.photos[photo['id']].preview==0)
