import pytest

from moonmind.schemas.saved_work_models import SAVED_WORK_FORMAT_SIZE_LIMITS
from moonmind.workflows.temporal.runtime.workspace_archive import (
    build_workspace_archive,
)


def test_archive_excludes_credentials_and_is_deterministic(tmp_path):
    (tmp_path / ".env").write_text("PASSWORD=do-not-export")
    (tmp_path / "source.py").write_text("print('hello')\n")
    first, entries = build_workspace_archive(tmp_path)
    (tmp_path / "source.py").touch()
    second, _ = build_workspace_archive(tmp_path)
    assert first == second
    assert [entry["path"] for entry in entries] == ["source.py"]


def test_archive_blocks_credential_bytes_before_returning_an_export(tmp_path):
    (tmp_path / "source.py").write_text("ghp_" + "a" * 35)
    with pytest.raises(ValueError, match="secret scanning"):
        build_workspace_archive(tmp_path)


def test_archive_enforces_limits_and_detects_mutation(tmp_path, monkeypatch):
    import tarfile

    source = tmp_path / "file.txt"
    source.write_text("original")
    monkeypatch.setitem(SAVED_WORK_FORMAT_SIZE_LIMITS, "max_file_bytes", 2)
    with pytest.raises(ValueError, match="limits"):
        build_workspace_archive(tmp_path)
    monkeypatch.setitem(SAVED_WORK_FORMAT_SIZE_LIMITS, "max_file_bytes", 100)
    real_add = tarfile.TarFile.addfile

    def mutate(archive, *args, **kwargs):
        real_add(archive, *args, **kwargs)
        source.write_text("changed while reading")

    monkeypatch.setattr(tarfile.TarFile, "addfile", mutate)
    with pytest.raises(ValueError, match="changed during"):
        build_workspace_archive(tmp_path)
