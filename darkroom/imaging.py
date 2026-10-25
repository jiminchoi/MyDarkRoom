from io import BytesIO
from pathlib import Path
import colorsys

import numpy as np
import rawpy
from PIL import Image, ImageCms
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .curves import curve_lut
from . import structure
from .masking import Mask, bake_masks, has_spatial
from .geometry import Geometry,apply_geometry
from .raw_support import compatible_develop


class Adjustments(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    exposure: float = Field(default=0, ge=-3, le=3)
    contrast: float = Field(default=0, ge=-100, le=100)
    highlights: float = Field(default=0, ge=-100, le=100)
    shadows: float = Field(default=0, ge=-100, le=100)
    temperature: float = Field(default=0, ge=-100, le=100)
    tint: float = Field(default=0, ge=-100, le=100)
    dehaze: float = Field(default=0,ge=-100,le=100)
    clarity: float = Field(default=0,ge=-100,le=100)
    texture: float = Field(default=0,ge=-100,le=100)
    saturation: float = Field(default=0, ge=-100, le=100)
    monochrome: bool = False
    mix_red: float = Field(default=30, ge=0, le=200)
    mix_green: float = Field(default=59, ge=0, le=200)
    mix_blue: float = Field(default=11, ge=0, le=200)
    tone_hue: float = Field(default=35, ge=0, le=360)
    tone_amount: float = Field(default=0, ge=0, le=100)
    grain: float = Field(default=0, ge=0, le=100)
    grain_size: float = Field(default=1, ge=1, le=8)
    geometry: Geometry = Field(default_factory=Geometry)
    masks: list[Mask] = Field(default_factory=list, max_length=8)
    curve_points: list[tuple[float,float]] | None = Field(default=None,min_length=2,max_length=32)
    curve_black: float = Field(default=0,ge=0,le=100)
    curve_shadow: float = Field(default=25,ge=0,le=100)
    curve_mid: float = Field(default=50,ge=0,le=100)
    curve_light: float = Field(default=75,ge=0,le=100)
    curve_white: float = Field(default=100,ge=0,le=100)

    @model_validator(mode="after")
    def ordered_curve(self):
        points=[self.curve_black,self.curve_shadow,self.curve_mid,self.curve_light,self.curve_white]
        if points!=sorted(points):raise ValueError("Tone curve must be nondecreasing")
        if self.curve_points is not None:
            p=self.curve_points
            if p[0][0]!=0 or p[-1][0]!=1 or any(not 0<=y<=1 for x,y in p) or any(p[i][0]-p[i-1][0]<.002 for i in range(1,len(p))):
                raise ValueError("Invalid curve points")
        return self


SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


PREVIEW_PIXELS = 4_000_000


def resize_preview(rgb: np.ndarray) -> np.ndarray:
    height, width = rgb.shape[:2]
    if height * width <= PREVIEW_PIXELS:
        return rgb
    scale = (PREVIEW_PIXELS / (height * width)) ** 0.5
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return np.stack([
        np.asarray(Image.fromarray(rgb[:, :, c]).resize(size, Image.Resampling.BILINEAR))
        for c in range(3)
    ], axis=2)


def develop(path: Path, *, preview: bool, metadata: dict | None = None) -> np.ndarray:
    return compatible_develop(path,_develop_native,preview=preview,metadata=metadata)


def _develop_native(path: Path, *, preview: bool, metadata: dict | None = None) -> np.ndarray:
    """Camera WB -> linear 16-bit sRGB. Never modify the original RAW."""
    with rawpy.imread(str(path)) as raw:
        if raw.raw_type != rawpy.RawType.Flat or raw.num_colors not in (3,4):
            raise ValueError("지원하지 않는 NEF 센서 형식입니다.")
        width, height = raw.sizes.width, raw.sizes.height
        if raw.sizes.flip in (5, 6):
            width, height = height, width
        if metadata is not None:
            metadata.update(width=width, height=height)
        rgb = raw.postprocess(
            use_camera_wb=True,
            no_auto_bright=True,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=16,
            gamma=(1, 1),
            # Do not undershoot 4 MP on 12/14 MP bodies by half-size decoding.
            half_size=preview and width * height >= PREVIEW_PIXELS * 4,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
        )
    return resize_preview(rgb) if preview else rgb


def render(rgb: np.ndarray, settings: Adjustments, *, origin=(0, 0), pixel_scale=(1, 1), full_size=None, structure_source=None) -> Image.Image:
    """Shared preview/export pipeline; process strips to bound temporary memory."""
    full_size=full_size or (round(rgb.shape[1]*pixel_scale[0]),round(rgb.shape[0]*pixel_scale[1]))
    spatial=structure.prepare(rgb,full_size=full_size,pixel_scale=pixel_scale,origin=origin,source=structure_source) if settings.dehaze or settings.clarity or settings.texture or has_spatial(settings.masks) else None
    result = np.empty(rgb.shape, dtype=np.uint8)
    warmth = settings.temperature / 100
    tint = settings.tint / 100
    gains = np.array([2 ** (0.45 * warmth + 0.15 * tint),
                      2 ** (-0.3 * tint),
                      2 ** (-0.45 * warmth + 0.15 * tint)], dtype=np.float32)
    curve=curve_lut(settings)
    identity=np.max(np.abs(curve-np.linspace(0,1,256)))<1e-7
    for row in range(0, rgb.shape[0], 128):
        strip=bake_masks(rgb[row:row+128],settings.masks,origin=(origin[0],origin[1]+row*pixel_scale[1]),pixel_scale=pixel_scale,full_size=full_size,structure_context=spatial)
        x = strip.astype(np.float32) / 65535
        x *= (2 ** settings.exposure) * gains
        luma = x @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        shadow_weight = np.square(1 - np.clip(luma / 0.5, 0, 1))
        highlight_weight = np.square(np.clip((luma - 0.2) / 0.8, 0, 1))
        ev = (settings.shadows / 100) * shadow_weight + (settings.highlights / 100) * highlight_weight
        x *= np.exp2(ev)[..., None]
        # Display-referred controls after the linear-light exposure/WB operations.
        x = np.clip(x, 0, 1)
        x = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)
        if settings.dehaze or settings.clarity or settings.texture:
            f=structure.fields(spatial,row,rgb.shape[1],len(x),origin=origin,pixel_scale=pixel_scale)
            x=structure.apply(x,f,settings.dehaze,settings.clarity,settings.texture)
        x = (x - 0.5) * (2 ** (settings.contrast / 100)) + 0.5
        if not identity:
            position=np.clip(x,0,1)*255
            index=np.minimum(position.astype(np.int32),254)
            fraction=position-index.astype(np.float32)
            x=curve[index]*(1-fraction)+curve[index+1]*fraction
        gray = x @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        x = gray[..., None] + (x - gray[..., None]) * (1 + settings.saturation / 100)
        if settings.monochrome:
            weights = np.array([settings.mix_red, settings.mix_green, settings.mix_blue], dtype=np.float32)
            weights = weights / weights.sum() if weights.sum() else np.array([.2126, .7152, .0722], dtype=np.float32)
            gray = x @ weights
            x = np.repeat(gray[..., None], 3, axis=2)
        if settings.tone_amount:
            tone = np.array(colorsys.hsv_to_rgb(settings.tone_hue / 360, .65, 1), dtype=np.float32)
            tone /= tone @ np.array([.2126,.7152,.0722], dtype=np.float32)
            gray = x @ np.array([.2126,.7152,.0722], dtype=np.float32)
            amount = settings.tone_amount / 100
            x = x * (1-amount) + gray[..., None] * tone * amount
        if settings.grain:
            cx = np.floor((origin[0] + (np.arange(rgb.shape[1])+.5)*pixel_scale[0])/settings.grain_size).astype(np.uint32)
            cy = np.floor((origin[1] + (np.arange(row, min(row+128,rgb.shape[0]))+.5)*pixel_scale[1])/settings.grain_size).astype(np.uint32)
            h = (cx[None,:]*np.uint32(374761393)) ^ (cy[:,None]*np.uint32(668265263)) ^ np.uint32(12345)
            h = (h ^ (h >> 13)) * np.uint32(1274126177)
            h ^= h >> 16
            noise = (h & 65535).astype(np.float32)/65535 - .5
            x += noise[...,None] * (settings.grain/100*.16)
        result[row:row + 128] = np.rint(np.clip(x, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(result)


def jpeg(rgb: np.ndarray, settings: Adjustments, *, quality: int, origin=(0,0), pixel_scale=(1,1), full_size=None,long_edge=0,dpi=300,geometry=True,structure_source=None) -> bytes:
    output = BytesIO()
    image=render(rgb, settings, origin=origin, pixel_scale=pixel_scale,full_size=full_size,structure_source=structure_source)
    if geometry:image=apply_geometry(image,settings.geometry)
    if long_edge and max(image.size)>long_edge:
        image.thumbnail((long_edge,long_edge),Image.Resampling.LANCZOS)
    image.save(output, format="JPEG", quality=quality,dpi=(dpi,dpi),subsampling=0, icc_profile=SRGB_PROFILE)
    image.close()
    return output.getvalue()
