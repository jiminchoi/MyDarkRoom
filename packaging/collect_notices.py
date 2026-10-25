"""Keep redistributed package licenses alongside the binary distribution."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

target=Path(sys.argv[1]);licenses=target/"licenses";licenses.mkdir(exist_ok=True)
lines=["MyDarkRoom bundled third-party components", "", "Python: "+sys.version, ""]
for dist in sorted(distributions(),key=lambda d:d.metadata.get("Name","").lower()):
    name=dist.metadata.get("Name","unknown")
    if name.lower() in {"pip","setuptools","pytest","pygments","iniconfig","pluggy","pyinstaller","pyinstaller-hooks-contrib","altgraph","pefile","pywin32-ctypes"}:continue
    lines+=[f"{name} {dist.version}",dist.metadata.get("License-Expression") or dist.metadata.get("License", "See bundled license files"),*dist.metadata.get_all("Project-URL",[]),""]
    for file in dist.files or []:
        if any(word in str(file).lower() for word in ["license","copying","notice"]) and ".." not in file.parts:
            source=Path(dist.locate_file(file))
            if source.is_file():
                output=licenses/name/str(file);output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,output)
python_license=Path(sys.base_prefix)/"LICENSE.txt"
if python_license.is_file():shutil.copy2(python_license,licenses/"Python-LICENSE.txt")
lines += ["LibRaw source and license: https://www.libraw.org/download", "https://github.com/LibRaw/LibRaw", "Adobe DNG Converter is separately installed and is not redistributed."]
(target/"THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines),encoding="utf-8")
