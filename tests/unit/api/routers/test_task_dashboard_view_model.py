"""Unit tests for task dashboard view-model helpers."""

from __future__ import annotations

from api_service.api.routers.task_dashboard_view_model import (
    build_runtime_config,
    normalize_status,
    status_maps,
)


def test_normalize_status_maps_queue_dead_letter_to_failed() -> None:
    assert normalize_status("queue", "dead_letter") == "failed"


def test_normalize_status_maps_speckit_retrying_to_queued() -> None:
    assert normalize_status("speckit", "retrying") == "queued"


def test_normalize_status_maps_speckit_in_progress_to_running() -> None:
    assert normalize_status("speckit", "in_progress") == "running"


def test_normalize_status_maps_orchestrator_awaiting_to_action() -> None:
    assert normalize_status("orchestrator", "awaiting_approval") == "awaiting_action"


def test_normalize_status_fallback_for_unknown_source() -> None:
    assert normalize_status("unknown-source", "anything") == "queued"


def test_status_maps_returns_copy() -> None:
    mapping = status_maps()
    mapping["queue"]["queued"] = "changed"
    assert status_maps()["queue"]["queued"] == "queued"


def test_build_runtime_config_contains_expected_keys() -> None:
    config = build_runtime_config("/tasks")
    assert config["initialPath"] == "/tasks"
    assert config["pollIntervalsMs"]["list"] > 0
    assert config["sources"]["queue"]["list"] == "/api/queue/jobs"
    assert config["sources"]["speckit"]["create"] == "/api/workflows/speckit/runs"
    assert config["sources"]["orchestrator"]["detail"] == "/orchestrator/runs/{id}"
    assert config["system"]["defaultQueue"]
    assert config["system"]["queueEnv"] == "MOONMIND_QUEUE"
    assert config["system"]["workerRuntimeEnv"] == "MOONMIND_WORKER_RUNTIME"
    assert "claude" in config["system"]["supportedWorkerRuntimes"]
