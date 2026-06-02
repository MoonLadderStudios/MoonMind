from __future__ import annotations

from moonmind.config.settings import WorkflowSettings


def test_workflow_settings_exposes_memory_context_token_budget() -> None:
    settings = WorkflowSettings(memory_context_budget_tokens=1234)

    assert settings.memory_context_budget_tokens == 1234


def test_workflow_settings_reads_memory_context_budget_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS", "2048")
    monkeypatch.delenv("MEMORY_CONTEXT_BUDGET_TOKENS", raising=False)

    settings = WorkflowSettings()

    assert settings.memory_context_budget_tokens == 2048
