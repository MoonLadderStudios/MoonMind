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


def test_large_repository_snapshot_round_trips_with_default_budget(
    tmp_path, monkeypatch
):
    from moonmind.publish.saved_work_source import _extract_snapshot

    # Exercise the measured 1.2 GB repository shape at 1/1024 scale, keeping
    # ordinary CI cheap while deriving both bounds from the deployed defaults.
    for key in ("max_file_bytes", "max_total_bytes"):
        monkeypatch.setitem(
            SAVED_WORK_FORMAT_SIZE_LIMITS,
            key,
            SAVED_WORK_FORMAT_SIZE_LIMITS[key] // 1024,
        )
    source = tmp_path / "source"
    source.mkdir()
    payload = b"repository content\n" * (96 * 1024 // 19)
    for index in range(12):
        (source / f"asset-{index}.txt").write_bytes(payload)

    archive, entries = build_workspace_archive(source, high_security_mode=False)
    restored = tmp_path / "restored"
    restored.mkdir()
    restored_entries = _extract_snapshot(archive, restored)

    assert len(entries) == len(restored_entries) == 12
    assert all((restored / entry["path"]).read_bytes() == payload for entry in entries)


def test_archive_reports_total_byte_limit_and_keeps_source(tmp_path, monkeypatch):
    monkeypatch.setitem(SAVED_WORK_FORMAT_SIZE_LIMITS, "max_total_bytes", 10)
    (tmp_path / "one").write_bytes(b"a" * 6)
    (tmp_path / "two").write_bytes(b"b" * 6)

    with pytest.raises(ValueError, match=r"total bytes 12 > 10"):
        build_workspace_archive(tmp_path, high_security_mode=False)

    assert (tmp_path / "one").read_bytes() == b"a" * 6
    assert (tmp_path / "two").read_bytes() == b"b" * 6
