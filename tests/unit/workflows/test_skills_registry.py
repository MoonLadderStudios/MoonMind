"""Unit tests for skills registry logic."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.skills import registry
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore


@pytest.fixture
def mock_settings(monkeypatch):
    def _set_setting(name: str, value: Any):
        monkeypatch.setattr(
            f"moonmind.workflows.skills.registry.settings.workflow.{name}",
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
            "agentkit",
            "test-skill",
            "agentkit-discover",
            "agentkit-submit",
            "agentkit-publish",
        ),
    )
    _set_setting("default_skill", "agentkit")
    _set_setting("discover_skill", "agentkit-discover")
    _set_setting("submit_skill", "agentkit-submit")
    _set_setting("publish_skill", "agentkit-publish")

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
    assert skill2 == "agentkit"  # Falls back to default


def test_select_stage_skill_uses_stage_mappings(mock_settings):
    assert registry._select_stage_skill("discover_next_phase", {}) == "agentkit-discover"
    assert registry._select_stage_skill("submit_codex_job", {}) == "agentkit-submit"
    assert registry._select_stage_skill("apply_and_publish", {}) == "agentkit-publish"
    assert registry._select_stage_skill("unknown_stage", {}) == "agentkit"


def test_skill_allowed(mock_settings):
    # Allowlist mode
    mock_settings("skill_policy_mode", "allowlist")
    mock_settings("allowed_skills", ("agentkit", "docs-lint"))

    assert registry._skill_allowed("agentkit") is True
    assert registry._skill_allowed("docs-lint") is True
    assert registry._skill_allowed("unknown") is False

    # Allowlist with no configured allowed_skills (fallback to True)
    mock_settings("allowed_skills", ())
    assert registry._skill_allowed("anything") is True

    # Permissive mode
    mock_settings("skill_policy_mode", "permissive")
    mock_settings("allowed_skills", ("agentkit",))
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
    assert decision.selected_skill == "agentkit-discover"
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
    mock_settings("allowed_skills", ("agentkit",))
    mock_settings("default_skill", "agentkit")

    # Try an unallowed skill override
    decision = registry.resolve_stage_execution(
        stage_name="stage-a",
        run_id="run-1",
        context={"skill_overrides": {"stage-a": "malicious-skill"}},
    )

    # Should fall back to the default allowed skill
    assert decision.selected_skill == "agentkit"


def test_get_stage_adapter():
    assert registry.get_stage_adapter("agentkit") == "agentkit"
    assert registry.get_stage_adapter("  agentkit  ") == "agentkit"
    assert registry.get_stage_adapter("unknown") is None
    assert registry.get_stage_adapter("") is None
    assert registry.get_stage_adapter(None) is None


def test_skill_requires_agentkit():
    assert registry.skill_requires_agentkit("agentkit") is True
    assert registry.skill_requires_agentkit("unknown") is False
    assert registry.skill_requires_agentkit("") is False


def test_configured_stage_skills(mock_settings):
    mock_settings("default_skill", "agentkit")
    mock_settings("discover_skill", "agentkit-discover")
    mock_settings("submit_skill", " agentkit-submit ")
    mock_settings("publish_skill", "")

    skills = registry.configured_stage_skills()
    assert skills == ("agentkit", "agentkit-discover", "agentkit-submit")


def test_configured_stage_skills_deduplicates(mock_settings):
    mock_settings("default_skill", "agentkit")
    mock_settings("discover_skill", "agentkit")
    mock_settings("submit_skill", "agentkit")
    mock_settings("publish_skill", "agentkit-publish")

    skills = registry.configured_stage_skills()
    assert skills == ("agentkit", "agentkit-publish")


def test_configured_stage_skills_empty(mock_settings):
    mock_settings("default_skill", "")
    mock_settings("discover_skill", "")
    mock_settings("submit_skill", None)
    mock_settings("publish_skill", "   ")

    assert registry.configured_stage_skills() == ()


def test_configured_stage_skills_require_agentkit(mock_settings):
    mock_settings("default_skill", "agentkit")
    assert registry.configured_stage_skills_require_agentkit() is True

    mock_settings("default_skill", "other-tool")
    mock_settings("discover_skill", "another-tool")
    mock_settings("submit_skill", "")
    mock_settings("publish_skill", "")
    assert registry.configured_stage_skills_require_agentkit() is False


def test_contract_registry_helpers_roundtrip_snapshot():
    store = InMemoryArtifactStore()
    skills = registry.parse_skill_registry(
        {
            "skills": [
                {
                    "name": "repo.run_tests",
                    "version": "1.0.0",
                    "description": "Run tests",
                    "inputs": {
                        "schema": {
                            "type": "object",
                            "required": ["repo_ref"],
                            "properties": {"repo_ref": {"type": "string"}},
                        }
                    },
                    "outputs": {
                        "schema": {
                            "type": "object",
                            "required": ["ok"],
                            "properties": {"ok": {"type": "boolean"}},
                        }
                    },
                    "executor": {
                        "activity_type": "mm.skill.execute",
                        "selector": {"mode": "by_capability"},
                    },
                    "requirements": {"capabilities": ["sandbox"]},
                    "policies": {
                        "timeouts": {
                            "start_to_close_seconds": 10,
                            "schedule_to_close_seconds": 20,
                        },
                        "retries": {"max_attempts": 1},
                    },
                }
            ]
        }
    )
    registry.validate_skill_registry(skills)

    snapshot = registry.create_registry_snapshot(skills=skills, artifact_store=store)
    loaded = registry.load_registry_snapshot_from_artifact(
        artifact_ref=snapshot.artifact_ref,
        artifact_store=store,
    )

    assert loaded.digest == snapshot.digest
    assert loaded.get_skill(
        name="repo.run_tests", version="1.0.0"
    ).executor.activity_type == ("mm.skill.execute")
