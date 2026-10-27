"""Versioned, atomic photo-specific edits, separate from disposable RAW caches."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .imaging import Adjustments

MAX_EDIT_BYTES = 4 * 1024 * 1024


class EditError(ValueError):
    pass


class EditConflict(EditError):
    pass


class PhotoIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SavedEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["mydarkroom-edit"] = "mydarkroom-edit"
    version: Literal[1] = 1
    photo: PhotoIdentity
    revision: int = Field(ge=1)
    saved_at: str
    settings: Adjustments


class EditUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    settings: Adjustments


class EditStore:
    def __init__(self, directory: Path):
        self.directory = directory
        # Independent from Studio.lock: saving never waits for RAW/JPEG processing.
        self.lock = threading.Lock()

    def _read(self, path, photo):
        try:
            if path.stat().st_size > MAX_EDIT_BYTES:
                raise ValueError("Edit file too large")
            saved = SavedEdit.model_validate_json(path.read_bytes())
            if saved.photo != photo:
                raise ValueError("Photo identity mismatch")
            return saved
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise EditError("저장한 보정정보를 읽지 못했습니다. edits 폴더의 파일과 백업을 보존해 주세요.") from exc

    def _load(self, photo):
        target = self.directory / f"{photo.sha256}.json"
        try:
            saved = self._read(target, photo)
            if saved is not None:
                return saved, None
        except EditError:
            backup = self._read(target.with_suffix(".bak"), photo)
            if backup is None:
                raise
            return backup, "최근 보정정보를 읽지 못해 이전 저장본을 복원했습니다."
        backup = self._read(target.with_suffix(".bak"), photo)
        return backup, "이전 저장본을 복원했습니다." if backup else None

    def load(self, photo):
        with self.lock:
            return self._load(photo)

    def _atomic(self, target, data):
        pending = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".edit-", suffix=".tmp", delete=False) as file:
                pending = Path(file.name)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            pending.replace(target)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    def save(self, photo, update):
        with self.lock:
            current, _ = self._load(photo)
            revision = current.revision if current else 0
            if revision != update.revision:
                raise EditConflict("다른 창에서 이 사진의 보정이 변경됐습니다. 보정정보를 파일로 내보낸 뒤 사진을 다시 열어 주세요.")
            saved = SavedEdit(photo=photo, revision=revision + 1,
                              saved_at=datetime.now(timezone.utc).isoformat(), settings=update.settings)
            data = saved.model_dump_json().encode("utf-8")
            if len(data) > MAX_EDIT_BYTES:
                raise EditError("보정정보가 저장 한도(4 MB)를 넘었습니다.")
            self.directory.mkdir(parents=True, exist_ok=True)
            target = self.directory / f"{photo.sha256}.json"
            # Back up validated state, never a damaged primary file.
            if current:
                self._atomic(target.with_suffix(".bak"), current.model_dump_json().encode("utf-8"))
            self._atomic(target, data)
            return saved


def exported_edit(photo, settings):
    return SavedEdit(photo=photo, revision=1, saved_at=datetime.now(timezone.utc).isoformat(), settings=settings)
