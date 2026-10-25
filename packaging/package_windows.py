"""Zip every verified bundle file, then verify byte-for-byte contents and SHA256."""
import hashlib
from pathlib import Path
import sys
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from darkroom.version import VERSION


def digest(file):
    result=hashlib.sha256()
    while chunk:=file.read(1024*1024):result.update(chunk)
    return result.hexdigest()


def package(bundle,target):
    target.mkdir(exist_ok=True)
    output=target/f"MyDarkRoom-{VERSION}-Windows-x64.zip"
    files={str(p.relative_to(bundle.parent)).replace('\\','/'):p for p in sorted(bundle.rglob('*')) if p.is_file()}
    assert files and all(not p.is_symlink() for p in files.values())
    with ZipFile(output,'w',compression=ZIP_DEFLATED,compresslevel=6) as archive:
        for name,path in files.items():archive.write(path,name)
    with ZipFile(output) as archive:
        assert set(archive.namelist())==set(files),'ZIP omitted bundle files'
        for name,path in files.items():
            with archive.open(name) as archived,path.open('rb') as original:
                assert digest(archived)==digest(original),f'ZIP content mismatch: {name}'
    with output.open('rb') as file:checksum=digest(file)
    (target/'SHA256SUMS.txt').write_text(f'{checksum}  {output.name}\n',encoding='ascii')
    print(f'Verified {len(files)} files; {output.stat().st_size:,} bytes; SHA256 {checksum}',flush=True)


if __name__=='__main__':package(ROOT/'dist/MyDarkRoom',ROOT/'release')
