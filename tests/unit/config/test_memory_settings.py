from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.config.settings import WorkflowSettings


def test_workflow_settings_exposes_memory_context_token_budget() -> None:
    settings = WorkflowSettings(memory_context_budget_tokens=1234)

    assert settings.memory_context_budget_tokens == 1234


def test_workflow_settings_reads_memory_context_budget_env(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS", "2048")
    monkeypatch.delenv("MEMORY_CONTEXT_BUDGET_TOKENS", raising=False)

    settings = WorkflowSettings()

    assert settings.memory_context_budget_tokens == 2048


def test_workflow_settings_reads_legacy_memory_context_budget_env(monkeypatch) -> None:
    monkeypatch.delenv("MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.setenv("MEMORY_CONTEXT_BUDGET_TOKENS", "8192")

    settings = WorkflowSettings(_env_file=None)

    assert settings.memory_context_budget_tokens == 8192


def test_workflow_memory_context_budget_primary_env_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS", "2048")
    monkeypatch.setenv("MEMORY_CONTEXT_BUDGET_TOKENS", "8192")

    settings = WorkflowSettings(_env_file=None)

    assert settings.memory_context_budget_tokens == 2048


@pytest.mark.parametrize("name", [
    "MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS",
    "MEMORY_CONTEXT_BUDGET_TOKENS",
])
def test_workflow_memory_context_budget_requires_positive_env_value(monkeypatch, name) -> None:
    monkeypatch.delenv("MOONMIND_MEMORY_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("MEMORY_CONTEXT_BUDGET_TOKENS", raising=False)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError):
        WorkflowSettings(_env_file=None)
