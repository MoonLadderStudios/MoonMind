"""Mission Control HTML routes return 503 when Vite assets cannot be resolved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    assert "tasks-list" in response.text
