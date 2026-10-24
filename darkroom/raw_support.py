"""Trusted, optional Adobe DNG Converter fallback for unsupported NEFs."""
import os
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory

import rawpy


class RawCompatibilityError(ValueError):
    pass


def converter_path():
    configured=os.environ.get("MYDARKROOM_DNG_CONVERTER")
    if configured:
        path=Path(configured)
        return path if path.is_file() else None
    if os.name!="nt":
        return None
    for key in ["ProgramFiles","ProgramW6432","ProgramFiles(x86)"]:
        root=os.environ.get(key)
        if root:
            for suffix in ["Adobe/Adobe DNG Converter/Adobe DNG Converter.exe","Adobe/Adobe DNG Converter.exe"]:
                path=Path(root)/suffix
                if path.is_file():return path
    return None


def camera_model(path):
    """Read only bounded TIFF model metadata; never parse/dump private EXIF."""
    try:
        with path.open("rb") as file:
            header=file.read(8)
            order="<" if header[:2]==b"II" else ">" if header[:2]==b"MM" else None
            if order is None or len(header)!=8 or struct.unpack(order+"H",header[2:4])[0]!=42:return ""
            offset=struct.unpack(order+"I",header[4:])[0]
            if offset>16*1024*1024:return ""
            file.seek(offset);count=struct.unpack(order+"H",file.read(2))[0]
            for _ in range(min(count,512)):
                entry=file.read(12)
                if len(entry)!=12:return ""
                tag,kind,length=struct.unpack(order+"HHI",entry[:8])
                if tag==272 and kind==2 and 0<length<=128:
                    if length<=4:data=entry[8:8+length]
                    else:
                        location=struct.unpack(order+"I",entry[8:])[0]
                        if location>16*1024*1024:return ""
                        file.seek(location);data=file.read(length)
                    return data.rstrip(b"\0").decode("ascii",errors="replace")
    except (OSError,struct.error):
        pass
    return ""


def compatible_develop(path, native, *, preview, metadata):
    converted=path.with_suffix(".dng")
    if converted.exists():
        return native(converted,preview=preview,metadata=metadata)
    try:
        return native(path,preview=preview,metadata=metadata)
    except (rawpy.LibRawFileUnsupportedError,rawpy.LibRawNotImplementedError) as exc:
        model=camera_model(path)
        converter=converter_path()
        if not converter:
            raise RawCompatibilityError(
                f"{model or '이 NEF'}는 현재 RAW 엔진에서 지원되지 않습니다. "
                "Nikon Z5 II 등 최신 기종 또는 HE/HE★ 압축일 수 있습니다. "
                "Windows용 최신 Adobe DNG Converter를 공식 사이트에서 설치한 뒤 앱을 다시 실행하세요. "
                "이미 설치했다면 MYDARKROOM_DNG_CONVERTER에 실행 파일 경로를 지정하세요. "
                f"LibRaw {'.'.join(map(str,rawpy.libraw_version))}.") from exc
        with TemporaryDirectory(prefix="dng-",dir=path.parent) as directory:
            try:
                result=subprocess.run([str(converter),"-c","-d",directory,str(path.resolve())],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180,
                    creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            except (OSError,subprocess.TimeoutExpired) as error:
                raise RawCompatibilityError("Adobe DNG 변환기를 실행하지 못했습니다. 설치 경로와 최신 버전을 확인하세요.") from error
            files=[p for p in Path(directory).iterdir() if p.suffix.lower()==".dng"]
            if result.returncode or len(files)!=1:
                raise RawCompatibilityError("Adobe DNG 변환에 실패했습니다. 최신 변환기가 해당 기종/압축을 지원하는지 확인하세요.")
            try:
                rgb=native(files[0],preview=preview,metadata=metadata)
            except rawpy.LibRawError as error:
                raise RawCompatibilityError("변환된 DNG를 현상하지 못했습니다. 원본 파일과 DNG Converter 버전을 확인하세요.") from error
            files[0].replace(converted)
            if metadata is not None:metadata["decoder"]="Adobe DNG → LibRaw"
            return rgb
