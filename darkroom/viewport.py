"""Bounded source regions for interactive zoom; never develop the entire frame per move."""
from io import BytesIO
import math
import numpy as np
from PIL import Image
from .geometry import forward_matrix
from .imaging import render

MAX_PIXELS=8_000_000
MAX_EDGE=4096


def bounded_size(w,h,pixels=MAX_PIXELS,edge=MAX_EDGE):
    scale=min(1,edge/max(w,h),math.sqrt(pixels/(w*h)))
    return max(1,int(w*scale)),max(1,int(h*scale))


def source_region(source,x,y,width,height):
    h,w=source.shape[:2]
    if x>=w or y>=h:raise ValueError("사진 범위를 벗어난 위치입니다.")
    region=source[y:min(h,y+height),x:min(w,x+width)]
    rh,rw=region.shape[:2];size=bounded_size(rw,rh)
    if size!=(rw,rh):region=np.stack([np.asarray(Image.fromarray(region[:,:,c]).resize(size,Image.Resampling.BILINEAR)) for c in range(3)],axis=2)
    return region,(rw,rh)


def crop_bounds(size,g):
    w,h=size;x,y,cw,ch=g.crop;left,top=round(x*w),round(y*h)
    return left,top,min(w,max(left+1,round((x+cw)*w)))-left,min(h,max(top+1,round((y+ch)*h)))-top


def viewport_image(source,settings,left,top,width,height):
    """Render only the inverse-projected source box plus interpolation margin."""
    h,w=source.shape[:2];cx,cy,cw,ch=crop_bounds((w,h),settings.geometry)
    if left>=cw or top>=ch:raise ValueError("크롭 영역 밖입니다.")
    width=min(width,cw-left);height=min(height,ch-top);out_size=bounded_size(width,height,pixels=4_000_000)
    inverse=np.linalg.inv(forward_matrix((w,h),settings.geometry))
    corners=np.array([[cx+left,cy+top,1],[cx+left+width,cy+top,1],[cx+left+width,cy+top+height,1],[cx+left,cy+top+height,1]],dtype=float).T
    q=inverse@corners
    if np.any(q[2]<=1e-8):x0=y0=0;x1=w;y1=h
    else:
        q=q[:2]/q[2];x0=max(0,math.floor(q[0].min())-4);y0=max(0,math.floor(q[1].min())-4)
        x1=min(w,math.ceil(q[0].max())+4);y1=min(h,math.ceil(q[1].max())+4)
    if x1<=x0 or y1<=y0:image=Image.new('RGB',out_size,(16,18,18))
    else:
        rgb,span=source_region(source,x0,y0,x1-x0,y1-y0);sx=span[0]/rgb.shape[1];sy=span[1]/rgb.shape[0]
        colored=render(rgb,settings,origin=(x0,y0),pixel_scale=(sx,sy),full_size=(w,h),structure_source=source if sx==sy==1 else None)
        into_output=np.array([[width/out_size[0],0,cx+left],[0,height/out_size[1],cy+top],[0,0,1.]])
        into_region=np.array([[1/sx,0,-x0/sx],[0,1/sy,-y0/sy],[0,0,1.]])
        matrix=into_region@inverse@into_output;matrix/=matrix[2,2]
        image=colored.transform(out_size,Image.Transform.PERSPECTIVE,tuple(matrix.flat)[:8],Image.Resampling.BICUBIC,fillcolor=(16,18,18));colored.close()
    output=BytesIO();image.save(output,format='JPEG',quality=95,subsampling=0);image.close()
    return output.getvalue(),(width,height),out_size
