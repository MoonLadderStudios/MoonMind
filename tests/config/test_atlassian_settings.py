from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.config.settings import AtlassianSettings, FeatureFlagsSettings

_ATLASSIAN_ENV_KEYS = (
    "ATLASSIAN_CONFLUENCE_ENABLED",
    "ATLASSIAN_JIRA_ENABLED",
    "ATLASSIAN_JIRA_TOOL_ENABLED",
    "ATLASSIAN_JIRA_REQUIRE_EXPLICIT_TRANSITION_LOOKUP",
    "ATLASSIAN_JIRA_ALLOWED_ACTIONS",
    "ATLASSIAN_JIRA_ALLOWED_PROJECTS",
    "ATLASSIAN_JIRA_CONNECT_TIMEOUT_SECONDS",
    "ATLASSIAN_JIRA_READ_TIMEOUT_SECONDS",
    "ATLASSIAN_JIRA_RETRY_ATTEMPTS",
    "ATLASSIAN_SITE_URL",
    "FEATURE_FLAGS__JIRA_CREATE_PAGE_DEFAULT_PROJECT_KEY",
    "FEATURE_FLAGS__JIRA_CREATE_PAGE_DEFAULT_BOARD_ID",
    "JIRA_CREATE_PAGE_DEFAULT_PROJECT_KEY",
    "JIRA_CREATE_PAGE_DEFAULT_BOARD_ID",
)


@pytest.fixture(autouse=True)
def _clear_atlassian_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ATLASSIAN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_atlassian_nested_jira_flags_and_timeout_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLASSIAN_CONFLUENCE_ENABLED", "true")
    monkeypatch.setenv("ATLASSIAN_JIRA_ENABLED", "true")
    monkeypatch.setenv("ATLASSIAN_JIRA_TOOL_ENABLED", "true")
    monkeypatch.setenv("ATLASSIAN_JIRA_REQUIRE_EXPLICIT_TRANSITION_LOOKUP", "false")
    monkeypatch.setenv("ATLASSIAN_JIRA_CONNECT_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("ATLASSIAN_JIRA_READ_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("ATLASSIAN_JIRA_RETRY_ATTEMPTS", "5")

    settings = AtlassianSettings(_env_file=None)

    assert settings.confluence.confluence_enabled is True
    assert settings.jira.jira_enabled is True
    assert settings.jira.jira_tool_enabled is True
    assert settings.jira.jira_require_explicit_transition_lookup is False
    assert settings.jira.jira_connect_timeout_seconds == 4.5
    assert settings.jira.jira_read_timeout_seconds == 12.0
    assert settings.jira.jira_retry_attempts == 5


def test_atlassian_settings_normalize_allowed_projects_actions_and_site_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ATLASSIAN_JIRA_ALLOWED_ACTIONS",
        " jira.create_issue , add_comment , jira.create_issue ",
    )
    monkeypatch.setenv("ATLASSIAN_JIRA_ALLOWED_PROJECTS", " eng , OPS, eng ")
    monkeypatch.setenv("ATLASSIAN_SITE_URL", "https://https://example.atlassian.net/")

    settings = AtlassianSettings(_env_file=None)

    assert settings.jira.jira_allowed_actions == "create_issue,add_comment"
    assert settings.jira.jira_allowed_projects == "ENG,OPS"
    assert settings.atlassian_site_url == "https://example.atlassian.net"


def test_atlassian_settings_reject_invalid_allowed_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ATLASSIAN_JIRA_ALLOWED_ACTIONS",
        "create_issue,raw_http",
    )

    with pytest.raises(ValidationError):
        AtlassianSettings(_env_file=None)


def test_jira_create_page_defaults_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_CREATE_PAGE_DEFAULT_PROJECT_KEY", " eng ")
    monkeypatch.setenv("JIRA_CREATE_PAGE_DEFAULT_BOARD_ID", " 42 ")

    settings = FeatureFlagsSettings(_env_file=None)

    assert settings.jira_create_page_default_project_key == "ENG"
    assert settings.jira_create_page_default_board_id == "42"


def test_jira_create_page_default_project_rejects_invalid_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_CREATE_PAGE_DEFAULT_PROJECT_KEY", "bad-key")

    with pytest.raises(ValidationError):
        FeatureFlagsSettings(_env_file=None)
