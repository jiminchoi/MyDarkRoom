"""Synthetic math checks plus an opt-in real NEF end-to-end test."""
from io import BytesIO
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from darkroom.app import app, Studio
from darkroom.imaging import Adjustments, PREVIEW_PIXELS, jpeg, render, resize_preview


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def test_srgb_encoding_and_exposure():
    rgb = np.full((2, 4, 3), 11796, dtype=np.uint16)  # 18% linear gray
    base = np.asarray(render(rgb, Adjustments()))
    brighter = np.asarray(render(rgb, Adjustments(exposure=1)))
    assert np.all(abs(base.astype(int) - 118) <= 1)
    assert np.all(brighter > base)
    assert np.array_equal(rgb, np.full((2, 4, 3), 11796, dtype=np.uint16))


def test_desaturation_and_white_balance():
    rgb = np.array([[[12000, 6000, 3000], [20000, 20000, 20000]]], dtype=np.uint16)
    gray = np.asarray(render(rgb, Adjustments(saturation=-100)))
    assert np.array_equal(gray[..., 0], gray[..., 1])
    assert np.array_equal(gray[..., 1], gray[..., 2])
    warm = np.asarray(render(rgb, Adjustments(temperature=100)))
    cool = np.asarray(render(rgb, Adjustments(temperature=-100)))
    assert warm[0, 1, 0] > cool[0, 1, 0]
    assert warm[0, 1, 2] < cool[0, 1, 2]


def test_tone_and_color_controls_change_output():
    ramp = np.linspace(2000, 50000, 40, dtype=np.uint16)
    rgb = np.stack([ramp, ramp // 2, ramp // 3], axis=-1)[None, ...]
    base = np.asarray(render(rgb, Adjustments()))
    for name in ["contrast", "highlights", "shadows", "tint", "saturation"]:
        adjusted = np.asarray(render(rgb, Adjustments(**{name: 60})))
        assert not np.array_equal(adjusted, base), name


def test_jpeg_is_color_managed():
    image = Image.open(BytesIO(jpeg(np.full((16, 24, 3), 12000, dtype=np.uint16), Adjustments(), quality=95)))
    assert image.format == "JPEG" and image.size == (24, 16)
    assert image.info["icc_profile"]


def test_health_and_page(client):
    assert client.get("/api/health").json()["libraw"] == "0.22.1"
    assert "MyDarkRoom" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_invalid_uploads_are_cleaned_up(client):
    for filename, content, expected in [("image.jpg", b"bad", 415), ("empty.NEF", b"", 422), ("broken.nef", b"not a raw file", 422)]:
        response = client.post("/api/photos", files={"file": (filename, content)})
        assert response.status_code == expected
    assert list(app.state.studio.directory.iterdir()) == []


def test_invalid_adjustments_and_missing_session(client):
    assert client.post("/api/photos/missing/preview", json={}).status_code == 404
    for settings in [{"exposure": 4}, {"tint": -101}, {"unknown": 0}, {"exposure": "NaN"}]:
        assert client.post("/api/photos/missing/preview", json=settings).status_code == 422


def test_cross_origin_upload_blocked(client):
    assert client.post("/api/photos", headers={"Origin": "https://other.example"}, files={"file": ("test.nef", b"bad")}).status_code == 403


def test_eviction_removes_only_old_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("darkroom.app.develop", lambda *args, **kwargs: np.zeros((8, 8, 3), dtype=np.uint16))
    studio = Studio(tmp_path)
    ids = []
    for i in range(4):
        path = tmp_path / f"{i}.nef"
        path.write_bytes(b"fixture")
        ids.append(studio.open(path, f"{i}.nef")["id"])
        path.with_suffix(".npy").write_bytes(b"cached")
    assert len(studio.photos) == 3
    assert ids[0] not in studio.photos
    assert not (tmp_path / "0.nef").exists()
    assert not (tmp_path / "0.npy").exists()
    assert (tmp_path / "3.nef").exists()


@pytest.mark.skipif(not os.environ.get("MYDARKROOM_TEST_NEF"), reason="Set MYDARKROOM_TEST_NEF to a real NEF path")
def test_real_nef_upload_adjust_export(client):
    import hashlib
    import rawpy

    path = Path(os.environ["MYDARKROOM_TEST_NEF"])
    original_hash = hashlib.sha256(path.read_bytes()).digest()
    with path.open("rb") as file:
        response = client.post("/api/photos", files={"file": (path.name, file)})
    assert response.status_code == 200, response.text
    photo = response.json()
    baseline = client.post(f"/api/photos/{photo['id']}/preview", json={})
    edited = client.post(f"/api/photos/{photo['id']}/preview", json={"exposure": 0.8, "temperature": 20})
    assert baseline.status_code == edited.status_code == 200
    assert baseline.content != edited.content
    preview = Image.open(BytesIO(edited.content))
    assert min(photo["width"]*photo["height"], PREVIEW_PIXELS)*.99 <= preview.width * preview.height <= PREVIEW_PIXELS
    linear = client.get(f"/api/photos/{photo['id']}/pixels")
    assert linear.status_code == 200
    assert linear.headers["X-Image-Width"] == str(preview.width)
    assert len(linear.content) == preview.width * preview.height * 6
    assert np.array_equal(np.frombuffer(linear.content, dtype="<u2").reshape(preview.height, preview.width, 3), app.state.studio.photos[photo["id"]].preview)
    detail = client.get(f"/api/photos/{photo['id']}/detail?x=100&y=100&width=80&height=60")
    assert detail.status_code == 200
    cached = np.load(app.state.studio.photos[photo["id"]].path.with_suffix(".npy"), mmap_mode="r")
    assert np.array_equal(np.frombuffer(detail.content,dtype="<u2").reshape(60,80,3),cached[100:160,100:180])
    del cached
    exported = client.post(f"/api/photos/{photo['id']}/export", json={"exposure": 0.8, "temperature": 20,
        "monochrome":True,"mix_red":60,"mix_green":30,"mix_blue":10,"tone_hue":35,"tone_amount":25,"grain":50,"grain_size":2})
    assert exported.status_code == 200
    image = Image.open(BytesIO(exported.content))
    image.load()
    with rawpy.imread(str(path)) as raw:
        expected = (raw.sizes.width, raw.sizes.height)
        if raw.sizes.flip in (5, 6):
            expected = expected[::-1]
    assert image.size == expected
    assert (photo["width"],photo["height"]) == image.size
    assert image.info["icc_profile"]
    assert hashlib.sha256(path.read_bytes()).digest() == original_hash


def test_preview_budget_and_no_upscaling():
    small = np.zeros((100, 200, 3), dtype=np.uint16)
    assert resize_preview(small) is small
    large = np.full((1800, 3000, 3), 12345, dtype=np.uint16)
    reduced = resize_preview(large)
    assert 3_990_000 < reduced.shape[0]*reduced.shape[1] <= PREVIEW_PIXELS
    assert abs(reduced.shape[1]/reduced.shape[0] - 3000/1800) < .002
    assert reduced.dtype == np.uint16 and np.all(reduced == 12345)


def test_detail_cache_and_bounds(client, monkeypatch):
    full = np.arange(100*120*3,dtype=np.uint16).reshape(100,120,3)
    calls = []
    def fake_develop(path, *, preview, metadata=None):
        if metadata is not None:
            metadata.update(width=120,height=100)
        calls.append(preview)
        return full[::2,::2].copy() if preview else full.copy()
    monkeypatch.setattr("darkroom.app.develop",fake_develop)
    photo = client.post("/api/photos",files={"file":("fixture.NEF",b"fixture")}).json()
    url = f"/api/photos/{photo['id']}/detail"
    response = client.get(url+"?x=10&y=20&width=30&height=40")
    assert response.status_code == 200
    assert np.array_equal(np.frombuffer(response.content,dtype="<u2").reshape(40,30,3),full[20:60,10:40])
    edge = client.get(url+"?x=118&y=99&width=30&height=40")
    assert edge.headers["X-Image-Width"] == "2" and edge.headers["X-Image-Height"] == "1"
    cpu = client.post(url+"?x=10&y=20&width=30&height=40",json={"exposure":1})
    assert cpu.status_code == 200 and Image.open(BytesIO(cpu.content)).size == (30,40)
    assert client.post(f"/api/photos/{photo['id']}/export",json={}).status_code == 200
    assert calls == [True, False]  # The full RAW is developed once, then mmap reused.
    for query in ["x=-1&y=0&width=1&height=1","x=120&y=0&width=1&height=1","x=0&y=0&width=4097&height=1"]:
        assert client.get(url+"?"+query).status_code == 422
