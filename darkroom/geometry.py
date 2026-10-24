"""Source-coordinate geometry: rectify first, crop last. Masks remain in source space."""
import itertools
import math
import numpy as np
from PIL import Image
from pydantic import BaseModel,ConfigDict,Field,model_validator

IDENTITY=[1,0,0,0,1,0,0,0,1]


class Geometry(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    rotation:float=Field(default=0,ge=-20,le=20)
    vertical:float=Field(default=0,ge=-.6,le=.6)
    horizontal:float=Field(default=0,ge=-.6,le=.6)
    upright:list[float]=Field(default_factory=lambda:IDENTITY.copy(),min_length=9,max_length=9)
    crop:tuple[float,float,float,float]=(0,0,1,1)

    @model_validator(mode="after")
    def validate_geometry(self):
        x,y,w,h=self.crop
        if min(x,y)<0 or min(w,h)<.01 or x+w>1.000001 or y+h>1.000001:raise ValueError("Invalid crop bounds")
        matrix=np.asarray(self.upright).reshape(3,3)
        if not np.all(np.isfinite(matrix)) or np.max(np.abs(matrix))>20 or abs(np.linalg.det(matrix))<1e-5:raise ValueError("Invalid upright matrix")
        return self


def forward_matrix(size,g):
    w,h=size;s=min(w,h)
    normalized=np.array([[1/s,0,-w/(2*s)],[0,1/s,-h/(2*s)],[0,0,1.]])
    t=math.radians(g.rotation);c=math.cos(t);sine=math.sin(t)
    rotate=np.array([[c,-sine,0],[sine,c,0],[0,0,1.]])
    perspective=np.array([[1,0,0],[0,1,0],[g.horizontal,g.vertical,1.]])
    return np.linalg.inv(normalized)@perspective@rotate@np.array(g.upright).reshape(3,3)@normalized


def apply_geometry(image,g):
    if g.rotation or g.vertical or g.horizontal or g.upright!=IDENTITY:
        inverse=np.linalg.inv(forward_matrix(image.size,g));inverse/=inverse[2,2]
        transformed=image.transform(image.size,Image.Transform.PERSPECTIVE,tuple(inverse.flat)[:8],Image.Resampling.BICUBIC,fillcolor=(16,18,18))
        image.close();image=transformed
    x,y,w,h=g.crop
    if g.crop!=(0,0,1,1):
        width,height=image.size
        left,top=round(x*width),round(y*height)
        right,bottom=min(width,max(left+1,round((x+w)*width))),min(height,max(top+1,round((y+h)*height)))
        cropped=image.crop((left,top,right,bottom));image.close();image=cropped
    return image


def estimate_geometry(image,mode):
    import cv2
    cv2.setNumThreads(2)
    image.thumbnail((1200,1200),Image.Resampling.LANCZOS)
    gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,50,150)
    lines=cv2.HoughLinesP(edges,1,np.pi/720,threshold=45,minLineLength=max(35,min(image.size)*.08),maxLineGap=12)
    if lines is None:raise ValueError("분석할 직선이 부족합니다. 수동 각도/원근 조절을 사용하세요.")
    groups={"h":[],"v":[]};angles=[]
    w,h=image.size;short=min(w,h)
    for x1,y1,x2,y2 in lines.reshape(-1,4):
        dx,dy=float(x2-x1),float(y2-y1);angle=math.degrees(math.atan2(dy,dx));angle=(angle+90)%180-90
        if abs(angle)<=20:groups['h'].append((x1,y1,x2,y2));angles.append(angle)
        elif abs(angle)>=70:groups['v'].append((x1,y1,x2,y2));angles.append(angle-90 if angle>0 else angle+90)
    if len(angles)<3:raise ValueError("신뢰할 수 있는 수평/수직선이 부족합니다. 자동 보정을 적용하지 않았습니다.")
    median=float(np.median(angles));inliers=[a for a in angles if abs(a-median)<4]
    if mode=="upright" and len(inliers)<3:inliers=angles
    if len(inliers)<3:raise ValueError("직선 방향이 일정하지 않습니다. 수동 보정을 사용하세요.")
    rotation=float(np.clip(-np.median(inliers),-15,15));g=Geometry(rotation=rotation)
    if mode=="level":return {"geometry":g.model_dump(),"lines":len(inliers),"message":f"직선 {len(inliers)}개 기준 수평 {rotation:.2f}°"}
    # Estimate vanishing points after leveling, in short-side normalized coordinates.
    t=math.radians(rotation);r=np.array([[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]])
    def vanishing(group):
        equations=[]
        for x1,y1,x2,y2 in group[:80]:
            a=r@np.array([(x1-w/2)/short,(y1-h/2)/short]);b=r@np.array([(x2-w/2)/short,(y2-h/2)/short])
            line=np.cross([*a,1],[*b,1]);length=np.linalg.norm(line[:2])
            if length>1e-6:equations.append(line/length)
        candidates=[]
        for a,b in itertools.combinations(equations,2):
            point=np.cross(a,b)
            if abs(point[2])<1e-4:continue
            p=point[:2]/point[2]
            if np.linalg.norm(p)<.75 or np.linalg.norm(p)>100:continue
            errors=np.abs(np.array(equations)[:,:2]@p+np.array(equations)[:,2]);support=errors<.035
            if support.sum()>=3:candidates.append((int(support.sum()),float(np.median(errors)),p))
        if not candidates:return None
        return sorted(candidates,key=lambda v:(-v[0],v[1]))[0][2]
    vp=vanishing(groups['v']);hp=vanishing(groups['h'])
    correction=np.eye(3);used=[]
    if vp is not None and abs(vp[1])>.6:
        if hp is not None and abs(hp[0])>.6:
            try:
                p,q=np.linalg.solve(np.array([hp,vp]),[-1,-1])
                axes=np.column_stack((hp/np.linalg.norm(hp),vp/np.linalg.norm(vp)))
                if axes[0,0]<0:axes[:,0]*=-1
                if axes[1,1]<0:axes[:,1]*=-1
                correction[:2,:2]=np.linalg.inv(axes);correction[2,:2]=[p,q];used=["수직","수평"]
            except np.linalg.LinAlgError:pass
        if not used:
            correction=np.array([[1,-vp[0]/vp[1],0],[0,1,0],[0,-1/vp[1],1.]])
            used=["수직"]
    corners=np.array([[-w/(2*short),-h/(2*short),1],[w/(2*short),-h/(2*short),1],[w/(2*short),h/(2*short),1],[-w/(2*short),h/(2*short),1]])
    if np.min((correction@corners.T)[2])<.2 or np.max(np.abs(correction))>5:raise ValueError("자동 원근 보정이 과도해 적용하지 않았습니다. 수동 조절을 사용하세요.")
    rotation_matrix=np.eye(3);rotation_matrix[:2,:2]=r
    g=Geometry(upright=(correction@rotation_matrix).ravel().tolist())
    return {"geometry":g.model_dump(),"lines":len(inliers),"message":("·".join(used)+" 원근 및 수평 보정") if used else "원근 추정이 불확실해 수평만 보정했습니다. 필요하면 수동 조절하세요."}
