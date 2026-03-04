"""Unit tests for skills registry logic."""

from __future__ import annotations

import pytest
from typing import Any

from moonmind.workflows.skills import registry


@pytest.fixture
def mock_settings(monkeypatch):
    def _set_setting(name: str, value: Any):
        monkeypatch.setattr(
            f"moonmind.workflows.skills.registry.settings.spec_workflow.{name}",
            value,
            raising=False,
        )

    # Setup some default mock values to ensure predictable test environment
    _set_setting("skills_enabled", True)
    _set_setting("skills_canary_percent", 100)
    _set_setting("skills_fallback_enabled", True)
    _set_setting("skills_shadow_mode", False)
    _set_setting("skill_policy_mode", "allowlist")
    _set_setting(
        "allowed_skills",
        (
            "speckit",
            "test-skill",
            "speckit-discover",
            "speckit-submit",
            "speckit-publish",
        ),
    )
    _set_setting("default_skill", "speckit")
    _set_setting("discover_skill", "speckit-discover")
    _set_setting("submit_skill", "speckit-submit")
    _set_setting("publish_skill", "speckit-publish")

    return _set_setting


def test_stable_percent():
    # Should always return a stable percentage 0-99
    val1 = registry._stable_percent("run-123", "stage-a")
    val2 = registry._stable_percent("run-123", "stage-a")

    assert 0 <= val1 <= 99
    assert val1 == val2


def test_select_stage_skill_uses_override(mock_settings):
    context = {"skill_overrides": {"stage-a": "custom-skill"}}
    skill = registry._select_stage_skill("stage-a", context)
    assert skill == "custom-skill"

    # Ignores empty/whitespace overrides
    context2 = {"skill_overrides": {"stage-a": "   "}}
    skill2 = registry._select_stage_skill("stage-a", context2)
    assert skill2 == "speckit"  # Falls back to default


def test_select_stage_skill_uses_stage_mappings(mock_settings):
    assert registry._select_stage_skill("discover_next_phase", {}) == "speckit-discover"
    assert registry._select_stage_skill("submit_codex_job", {}) == "speckit-submit"
    assert registry._select_stage_skill("apply_and_publish", {}) == "speckit-publish"
    assert registry._select_stage_skill("unknown_stage", {}) == "speckit"


def test_skill_allowed(mock_settings):
    # Allowlist mode
    mock_settings("skill_policy_mode", "allowlist")
    mock_settings("allowed_skills", ("speckit", "docs-lint"))

    assert registry._skill_allowed("speckit") is True
    assert registry._skill_allowed("docs-lint") is True
    assert registry._skill_allowed("unknown") is False

    # Allowlist with no configured allowed_skills (fallback to True)
    mock_settings("allowed_skills", ())
    assert registry._skill_allowed("anything") is True

    # Permissive mode
    mock_settings("skill_policy_mode", "permissive")
    mock_settings("allowed_skills", ("speckit",))
    assert registry._skill_allowed("unknown") is True


def test_resolve_stage_execution_uses_skills(mock_settings):
    mock_settings("skills_enabled", True)
    mock_settings("skills_canary_percent", 100)
    mock_settings("skills_fallback_enabled", True)
    mock_settings("skills_shadow_mode", False)

    decision = registry.resolve_stage_execution(
        stage_name="discover_next_phase", run_id="run-1", context={}
    )

    assert decision.stage_name == "discover_next_phase"
    assert decision.selected_skill == "speckit-discover"
    assert decision.use_skills is True
    assert decision.execution_path == "skill"
    assert decision.fallback_enabled is True
    assert decision.shadow_mode is False


def test_resolve_stage_execution_direct_only_when_disabled(mock_settings):
    mock_settings("skills_enabled", False)
    mock_settings("skills_canary_percent", 100)

    decision = registry.resolve_stage_execution(
        stage_name="discover_next_phase", run_id="run-1", context={}
    )

    assert decision.use_skills is False
    assert decision.execution_path == "direct_only"


def test_resolve_stage_execution_direct_only_outside_canary(mock_settings):
    mock_settings("skills_enabled", True)
    mock_settings("skills_canary_percent", 0)  # Nobody gets it

    decision = registry.resolve_stage_execution(
        stage_name="discover_next_phase", run_id="run-1", context={}
    )

    assert decision.use_skills is False
    assert decision.execution_path == "direct_only"


def test_resolve_stage_execution_unallowed_skill_fallback(mock_settings):
    mock_settings("skill_policy_mode", "allowlist")
    mock_settings("allowed_skills", ("speckit",))
    mock_settings("default_skill", "speckit")

    # Try an unallowed skill override
    decision = registry.resolve_stage_execution(
        stage_name="stage-a",
        run_id="run-1",
        context={"skill_overrides": {"stage-a": "malicious-skill"}},
    )

    # Should fall back to the default allowed skill
    assert decision.selected_skill == "speckit"


def test_get_stage_adapter():
    assert registry.get_stage_adapter("speckit") == "speckit"
    assert registry.get_stage_adapter("  speckit  ") == "speckit"
    assert registry.get_stage_adapter("unknown") is None
    assert registry.get_stage_adapter("") is None
    assert registry.get_stage_adapter(None) is None


def test_skill_requires_speckit():
    assert registry.skill_requires_speckit("speckit") is True
    assert registry.skill_requires_speckit("unknown") is False
    assert registry.skill_requires_speckit("") is False


def test_configured_stage_skills(mock_settings):
    mock_settings("default_skill", "speckit")
    mock_settings("discover_skill", "speckit-discover")
    mock_settings("submit_skill", " speckit-submit ")
    mock_settings("publish_skill", "")

    skills = registry.configured_stage_skills()
    assert skills == ("speckit", "speckit-discover", "speckit-submit")


def test_configured_stage_skills_deduplicates(mock_settings):
    mock_settings("default_skill", "speckit")
    mock_settings("discover_skill", "speckit")
    mock_settings("submit_skill", "speckit")
    mock_settings("publish_skill", "speckit-publish")

    skills = registry.configured_stage_skills()
    assert skills == ("speckit", "speckit-publish")


def test_configured_stage_skills_empty(mock_settings):
    mock_settings("default_skill", "")
    mock_settings("discover_skill", "")
    mock_settings("submit_skill", None)
    mock_settings("publish_skill", "   ")

    assert registry.configured_stage_skills() == ()


def test_configured_stage_skills_require_speckit(mock_settings):
    mock_settings("default_skill", "speckit")
    assert registry.configured_stage_skills_require_speckit() is True

    mock_settings("default_skill", "other-tool")
    mock_settings("discover_skill", "another-tool")
    mock_settings("submit_skill", "")
    mock_settings("publish_skill", "")
    assert registry.configured_stage_skills_require_speckit() is False
