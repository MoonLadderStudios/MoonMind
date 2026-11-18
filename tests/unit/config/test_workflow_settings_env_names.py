"""Workflow settings keep resolving from every environment name they accept.

Source issue: MoonLadderStudios/MoonMind#5334, which removed names each of
these fields listed twice. Every remaining name must still set the field, and
the first-listed name must still win when several are set.
"""

from __future__ import annotations

import pytest

from moonmind.config.settings import AppSettings, AppWorkflowSettings, WorkflowSettings

# field -> (names in precedence order, raw value, parsed value, losing raw value)
_ENV_NAMES: dict[str, tuple[tuple[str, ...], str, object, str]] = {
    "artifacts_root": (
        ("WORKFLOW_ARTIFACT_ROOT", "WORKFLOW_ARTIFACTS_ROOT"),
        "/srv/artifacts",
        "/srv/artifacts",
        "/srv/other",
    ),
    "default_publish_mode": (
        ("WORKFLOW_DEFAULT_PUBLISH_MODE", "MOONMIND_DEFAULT_PUBLISH_MODE"),
        "branch",
        "branch",
        "none",
    ),
    "github_repository": (
        ("WORKFLOW_GITHUB_REPOSITORY",),
        "Example/Repo",
        "Example/Repo",
        "Other/Repo",
    ),
    "git_user_name": (
        ("WORKFLOW_GIT_USER_NAME", "MOONMIND_GIT_USER_NAME"),
        "Example Bot",
        "Example Bot",
        "Other Bot",
    ),
    "git_user_email": (
        ("WORKFLOW_GIT_USER_EMAIL", "MOONMIND_GIT_USER_EMAIL"),
        "bot@example.com",
        "bot@example.com",
        "other@example.com",
    ),
    "github_token": (
        ("WORKFLOW_GITHUB_TOKEN",),
        "example-value",
        "example-value",
        "other-value",
    ),
    "test_mode": (("WORKFLOW_TEST_MODE",), "true", True, "false"),
    "default_skill": (
        ("WORKFLOW_DEFAULT_SKILL", "MOONMIND_DEFAULT_SKILL"),
        "speckit",
        "speckit",
        "other",
    ),
    "skill_policy_mode": (
        (
            "WORKFLOW_SKILL_POLICY_MODE",
            "MOONMIND_SKILL_POLICY_MODE",
            "SKILL_POLICY_MODE",
        ),
        "allowlist",
        "allowlist",
        "permissive",
    ),
    "allowed_skills": (
        ("WORKFLOW_ALLOWED_SKILLS", "MOONMIND_ALLOWED_SKILLS"),
        "alpha,beta",
        ("alpha", "beta"),
        "gamma",
    ),
}

_SETTINGS_TYPES = (WorkflowSettings, AppWorkflowSettings)


@pytest.fixture(autouse=True)
def _clear_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for names, *_ in _ENV_NAMES.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("settings_type", _SETTINGS_TYPES)
@pytest.mark.parametrize(
    ("field", "name"),
    [(field, name) for field, (names, *_) in _ENV_NAMES.items() for name in names],
)
def test_each_environment_name_sets_the_field(
    monkeypatch: pytest.MonkeyPatch, settings_type, field: str, name: str
) -> None:
    _, raw, expected, _ = _ENV_NAMES[field]
    monkeypatch.setenv(name, raw)

    settings = settings_type(_env_file=None)

    assert getattr(settings, field) == expected


@pytest.mark.parametrize("settings_type", _SETTINGS_TYPES)
@pytest.mark.parametrize(
    "field", [field for field, (names, *_) in _ENV_NAMES.items() if len(names) > 1]
)
def test_first_listed_environment_name_wins(
    monkeypatch: pytest.MonkeyPatch, settings_type, field: str
) -> None:
    names, raw, expected, losing_raw = _ENV_NAMES[field]
    monkeypatch.setenv(names[0], raw)
    for name in names[1:]:
        monkeypatch.setenv(name, losing_raw)

    settings = settings_type(_env_file=None)

    assert getattr(settings, field) == expected


def test_app_settings_repository_override_reads_its_environment_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKFLOW_GITHUB_REPOSITORY", "Example/Repo")

    settings = AppSettings(_env_file=None)

    assert settings.workflow_github_repository == "Example/Repo"
    assert settings.workflow.github_repository == "Example/Repo"
