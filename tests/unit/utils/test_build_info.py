from __future__ import annotations

from pathlib import Path

from moonmind.utils.build_info import resolve_moonmind_build_id

def test_resolve_moonmind_build_id_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_ID", "20260408.1703")
    monkeypatch.setenv("MOONMIND_BUILD_ID_PATH", "/does/not/matter")

    assert resolve_moonmind_build_id() == "20260408.1703"

def test_resolve_moonmind_build_id_reads_baked_file(monkeypatch, tmp_path: Path) -> None:
    build_id_path = tmp_path / ".moonmind-build-id"
    build_id_path.write_text("20260408.1703\n", encoding="utf-8")

    monkeypatch.delenv("MOONMIND_BUILD_ID", raising=False)
    monkeypatch.setenv("MOONMIND_BUILD_ID_PATH", str(build_id_path))

    assert resolve_moonmind_build_id() == "20260408.1703"

def test_resolve_moonmind_build_id_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_BUILD_ID", raising=False)
    monkeypatch.setenv("MOONMIND_BUILD_ID_PATH", "/path/that/does/not/exist")

    assert resolve_moonmind_build_id() is None
