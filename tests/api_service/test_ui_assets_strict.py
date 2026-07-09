"""Strict vs lenient behavior for dashboard Vite asset resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import api_service.ui_assets as ui_assets_module
from api_service.dashboard_static import DashboardStaticFiles
from api_service.ui_assets import (
    AssetFileMissingError,
    EntrypointMissingError,
    ManifestNotFoundError,
    resolve_dashboard_dist_root,
    ui_assets,
)

def test_ui_assets_strict_raises_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(ManifestNotFoundError):
        ui_assets("dashboard")

def test_ui_assets_lenient_returns_comment_when_manifest_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-manifest.json"
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(missing))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("dashboard")
    assert "Vite manifest not found" in html

def test_ui_assets_strict_raises_when_entrypoint_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    with pytest.raises(EntrypointMissingError):
        ui_assets("dashboard")

def test_ui_assets_lenient_returns_comment_when_entrypoint_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.setenv("MOONMIND_LENIENT_UI_ASSETS", "1")
    html = ui_assets("dashboard")
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
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
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

    (assets_dir / "dashboard.js").write_text(
        "console.log('entry');", encoding="utf-8"
    )
    (assets_dir / "mountPage.js").write_text("console.log('shared');", encoding="utf-8")
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    html = ui_assets("dashboard")

    assert 'src="/static/workflow_console/dist/assets/dashboard.js"' in html
    assert 'href="/static/workflow_console/dist/assets/mountPage.css"' in html

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
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "dashboard.js").write_text(
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

    html = ui_assets("dashboard")

    assert 'src="/static/workflow_console/dist/assets/dashboard.js"' in html
    assert resolve_dashboard_dist_root() == dist_root

def test_ui_assets_falls_back_to_bundled_dist_when_local_manifest_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_dist_root = tmp_path / "local-dist"
    local_manifest_dir = local_dist_root / ".vite"
    local_assets_dir = local_dist_root / "assets"
    local_manifest_dir.mkdir(parents=True)
    local_assets_dir.mkdir()
    (local_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/workflow-list.tsx": {
                    "file": "assets/workflow-list.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (local_assets_dir / "workflow-list.js").write_text(
        "console.log('stale local dist');", encoding="utf-8"
    )

    bundled_dist_root = tmp_path / "bundled-dist"
    bundled_manifest_dir = bundled_dist_root / ".vite"
    bundled_assets_dir = bundled_dist_root / "assets"
    bundled_manifest_dir.mkdir(parents=True)
    bundled_assets_dir.mkdir()
    (bundled_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (bundled_assets_dir / "dashboard.js").write_text(
        "console.log('bundled dist');", encoding="utf-8"
    )

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(bundled_dist_root))
    monkeypatch.setattr(ui_assets_module, "local_ui_dist_root", lambda: local_dist_root)

    html = ui_assets("dashboard")

    assert 'src="/static/workflow_console/dist/assets/dashboard.js"' in html
    assert resolve_dashboard_dist_root() == bundled_dist_root

def test_ui_assets_prefers_newer_usable_bundled_dist_over_older_local_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_dist_root = tmp_path / "local-dist"
    local_manifest_dir = local_dist_root / ".vite"
    local_assets_dir = local_dist_root / "assets"
    local_manifest_dir.mkdir(parents=True)
    local_assets_dir.mkdir()
    (local_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                    "css": ["assets/dashboard.css"],
                }
            }
        ),
        encoding="utf-8",
    )
    (local_assets_dir / "dashboard.js").write_text(
        "console.log('older local dist');", encoding="utf-8"
    )
    (local_assets_dir / "dashboard.css").write_text(
        "body { color: red; }", encoding="utf-8"
    )

    bundled_dist_root = tmp_path / "bundled-dist"
    bundled_manifest_dir = bundled_dist_root / ".vite"
    bundled_assets_dir = bundled_dist_root / "assets"
    bundled_manifest_dir.mkdir(parents=True)
    bundled_assets_dir.mkdir()
    (bundled_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                    "css": ["assets/dashboard.css"],
                }
            }
        ),
        encoding="utf-8",
    )
    (bundled_assets_dir / "dashboard.js").write_text(
        "console.log('newer bundled dist');", encoding="utf-8"
    )
    (bundled_assets_dir / "dashboard.css").write_text(
        "body { color: blue; }", encoding="utf-8"
    )

    older = 1_700_000_000
    newer = older + 300
    local_manifest = local_manifest_dir / "manifest.json"
    bundled_manifest = bundled_manifest_dir / "manifest.json"
    os.utime(local_manifest, (older, older))
    os.utime(bundled_manifest, (newer, newer))

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(bundled_dist_root))
    monkeypatch.setattr(ui_assets_module, "local_ui_dist_root", lambda: local_dist_root)

    html = ui_assets("dashboard")

    assert 'src="/static/workflow_console/dist/assets/dashboard.js"' in html
    assert 'href="/static/workflow_console/dist/assets/dashboard.css"' in html
    assert resolve_dashboard_dist_root() == bundled_dist_root

def test_ui_assets_prefers_bundled_dist_over_newer_local_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_dist_root = tmp_path / "local-dist"
    local_manifest_dir = local_dist_root / ".vite"
    local_assets_dir = local_dist_root / "assets"
    local_manifest_dir.mkdir(parents=True)
    local_assets_dir.mkdir()
    (local_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/local-dashboard.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (local_assets_dir / "local-dashboard.js").write_text(
        "console.log('newer local dist');", encoding="utf-8"
    )

    bundled_dist_root = tmp_path / "bundled-dist"
    bundled_manifest_dir = bundled_dist_root / ".vite"
    bundled_assets_dir = bundled_dist_root / "assets"
    bundled_manifest_dir.mkdir(parents=True)
    bundled_assets_dir.mkdir()
    (bundled_manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/bundled-dashboard.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (bundled_assets_dir / "bundled-dashboard.js").write_text(
        "console.log('bundled dist');", encoding="utf-8"
    )

    older = 1_700_000_000
    newer = older + 300
    os.utime(bundled_manifest_dir / "manifest.json", (older, older))
    os.utime(local_manifest_dir / "manifest.json", (newer, newer))

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(bundled_dist_root))
    monkeypatch.setattr(ui_assets_module, "local_ui_dist_root", lambda: local_dist_root)

    html = ui_assets("dashboard")

    assert 'src="/static/workflow_console/dist/assets/bundled-dashboard.js"' in html
    assert "local-dashboard.js" not in html
    assert resolve_dashboard_dist_root() == bundled_dist_root

def test_resolve_dashboard_dist_root_returns_after_bundled_usable_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_dist_root = tmp_path / "local-dist"
    bundled_dist_root = tmp_path / "bundled-dist"
    local_dist_root.mkdir()
    bundled_dist_root.mkdir()

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(ui_assets_module, "local_ui_dist_root", lambda: local_dist_root)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(bundled_dist_root))

    mtimes = {
        local_dist_root: 1,
        bundled_dist_root: 2,
    }
    monkeypatch.setattr(
        ui_assets_module,
        "_dist_root_manifest_mtime_ns",
        lambda dist_root: mtimes[dist_root],
    )

    checked_candidates: list[Path] = []

    def fake_manifest_tree_is_usable(dist_root: Path, entrypoint: str | None = None) -> bool:
        checked_candidates.append(dist_root)
        return dist_root == bundled_dist_root

    monkeypatch.setattr(
        ui_assets_module,
        "_manifest_tree_is_usable",
        fake_manifest_tree_is_usable,
    )

    assert resolve_dashboard_dist_root() == bundled_dist_root
    assert checked_candidates == [bundled_dist_root]

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
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "dashboard.js").write_text(
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
        "/static/workflow_console/dist",
        DashboardStaticFiles(directory=str(resolve_dashboard_dist_root())),
        name="workflow-console-dist",
    )
    app.mount("/static", StaticFiles(directory=str(empty_static)), name="static")

    with TestClient(app) as client:
        response = client.get("/static/workflow_console/dist/assets/dashboard.js")

    assert response.status_code == 200
    assert "bundled" in response.text

def test_dashboard_static_files_cache_hashed_assets_immutably(tmp_path: Path) -> None:
    dist_root = tmp_path / "dist"
    assets_dir = dist_root / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "dashboard-abc123.js").write_text(
        "console.log('hashed');", encoding="utf-8"
    )

    app = FastAPI()
    app.mount(
        "/static/workflow_console/dist",
        DashboardStaticFiles(directory=str(dist_root)),
        name="workflow-console-dist",
    )

    with TestClient(app) as client:
        response = client.get(
            "/static/workflow_console/dist/assets/dashboard-abc123.js"
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"

def test_dashboard_static_files_revalidate_manifest_and_non_asset_dist_files(
    tmp_path: Path,
) -> None:
    dist_root = tmp_path / "dist"
    manifest_dir = dist_root / ".vite"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("{}", encoding="utf-8")
    (dist_root / "favicon.ico").write_text("icon", encoding="utf-8")

    app = FastAPI()
    app.mount(
        "/static/workflow_console/dist",
        DashboardStaticFiles(directory=str(dist_root)),
        name="workflow-console-dist",
    )

    with TestClient(app) as client:
        manifest_response = client.get(
            "/static/workflow_console/dist/.vite/manifest.json"
        )
        favicon_response = client.get("/static/workflow_console/dist/favicon.ico")

    assert manifest_response.status_code == 200
    assert favicon_response.status_code == 200
    assert manifest_response.headers["Cache-Control"] == "no-cache, must-revalidate"
    assert favicon_response.headers["Cache-Control"] == "no-cache, must-revalidate"

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
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
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

    (assets_dir / "dashboard.js").write_text(
        "console.log('entry');", encoding="utf-8"
    )
    (assets_dir / "mountPage.css").write_text("body { color: red; }", encoding="utf-8")

    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    with pytest.raises(AssetFileMissingError):
        ui_assets("dashboard")
