# Build with CPython 3.12 x64 on Windows. One directory: no runtime unpacking/UPX.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all,collect_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo,FixedFileInfo,StringFileInfo,StringTable,StringStruct,VarFileInfo,VarStruct

root=Path(SPECPATH).parent
version={}
exec((root/"darkroom/version.py").read_text(encoding="utf-8"),version)
number=tuple(map(int,version["VERSION"].split(".")))+(0,)
metadata=[]
for name in ["fastapi","starlette","uvicorn","rawpy","numpy","pillow","pydantic","python-multipart","opencv-python-headless"]:
    metadata+=collect_metadata(name)
raw_data,raw_bins,raw_imports=collect_all("rawpy")
info=VSVersionInfo(ffi=FixedFileInfo(filevers=number,prodvers=number,mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable("040904B0",[
    StringStruct("FileDescription","MyDarkRoom Photo Editor"),StringStruct("ProductName","MyDarkRoom"),StringStruct("FileVersion",version["VERSION"]),StringStruct("ProductVersion",version["VERSION"]),StringStruct("OriginalFilename","MyDarkRoom.exe")])]),VarFileInfo([VarStruct("Translation",[1033,1200])])])

analysis=Analysis([str(root/"desktop.py")],pathex=[str(root)],binaries=raw_bins,datas=[(str(root/"darkroom/static"),"darkroom/static")]+metadata+raw_data,
    hiddenimports=raw_imports+["uvicorn.logging","uvicorn.loops.asyncio","uvicorn.protocols.http.h11_impl","uvicorn.lifespan.on","darkroom.desktop_ui"],
    hookspath=[],hooksconfig={},runtime_hooks=[],excludes=["pytest","IPython","matplotlib","scipy","pandas","notebook","PyQt5","PyQt6","PySide6"],noarchive=False,optimize=1)
pyz=PYZ(analysis.pure)
exe=EXE(pyz,analysis.scripts,[],exclude_binaries=True,name="MyDarkRoom",debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False,version=info)
bundle=COLLECT(exe,analysis.binaries,analysis.datas,strip=False,upx=False,name="MyDarkRoom")
