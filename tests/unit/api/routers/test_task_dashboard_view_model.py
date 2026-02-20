"""Unit tests for task dashboard view-model helpers."""

from __future__ import annotations

from api_service.api.routers.task_dashboard_view_model import (
    build_runtime_config,
    normalize_status,
    status_maps,
)
from moonmind.config.settings import settings


def test_normalize_status_maps_queue_dead_letter_to_failed() -> None:
    assert normalize_status("queue", "dead_letter") == "failed"


def test_normalize_status_maps_removed_speckit_to_fallback_queued() -> None:
    assert normalize_status("speckit", "retrying") == "queued"


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
    assert config["sources"]["queue"]["cancel"] == "/api/queue/jobs/{id}/cancel"
    assert (
        config["sources"]["queue"]["eventsStream"]
        == "/api/queue/jobs/{id}/events/stream"
    )
    assert (
        config["sources"]["queue"]["migrationTelemetry"]
        == "/api/queue/telemetry/migration"
    )
    assert config["sources"]["queue"]["skills"] == "/api/tasks/skills"
    assert (
        config["sources"]["queue"]["liveSession"] == "/api/queue/jobs/{id}/live-session"
    )
    assert (
        config["sources"]["queue"]["liveSessionGrantWrite"]
        == "/api/queue/jobs/{id}/live-session/grant-write"
    )
    assert (
        config["sources"]["queue"]["liveSessionRevoke"]
        == "/api/queue/jobs/{id}/live-session/revoke"
    )
    assert config["sources"]["queue"]["taskControl"] == "/api/queue/jobs/{id}/control"
    assert (
        config["sources"]["queue"]["operatorMessages"]
        == "/api/queue/jobs/{id}/operator-messages"
    )
    assert config["sources"]["queue"]["taskStepTemplates"] == "/api/task-step-templates"
    assert (
        config["sources"]["queue"]["taskStepTemplateDetail"]
        == "/api/task-step-templates/{slug}"
    )
    assert (
        config["sources"]["queue"]["taskStepTemplateExpand"]
        == "/api/task-step-templates/{slug}:expand"
    )
    assert (
        config["sources"]["queue"]["taskStepTemplateSave"]
        == "/api/task-step-templates/save-from-task"
    )
    assert (
        config["sources"]["queue"]["taskStepTemplateFavorite"]
        == "/api/task-step-templates/{slug}:favorite"
    )
    assert config["sources"]["manifests"]["list"].startswith(
        "/api/queue/jobs?type=manifest"
    )
    assert config["sources"]["manifests"]["create"] == "/api/queue/jobs"
    assert config["sources"]["manifests"]["registry"] == "/api/manifests"
    assert config["sources"]["manifests"]["registryRun"] == "/api/manifests/{name}/runs"
    assert "speckit" not in config["sources"]
    assert config["sources"]["orchestrator"]["detail"] == "/orchestrator/runs/{id}"
    assert config["system"]["defaultQueue"]
    assert "defaultRepository" in config["system"]
    assert config["system"]["defaultTaskRuntime"] in ("codex", "gemini", "claude")
    assert config["system"]["defaultTaskModel"]
    assert config["system"]["defaultTaskEffort"]
    assert config["system"]["defaultTaskModelByRuntime"]["codex"]
    assert config["system"]["defaultTaskEffortByRuntime"]["codex"]
    assert config["system"]["queueEnv"] == "MOONMIND_QUEUE"
    assert config["system"]["workerRuntimeEnv"] == "MOONMIND_WORKER_RUNTIME"
    assert config["system"]["supportedTaskRuntimes"] == ["codex", "gemini", "claude"]
    assert "claude" in config["system"]["supportedWorkerRuntimes"]
    assert "taskTemplateCatalog" in config["system"]
    assert "enabled" in config["system"]["taskTemplateCatalog"]
    assert "templateSaveEnabled" in config["system"]["taskTemplateCatalog"]
    worker_pause = config["system"]["workerPause"]
    assert worker_pause["get"] == "/api/system/worker-pause"
    assert worker_pause["post"] == "/api/system/worker-pause"
    assert worker_pause["pollIntervalMs"] == 5000


def test_build_runtime_config_uses_runtime_env_for_task_default(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_WORKER_RUNTIME", "gemini")
    config = build_runtime_config("/tasks")
    assert config["system"]["defaultTaskRuntime"] == "gemini"
    assert config["system"]["defaultTaskModel"] == ""
    assert config["system"]["defaultTaskEffort"] == ""


def test_build_runtime_config_uses_settings_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings.spec_workflow, "github_repository", "Octo/Repo")
    monkeypatch.setattr(settings.spec_workflow, "codex_model", "gpt-test-codex")
    monkeypatch.setattr(settings.spec_workflow, "codex_effort", "medium")

    config = build_runtime_config("/tasks")

    assert config["system"]["defaultRepository"] == "Octo/Repo"
    assert config["system"]["defaultTaskModel"] == "gpt-test-codex"
    assert config["system"]["defaultTaskEffort"] == "medium"
    assert config["system"]["defaultTaskModelByRuntime"]["codex"] == "gpt-test-codex"
    assert config["system"]["defaultTaskEffortByRuntime"]["codex"] == "medium"
