import pytest


@pytest.fixture(autouse=True)
def isolated_edit_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MYDARKROOM_DATA_DIR", str(tmp_path / "profile"))
