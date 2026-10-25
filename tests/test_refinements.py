"""Cubic curve bounds/locality and color-preserving exposure recommendations."""
import numpy as np
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from darkroom.app import app
from darkroom.imaging import Adjustments,render
from darkroom.curves import curve_lut


def test_cubic_curve_interpolates_ten_points_without_overshoot():
    x=np.linspace(0,1,11);points=list(zip(x,x**.7))
    lut=curve_lut(Adjustments(curve_points=points))
    assert lut.min()>=0 and lut.max()<=1 and np.all(np.diff(lut)>=0)
    assert np.interp(x,np.linspace(0,1,256),lut)==pytest.approx(x**.7,abs=.002)
    identity=curve_lut(Adjustments(curve_points=[[0,0],[1,1]]))
    assert np.allclose(identity,np.linspace(0,1,256))
    raw=np.arange(300,dtype=np.uint16).reshape(10,10,3)*210
    assert np.array_equal(np.asarray(render(raw,Adjustments())),np.asarray(render(raw,Adjustments(curve_points=[[0,0],[1,1]]))))


def test_cubic_edit_has_local_effect():
    points=[[i/10,i/10] for i in range(11)]
    before=curve_lut(Adjustments(curve_points=points));points[5][1]=.58
    after=curve_lut(Adjustments(curve_points=points))
    assert np.array_equal(before[:76],after[:76]) and np.array_equal(before[180:],after[180:])
    assert np.max(after-before)>.06


@pytest.mark.parametrize('points',[
    [[.1,0],[1,1]],[[0,0],[.5,.5],[.5,.6],[1,1]],[[0,0],[1,1.1]],
    [[0,0],[.001,.5],[1,1]],[[0,0],[1,float('nan')]],[[i/32,i/32] for i in range(33)]])
def test_bad_custom_curves_rejected(points):
    with pytest.raises(ValidationError):Adjustments(curve_points=points)


@pytest.mark.parametrize('brightness',[0,300,3000,16000,60000])
def test_auto_light_is_bounded_and_only_changes_light(monkeypatch,brightness):
    raw=np.full((80,120,3),brightness,dtype=np.uint16)
    def develop(path,*,preview,metadata=None):
        if metadata is not None:metadata.update(width=120,height=80)
        return raw.copy()
    monkeypatch.setattr('darkroom.app.develop',develop)
    with TestClient(app) as client:
        photo=client.post('/api/photos',files={'file':('test.nef',b'fixture')}).json()
        result=client.post(f"/api/photos/{photo['id']}/auto-light")
        assert result.status_code==200
        values=result.json()['adjustments']
        assert set(values)=={'exposure','contrast','highlights','shadows'}
        assert -1<=values['exposure']<=1
        Adjustments(**values)
        assert np.array_equal(app.state.studio.photos[photo['id']].preview,raw)
