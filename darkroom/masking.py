"""Normalized mask geometry and local linear-light corrections."""
from functools import lru_cache
import json
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(Model):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Stroke(Model):
    points: list[Point] = Field(min_length=1, max_length=256)
    radius: float = Field(default=.04, ge=.002, le=.25)
    feather: float = Field(default=.6, ge=0, le=1)
    erase: bool = False


class LocalAdjustments(Model):
    exposure: float = Field(default=0, ge=-3, le=3)
    contrast: float = Field(default=0, ge=-100, le=100)
    highlights: float = Field(default=0, ge=-100, le=100)
    shadows: float = Field(default=0, ge=-100, le=100)
    temperature: float = Field(default=0, ge=-100, le=100)
    tint: float = Field(default=0, ge=-100, le=100)
    saturation: float = Field(default=0, ge=-100, le=100)


class Mask(Model):
    name: str = Field(default="마스크", max_length=60)
    kind: Literal["brush", "gradient", "radial", "color", "luminance"]
    enabled: bool = True
    invert: bool = False
    opacity: float = Field(default=1, ge=0, le=1)
    start: Point = Field(default_factory=lambda: Point(x=.2,y=.5))
    end: Point = Field(default_factory=lambda: Point(x=.8,y=.5))
    color: tuple[float, float, float] = (1,0,0)
    tolerance: float = Field(default=.25, ge=.001, le=1)
    low: float = Field(default=.2, ge=0, le=1)
    high: float = Field(default=.8, ge=0, le=1)
    feather: float = Field(default=.15, ge=0, le=1)
    density: list[float] | None = Field(default=None,min_length=5,max_length=5)
    strokes: list[Stroke] = Field(default_factory=list, max_length=64)
    adjustments: LocalAdjustments = Field(default_factory=LocalAdjustments)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.low > self.high or any(not 0 <= v <= 1 for v in self.color):
            raise ValueError("Invalid mask range/color")
        if self.density is not None and any(not 0<=v<=1 for v in self.density):
            raise ValueError("Gradient densities must be between 0 and 1")
        if sum(len(s.points) for s in self.strokes) > 2048:
            raise ValueError("Brush limit: 2048 points per mask")
        return self


def ramp(a, b, value):
    t = np.clip((value-a)/max(float(b-a),1e-8),0,1)
    return t*t*(3-2*t)


@lru_cache(maxsize=16)
def brush_grid(serialized: str, width: int, height: int):
    """Cache an anti-aliased 1024-long-side brush field, not a full RAW-sized mask."""
    scale=min(1,1024/max(width,height))
    w,h=max(1,round(width*scale)),max(1,round(height*scale))
    short=min(width,height)
    result=np.zeros((h,w),dtype=np.float32)
    for stroke in json.loads(serialized):
        radius=stroke["radius"]*short
        points=stroke["points"]
        field=np.zeros_like(result)
        for a,b in zip(points,points[1:] or points):
            ax,ay=a["x"]*width,a["y"]*height
            bx,by=b["x"]*width,b["y"]*height
            x0=max(0,int((min(ax,bx)-radius)/width*w)-1)
            x1=min(w,int((max(ax,bx)+radius)/width*w)+2)
            y0=max(0,int((min(ay,by)-radius)/height*h)-1)
            y1=min(h,int((max(ay,by)+radius)/height*h)+2)
            if x1<=x0 or y1<=y0:
                continue
            xx=(np.arange(x0,x1,dtype=np.float32)[None,:]+.5)/w*width
            yy=(np.arange(y0,y1,dtype=np.float32)[:,None]+.5)/h*height
            dx,dy=bx-ax,by-ay
            t=np.clip(((xx-ax)*dx+(yy-ay)*dy)/max(dx*dx+dy*dy,1e-12),0,1)
            distance=np.sqrt((xx-ax-t*dx)**2+(yy-ay-t*dy)**2)
            weight=(distance<=radius).astype(np.float32) if not stroke["feather"] else 1-ramp(radius*(1-stroke["feather"]),radius,distance)
            field[y0:y1,x0:x1]=np.maximum(field[y0:y1,x0:x1],weight)
        result = result*(1-field) if stroke["erase"] else np.maximum(result,field)
    return result


def sample_grid(grid, xx, yy):
    h,w=grid.shape
    px=xx*w-.5; py=yy*h-.5
    x0=np.floor(px).astype(int); y0=np.floor(py).astype(int)
    fx=px-x0; fy=py-y0
    a=grid[np.clip(y0,0,h-1),np.clip(x0,0,w-1)]
    b=grid[np.clip(y0,0,h-1),np.clip(x0+1,0,w-1)]
    c=grid[np.clip(y0+1,0,h-1),np.clip(x0,0,w-1)]
    d=grid[np.clip(y0+1,0,h-1),np.clip(x0+1,0,w-1)]
    return (a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy


def gradient_density(distance,mask):
    position=ramp(0,1,distance)
    if mask.density is None:
        value=1-position if mask.kind=="radial" else position
    else:
        # Controls correspond exactly to 0/25/50/75/100% of geometric distance.
        # Using the old easing curve as the interpolation axis preserves its default.
        knots=np.array([0,.15625,.5,.84375,1],dtype=np.float32)
        value=np.interp(position,knots,mask.density).astype(np.float32)
    return np.where(distance<=1,value,0) if mask.kind=="radial" else value


def mask_weight(mask: Mask, original, xx, yy, full_size):
    if mask.kind=="brush":
        serialized=json.dumps([s.model_dump() for s in mask.strokes],sort_keys=True)
        weight=sample_grid(brush_grid(serialized,*full_size),xx,yy)
    elif mask.kind in ("gradient","radial"):
        width,height=full_size
        ax,ay=mask.start.x*width,mask.start.y*height
        dx,dy=(mask.end.x-mask.start.x)*width,(mask.end.y-mask.start.y)*height
        if mask.kind=="radial":
            radius=max(float(np.hypot(dx,dy)),1)
            t=np.sqrt((xx*width-ax)**2+(yy*height-ay)**2)/radius
        else:
            t=((xx*width-ax)*dx+(yy*height-ay)*dy)/max(dx*dx+dy*dy,1e-12)
        weight=gradient_density(t,mask)
    else:
        display=np.clip(original,0,1)
        display=np.where(display<=.0031308,display*12.92,1.055*display**(1/2.4)-.055)
        if mask.kind=="color":
            distance=np.sqrt(np.mean((display-np.array(mask.color,dtype=np.float32))**2,axis=2))
            weight=1-ramp(mask.tolerance*(1-mask.feather),mask.tolerance,distance)
        else:
            lum=display @ np.array([.2126,.7152,.0722],dtype=np.float32)
            if mask.feather:
                weight=ramp(mask.low-mask.feather,mask.low,lum)*(1-ramp(mask.high,mask.high+mask.feather,lum))
            else:
                weight=((lum>=mask.low)&(lum<=mask.high)).astype(np.float32)
    if mask.invert:
        weight=1-weight
    return weight*mask.opacity if mask.enabled else weight*0


def bake_masks(rgb, masks, *, origin=(0,0), pixel_scale=(1,1), full_size=None):
    masks=[m for m in masks if m.enabled and m.opacity and any(m.adjustments.model_dump().values())]
    if not masks:
        return rgb
    full_size=full_size or (round(rgb.shape[1]*pixel_scale[0]),round(rgb.shape[0]*pixel_scale[1]))
    result=np.empty_like(rgb)
    xx=(origin[0]+(np.arange(rgb.shape[1],dtype=np.float32)[None,:]+.5)*pixel_scale[0])/full_size[0]
    for row in range(0,rgb.shape[0],128):
        yy=(origin[1]+(np.arange(row,min(row+128,rgb.shape[0]),dtype=np.float32)[:,None]+.5)*pixel_scale[1])/full_size[1]
        original=rgb[row:row+128].astype(np.float32)/65535
        x=original.copy()
        for mask in masks:
            if not mask.enabled:
                continue
            weight=mask_weight(mask,original,xx,yy,full_size)[...,None]
            a=mask.adjustments
            gains=np.exp2(np.array([.45*a.temperature/100+.15*a.tint/100,-.3*a.tint/100,-.45*a.temperature/100+.15*a.tint/100],dtype=np.float32))
            q=x*(2**a.exposure)*gains
            lum=q @ np.array([.2126,.7152,.0722],dtype=np.float32)
            ev=a.shadows/100*(1-np.clip(lum/.5,0,1))**2+a.highlights/100*np.clip((lum-.2)/.8,0,1)**2
            q*=np.exp2(ev)[...,None]
            q=(q-.18)*(2**(a.contrast/100))+.18
            gray=q @ np.array([.2126,.7152,.0722],dtype=np.float32)
            q=gray[...,None]+(q-gray[...,None])*(1+a.saturation/100)
            x=x*(1-weight)+q*weight
        result[row:row+128]=np.rint(np.clip(x,0,1)*65535).astype(np.uint16)
    return result
