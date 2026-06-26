"""Unit tests for skills-first stage runner policy."""

from __future__ import annotations

from moonmind.workflows.skills.registry import resolve_stage_execution
from moonmind.workflows.skills.runner import execute_stage

def _set_skill_defaults(monkeypatch) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skills_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skills_canary_percent",
        100,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skills_shadow_mode",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skills_fallback_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.default_skill",
        "auto",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.discover_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.submit_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.publish_skill",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skill_policy_mode",
        "allowlist",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.allowed_skills",
        ("auto",),
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
    assert outcome.selected_skill == "auto"
    assert outcome.execution_path == "skill"
    assert outcome.used_skills is True
    assert outcome.used_fallback is False
    assert outcome.adapter_id is None
    assert calls == ["direct"]

def test_execute_stage_uses_direct_path_when_skills_disabled(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.skills_enabled",
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
    assert outcome.selected_skill == "auto"
    assert outcome.adapter_id is None

def test_stage_override_respects_allowlist(monkeypatch):
    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.allowed_skills",
        ("auto", "custom"),
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
        "moonmind.workflows.skills.registry.settings.workflow.skill_policy_mode",
        "permissive",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.allowed_skills",
        ("auto",),
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

def test_execute_stage_custom_skill_still_runs_directly(monkeypatch):
    """All skills now execute directly — custom overrides just change metadata."""

    _set_skill_defaults(monkeypatch)
    monkeypatch.setattr(
        "moonmind.workflows.skills.registry.settings.workflow.allowed_skills",
        ("auto", "custom"),
        raising=False,
    )

    calls: list[str] = []
    context = {"skill_overrides": {"submit_codex_job": "custom"}}

    outcome = execute_stage(
        stage_name="submit_codex_job",
        run_id="run-custom",
        context=context,
        execute_direct=lambda: calls.append("direct") or "ok",
    )

    assert outcome.selected_skill == "custom"
    assert outcome.execution_path == "skill"
    assert outcome.adapter_id is None
    assert calls == ["direct"]
