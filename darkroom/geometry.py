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


def auto_crop(size,g):
    """Largest original-aspect rectangle inside the transformed source and canvas.

    Three-variable linear program: center x/y and common width/height fraction.
    Enumerate vertices of the small feasible polytope; no numerical optimizer needed.
    """
    w,h=size;matrix=forward_matrix(size,g)
    inset=2
    corners=np.array([[inset,inset,1],[w-inset,inset,1],[w-inset,h-inset,1],[inset,h-inset,1]],dtype=float).T
    q=matrix@corners
    if np.min(q[2])<=0:raise ValueError("안전하게 크롭할 수 없는 원근입니다.")
    q=(q[:2]/q[2]).T/np.array([w,h])
    constraints=[]
    for polygon in (q,np.array([[0,0],[1,0],[1,1],[0,1]])):
        for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
            delta=b-a;normal=np.array([delta[1],-delta[0]])
            constraints.append([*normal,(abs(normal[0])+abs(normal[1]))/2,float(normal@a)])
    a=np.array(constraints)[:,:3];b=np.array(constraints)[:,3]
    best=None
    for ids in itertools.combinations(range(len(b)),3):
        try:solution=np.linalg.solve(a[list(ids)],b[list(ids)])
        except np.linalg.LinAlgError:continue
        if solution[2]>0 and np.all(a@solution<=b+1e-8):
            if best is None or solution[2]>best[2]+1e-8 or (abs(solution[2]-best[2])<=1e-8 and np.linalg.norm(solution[:2]-.5)<np.linalg.norm(best[:2]-.5)):best=solution
    if best is None:raise ValueError("자동 크롭 영역을 찾지 못했습니다.")
    x,y,scale=best
    return [max(0,float(x-scale/2)),max(0,float(y-scale/2)),float(scale),float(scale)]


def estimate_geometry(image,mode):
    import cv2
    cv2.setNumThreads(2)
    image.thumbnail((1400,1400),Image.Resampling.LANCZOS)
    gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY);w,h=image.size;short=min(w,h)
    # LSD validates straight segments locally; Hough votes alone overcount foliage.
    detected=cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if detected is None:raise ValueError("분석할 직선이 부족합니다. 보정을 적용하지 않았습니다.")
    groups={"h":[],"v":[]}
    for x1,y1,x2,y2 in detected.reshape(-1,4):
        dx,dy=float(x2-x1),float(y2-y1);length=math.hypot(dx,dy)
        if length<max(50,short*.16):continue
        angle=(math.degrees(math.atan2(dy,dx))+90)%180-90
        kind="h" if abs(angle)<12 else "v" if abs(angle)>78 else None
        if kind is None:continue
        points=np.array([[x1,y1],[x2,y2]],dtype=float);mid=points.mean(axis=0)
        # Merge both edges of one physical line, including overlapping fragments.
        direction=np.array([dx,dy])/length;normal=np.array([-direction[1],direction[0]])
        duplicate=False
        for item in groups[kind]:
            if abs(((angle-item['angle']+90)%180)-90)<1 and abs(normal@(mid-item['mid']))<8:
                projected=(item['points']-points[0])@direction
                if projected.max()>-12 and projected.min()<length+12:duplicate=True;break
        if not duplicate:groups[kind].append(dict(points=points,mid=mid,angle=angle,length=length))
    def support(group):
        if len(group)<3:return False
        centers=np.array([v['mid'] for v in group]);axis=1 if group is groups['h'] else 0
        return np.ptp(centers[:,axis])>image.size[axis]*.25 and sum(v['length'] for v in group)>short*1.1
    def roll(group,vertical=False):
        if not support(group):return None
        angles=np.array([v['angle']-(90 if v['angle']>0 else -90) if vertical else v['angle'] for v in group])
        weights=np.array([v['length'] for v in group]);median=np.median(angles);good=np.abs(angles-median)<.8
        if good.sum()<3 or weights[good].sum()/weights.sum()<.75:return None
        return float(np.average(angles[good],weights=weights[good]))
    horizontal=roll(groups['h']);vertical=roll(groups['v'],True)
    if horizontal is not None and vertical is not None and abs(horizontal-vertical)>1.2:
        if mode=="level":raise ValueError("수평선과 수직선의 판단이 충돌해 적용하지 않았습니다.")
        vertical=None
    angle=horizontal if horizontal is not None else vertical
    if angle is None or abs(angle)>8:raise ValueError("일관된 수평·수직 기준이 부족해 자동 보정을 적용하지 않았습니다.")
    rotation=-angle;g=Geometry(rotation=rotation);used="수평"
    if mode!="level":
        # Perspective requires evidence for both building axes across the image.
        if not support(groups['h']) or not support(groups['v']):raise ValueError("건축물의 수평·수직 기준이 부족해 원근 보정을 적용하지 않았습니다.")
        t=math.radians(rotation);r=np.array([[math.cos(t),-math.sin(t)],[math.sin(t),math.cos(t)]])
        def vanishing(group):
            equations=[]
            for item in sorted(group,key=lambda v:-v['length'])[:40]:
                a,b=(item['points']-np.array([w/2,h/2]))/short;a=r@a;b=r@b
                line=np.cross([*a,1],[*b,1]);equations.append(line/np.linalg.norm(line[:2]))
            lines=np.array(equations);candidates=[]
            for a,b in itertools.combinations(lines,2):
                point=np.cross(a,b)
                if abs(point[2])<1e-6:continue
                p=point[:2]/point[2];distance=np.linalg.norm(p)
                if distance<3 or distance>300:continue
                errors=np.abs(lines[:,:2]@p+lines[:,2])/distance;good=errors<.006
                if good.sum()>=3 and good.mean()>=.75:candidates.append((good.sum(),-np.median(errors),p,good))
            if not candidates:return None
            candidate=max(candidates,key=lambda v:(v[0],v[1]));selected=lines[candidate[3]]
            p=np.linalg.lstsq(selected[:,:2],-selected[:,2],rcond=None)[0]
            return p
        vp=vanishing(groups['v']);hp=vanishing(groups['h']) if mode=="upright" else None
        correction=np.eye(3)
        if vp is not None and abs(vp[1])>3:
            correction=np.array([[1,-vp[0]/vp[1],0],[0,1,0],[0,-1/vp[1],1.]])
            used="수직 원근·수평"
            if hp is not None and abs(hp[0])>3:
                try:
                    p,q=np.linalg.solve(np.array([hp,vp]),[-1,-1])
                    axes=np.column_stack((hp/np.linalg.norm(hp),vp/np.linalg.norm(vp)))
                    if axes[0,0]<0:axes[:,0]*=-1
                    if axes[1,1]<0:axes[:,1]*=-1
                    correction[:2,:2]=np.linalg.inv(axes);correction[2,:2]=[p,q];used="수평·수직 원근"
                except np.linalg.LinAlgError:raise ValueError("원근 추정이 불안정해 적용하지 않았습니다.")
        elif vertical is None:raise ValueError("수직 원근의 신뢰도가 낮아 적용하지 않았습니다.")
        rotation_matrix=np.eye(3);rotation_matrix[:2,:2]=r
        matrix=correction@rotation_matrix
        corners=np.array([[-w/(2*short),-h/(2*short),1],[w/(2*short),-h/(2*short),1],[w/(2*short),h/(2*short),1],[-w/(2*short),h/(2*short),1]])
        q=matrix@corners.T;den=q[2]
        if den.min()<.7 or den.max()/den.min()>1.4 or np.max(np.abs(matrix[:2,:2]-np.eye(2)))>.15:
            raise ValueError("원근 변화가 커서 자동 적용하지 않았습니다. 수동 조절을 사용하세요.")
        g=Geometry(upright=matrix.ravel().tolist())
    g.crop=tuple(auto_crop(image.size,g))
    if g.crop[2]*g.crop[3]<.65:raise ValueError("자동 보정 시 잘리는 영역이 커서 적용하지 않았습니다.")
    return {"geometry":g.model_dump(),"lines":len(groups['h'])+len(groups['v']),"message":f"{used} 보정 · 원본 비율 자동 크롭"}
