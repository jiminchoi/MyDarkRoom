import numpy as np
from darkroom.imaging import Adjustments, render


def test_monochrome_channel_isolation_and_neutral_weights():
    rgb=np.array([[[10000,30000,50000],[20000,20000,20000]]],dtype=np.uint16)
    base=np.asarray(render(rgb,Adjustments()))
    red=np.asarray(render(rgb,Adjustments(monochrome=True,mix_red=100,mix_green=0,mix_blue=0)))
    assert np.all(red[0,0]==base[0,0,0])
    assert np.all(red[0,1]==base[0,1,0])
    zero=np.asarray(render(rgb,Adjustments(monochrome=True,mix_red=0,mix_green=0,mix_blue=0)))
    assert np.array_equal(zero[...,0],zero[...,1])
    assert np.array_equal(zero[...,1],zero[...,2])


def test_toning_and_grain_are_repeatable_and_crop_aligned():
    rgb=np.full((260,180,3),20000,dtype=np.uint16)
    tone=np.asarray(render(rgb,Adjustments(monochrome=True,tone_hue=35,tone_amount=80)))
    assert np.all(tone[...,0]>tone[...,2])
    settings=Adjustments(monochrome=True,grain=80,grain_size=2)
    first=np.asarray(render(rgb,settings))
    assert np.array_equal(first,np.asarray(render(rgb,settings)))
    assert len(np.unique(first[...,0]))>10
    assert np.array_equal(first[...,0],first[...,1])
    cropped=np.asarray(render(rgb[129:200,35:120],settings,origin=(35,129)))
    assert np.array_equal(cropped,first[129:200,35:120])
