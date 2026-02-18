import pytest
from pydantic import ValidationError

from moonmind.config.settings import (
    AppSettings,
    AtlassianSettings,
    GoogleSettings,
    OIDCSettings,
    OllamaSettings,
    OpenAISettings,
    SpecWorkflowSettings,
)


# Fixture for default settings, can be customized in tests
@pytest.fixture
def app_settings_defaults():
    return {
        "google": GoogleSettings(
            google_chat_model="test-google-chat",
            google_embedding_model="test-google-embed",
            google_api_key="test_google_key",  # Required for is_provider_enabled
        ),
        "openai": OpenAISettings(
            openai_chat_model="test-openai-chat",
            openai_api_key="test_openai_key",  # Required for is_provider_enabled
        ),
        "ollama": OllamaSettings(
            ollama_chat_model="test-ollama-chat",
            ollama_embedding_model="test-ollama-embed",
        ),
        # Ensure other required fields for AppSettings have defaults if not provided
        "default_chat_provider": "google",  # Default to google for fixture
        "default_embedding_provider": "google",  # Default to google for fixture
        "qdrant": {"qdrant_enabled": False},  # Disable things not under test
        "rag": {},
        "github": {"github_enabled": False},
        "google_drive": {"google_drive_enabled": False},
        "atlassian": {
            "confluence": {"confluence_enabled": False},
            "jira": {"jira_enabled": False},
        },
    }


# Test that the removed fields are indeed gone
def test_default_model_fields_removed(app_settings_defaults):
    with pytest.raises(ValidationError):
        AppSettings(
            **app_settings_defaults, default_chat_model="some-model"
        )  # This should fail
    with pytest.raises(ValidationError):
        AppSettings(
            **app_settings_defaults, default_embed_model="some-model"
        )  # This should fail

    # Check that they are not present as attributes either
    settings = AppSettings(**app_settings_defaults)
    assert not hasattr(settings, "default_chat_model")
    assert not hasattr(settings, "default_embed_model")


class TestAtlassianSettings:
    def test_atlassian_url_correction(self, monkeypatch):
        # Test case 1: URL with "https://https://"
        monkeypatch.setenv("ATLASSIAN_URL", "https://https://example.atlassian.net")
        settings = AtlassianSettings(_env_file=None)
        assert settings.atlassian_url == "https://example.atlassian.net"

        # Test case 2: Correct HTTPS URL
        monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")
        settings2 = AtlassianSettings(_env_file=None)
        assert settings2.atlassian_url == "https://example.atlassian.net"

        # Test case 3: HTTP URL (should remain unchanged)
        monkeypatch.setenv("ATLASSIAN_URL", "http://example.atlassian.net")
        settings3 = AtlassianSettings(_env_file=None)
        assert settings3.atlassian_url == "http://example.atlassian.net"

        # Test case 4: Empty URL (should remain None or empty)
        monkeypatch.delenv("ATLASSIAN_URL", raising=False)
        settings4 = AtlassianSettings(_env_file=None)
        assert settings4.atlassian_url is None

        # Test case 5: URL that only starts with "https://" once
        monkeypatch.setenv("ATLASSIAN_URL", "https://another.example.com")
        settings5 = AtlassianSettings(_env_file=None)
        assert settings5.atlassian_url == "https://another.example.com"

        # Test case 6: URL with no scheme
        monkeypatch.setenv("ATLASSIAN_URL", "example.atlassian.net")
        settings6 = AtlassianSettings(_env_file=None)
        assert settings6.atlassian_url == "example.atlassian.net"

        # Clean up environment variable
        monkeypatch.delenv("ATLASSIAN_URL", raising=False)


class TestOIDCSettings:
    def test_auth_provider_default(self, monkeypatch):
        """Test that AUTH_PROVIDER defaults to 'disabled'."""
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
        settings = OIDCSettings()
        assert settings.AUTH_PROVIDER == "disabled"

    def test_auth_provider_env_override(self, monkeypatch):
        """Test that AUTH_PROVIDER can be set by environment variable."""
        monkeypatch.setenv("AUTH_PROVIDER", "google")
        settings = OIDCSettings()
        assert settings.AUTH_PROVIDER == "google"
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)

    def test_default_user_defaults(self, monkeypatch):
        """Test that DEFAULT_USER_ID and DEFAULT_USER_EMAIL default to None."""
        monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
        monkeypatch.delenv("DEFAULT_USER_EMAIL", raising=False)
        settings = OIDCSettings()
        assert settings.DEFAULT_USER_ID is None
        assert settings.DEFAULT_USER_EMAIL is None

    def test_default_user_env_override(self, monkeypatch):
        """Test that DEFAULT_USER_ID and DEFAULT_USER_EMAIL can be set by env vars."""
        test_id = "test_user_123"
        test_email = "test@example.com"
        monkeypatch.setenv("DEFAULT_USER_ID", test_id)
        monkeypatch.setenv("DEFAULT_USER_EMAIL", test_email)

        settings = OIDCSettings()
        assert settings.DEFAULT_USER_ID == test_id
        assert settings.DEFAULT_USER_EMAIL == test_email

        monkeypatch.delenv("DEFAULT_USER_ID", raising=False)
        monkeypatch.delenv("DEFAULT_USER_EMAIL", raising=False)


class TestSpecWorkflowSettings:
    def test_agent_job_artifact_defaults(self):
        """Milestone 2 artifact settings should keep stable defaults."""

        assert (
            SpecWorkflowSettings.model_fields["agent_job_artifact_root"].default
            == "var/artifacts/agent_jobs"
        )
        assert (
            SpecWorkflowSettings.model_fields["agent_job_artifact_max_bytes"].default
            == 10 * 1024 * 1024
        )

    def test_agent_job_artifact_env_overrides(self, monkeypatch):
        """Environment variables should override queue artifact settings."""

        monkeypatch.setenv("AGENT_JOB_ARTIFACT_ROOT", "/tmp/queue-artifacts")
        monkeypatch.setenv("AGENT_JOB_ARTIFACT_MAX_BYTES", "2048")
        settings = SpecWorkflowSettings(_env_file=None)

        assert settings.agent_job_artifact_root == "/tmp/queue-artifacts"
        assert settings.agent_job_artifact_max_bytes == 2048

        monkeypatch.delenv("AGENT_JOB_ARTIFACT_ROOT", raising=False)
        monkeypatch.delenv("AGENT_JOB_ARTIFACT_MAX_BYTES", raising=False)

    def test_task_default_baselines(self):
        """Task defaults should provide stable queue execution baselines."""

        settings = SpecWorkflowSettings(_env_file=None)
        assert settings.codex_model == "gpt-5.3-codex"
        assert settings.codex_effort == "high"
        assert settings.github_repository == "MoonLadderStudios/MoonMind"

    def test_task_default_env_overrides(self, monkeypatch):
        """Task defaults should accept explicit env overrides."""

        monkeypatch.setenv("MOONMIND_CODEX_MODEL", "gpt-custom-codex")
        monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
        monkeypatch.setenv("SPEC_WORKFLOW_GITHUB_REPOSITORY", "Example/Repo")
        monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_NAME", "  Nate Sticco  ")
        monkeypatch.setenv("SPEC_WORKFLOW_GIT_USER_EMAIL", "  nsticco@gmail.com  ")

        settings = SpecWorkflowSettings(_env_file=None)

        assert settings.codex_model == "gpt-custom-codex"
        assert settings.codex_effort == "medium"
        assert settings.github_repository == "Example/Repo"
        assert settings.git_user_name == "Nate Sticco"
        assert settings.git_user_email == "nsticco@gmail.com"

        monkeypatch.delenv("MOONMIND_CODEX_MODEL", raising=False)
        monkeypatch.delenv("MOONMIND_CODEX_EFFORT", raising=False)
        monkeypatch.delenv("SPEC_WORKFLOW_GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("SPEC_WORKFLOW_GIT_USER_NAME", raising=False)
        monkeypatch.delenv("SPEC_WORKFLOW_GIT_USER_EMAIL", raising=False)

    def test_skills_defaults(self):
        """Skills-first settings should have stable defaults."""

        settings = SpecWorkflowSettings(_env_file=None)
        assert settings.skills_enabled is True
        assert settings.skills_canary_percent == 100
        assert settings.default_skill == "speckit"
        assert settings.skill_policy_mode == "permissive"
        assert settings.allowed_skills == ("speckit",)

    def test_skills_overrides(self):
        """Skill settings should accept explicit override values."""

        settings = SpecWorkflowSettings(
            _env_file=None,
            skills_enabled=False,
            skills_canary_percent=25,
            default_skill="custom",
            allowed_skills=("speckit", "custom"),
            submit_skill="custom",
        )

        assert settings.skills_enabled is False
        assert settings.skills_canary_percent == 25
        assert settings.default_skill == "custom"
        assert settings.allowed_skills == ("speckit", "custom")
        assert settings.submit_skill == "custom"

    def test_default_skill_is_added_to_allowlist(self):
        """Allowlist mode should include default skill in allowlist."""

        settings = SpecWorkflowSettings(
            _env_file=None,
            skill_policy_mode="allowlist",
            default_skill="custom-default",
            allowed_skills=("speckit",),
        )

        assert settings.default_skill == "custom-default"
        assert settings.allowed_skills == ("speckit", "custom-default")

    def test_permissive_mode_does_not_modify_allowlist(self):
        """Permissive mode should not force default skill into allowlist."""

        settings = SpecWorkflowSettings(
            _env_file=None,
            skill_policy_mode="permissive",
            default_skill="custom-default",
            allowed_skills=("speckit",),
        )

        assert settings.default_skill == "custom-default"
        assert settings.allowed_skills == ("speckit",)

    def test_app_settings_defaults_codex_queue_to_celery_default(
        self, app_settings_defaults
    ):
        """When codex queue is unset, app settings should align it to default queue."""

        settings = AppSettings(
            **app_settings_defaults,
            celery={
                "default_queue": "moonmind.jobs",
                "default_exchange": "moonmind.jobs",
                "default_routing_key": "moonmind.jobs",
            },
            spec_workflow={"codex_queue": None},
        )

        assert settings.celery.default_queue == "moonmind.jobs"
        assert settings.spec_workflow.codex_queue == "moonmind.jobs"
