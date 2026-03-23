"""Unit tests for task dashboard view-model helpers."""

from __future__ import annotations

from api_service.api.routers.task_dashboard_view_model import (
    build_runtime_config,
    normalize_status,
    status_maps,
)
from moonmind.config.settings import settings





def test_normalize_status_maps_temporal_awaiting_external_to_action() -> None:
    assert normalize_status("temporal", "awaiting_external") == "awaiting_action"


def test_normalize_status_maps_temporal_executing_to_running() -> None:
    assert normalize_status("temporal", "executing") == "running"


def test_normalize_status_maps_temporal_planning_to_running() -> None:
    assert normalize_status("temporal", "planning") == "running"


def test_normalize_status_maps_temporal_canceled_spellings_to_cancelled() -> None:
    assert normalize_status("temporal", "canceled") == "cancelled"
    assert normalize_status("temporal", "cancelled") == "cancelled"


def test_normalize_status_fallback_for_unknown_source() -> None:
    assert normalize_status("unknown-source", "anything") == "queued"


def test_status_maps_returns_copy() -> None:
    mapping = status_maps()
    mapping["temporal"]["queued"] = "changed"
    assert status_maps()["temporal"]["queued"] == "queued"


def test_build_runtime_config_contains_expected_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings.anthropic, "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)

    config = build_runtime_config("/tasks")
    assert config["initialPath"] == "/tasks"
    assert config["pollIntervalsMs"]["list"] > 0
    assert config["sources"]["temporal"]["list"] == "/api/executions"
    assert config["sources"]["temporal"]["create"] == "/api/executions"
    assert config["sources"]["temporal"]["detail"] == "/api/executions/{workflowId}"
    assert (
        config["sources"]["temporal"]["update"] == "/api/executions/{workflowId}/update"
    )
    assert (
        config["sources"]["temporal"]["manifestStatus"]
        == "/api/executions/{workflowId}/manifest-status"
    )
    assert (
        config["sources"]["temporal"]["manifestNodes"]
        == "/api/executions/{workflowId}/manifest-nodes"
    )
    assert (
        config["sources"]["temporal"]["signal"] == "/api/executions/{workflowId}/signal"
    )
    assert (
        config["sources"]["temporal"]["cancel"] == "/api/executions/{workflowId}/cancel"
    )
    assert (
        config["sources"]["temporal"]["artifacts"]
        == "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts"
    )
    assert config["sources"]["temporal"]["artifactCreate"] == "/api/artifacts"
    assert (
        config["sources"]["temporal"]["artifactMetadata"]
        == "/api/artifacts/{artifactId}"
    )
    assert (
        config["sources"]["temporal"]["artifactPresignDownload"]
        == "/api/artifacts/{artifactId}/presign-download"
    )
    assert (
        config["sources"]["temporal"]["artifactDownload"]
        == "/api/artifacts/{artifactId}/download"
    )
    assert "speckit" not in config["sources"]
    assert "orchestrator" not in config["sources"]
    assert "externalRuns" not in config["sources"]
    assert "external" not in config["statusMaps"]
    temporal_dashboard = config["features"]["temporalDashboard"]
    assert temporal_dashboard["enabled"] is True
    assert temporal_dashboard["listEnabled"] is True
    assert temporal_dashboard["detailEnabled"] is True
    assert temporal_dashboard["actionsEnabled"] is True
    assert temporal_dashboard["submitEnabled"] is True
    assert temporal_dashboard["debugFieldsEnabled"] is False
    assert config["statusMaps"]["temporal"]["executing"] == "running"
    assert config["system"]["defaultQueue"]
    assert "defaultRepository" in config["system"]
    assert config["system"]["defaultTaskRuntime"] in ("codex", "gemini_cli", "claude")
    assert "defaultTaskModel" in config["system"]
    assert "defaultTaskEffort" in config["system"]
    assert "defaultTaskModelByRuntime" in config["system"]
    assert "defaultTaskEffortByRuntime" in config["system"]
    assert config["system"]["queueEnv"] == "MOONMIND_QUEUE"
    assert config["system"]["taskSourceResolver"] == "/api/tasks/{taskId}/source"
    assert config["system"]["workerRuntimeEnv"] == "MOONMIND_WORKER_RUNTIME"
    assert config["system"]["supportedTaskRuntimes"] == ["codex", "gemini_cli", "claude"]
    assert "claude" in config["system"]["supportedWorkerRuntimes"]
    assert "taskTemplateCatalog" in config["system"]
    assert "enabled" in config["system"]["taskTemplateCatalog"]
    assert "templateSaveEnabled" in config["system"]["taskTemplateCatalog"]
    temporal_compat = config["system"]["temporalCompatibility"]
    assert temporal_compat["enabled"] is True
    assert temporal_compat["uiQueryModel"] == "compatibility_adapter"
    assert temporal_compat["list"] == "/api/executions"
    assert temporal_compat["detail"] == "/api/executions/{workflowId}"
    assert temporal_compat["actionExecutionField"] == "execution"
    assert temporal_compat["actionRefreshField"] == "refresh"
    assert temporal_compat["staleStateField"] == "staleState"
    assert temporal_compat["refreshedAtField"] == "refreshedAt"
    assert temporal_compat["countModeField"] == "countMode"
    assert temporal_compat["degradedCountField"] == "degradedCount"
    assert temporal_compat["backgroundRefetchMs"] == config["pollIntervalsMs"]["list"]
    assert config["system"]["taskResolution"] == "/api/tasks/{taskId}/resolution"
    attachment_policy = config["system"]["attachmentPolicy"]
    assert attachment_policy["enabled"] is True
    assert attachment_policy["maxCount"] >= 1
    assert attachment_policy["maxBytes"] >= 1
    assert attachment_policy["totalBytes"] >= attachment_policy["maxBytes"]
    assert "image/png" in attachment_policy["allowedContentTypes"]


def test_build_runtime_config_normalizes_attachment_policy_settings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_max_count", 0)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_max_bytes", 0)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_total_bytes", 0)
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_allowed_content_types",
        (),
    )

    config = build_runtime_config("/tasks")
    attachment_policy = config["system"]["attachmentPolicy"]

    assert isinstance(attachment_policy["enabled"], bool)
    assert attachment_policy["maxCount"] == 1
    assert attachment_policy["maxBytes"] == 1
    assert attachment_policy["totalBytes"] == 1
    assert attachment_policy["allowedContentTypes"] == [
        "image/png",
        "image/jpeg",
        "image/webp",
    ]


def test_build_runtime_config_uses_runtime_env_for_task_default(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_WORKER_RUNTIME", "gemini_cli")
    monkeypatch.setenv("MOONMIND_GEMINI_MODEL", "gemini-2.5-flash")
    config = build_runtime_config("/tasks")
    assert config["system"]["defaultTaskRuntime"] == "gemini_cli"
    assert config["system"]["defaultTaskModel"] == "gemini-2.5-flash"
    assert config["system"]["defaultTaskEffort"] == ""
    assert config["system"]["defaultTaskModelByRuntime"]["gemini_cli"] == (
        "gemini-2.5-flash"
    )
    monkeypatch.delenv("MOONMIND_WORKER_RUNTIME", raising=False)
    monkeypatch.delenv("MOONMIND_GEMINI_MODEL", raising=False)


def test_build_runtime_config_uses_claude_from_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_WORKER_RUNTIME", "claude")
    monkeypatch.setenv("CLAUDE_API_KEY", "enabled")
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)

    config = build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == ["codex", "gemini_cli", "claude"]
    assert config["system"]["defaultTaskRuntime"] == "claude"


def test_build_runtime_config_uses_settings_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings.workflow, "github_repository", "Octo/Repo")
    monkeypatch.setattr(settings.workflow, "codex_model", "gpt-test-codex")
    monkeypatch.setattr(settings.workflow, "codex_effort", "medium")
    monkeypatch.setattr(settings.workflow, "default_task_runtime", "codex")
    monkeypatch.setenv("MOONMIND_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setattr(settings.workflow, "default_publish_mode", "branch")

    config = build_runtime_config("/tasks")

    assert config["system"]["defaultRepository"] == "Octo/Repo"
    assert config["system"]["defaultTaskModel"] == "gpt-test-codex"
    assert config["system"]["defaultTaskEffort"] == "medium"
    assert config["system"]["defaultTaskModelByRuntime"]["codex"] == "gpt-test-codex"
    assert config["system"]["defaultTaskModelByRuntime"]["gemini_cli"] == "gemini-2.5-pro"
    assert config["system"]["defaultTaskEffortByRuntime"]["codex"] == "medium"
    assert config["system"]["defaultPublishMode"] == "branch"
    assert config["system"]["defaultProposeTasks"] is False


def test_normalize_status_maps_temporal_waits_to_awaiting_action() -> None:
    assert normalize_status("temporal", "awaiting_external") == "awaiting_action"


def test_build_runtime_config_includes_claude_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)

    config = build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == ["codex", "gemini_cli", "claude"]


def test_build_runtime_config_uses_temporal_dashboard_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "list_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "detail_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "list_endpoint",
        "/api/temporal/executions",
    )
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "artifact_download_endpoint",
        "/api/temporal/artifacts/{artifactId}/download",
    )

    config = build_runtime_config("/tasks")

    assert config["features"]["temporalDashboard"] == {
        "enabled": False,
        "listEnabled": False,
        "detailEnabled": True,
        "actionsEnabled": True,
        "submitEnabled": True,
        "debugFieldsEnabled": True,
    }
    assert config["sources"]["temporal"]["list"] == "/api/temporal/executions"
    assert (
        config["sources"]["temporal"]["artifactDownload"]
        == "/api/temporal/artifacts/{artifactId}/download"
    )
    assert "temporal" not in config["system"]["supportedTaskRuntimes"]


def test_build_runtime_config_includes_jules_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings.anthropic, "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", True)
    monkeypatch.setattr(settings.jules, "jules_api_url", "https://jules.example.test")
    monkeypatch.setattr(settings.jules, "jules_api_key", "test-key")

    config = build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == [
        "codex",
        "gemini_cli",
        "claude",
        "jules",
    ]


def test_build_runtime_config_log_tailing_enabled_by_default() -> None:
    config = build_runtime_config("/tasks")
    assert config["features"]["logTailingEnabled"] is True


def test_build_runtime_config_log_tailing_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_LOG_TAILING_ENABLED", "false")
    config = build_runtime_config("/tasks")
    assert config["features"]["logTailingEnabled"] is False


def test_build_runtime_config_temporal_live_session_endpoint() -> None:
    config = build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["liveSession"]
        == "/api/task-runs/{id}/live-session"
    )


# ---------------------------------------------------------------------------
# T024: Run-index pagination and shared visibility totals
# ---------------------------------------------------------------------------


def test_runtime_config_exposes_manifest_status_endpoint() -> None:
    """Manifest-status endpoint must be present for run-index visibility."""
    config = build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["manifestStatus"]
        == "/api/executions/{workflowId}/manifest-status"
    )


def test_runtime_config_exposes_manifest_nodes_endpoint() -> None:
    """Manifest-nodes endpoint must be present for run-index pagination."""
    config = build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["manifestNodes"]
        == "/api/executions/{workflowId}/manifest-nodes"
    )





def test_normalize_status_maps_manifest_ingest_states() -> None:
    """Manifest-ingest-specific temporal states should map correctly."""
    assert normalize_status("temporal", "executing") == "running"
    assert normalize_status("temporal", "planning") == "running"
    assert normalize_status("temporal", "canceled") == "cancelled"
    assert normalize_status("temporal", "awaiting_external") == "awaiting_action"


def test_runtime_config_temporal_update_endpoint_for_manifest_updates() -> None:
    """The update endpoint used for manifest SetConcurrency/Pause/Resume must exist."""
    config = build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["update"]
        == "/api/executions/{workflowId}/update"
    )

