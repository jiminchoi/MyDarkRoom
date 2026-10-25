"""Bounded, local cubic Hermite curve. Same 256-sample LUT as WebGL."""
import numpy as np


def curve_lut(settings):
    if settings.curve_points is None:
        points=np.array([[0,settings.curve_black/100],[.25,settings.curve_shadow/100],[.5,settings.curve_mid/100],[.75,settings.curve_light/100],[1,settings.curve_white/100]])
        return np.interp(np.linspace(0,1,256),points[:,0],points[:,1]).astype(np.float32)
    points=np.asarray(settings.curve_points,dtype=float)
    x,y=points.T;h=np.diff(x);d=np.diff(y)/h;m=np.zeros(len(x));m[0]=d[0];m[-1]=d[-1]
    for i in range(1,len(x)-1):
        if d[i-1]*d[i]>0:
            a=2*h[i]+h[i-1];b=h[i]+2*h[i-1]
            m[i]=(a+b)/(a/d[i-1]+b/d[i])
    positions=np.linspace(0,1,256);i=np.clip(np.searchsorted(x,positions,side='right')-1,0,len(x)-2)
    t=(positions-x[i])/h[i]
    result=(2*t**3-3*t**2+1)*y[i]+(t**3-2*t**2+t)*h[i]*m[i]+(-2*t**3+3*t**2)*y[i+1]+(t**3-t**2)*h[i]*m[i+1]
    return np.clip(result,0,1).astype(np.float32)
