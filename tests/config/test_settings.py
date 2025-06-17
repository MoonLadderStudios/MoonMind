import os
import pytest

from moonmind.config.settings import AtlassianSettings

@pytest.mark.parametrize(
    "env_value, expected",
    [
        ("True", True),
        ("true", True),
        ("TRUE", True),
        ("tRuE", True),
        ("False", False),
        ("false", False),
        ("FALSE", False),
        ("fAlSe", False),
        ("", False),  # Default to False if empty
        ("other", False), # Default to False if not a valid boolean string
    ],
)
def test_atlassian_confluence_enabled_parsing(monkeypatch, env_value, expected):
    monkeypatch.setenv("ATLASSIAN_CONFLUENCE_ENABLED", env_value)
    # We also need to set other required env vars for AtlassianSettings if any,
    # or ensure the model allows them to be None for this test.
    # For now, assuming other fields can be None or have defaults.
    settings = AtlassianSettings()
    assert settings.confluence.confluence_enabled == expected

@pytest.mark.parametrize(
    "env_value, expected",
    [
        ("True", True),
        ("true", True),
        ("TRUE", True),
        ("tRuE", True),
        ("False", False),
        ("false", False),
        ("FALSE", False),
        ("fAlSe", False),
        ("", False),
        ("other", False),
    ],
)
def test_atlassian_jira_enabled_parsing(monkeypatch, env_value, expected):
    monkeypatch.setenv("ATLASSIAN_JIRA_ENABLED", env_value)
    settings = AtlassianSettings()
    assert settings.jira.jira_enabled == expected

def test_atlassian_settings_init_no_env_vars(monkeypatch):
    # Ensure that if the env vars are not set, the defaults (False) are used.
    monkeypatch.delenv("ATLASSIAN_CONFLUENCE_ENABLED", raising=False)
    monkeypatch.delenv("ATLASSIAN_JIRA_ENABLED", raising=False)
    settings = AtlassianSettings()
    assert settings.confluence.confluence_enabled is False
    assert settings.jira.jira_enabled is False
