"""Mission Control HTML routes return 503 when Vite assets cannot be resolved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import api_service.ui_assets as ui_assets_module
from tests.unit.api.routers.test_task_dashboard import _client_with_mock_service


def test_tasks_list_returns_503_when_manifest_entry_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)

    with _client_with_mock_service() as (client, _mock):
        response = client.get("/tasks/list")

    assert response.status_code == 503
    assert "Mission Control UI unavailable" in response.text
    assert "shared Mission Control entrypoint" in response.text
    assert "mission-control" in response.text
    assert "tasks-list" in response.text


def test_tasks_list_uses_bundled_manifest_fallback_when_repo_dist_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dist_root = tmp_path / "bundled-dist"
    manifest_dir = dist_root / ".vite"
    assets_dir = dist_root / "assets"
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir()
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "entrypoints/mission-control.tsx": {
                    "file": "assets/mission-control.js",
                },
            }
        ),
        encoding="utf-8",
    )
    (assets_dir / "mission-control.js").write_text(
        "console.log('mission-control');", encoding="utf-8"
    )

    monkeypatch.delenv("VITE_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("MOONMIND_LENIENT_UI_ASSETS", raising=False)
    monkeypatch.setenv("MOONMIND_BUNDLED_UI_DIST_ROOT", str(dist_root))
    monkeypatch.setattr(
        ui_assets_module,
        "local_ui_dist_root",
        lambda: tmp_path / "missing-local-dist",
    )

    with _client_with_mock_service() as (client, _mock):
        response = client.get("/tasks/list")

    assert response.status_code == 200
    assert "/static/task_dashboard/dist/assets/mission-control.js" in response.text
