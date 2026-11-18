"""Retired queue-era controls do not hide retained automation history."""

from api_service.main import app


def test_queue_controls_retired_but_history_routes_remain():
    paths = set(app.openapi()["paths"])
    assert not any(path.startswith(("/api/workflows/runs", "/api/workflows/codex/")) for path in paths)
    assert not any(path.startswith("/api/v1/operations/codex/") for path in paths)
    assert "/api/workflows/skills" in paths
    assert "/api/v1/operations/automation/runs/{run_id}" in paths
    assert "/api/v1/operations/automation/runs/{run_id}/artifacts/{artifact_id}" in paths
