"""Unit tests for skills-first stage runner policy."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.registry import resolve_stage_execution
from moonmind.workflows.skills.runner import execute_stage
from moonmind.workflows.skills.speckit_adapter import SkillAdapterError


def _set_skill_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skills_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skills_canary_percent",
        100,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skills_shadow_mode",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skills_fallback_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.discover_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.submit_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.publish_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skill_policy_mode",
        "allowlist",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.allowed_skills",
        ("speckit",),
        raising=False,
    )


def test_execute_stage_uses_skill_path_by_default(monkeypatch):
    _set_skill_defaults(monkeypatch)

    calls: list[str] = []

    def run_direct() -> str:
        calls.append("direct")
        return "ok"

    outcome = execute_stage(
        stage_name="submit_codex_job",
        run_id="run-1",
        context={},
        execute_direct=run_direct,
    )

    assert outcome.result == "ok"
    assert outcome.selected_skill == "speckit"
    assert outcome.execution_path == "skill"
    assert outcome.used_skills is True
    assert outcome.used_fallback is False
    assert calls == ["direct"]


def test_execute_stage_uses_direct_path_when_skills_disabled(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skills_enabled",
        False,
        raising=False,
    )

    outcome = execute_stage(
        stage_name="discover_next_phase",
        run_id="run-2",
        context={},
        execute_direct=lambda: {"status": "ok"},
    )

    assert outcome.execution_path == "direct_only"
    assert outcome.used_skills is False
    assert outcome.selected_skill == "speckit"


def test_stage_override_respects_allowlist(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.allowed_skills",
        ("speckit", "custom"),
        raising=False,
    )

    context = {"skill_overrides": {"apply_and_publish": "custom"}}
    decision = resolve_stage_execution(
        stage_name="apply_and_publish",
        run_id="run-3",
        context=context,
    )

    assert decision.selected_skill == "custom"
    assert decision.execution_path == "skill"


def test_stage_override_ignores_allowlist_in_permissive_mode(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.skill_policy_mode",
        "permissive",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.allowed_skills",
        ("speckit",),
        raising=False,
    )

    context = {"skill_overrides": {"apply_and_publish": "custom"}}
    decision = resolve_stage_execution(
        stage_name="apply_and_publish",
        run_id="run-3b",
        context=context,
    )

    assert decision.selected_skill == "custom"
    assert decision.execution_path == "skill"


def test_execute_stage_unregistered_skill_fails_fast(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.spec_workflow.allowed_skills",
        ("speckit", "custom"),
        raising=False,
    )

    calls: list[str] = []
    context = {"skill_overrides": {"submit_codex_job": "custom"}}

    with pytest.raises(SkillAdapterError, match="skill_adapter_not_registered"):
        execute_stage(
            stage_name="submit_codex_job",
            run_id="run-unregistered",
            context=context,
            execute_direct=lambda: calls.append("direct"),
        )

    assert calls == []


def test_execute_stage_fallback_when_adapter_errors(monkeypatch):
    _set_skill_defaults(monkeypatch)

    def raise_adapter_error(*, execute_direct):
        raise SkillAdapterError("adapter unavailable")

    monkeypatch.setattr(
        "moonmind.workflows.skills.runner.run_speckit_stage",
        raise_adapter_error,
    )

    calls: list[str] = []

    def run_direct() -> str:
        calls.append("fallback")
        return "fallback-ok"

    outcome = execute_stage(
        stage_name="submit_codex_job",
        run_id="run-4",
        context={},
        execute_direct=run_direct,
    )

    assert outcome.result == "fallback-ok"
    assert outcome.execution_path == "direct_fallback"
    assert outcome.used_fallback is True
    assert calls == ["fallback"]
