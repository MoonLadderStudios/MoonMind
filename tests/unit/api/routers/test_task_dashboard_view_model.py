"""Unit tests for task dashboard view-model helpers."""

from __future__ import annotations

import pytest

import api_service.api.routers.task_dashboard_view_model as dashboard_view_model
from moonmind.config.settings import settings





def test_normalize_status_maps_temporal_awaiting_external_to_action() -> None:
    assert (
        dashboard_view_model.normalize_status("temporal", "awaiting_external")
        == "awaiting_action"
    )


def test_normalize_status_maps_temporal_executing_to_running() -> None:
    assert dashboard_view_model.normalize_status("temporal", "executing") == "running"


def test_normalize_status_maps_temporal_planning_to_running() -> None:
    assert dashboard_view_model.normalize_status("temporal", "planning") == "running"


def test_normalize_status_maps_temporal_canceled_spellings_to_canceled() -> None:
    assert dashboard_view_model.normalize_status("temporal", "canceled") == "canceled"
    assert dashboard_view_model.normalize_status("temporal", "cancelled") == "canceled"


def test_normalize_status_fallback_for_unknown_source() -> None:
    assert dashboard_view_model.normalize_status("unknown-source", "anything") == "queued"


def test_status_maps_returns_copy() -> None:
    mapping = dashboard_view_model.status_maps()
    mapping["temporal"]["queued"] = "changed"
    assert dashboard_view_model.status_maps()["temporal"]["queued"] == "queued"


def test_build_runtime_config_contains_expected_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings.anthropic, "anthropic_api_key", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", False)
    monkeypatch.setattr(
        settings.temporal_dashboard, "temporal_task_editing_enabled", False
    )

    config = dashboard_view_model.build_runtime_config("/tasks")
    assert config["initialPath"] == "/tasks"
    assert config["pollIntervalsMs"]["list"] > 0
    assert config["sources"]["temporal"]["list"] == "/api/executions"
    assert config["sources"]["temporal"]["create"] == "/api/executions"
    assert config["sources"]["temporal"]["detail"] == "/api/executions/{workflowId}"
    assert config["sources"]["temporal"]["steps"] == "/api/executions/{workflowId}/steps"
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
    assert config["sources"]["github"]["branches"] == (
        "/api/github/branches?repository={repository}"
    )
    assert config["sources"]["taskRuns"]["observabilitySummary"] == "/api/task-runs/{taskRunId}/observability-summary"
    assert config["sources"]["taskRuns"]["observabilityEvents"] == "/api/task-runs/{taskRunId}/observability/events"
    assert config["sources"]["taskRuns"]["logsStream"] == "/api/task-runs/{taskRunId}/logs/stream"
    assert config["sources"]["taskRuns"]["logsStdout"] == "/api/task-runs/{taskRunId}/logs/stdout"
    assert config["sources"]["taskRuns"]["logsStderr"] == "/api/task-runs/{taskRunId}/logs/stderr"
    assert config["sources"]["taskRuns"]["logsMerged"] == "/api/task-runs/{taskRunId}/logs/merged"
    assert config["sources"]["taskRuns"]["diagnostics"] == "/api/task-runs/{taskRunId}/diagnostics"
    assert config["sources"]["taskRuns"]["artifactSession"] == "/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}"
    assert config["sources"]["taskRuns"]["artifactSessionControl"] == "/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}/control"
    assert "speckit" not in config["sources"]
    assert "orchestrator" not in config["sources"]
    assert "externalRuns" not in config["sources"]
    assert "jira" not in config["sources"]
    assert "jiraIntegration" not in config["system"]
    assert "external" not in config["statusMaps"]
    temporal_dashboard = config["features"]["temporalDashboard"]
    assert temporal_dashboard["enabled"] is True
    assert temporal_dashboard["listEnabled"] is True
    assert temporal_dashboard["detailEnabled"] is True
    assert temporal_dashboard["actionsEnabled"] is True
    assert temporal_dashboard["submitEnabled"] is True
    assert temporal_dashboard["temporalTaskEditing"] is False
    assert temporal_dashboard["debugFieldsEnabled"] is False
    assert config["statusMaps"]["temporal"]["executing"] == "running"
    assert "defaultRepository" in config["system"]
    assert "buildId" in config["system"]
    assert config["system"]["defaultTaskRuntime"] in ("codex_cli", "gemini_cli", "claude_code")
    assert "defaultTaskModel" in config["system"]
    assert "defaultTaskEffort" in config["system"]
    assert "defaultTaskModelByRuntime" in config["system"]
    assert "defaultTaskEffortByRuntime" in config["system"]
    assert config["system"]["workerRuntimeEnv"] == "MOONMIND_WORKER_RUNTIME"
    assert config["system"]["supportedTaskRuntimes"] == ["codex_cli", "gemini_cli", "claude_code", "codex_cloud"]
    assert "claude_code" in config["system"]["supportedWorkerRuntimes"]
    assert "taskTemplateCatalog" in config["system"]
    assert "enabled" in config["system"]["taskTemplateCatalog"]
    assert "templateSaveEnabled" in config["system"]["taskTemplateCatalog"]
    assert config["system"]["taskTemplateCatalog"]["list"] == "/api/task-step-templates"
    assert config["system"]["taskTemplateCatalog"]["detail"] == "/api/task-step-templates/{slug}"
    assert config["system"]["taskTemplateCatalog"]["expand"] == "/api/task-step-templates/{slug}:expand"
    attachment_policy = config["system"]["attachmentPolicy"]
    assert attachment_policy["enabled"] is True
    assert attachment_policy["maxCount"] >= 1
    assert attachment_policy["maxBytes"] >= 1
    assert attachment_policy["totalBytes"] >= attachment_policy["maxBytes"]
    assert "image/png" in attachment_policy["allowedContentTypes"]


def test_build_runtime_config_omits_jira_ui_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", False)

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert "jira" not in config["sources"]
    assert "jiraIntegration" not in config["system"]


def test_jira_create_page_enabled_reads_feature_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", False)
    assert dashboard_view_model._jira_create_page_enabled() is False

    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", True)
    assert dashboard_view_model._jira_create_page_enabled() is True


def test_build_runtime_config_keeps_jira_ui_separate_from_trusted_tooling(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", False)
    monkeypatch.setattr(settings.atlassian.jira, "jira_enabled", True)
    monkeypatch.setattr(settings.atlassian.jira, "jira_tool_enabled", True)

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert "jira" not in config["sources"]
    assert "jiraIntegration" not in config["system"]


def test_build_runtime_config_exposes_jira_ui_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", True)
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_default_project_key",
        "",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_default_board_id",
        "",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_remember_last_board_in_session",
        True,
    )

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert config["sources"]["jira"] == {
        "connections": "/api/jira/connections/verify",
        "projects": "/api/jira/projects",
        "boards": "/api/jira/projects/{projectKey}/boards",
        "columns": "/api/jira/boards/{boardId}/columns",
        "issues": "/api/jira/boards/{boardId}/issues",
        "issue": "/api/jira/issues/{issueKey}",
    }
    assert config["system"]["jiraIntegration"] == {
        "enabled": True,
        "defaultProjectKey": "",
        "defaultBoardId": "",
        "rememberLastBoardInSession": True,
    }
    assert all(
        value.startswith("/api/") and "://" not in value
        for value in config["sources"]["jira"].values()
    )


def test_build_jira_sources_returns_independent_moonmind_endpoint_templates() -> None:
    first = dashboard_view_model._build_jira_sources()
    first["projects"] = "https://jira.example.test/rest/api/3/project"

    second = dashboard_view_model._build_jira_sources()

    assert second["projects"] == "/api/jira/projects"
    assert all(
        value.startswith("/api/") and "://" not in value
        for value in second.values()
    )


def test_validate_jira_source_templates_rejects_non_moonmind_paths() -> None:
    sources = {
        **dashboard_view_model._JIRA_CREATE_PAGE_SOURCES,
        "projects": "https://jira.example.test/rest/api/3/project",
    }

    with pytest.raises(ValueError, match="MoonMind API path"):
        dashboard_view_model._validate_jira_source_templates(sources)


def test_validate_jira_source_templates_rejects_blank_paths() -> None:
    sources = {
        **dashboard_view_model._JIRA_CREATE_PAGE_SOURCES,
        "issues": " ",
    }

    with pytest.raises(ValueError, match="MoonMind API path"):
        dashboard_view_model._validate_jira_source_templates(sources)


def test_validate_jira_source_templates_rejects_trailing_whitespace() -> None:
    sources = {
        **dashboard_view_model._JIRA_CREATE_PAGE_SOURCES,
        "projects": "/api/jira/projects ",
    }

    with pytest.raises(ValueError, match="MoonMind API path"):
        dashboard_view_model._validate_jira_source_templates(sources)


def test_build_runtime_config_exposes_jira_ui_defaults_when_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.feature_flags, "jira_create_page_enabled", True)
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_default_project_key",
        "ENG",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_default_board_id",
        "42",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "jira_create_page_remember_last_board_in_session",
        False,
    )

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert config["system"]["jiraIntegration"]["defaultProjectKey"] == "ENG"
    assert config["system"]["jiraIntegration"]["defaultBoardId"] == "42"
    assert config["system"]["jiraIntegration"]["rememberLastBoardInSession"] is False


def test_build_runtime_config_includes_dashboard_build_metadata(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_BUILD_ID", "20260408.1703")

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["buildId"] == "20260408.1703"


def test_build_runtime_config_reads_baked_build_id_when_env_missing(
    monkeypatch,
    tmp_path,
) -> None:
    build_id_path = tmp_path / ".moonmind-build-id"
    build_id_path.write_text("20260408.1703\n", encoding="utf-8")
    monkeypatch.delenv("MOONMIND_BUILD_ID", raising=False)
    monkeypatch.setenv("MOONMIND_BUILD_ID_PATH", str(build_id_path))

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["buildId"] == "20260408.1703"


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

    config = dashboard_view_model.build_runtime_config("/tasks")
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
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert config["system"]["defaultTaskRuntime"] == "gemini_cli"
    assert config["system"]["defaultTaskModel"] == "gemini-2.5-flash"
    assert config["system"]["defaultTaskEffort"] == ""
    assert config["system"]["defaultTaskModelByRuntime"]["gemini_cli"] == (
        "gemini-2.5-flash"
    )
    monkeypatch.delenv("MOONMIND_WORKER_RUNTIME", raising=False)
    monkeypatch.delenv("MOONMIND_GEMINI_MODEL", raising=False)


def test_build_runtime_config_uses_claude_from_runtime_env(monkeypatch) -> None:
    # The alias 'claude' is normalized to 'claude_code' internally, but since
    # 'claude' is not in supportedTaskRuntimes (now 'claude_code' is), it will
    # fall back to the settings-configured default (codex_cli) rather than 'claude'.
    monkeypatch.setenv("MOONMIND_WORKER_RUNTIME", "claude_code")
    monkeypatch.setenv("CLAUDE_API_KEY", "enabled")
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == ["codex_cli", "gemini_cli", "claude_code", "codex_cloud"]
    assert config["system"]["defaultTaskRuntime"] == "claude_code"
    assert config["system"]["defaultTaskModel"] == "Sonnet 4.6"


def test_build_runtime_config_uses_settings_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings.workflow, "github_repository", "Octo/Repo")
    monkeypatch.setattr(settings.github, "github_repos", None)
    monkeypatch.setattr(settings.github, "github_token", None)
    monkeypatch.setattr(settings.workflow, "codex_model", "gpt-test-codex")
    monkeypatch.setattr(settings.workflow, "codex_effort", "medium")
    monkeypatch.setattr(settings.workflow, "default_task_runtime", "codex_cli")
    monkeypatch.setenv("MOONMIND_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setattr(settings.workflow, "default_publish_mode", "branch")

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["defaultRepository"] == "Octo/Repo"
    assert config["system"]["repositoryOptions"] == {
        "items": [{"value": "Octo/Repo", "label": "Octo/Repo", "source": "default"}],
        "error": None,
    }
    assert config["system"]["defaultTaskModel"] == "gpt-test-codex"
    assert config["system"]["defaultTaskEffort"] == "medium"
    assert config["system"]["defaultTaskModelByRuntime"]["codex_cli"] == "gpt-test-codex"
    assert config["system"]["defaultTaskModelByRuntime"]["gemini_cli"] == "gemini-2.5-pro"
    assert config["system"]["defaultTaskEffortByRuntime"]["codex_cli"] == "medium"
    assert config["system"]["defaultPublishMode"] == "branch"
    assert config["system"]["defaultProposeTasks"] is False


def test_build_runtime_config_includes_configured_repository_options(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "github_repository", "Octo/Repo")
    monkeypatch.setattr(
        settings.github,
        "github_repos",
        "Octo/Repo, MoonLadderStudios/MoonMind, https://github.com/Example/App.git, bad-value",
    )
    monkeypatch.setattr(settings.github, "github_token", None)

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert config["system"]["repositoryOptions"]["items"] == [
        {"value": "Octo/Repo", "label": "Octo/Repo", "source": "default"},
        {
            "value": "MoonLadderStudios/MoonMind",
            "label": "MoonLadderStudios/MoonMind",
            "source": "configured",
        },
        {"value": "Example/App", "label": "Example/App", "source": "configured"},
    ]


def test_build_runtime_config_includes_credential_visible_github_repositories(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "github_repository", "Octo/Repo")
    monkeypatch.setattr(settings.github, "github_repos", None)
    monkeypatch.setattr(settings.github, "github_token", "ghp_test_token")
    monkeypatch.setattr(settings.github, "github_enabled", True)
    monkeypatch.setattr(
        dashboard_view_model,
        "_fetch_github_repository_options",
        lambda token: (
            [
                dashboard_view_model.RepositoryOption(
                    value="MoonLadderStudios/MoonMind",
                    label="MoonLadderStudios/MoonMind",
                    source="github",
                )
            ],
            None,
        ),
    )

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert config["system"]["repositoryOptions"]["items"] == [
        {"value": "Octo/Repo", "label": "Octo/Repo", "source": "default"},
        {
            "value": "MoonLadderStudios/MoonMind",
            "label": "MoonLadderStudios/MoonMind",
            "source": "github",
        },
    ]


def test_build_runtime_config_sanitizes_repository_options_and_errors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        settings.workflow,
        "github_repository",
        "https://token@example.com/Secret/Repo.git",
    )
    monkeypatch.setattr(
        settings.github,
        "github_repos",
        "Octo/Repo, octo/repo, https://github.com/Valid/Repo.git?token=secret, git@github.com:Another/Repo.git",
    )
    monkeypatch.setattr(settings.github, "github_token", "ghp_secret_token")
    monkeypatch.setattr(settings.github, "github_enabled", True)
    monkeypatch.setattr(
        dashboard_view_model,
        "_fetch_github_repository_options",
        lambda token: (
            [],
            "GitHub repository discovery failed; ghp_secret_token was not exposed.",
        ),
    )

    config = dashboard_view_model.build_runtime_config("/tasks/new")

    assert config["system"]["repositoryOptions"]["items"] == [
        {"value": "Octo/Repo", "label": "Octo/Repo", "source": "configured"},
        {"value": "Another/Repo", "label": "Another/Repo", "source": "configured"},
    ]
    assert "ghp_secret_token" not in config["system"]["repositoryOptions"]["error"]


def test_build_repository_branch_options_uses_github_lookup(monkeypatch) -> None:
    monkeypatch.setattr(settings.github, "github_token", "ghp_test_token")
    monkeypatch.setattr(settings.github, "github_enabled", True)
    monkeypatch.setattr(
        dashboard_view_model,
        "_get_cached_github_branch_options",
        lambda token, repository: (
            [
                dashboard_view_model.BranchOption(
                    value="main",
                    label="main",
                    source="github",
                ),
                dashboard_view_model.BranchOption(
                    value="feature/create-page",
                    label="feature/create-page",
                    source="github",
                ),
            ],
            None,
        ),
    )

    payload = dashboard_view_model.build_repository_branch_options("Octo/Repo")

    assert payload == {
        "items": [
            {"value": "main", "label": "main", "source": "github"},
            {
                "value": "feature/create-page",
                "label": "feature/create-page",
                "source": "github",
            },
        ],
        "error": None,
    }


def test_build_repository_branch_options_sanitizes_errors(monkeypatch) -> None:
    monkeypatch.setattr(settings.github, "github_token", "ghp_secret_token")
    monkeypatch.setattr(settings.github, "github_enabled", True)
    monkeypatch.setattr(
        dashboard_view_model,
        "_get_cached_github_branch_options",
        lambda token, repository: (
            [],
            "GitHub failed with ghp_secret_token",
        ),
    )

    payload = dashboard_view_model.build_repository_branch_options("Octo/Repo")

    assert payload["items"] == []
    assert payload["error"] == "GitHub branch lookup is unavailable."
    assert "ghp_secret_token" not in payload["error"]


def test_build_runtime_config_uses_repo_runtime_model_defaults(monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_CODEX_MODEL", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("MOONMIND_GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("MOONMIND_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.setattr(settings.workflow, "codex_model", None)
    monkeypatch.setattr(settings.workflow, "codex_effort", None)

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["defaultTaskModelByRuntime"]["codex_cli"] == "gpt-5.4"
    assert config["system"]["defaultTaskModelByRuntime"]["gemini_cli"] == "gemini-3.1-pro-preview"
    assert config["system"]["defaultTaskModelByRuntime"]["claude_code"] == "Sonnet 4.6"


def test_normalize_status_maps_temporal_waits_to_awaiting_action() -> None:
    assert dashboard_view_model.normalize_status("temporal", "awaiting_external") == "awaiting_action"


def test_build_runtime_config_includes_claude_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(settings.jules, "jules_enabled", False)
    monkeypatch.setattr(settings.jules, "jules_api_url", None)
    monkeypatch.setattr(settings.jules, "jules_api_key", None)

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == ["codex_cli", "gemini_cli", "claude_code", "codex_cloud"]


def test_build_runtime_config_uses_temporal_dashboard_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings.temporal_dashboard, "enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "list_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "detail_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "actions_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(settings.temporal_dashboard, "temporal_task_editing_enabled", False)
    monkeypatch.setattr(settings.temporal_dashboard, "debug_fields_enabled", True)
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "list_endpoint",
        "/api/temporal/executions",
    )
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "detail_endpoint",
        "/gateway/api/executions/{workflowId}",
    )
    monkeypatch.setattr(
        settings.temporal_dashboard,
        "artifact_download_endpoint",
        "/api/temporal/artifacts/{artifactId}/download",
    )

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["features"]["temporalDashboard"] == {
        "enabled": False,
        "listEnabled": False,
        "detailEnabled": True,
        "actionsEnabled": True,
        "submitEnabled": True,
        "temporalTaskEditing": False,
        "debugFieldsEnabled": True,
    }
    assert config["sources"]["temporal"]["list"] == "/api/temporal/executions"
    assert (
        config["sources"]["temporal"]["steps"]
        == "/gateway/api/executions/{workflowId}/steps"
    )
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

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["system"]["supportedTaskRuntimes"] == [
        "codex_cli",
        "gemini_cli",
        "claude_code",
        "codex_cloud",
        "jules",
    ]


def test_build_runtime_config_log_streaming_enabled_by_default() -> None:
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert config["features"]["logStreamingEnabled"] is True


def test_build_runtime_config_log_streaming_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_LOG_STREAMING_ENABLED", "false")
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert config["features"]["logStreamingEnabled"] is False


def test_build_runtime_config_session_timeline_rollout_defaults_off(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_session_timeline_rollout",
        "off",
    )

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["features"]["liveLogsSessionTimelineEnabled"] is False
    assert config["features"]["liveLogsSessionTimelineRollout"] == "off"


def test_build_runtime_config_session_timeline_rollout_is_exposed(monkeypatch) -> None:
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_session_timeline_rollout",
        "codex_managed",
    )

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["features"]["liveLogsSessionTimelineEnabled"] is True
    assert config["features"]["liveLogsSessionTimelineRollout"] == "codex_managed"


def test_build_runtime_config_groups_live_logs_feature_flags(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_LOG_STREAMING_ENABLED", "true")
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_session_timeline_rollout",
        "internal",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_structured_history_enabled",
        False,
    )

    config = dashboard_view_model.build_runtime_config("/tasks")

    assert config["features"]["logStreamingEnabled"] is True
    assert config["features"]["liveLogsSessionTimelineEnabled"] is True
    assert config["features"]["liveLogsSessionTimelineRollout"] == "internal"
    assert config["features"]["liveLogsStructuredHistoryEnabled"] is False


def test_build_live_logs_feature_config_exposes_all_managed_rollout(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_LOG_STREAMING_ENABLED", "true")
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_session_timeline_rollout",
        "all_managed",
    )
    monkeypatch.setattr(
        settings.feature_flags,
        "live_logs_structured_history_enabled",
        True,
    )

    feature_config = dashboard_view_model.build_live_logs_feature_config()

    assert feature_config == {
        "logStreamingEnabled": True,
        "liveLogsSessionTimelineEnabled": True,
        "liveLogsSessionTimelineRollout": "all_managed",
        "liveLogsStructuredHistoryEnabled": True,
    }


def test_build_runtime_config_omits_temporal_live_session_endpoint() -> None:
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert "liveSession" not in config["sources"]["temporal"]


# ---------------------------------------------------------------------------
# T024: Run-index pagination and shared visibility totals
# ---------------------------------------------------------------------------


def test_runtime_config_exposes_manifest_status_endpoint() -> None:
    """Manifest-status endpoint must be present for run-index visibility."""
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["manifestStatus"]
        == "/api/executions/{workflowId}/manifest-status"
    )


def test_runtime_config_exposes_manifest_nodes_endpoint() -> None:
    """Manifest-nodes endpoint must be present for run-index pagination."""
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["manifestNodes"]
        == "/api/executions/{workflowId}/manifest-nodes"
    )





def test_normalize_status_maps_manifest_ingest_states() -> None:
    """Manifest-ingest-specific temporal states should map correctly."""
    assert dashboard_view_model.normalize_status("temporal", "executing") == "running"
    assert dashboard_view_model.normalize_status("temporal", "planning") == "running"
    assert dashboard_view_model.normalize_status("temporal", "canceled") == "canceled"
    assert dashboard_view_model.normalize_status("temporal", "awaiting_external") == "awaiting_action"


def test_runtime_config_temporal_update_endpoint_for_manifest_updates() -> None:
    """The update endpoint used for manifest SetConcurrency/Pause/Resume must exist."""
    config = dashboard_view_model.build_runtime_config("/tasks")
    assert (
        config["sources"]["temporal"]["update"]
        == "/api/executions/{workflowId}/update"
    )


def test_normalize_status_maps_temporal_awaiting_slot_to_queued() -> None:
    """awaiting_slot (auth-slot wait) should map to queued on the dashboard."""
    assert dashboard_view_model.normalize_status("temporal", "awaiting_slot") == "queued"


def test_normalize_status_maps_temporal_waiting_on_dependencies_to_waiting() -> None:
    """waiting_on_dependencies should map to waiting on the dashboard."""
    assert dashboard_view_model.normalize_status("temporal", "waiting_on_dependencies") == "waiting"
