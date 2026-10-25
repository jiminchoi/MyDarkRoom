"""Source-anchored spatial fields; slider changes only mix cached fields on GPU."""
import numpy as np
import cv2

LUMA=np.array([.2126,.7152,.0722],dtype=np.float32)

def display(rgb):
    x=rgb.astype(np.float32)/65535
    return np.where(x<=.0031308,x*12.92,1.055*np.power(x,1/2.4)-.055)

def prepare(rgb,*,full_size,pixel_scale=(1,1),origin=(0,0),source=None):
    cv2.setNumThreads(2)
    source=rgb if source is None else source
    source_origin=origin if source is rgb else (0,0)
    h,w=source.shape[:2];factor=min(1,1200/max(w,h));cw,ch=max(1,round(w*factor)),max(1,round(h*factor))
    # Sample before converting to float to keep full-RAW temporary memory bounded.
    ys=np.minimum(h-1,((np.arange(ch)+.5)*h/ch).astype(int));xs=np.minimum(w-1,((np.arange(cw)+.5)*w/cw).astype(int))
    color=display(source[ys[:,None],xs[None,:]]);lum=color@LUMA
    sigma=max(.6,.008*min(full_size)/(pixel_scale[1]*h/ch))
    broad=cv2.GaussianBlur(lum,(0,0),sigma,borderType=cv2.BORDER_REFLECT_101)
    radius=max(1,round(.008*min(cw,ch)));dark=cv2.erode(color.min(axis=2),np.ones((radius*2+1,radius*2+1),np.uint8))
    veil=cv2.GaussianBlur(dark,(0,0),max(1,radius/2),borderType=cv2.BORDER_REFLECT_101)
    return dict(source=source,source_origin=source_origin,scale=pixel_scale,full_size=full_size,broad=broad,veil=veil,w=w,h=h)

def fields(ctx,row,width,height,*,origin=(0,0),pixel_scale=(1,1)):
    source=ctx['source'];sx,sy=ctx['scale'];ox,oy=ctx['source_origin']
    x=round((origin[0]-ox)/sx);y=round((origin[1]-oy)/sy)+row
    sigma=max(.6,.00065*min(ctx['full_size'])/sy);halo=int(np.ceil(sigma*4))+2
    left=max(0,x-halo);right=min(ctx['w'],x+width+halo);top=max(0,y-halo);bottom=min(ctx['h'],y+height+halo)
    lum=display(source[top:bottom,left:right])@LUMA
    fine=cv2.GaussianBlur(lum,(0,0),sigma,borderType=cv2.BORDER_REFLECT_101)[y-top:y-top+height,x-left:x-left+width]
    base=lum[y-top:y-top+height,x-left:x-left+width]
    xx=((np.arange(width,dtype=np.float32)+x+.5)/ctx['w']*ctx['broad'].shape[1]-.5)[None,:]
    yy=((np.arange(height,dtype=np.float32)+y+.5)/ctx['h']*ctx['broad'].shape[0]-.5)[:,None]
    from .masking import sample_grid
    # sample_grid takes normalized source coordinates.
    u=(xx+.5)/ctx['broad'].shape[1];v=(yy+.5)/ctx['broad'].shape[0]
    broad=sample_grid(ctx['broad'],u,v);veil=sample_grid(ctx['veil'],u,v)
    return np.stack([base,base-fine,base-broad,veil],axis=-1).astype(np.float32)

def packed(rgb,*,full_size,pixel_scale=(1,1),origin=(0,0),source=None):
    ctx=prepare(rgb,full_size=full_size,pixel_scale=pixel_scale,origin=origin,source=source)
    h,w=rgb.shape[:2];out=np.empty((h,w,4),dtype='<u2')
    for row in range(0,h,128):
        f=fields(ctx,row,w,min(128,h-row),origin=origin,pixel_scale=pixel_scale);f[:,:,1:3]+=.5
        out[row:row+128]=np.rint(np.clip(f,0,1)*65535).astype(np.uint16)
    return out

def sharp_field(source,row,width,height,*,origin=(0,0),source_origin=(0,0),pixel_scale=(1,1)):
    """Small-radius luminance high-pass; real-source halo prevents ROI seams."""
    x=round((origin[0]-source_origin[0])/pixel_scale[0]);y=round((origin[1]-source_origin[1])/pixel_scale[1])+row
    h,w=source.shape[:2];xx=np.arange(x-1,x+width+1);yy=np.arange(y-1,y+height+1)
    xx=np.where(xx<0,-xx,np.where(xx>=w,2*w-2-xx,xx)).clip(0,w-1)
    yy=np.where(yy<0,-yy,np.where(yy>=h,2*h-2-yy,yy)).clip(0,h-1)
    lum=display(source[yy[:,None],xx[None,:]])@LUMA
    return lum[1:-1,1:-1]-(lum[:-2,1:-1]+lum[2:,1:-1]+lum[1:-1,:-2]+lum[1:-1,2:])*.25


def packed_sharpness(rgb,*,pixel_scale=(1,1),origin=(0,0),source=None):
    source_origin=origin if source is None else (0,0);source=rgb if source is None else source
    h,w=rgb.shape[:2];out=np.empty((h,w),dtype='<u2')
    for row in range(0,h,128):
        f=sharp_field(source,row,w,min(128,h-row),origin=origin,source_origin=source_origin,pixel_scale=pixel_scale)
        out[row:row+128]=np.rint(np.clip(f+.5,0,1)*65535).astype(np.uint16)
    return out


def apply(x,f,dehaze,clarity,texture,sharpness=0,sharp=None):
    lum=x@LUMA;base,fine,broad,veil=np.moveaxis(f,-1,0)
    # Soft threshold small high-frequency fluctuations; bound large-edge boosts.
    gate=np.clip((np.abs(fine)-.004)/.016,0,1);gate=gate*gate*(3-2*gate)
    middle=4*np.clip(lum,0,1)*(1-np.clip(lum,0,1))
    delta=(clarity/100*.55*np.clip(broad,-.10,.10)+texture/100*.8*np.clip(fine,-.05,.05)*gate)*middle
    if sharp is not None:
        gate=np.clip((np.abs(sharp)-.002)/.010,0,1);gate=gate*gate*(3-2*gate)
        delta+=sharpness/100*.65*np.clip(sharp,-.04,.04)*gate*middle
    target=lum+delta*np.clip(lum/np.maximum(base,.04),.25,2)
    amount=dehaze/100
    # Amounts may be per-pixel maps from overlapping local masks.
    v=np.clip(veil,0,.75)*np.maximum(amount,0)*.55
    target=np.maximum((target-v)/(1-v),target*.55)
    target=target+(1-target)*np.maximum(-amount,0)*.22
    # Preserve hue with a luminance gain; protect blacks and avoid unstable division.
    gain=np.clip(target/np.maximum(lum,.01),.5,2)
    lift=np.maximum(target-lum*gain,0)
    return x*gain[...,None]+lift[...,None]
