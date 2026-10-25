from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from uuid import uuid4
from io import BytesIO
from PIL import Image

import numpy as np
import rawpy
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .imaging import Adjustments, develop, jpeg,render
from .geometry import estimate_geometry
from .masking import bake_masks, mask_weight,brush_grid
from .raw_support import RawCompatibilityError,converter_path

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 256 * 1024 * 1024
MAX_SESSIONS = 3


@dataclass
class Photo:
    path: Path
    filename: str
    preview: np.ndarray
    touched: float
    width: int
    height: int


class Studio:
    def __init__(self, directory: Path):
        self.directory = directory
        self.photos: dict[str, Photo] = {}
        # Serialize LibRaw work and protect against eviction during export.
        self.lock = threading.Lock()

    def open(self, path: Path, filename: str) -> dict:
        with self.lock:
            try:
                metadata = {}
                preview = develop(path, preview=True, metadata=metadata)
            except RawCompatibilityError as exc:
                raise HTTPException(422,str(exc)) from exc
            except (rawpy.LibRawError, ValueError) as exc:
                raise HTTPException(422, f"NEF 로딩 실패 ({type(exc).__name__}): {str(exc)[:200]}. 파일 또는 RAW 엔진 지원 범위를 확인하세요.") from exc
            while len(self.photos) >= MAX_SESSIONS:
                oldest = min(self.photos, key=lambda key: self.photos[key].touched)
                self.remove(oldest)
            photo_id = uuid4().hex
            width = metadata.get("width", preview.shape[1])
            height = metadata.get("height", preview.shape[0])
            self.photos[photo_id] = Photo(path, filename, preview, time.monotonic(), width, height)
            return {"id": photo_id, "filename": filename,
                    "width": width, "height": height,
                    "decoder":metadata.get("decoder","LibRaw"),
                    "preview_width": preview.shape[1], "preview_height": preview.shape[0]}

    def remove(self, photo_id: str):
        photo = self.photos.pop(photo_id)
        photo.path.unlink(missing_ok=True)
        photo.path.with_suffix(".npy").unlink(missing_ok=True)
        photo.path.with_suffix(".dng").unlink(missing_ok=True)
        brush_grid.cache_clear()

    def get(self, photo_id: str) -> Photo:
        photo = self.photos.get(photo_id)
        if photo is None:
            raise HTTPException(404, "사진 세션이 만료되었습니다. NEF를 다시 열어 주세요.")
        photo.touched = time.monotonic()
        return photo

    def full(self, photo: Photo) -> np.ndarray:
        cache = photo.path.with_suffix(".npy")
        if not cache.exists():
            # Keep at most one full-resolution disk cache across all sessions.
            for other in self.photos.values():
                other.path.with_suffix(".npy").unlink(missing_ok=True)
            pending = cache.with_suffix(".pending")
            try:
                rgb = develop(photo.path, preview=False)
                with pending.open("wb") as output:
                    np.save(output, rgb, allow_pickle=False)
                del rgb
                pending.replace(cache)
            finally:
                pending.unlink(missing_ok=True)
        return np.load(cache, mmap_mode="r", allow_pickle=False)

    def pixels(self, photo_id: str, region: tuple | None = None, settings: Adjustments | None = None, linear=False):
        with self.lock:
            photo = self.get(photo_id)
            x = y = 0
            mapping=None
            try:
                rgb = photo.preview
                if region is not None:
                    x, y, width, height = region
                    if x >= photo.width or y >= photo.height:
                        raise HTTPException(422, "사진 범위를 벗어난 위치입니다.")
                    mapping=self.full(photo)
                    rgb = mapping[y:y + height, x:x + width]
                height, width = rgb.shape[:2]
                headers = {"X-Image-Width": str(width), "X-Image-Height": str(height),
                           "X-Image-X": str(x), "X-Image-Y": str(y)}
                if settings is not None:
                    scale=(1,1) if region else (photo.width/width,photo.height/height)
                    if linear:
                        rgb=bake_masks(rgb,settings.masks,origin=(x,y),pixel_scale=scale,full_size=(photo.width,photo.height))
                    else:
                        return jpeg(rgb, settings, quality=95, origin=(x,y),pixel_scale=scale,full_size=(photo.width,photo.height),geometry=False), headers
                return rgb.astype("<u2", copy=False).tobytes(), headers
            except (rawpy.LibRawError, ValueError) as exc:
                raise HTTPException(422, "원본 해상도 미리보기를 만들 수 없습니다.") from exc
            finally:
                if mapping is not None:mapping._mmap.close()

    def image(self, photo_id: str, settings: Adjustments, *, full: bool,long_edge=0,dpi=300,quality=95) -> bytes:
        with self.lock:
            photo = self.get(photo_id)
            try:
                rgb = self.full(photo) if full else photo.preview
                pixel_scale = (1,1) if full else (photo.width/rgb.shape[1],photo.height/rgb.shape[0])
                return jpeg(rgb, settings, quality=quality if full else 85, pixel_scale=pixel_scale,full_size=(photo.width,photo.height),long_edge=long_edge if full else 0,dpi=dpi)
            except (rawpy.LibRawError, ValueError) as exc:
                raise HTTPException(422, "RAW 현상에 실패했습니다. 다른 NEF 파일을 확인해 주세요.") from exc
            finally:
                if full and "rgb" in locals() and isinstance(rgb,np.memmap):rgb._mmap.close()

    def overlay(self,photo_id,settings,selected):
        with self.lock:
            photo=self.get(photo_id)
            if selected>=len(settings.masks):
                raise HTTPException(422,"마스크를 선택해 주세요.")
            h,w=photo.preview.shape[:2]
            xx=(np.arange(w,dtype=np.float32)[None,:]+.5)/w
            yy=(np.arange(h,dtype=np.float32)[:,None]+.5)/h
            weight=mask_weight(settings.masks[selected],photo.preview.astype(np.float32)/65535,xx,yy,(photo.width,photo.height))
            rgba=np.empty((h,w,4),dtype=np.uint8);rgba[:,:,:3]=[255,60,70]
            rgba[:,:,3]=np.rint(np.broadcast_to(weight,(h,w))*.45*255).astype(np.uint8)
            output=BytesIO();Image.fromarray(rgba).save(output,format="PNG")
            return output.getvalue()


@asynccontextmanager
async def lifespan(app: FastAPI):
    with TemporaryDirectory(prefix="mydarkroom-") as directory:
        app.state.studio = Studio(Path(directory))
        try:
            yield
        finally:
            brush_grid.cache_clear()


app = FastAPI(title="MyDarkRoom", lifespan=lifespan)


@app.middleware("http")
async def local_requests(request: Request, call_next):
    # A local personal tool: reject cross-origin browser mutation requests.
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD"} and origin and origin != str(request.base_url).rstrip("/"):
        return Response("Cross-origin requests are disabled", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "rawpy": rawpy.__version__, "libraw": ".".join(map(str, rawpy.libraw_version)),"dng_converter":bool(converter_path()),"app_version":"0.7.0"}


@app.post("/api/photos")
async def upload(request: Request, file: UploadFile):
    path = request.app.state.studio.directory / f"{uuid4().hex}.nef"
    accepted = False
    try:
        filename = (file.filename or "photo.nef").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".nef"):
            raise HTTPException(415, "NEF 파일만 열 수 있습니다.")
        total = 0
        with path.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, "사진 한 장의 최대 크기는 256 MB입니다.")
                output.write(chunk)
        if total == 0:
            raise HTTPException(422, "빈 파일입니다.")
        result = await run_in_threadpool(request.app.state.studio.open, path, filename)
        accepted = True
        return result
    finally:
        await file.close()
        if not accepted:
            path.unlink(missing_ok=True)
            path.with_suffix(".dng").unlink(missing_ok=True)


@app.post("/api/photos/{photo_id}/preview")
def preview(photo_id: str, settings: Adjustments, request: Request):
    data = request.app.state.studio.image(photo_id, settings, full=False)
    return Response(data, media_type="image/jpeg")


@app.post("/api/photos/{photo_id}/export")
def export(photo_id: str, settings: Adjustments, request: Request,long_edge:int=Query(default=0,ge=0,le=20000),dpi:int=Query(default=300,ge=1,le=2400),quality:int=Query(default=95,ge=1,le=100)):
    data = request.app.state.studio.image(photo_id, settings, full=True,long_edge=long_edge,dpi=dpi,quality=quality)
    return Response(data, media_type="image/jpeg",
                    headers={"Content-Disposition": 'attachment; filename="mydarkroom.jpg"'})


@app.delete("/api/photos/{photo_id}")
def close_photo(photo_id:str,request:Request):
    studio=request.app.state.studio
    with studio.lock:
        if photo_id in studio.photos:studio.remove(photo_id)
    return {"status":"closed"}


@app.post("/api/photos/{photo_id}/masked-pixels")
def masked_pixels(photo_id:str,settings:Adjustments,request:Request):
    data,headers=request.app.state.studio.pixels(photo_id,settings=settings,linear=True)
    return Response(data,media_type="application/octet-stream",headers=headers)


@app.post("/api/photos/{photo_id}/masked-detail")
def masked_detail(photo_id:str,settings:Adjustments,request:Request,
                  x:int=Query(ge=0),y:int=Query(ge=0),width:int=Query(ge=1,le=4096),height:int=Query(ge=1,le=4096)):
    data,headers=request.app.state.studio.pixels(photo_id,(x,y,width,height),settings,linear=True)
    return Response(data,media_type="application/octet-stream",headers=headers)


@app.post("/api/photos/{photo_id}/mask-overlay")
def mask_overlay(photo_id:str,settings:Adjustments,request:Request,selected:int=Query(ge=0,le=7)):
    return Response(request.app.state.studio.overlay(photo_id,settings,selected),media_type="image/png")


@app.get("/api/photos/{photo_id}/pixels")
def pixels(photo_id: str, request: Request):
    data, headers = request.app.state.studio.pixels(photo_id)
    return Response(data, media_type="application/octet-stream", headers=headers)


@app.get("/api/photos/{photo_id}/sample-color")
def sample_color(photo_id:str,request:Request,x:float=Query(ge=0,le=1),y:float=Query(ge=0,le=1)):
    studio=request.app.state.studio
    with studio.lock:
        photo=studio.get(photo_id)
        h,w=photo.preview.shape[:2]
        rgb=photo.preview[min(h-1,int(y*h)),min(w-1,int(x*w))].astype(np.float32)/65535
        rgb=np.where(rgb<=.0031308,rgb*12.92,1.055*rgb**(1/2.4)-.055)
        return {"color":[round(float(v)*255)/255 for v in np.clip(rgb,0,1)]}


@app.get("/api/photos/{photo_id}/detail")
def detail(photo_id: str, request: Request, x: int = Query(ge=0), y: int = Query(ge=0),
           width: int = Query(ge=1, le=4096), height: int = Query(ge=1, le=4096)):
    data, headers = request.app.state.studio.pixels(photo_id, (x, y, width, height))
    return Response(data, media_type="application/octet-stream", headers=headers)


@app.post("/api/photos/{photo_id}/detail")
def detail_cpu(photo_id: str, settings: Adjustments, request: Request,
               x: int = Query(ge=0), y: int = Query(ge=0),
               width: int = Query(ge=1, le=4096), height: int = Query(ge=1, le=4096)):
    data, headers = request.app.state.studio.pixels(photo_id, (x, y, width, height), settings)
    return Response(data, media_type="image/jpeg", headers=headers)


app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.post("/api/photos/{photo_id}/auto-geometry")
def auto_geometry(photo_id:str,request:Request,mode:str=Query(pattern="^(level|vertical|upright)$")):
    studio=request.app.state.studio
    with studio.lock:
        photo=studio.get(photo_id)
        image=render(photo.preview,Adjustments())
        try:return estimate_geometry(image,mode)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        except ImportError as exc:raise HTTPException(503,"자동 분석 모듈을 불러오지 못했습니다. start_windows.cmd로 패키지를 설치한 뒤 다시 실행하세요.") from exc
        finally:image.close()

@app.post("/api/photos/{photo_id}/auto-light")
def auto_light(photo_id:str,request:Request):
    """Conservative source-based recommendation, never changes color controls."""
    studio=request.app.state.studio
    with studio.lock:
        rgb=studio.get(photo_id).preview[::4,::4].astype(np.float32)/65535
        luma=rgb@np.array([.2126,.7152,.0722],dtype=np.float32)
        low,median,high=np.percentile(luma,[5,50,99.5])
        if high<1e-5:
            return {"adjustments":{"exposure":0,"contrast":0,"highlights":0,"shadows":0}}
        exposure=float(np.clip(np.log2(.18/max(median,.001))*.55,-1,1))
        # Preserve low-key scenes and protect existing highlights.
        if median<.015:exposure=min(exposure,.35)
        exposure=min(exposure,max(0,float(np.log2(.95/max(high,.001))))) if exposure>0 else exposure
        shifted_high=high*2**exposure
        highlights=float(np.clip((.8-shifted_high)*90,-45,0))
        shadows=float(np.clip((.06-low*2**exposure)*250,0,25)) if high>.12 else 0
        contrast=float(np.clip((.5-(high-low))*12,-8,8))
        return {"adjustments":{"exposure":round(exposure,2),"contrast":round(contrast),"highlights":round(highlights),"shadows":round(shadows)}}
