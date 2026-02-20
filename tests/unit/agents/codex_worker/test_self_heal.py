"""Unit tests for the self-heal controller primitives."""

from __future__ import annotations

import os

import pytest

from moonmind.agents.codex_worker.self_heal import (
    AttemptBudgetExceeded,
    SelfHealConfig,
    SelfHealController,
    StepAttemptState,
    build_failure_signature,
)
from moonmind.utils.logging import SecretRedactor


def test_self_heal_config_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config should fall back to documented defaults when env vars are absent."""

    for key in [
        "STEP_MAX_ATTEMPTS",
        "STEP_TIMEOUT_SECONDS",
        "STEP_IDLE_TIMEOUT_SECONDS",
        "STEP_NO_PROGRESS_LIMIT",
        "JOB_SELF_HEAL_MAX_RESETS",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = SelfHealConfig.from_env()

    assert config.step_max_attempts == 3
    assert config.step_timeout_seconds == 900
    assert config.step_idle_timeout_seconds == 300
    assert config.step_no_progress_limit == 2
    assert config.job_self_heal_max_resets == 1


def test_self_heal_config_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars should override defaults with integer parsing."""

    monkeypatch.setenv("STEP_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("STEP_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("STEP_IDLE_TIMEOUT_SECONDS", "400")
    monkeypatch.setenv("STEP_NO_PROGRESS_LIMIT", "5")
    monkeypatch.setenv("JOB_SELF_HEAL_MAX_RESETS", "3")

    config = SelfHealConfig.from_env()

    assert config.step_max_attempts == 4
    assert config.step_timeout_seconds == 1200
    assert config.step_idle_timeout_seconds == 400
    assert config.step_no_progress_limit == 5
    assert config.job_self_heal_max_resets == 3


def test_build_failure_signature_scrubs_secrets() -> None:
    """Failure signatures should redact secret-like content."""

    redactor = SecretRedactor(secrets=["TOKEN"], placeholder="[REDACTED]")
    signature = build_failure_signature(
        message="Command failed: token=TOKEN",
        step_id="step-1",
        skill_id="speckit",
        exit_code=137,
        secret_redactor=redactor,
    )

    assert signature is not None
    assert "[redacted]" in signature.value
    assert "token=token" not in signature.value


def test_step_attempt_state_detects_no_progress() -> None:
    """No-progress should increment only when signature+diff repeat."""

    state = StepAttemptState(step_id="step-1", step_index=0)
    redactor = SecretRedactor(secrets=())
    first_sig = build_failure_signature(
        message="lint failure",
        step_id=state.step_id,
        secret_redactor=redactor,
    )
    assert not state.record_failure(signature=first_sig, diff_hash="abc123")
    assert state.consecutive_no_progress == 1

    repeat_sig = build_failure_signature(
        message="lint failure",
        step_id=state.step_id,
        secret_redactor=redactor,
    )
    assert state.record_failure(signature=repeat_sig, diff_hash="abc123")
    assert state.consecutive_no_progress == 2

    state.reset_no_progress()
    assert state.consecutive_no_progress == 0
    assert state.last_failure_signature is None


def test_self_heal_controller_enforces_attempt_budget() -> None:
    """Controller should raise when attempts exceed configured budget."""

    controller = SelfHealController(
        config=SelfHealConfig(
            step_max_attempts=2,
            step_timeout_seconds=10,
            step_idle_timeout_seconds=5,
            step_no_progress_limit=1,
            job_self_heal_max_resets=1,
        ),
        secret_redactor=SecretRedactor(secrets=()),
    )
    controller.begin_step(step_id="step-1", step_index=0)

    controller.new_attempt()
    controller.new_attempt()
    with pytest.raises(AttemptBudgetExceeded):
        controller.new_attempt()
