"""Strict vs lenient behavior for Mission Control Vite asset resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import api_service.ui_assets as ui_assets_module
from api_service.ui_assets import (
    AssetFileMissingError,
    EntrypointMissingError,
    ManifestNotFoundError,
    resolve_mission_control_dist_root,
    ui_assets,
)


def test_ui_assets_strict_raises_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(ManifestNotFoundError):
        ui_assets("mission-control")


def test_ui_assets_lenient_returns_comment_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("mission-control")
    assert "Vite manifest not found" in html


def test_ui_assets_strict_raises_when_entrypoint_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(EntrypointMissingError):
        ui_assets("mission-control")


def test_ui_assets_lenient_returns_comment_when_entrypoint_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("mission-control")
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
                "entrypoints/mission-control.tsx": {
                    "file": "assets/mission-control.js",
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

    (assets_dir / "mission-control.js").write_text(
        "console.log('entry');", encoding="utf-8"
    )
    (assets_dir / "mountPage.js").write_text("console.log('shared');", encoding="utf-8")
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    html = ui_assets("mission-control")

    assert 'src="/static/task_dashboard/dist/assets/mission-control.js"' in html
    assert 'href="/static/task_dashboard/dist/assets/mountPage.css"' in html


def test_ui_assets_falls_back_to_bundled_dist_when_local_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist_root = tmp_path / "bundled-dist"
    manifest_dir = dist_root / ".vite"
    assets_dir = dist_root / "assets"
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir()

    manifest = manifest_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entrypoints/mission-control.tsx": {
                    "file": "assets/mission-control.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "mission-control.js").write_text(
        "console.log('entry');", encoding="utf-8"
    )

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(dist_root))
    monkeypatch.setattr(
        ui_assets_module,
        "local_ui_dist_root",
        lambda: tmp_path / "missing-local-dist",
    )

    html = ui_assets("mission-control")

    assert 'src="/static/task_dashboard/dist/assets/mission-control.js"' in html
    assert resolve_mission_control_dist_root() == dist_root


def test_bundled_dist_root_can_serve_static_assets_when_repo_dist_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist_root = tmp_path / "bundled-dist"
    assets_dir = dist_root / "assets"
    manifest_dir = dist_root / ".vite"
    assets_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/mission-control.tsx": {
                    "file": "assets/mission-control.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "mission-control.js").write_text(
        "console.log('bundled');", encoding="utf-8"
    )

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(dist_root))
    monkeypatch.setattr(
        ui_assets_module,
        "local_ui_dist_root",
        lambda: tmp_path / "missing-local-dist",
    )

    app = FastAPI()
    empty_static = tmp_path / "empty-static"
    empty_static.mkdir()
    app.mount(
        "/static/task_dashboard/dist",
        StaticFiles(directory=str(resolve_mission_control_dist_root())),
        name="task-dashboard-dist",
    )
    app.mount("/static", StaticFiles(directory=str(empty_static)), name="static")

    with TestClient(app) as client:
        response = client.get("/static/task_dashboard/dist/assets/mission-control.js")

    assert response.status_code == 200
    assert "bundled" in response.text


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
                "entrypoints/mission-control.tsx": {
                    "file": "assets/mission-control.js",
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

    (assets_dir / "mission-control.js").write_text(
        "console.log('entry');", encoding="utf-8"
    )
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    with pytest.raises(AssetFileMissingError):
        ui_assets("mission-control")
