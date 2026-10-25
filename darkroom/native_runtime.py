"""Initialize the packaged .NET host explicitly and record actionable diagnostics."""
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys
import traceback

LOG=logging.getLogger('mydarkroom')
NET472_RELEASE=461808


def framework_release():
    if sys.platform!='win32':return None
    import winreg
    releases=[]
    for view in [winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY]:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full',0,winreg.KEY_READ|view) as key:
                releases.append(int(winreg.QueryValueEx(key,'Release')[0]))
        except OSError:pass
    return max(releases,default=0)


def configuration_path():return Path(str(sys.executable)+'.config')


def assembly_path():
    if getattr(sys,'frozen',False):return Path(sys._MEIPASS)/'pythonnet/runtime/Python.Runtime.dll'
    spec=importlib.util.find_spec('pythonnet')
    return Path(spec.origin).parent/'runtime/Python.Runtime.dll' if spec and spec.origin else None


def diagnostics():
    assembly=assembly_path();zone=None;digest=None
    if assembly and assembly.is_file():
        digest=hashlib.sha256(assembly.read_bytes()).hexdigest()
        if sys.platform=='win32':
            try:
                # Record the zone only; download URLs in ADS may contain private data.
                data=Path(str(assembly)+':Zone.Identifier').read_text(encoding='utf-8',errors='replace')[:4096]
                match=re.search(r'^ZoneId=(\d+)\s*$',data,re.M);zone=int(match[1]) if match else None
            except OSError:pass
    return dict(framework_release=framework_release(),host_config_present=configuration_path().is_file(),assembly_present=bool(assembly and assembly.is_file()),assembly_sha256=digest,assembly_zone=zone)


def initialize(require_config=True):
    info=diagnostics();LOG.info('Native runtime diagnostics: %s',json.dumps(info))
    if info['framework_release'] is not None and info['framework_release']<NET472_RELEASE:
        raise RuntimeError('자체 편집 창에는 Microsoft .NET Framework 4.7.2 이상이 필요합니다. .NET Framework 4.8 설치 안내: https://dotnet.microsoft.com/download/dotnet-framework/net48')
    if require_config and getattr(sys,'frozen',False) and not info['host_config_present']:
        raise RuntimeError('MyDarkRoom.exe.config 파일이 없습니다. 새 ZIP 전체를 새 폴더에 압축 해제하고 EXE 옆 설정 파일도 함께 보관해 주세요.')
    if not info['assembly_present']:raise RuntimeError('Python.Runtime.dll이 없습니다. ZIP 전체를 다시 압축 해제해 주세요.')
    # The app uses Windows .NET Framework; unrelated Python installations/env vars
    # must not choose a different CLR for this frozen process.
    import pythonnet
    pythonnet.load('netfx')
    return info


def probe(report):
    result=diagnostics()
    try:
        initialize(require_config=False)
        from webview.platforms.winforms import is_chromium
        if not is_chromium:raise RuntimeError('Microsoft WebView2 Runtime is unavailable')
        result['passed']=True
    except Exception:
        result.update(passed=False,error=traceback.format_exc())
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return 0 if result['passed'] else 1
