"""Removed inert configuration must not block an otherwise valid startup."""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from moonmind.config.settings import AppSettings
from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer


@pytest.mark.parametrize("source", ["empty", "template", "old_environment", "old_dotenv"])
def test_settings_load_without_unused_controls(monkeypatch, tmp_path, source):
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    if source == "template":
        for key, value in dotenv_values(Path(__file__).parents[3] / ".env-template").items():
            if value is not None:
                monkeypatch.setenv(key, value)
    env_file = None
    if source in {"old_environment", "old_dotenv"}:
        obsolete_names = (
            "TEMPORAL_DASHBOARD_LIST_COUNT_TIMEOUT_SECONDS",
            "AGENT_JOB_MAX_RUNTIME_SECONDS", "AGENT_JOB_STALE_LEASE_GRACE_SECONDS",
            "WORKFLOW_STAGE_COMMAND_TIMEOUT_SECONDS", "MODEL_CACHE_REFRESH_INTERVAL_SECONDS",
            "MODEL_CACHE_REFRESH_INTERVAL", "WORKFLOW_METRICS_ENABLED", "WORKFLOW_METRICS_PORT",
            "ATLASSIAN_JIRA_FETCH_BATCH_SIZE", "ATLASSIAN_CONFLUENCE_ENABLED", "FASTAPI_RELOAD",
            "WORKFLOW_CODEX_QUEUE", "WORKFLOW_DEFAULT_QUEUE", "WORKFLOW_DEFAULT_ROUTING_KEY",
            "CODEX_SHARDS", "CODEX_LOGIN_CHECK_IMAGE",
            "OMNIGENT_HOST_AUTH_ENABLED", "OMNIGENT_MOONMIND_WORKSPACE",
            "MOONMIND_GEMINI_CAPACITY_RETRY_MAX_ATTEMPTS", "MOONMIND_CODEX_SANDBOX_MODE",
            "CODEX_PROFILE", "MOONMIND_WORKER_CAPABILITIES", "TEMPORAL_POSTGRES_VERSION",
            "FERNET_KEY", "LocalData", "ATLASSIAN_JIRA_JQL_QUERY",
            "MODEL_CONTEXT_PROTOCOL_ENABLED", "MODEL_CONTEXT_PROTOCOL_PORT",
            "MODEL_CONTEXT_PROTOCOL_HOST", "MOONMIND_CLAUDE_CLI_AUTH_MODE",
        )
        if source == "old_environment":
            for key in obsolete_names:
                monkeypatch.setenv(key, "obsolete-value")
        else:
            env_file = tmp_path / ".env"
            env_file.write_text("".join(f"{key}=obsolete-value\n" for key in obsolete_names))
    settings = AppSettings(_env_file=env_file)
    assert "model_cache_refresh_interval_seconds" not in settings.model_dump()
    assert "stage_command_timeout_seconds" not in settings.workflow.model_dump()
    assert "confluence" not in settings.atlassian.model_dump()
    assert settings.workflow.codex_model
    assert settings.atlassian.jira.jira_enabled is False


@pytest.mark.parametrize("provider", ["google", "openai", "anthropic"])
def test_retained_provider_settings_drive_reviewer(monkeypatch, provider):
    monkeypatch.setenv("DEFAULT_CHAT_PROVIDER", provider)
    monkeypatch.setenv(f"{provider.upper()}_CHAT_MODEL", f"{provider}-review-model")
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "false")
    settings = AppSettings(_env_file=None)
    route = ConfiguredStepReviewer(settings).describe_route("default")
    assert route == {"provider": provider, "model": f"{provider}-review-model"}
    assert getattr(getattr(settings, provider), f"{provider}_enabled") is False
    assert ConfiguredStepReviewer(settings).describe_route("explicit-model")["model"] == "explicit-model"
