"""Unit tests for the self-heal controller primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.agents.codex_worker.self_heal import (
    AttemptBudgetExceeded,
    HardResetWorkspaceBuilder,
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


class _PreparedWorkspace:
    def __init__(self, root: Path) -> None:
        self.repo_dir = root / "repo"
        self.job_root = root
        self.execute_log_path = root / "execute.log"
        self.starting_branch = "main"
        self.new_branch: str | None = None


@pytest.mark.asyncio
async def test_hard_reset_builder_clone_uses_option_separator(tmp_path: Path) -> None:
    """git clone command must include `--` before clone_url to block option injection."""

    commands: list[tuple[str, ...]] = []

    async def _run_stage_command(command, **kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs
        commands.append(tuple(str(part) for part in command))
        return object()

    async def _ensure_working_branch(**kwargs):  # type: ignore[no-untyped-def]
        _ = kwargs

    prepared = _PreparedWorkspace(tmp_path)
    builder = HardResetWorkspaceBuilder(run_stage_command=_run_stage_command)
    await builder.rebuild(
        repository="example/repo",
        prepared=prepared,
        resolve_clone_url=lambda _repository: "-uhoh",
        ensure_working_branch=_ensure_working_branch,
        patch_paths=(),
        env=None,
    )

    assert commands
    assert commands[0][:3] == ("git", "clone", "--")
    assert commands[0][3] == "-uhoh"
