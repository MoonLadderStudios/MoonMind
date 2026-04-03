"""Strict vs lenient behavior for Mission Control Vite asset resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_service.ui_assets import (
    AssetFileMissingError,
    EntrypointMissingError,
    ManifestNotFoundError,
    ui_assets,
)


def test_ui_assets_strict_raises_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(ManifestNotFoundError):
        ui_assets("tasks-list")


def test_ui_assets_lenient_returns_comment_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("tasks-list")
    assert "Vite manifest not found" in html


def test_ui_assets_strict_raises_when_entrypoint_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(EntrypointMissingError):
        ui_assets("tasks-list")


def test_ui_assets_lenient_returns_comment_when_entrypoint_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("tasks-list")
    assert "manifest entry not found" in html


def test_ui_assets_includes_css_from_imported_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist_root = tmp_path / "dist"
    manifest_dir = dist_root / ".vite"
    assets_dir = dist_root / "assets"
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir()

    manifest = dist_root / ".vite" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entrypoints/tasks-list.tsx": {
                    "file": "assets/tasks-list.js",
                    "imports": ["_mountPage-shared.js"],
                },
                "_mountPage-shared.js": {
                    "file": "assets/mountPage.js",
                    "css": ["assets/mountPage.css"],
                },
            }
        ),
        encoding="utf-8",
    )

    (assets_dir / "tasks-list.js").write_text("console.log('entry');", encoding="utf-8")
    (assets_dir / "mountPage.js").write_text("console.log('shared');", encoding="utf-8")
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    html = ui_assets("tasks-list")

    assert 'src="/static/task_dashboard/dist/assets/tasks-list.js"' in html
    assert 'href="/static/task_dashboard/dist/assets/mountPage.css"' in html


def test_ui_assets_strict_raises_when_imported_chunk_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist_root = tmp_path / "dist"
    manifest_dir = dist_root / ".vite"
    assets_dir = dist_root / "assets"
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir()

    manifest = dist_root / ".vite" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entrypoints/tasks-list.tsx": {
                    "file": "assets/tasks-list.js",
                    "imports": ["_mountPage-shared.js"],
                },
                "_mountPage-shared.js": {
                    "file": "assets/mountPage.js",
                    "css": ["assets/mountPage.css"],
                },
            }
        ),
        encoding="utf-8",
    )

    (assets_dir / "tasks-list.js").write_text("console.log('entry');", encoding="utf-8")
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    with pytest.raises(AssetFileMissingError):
        ui_assets("tasks-list")
