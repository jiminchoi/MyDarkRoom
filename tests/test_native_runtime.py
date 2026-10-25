import sys
from types import SimpleNamespace
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from darkroom import native_runtime


def test_host_config_allows_downloaded_assemblies_only_for_this_app():
    root=ET.parse(Path(__file__).resolve().parents[1]/'packaging/MyDarkRoom.exe.config').getroot()
    assert root.tag=='configuration'
    assert root.find('./runtime/loadFromRemoteSources').get('enabled')=='true'
    assert root.find('./startup/supportedRuntime').get('version')=='v4.0'


def test_missing_host_config_and_old_framework_have_actionable_errors(monkeypatch):
    info=dict(framework_release=528040,host_config_present=False,assembly_present=True)
    monkeypatch.setattr(native_runtime,'diagnostics',lambda:info)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    with pytest.raises(RuntimeError,match='MyDarkRoom.exe.config'):native_runtime.initialize()
    info.update(framework_release=400000,host_config_present=True)
    with pytest.raises(RuntimeError,match='4.7.2'):native_runtime.initialize()


def test_bundled_host_explicitly_selects_netfx(monkeypatch):
    info=dict(framework_release=528040,host_config_present=True,assembly_present=True)
    monkeypatch.setattr(native_runtime,'diagnostics',lambda:info)
    calls=[];monkeypatch.setitem(sys.modules,'pythonnet',SimpleNamespace(load=lambda name:calls.append(name)))
    monkeypatch.setenv('PYTHONNET_RUNTIME','unrelated-runtime')
    assert native_runtime.initialize()==info and calls==['netfx']
