"""Self-heal controller, timers, and checkpoint helpers for Codex workers."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

from moonmind.utils.logging import SecretRedactor


class SelfHealError(RuntimeError):
    """Base error for self-heal controller failures."""


class AttemptBudgetExceeded(SelfHealError):
    """Raised when a step exhausts its configured attempt budget."""


class HardResetBudgetExceeded(SelfHealError):
    """Raised when a job exhausts its configured hard reset budget."""


class FailureClass(str, Enum):
    """Classification buckets that determine retry behavior."""

    TRANSIENT_RUNTIME = "transient_runtime"
    STUCK_NO_PROGRESS = "stuck_no_progress"
    DETERMINISTIC_CONTRACT = "deterministic_contract"
    DETERMINISTIC_POLICY = "deterministic_policy"
    DETERMINISTIC_REPO = "deterministic_repo"


class SelfHealStrategy(str, Enum):
    """Strategy selected for the next attempt."""

    NONE = "none"
    SOFT_RESET = "soft_reset"
    HARD_RESET = "hard_reset"
    QUEUE_RETRY = "queue_retry"
    OPERATOR_REQUEST = "operator_request"


class StepTimeoutExceeded(SelfHealError):
    """Raised when a step attempt exceeds the configured wall-clock timeout."""


class StepIdleTimeoutExceeded(SelfHealError):
    """Raised when a step attempt emits no output for the configured idle window."""


class NoProgressThresholdExceeded(SelfHealError):
    """Raised when repeated attempts yield the same outcome and diff hash."""


class WorkspaceReplayError(SelfHealError):
    """Raised when hard reset replay fails."""


@dataclass(frozen=True, slots=True)
class SelfHealConfig:
    """Runtime-configurable self-heal thresholds."""

    step_max_attempts: int = 3
    step_timeout_seconds: int = 900
    step_idle_timeout_seconds: int = 300
    step_no_progress_limit: int = 2
    job_self_heal_max_resets: int = 1

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SelfHealConfig":
        """Create a config instance from environment variables."""

        source = env or os.environ
        return cls(
            step_max_attempts=_read_positive_int(
                source, "STEP_MAX_ATTEMPTS", default=3
            ),
            step_timeout_seconds=_read_positive_int(
                source, "STEP_TIMEOUT_SECONDS", default=900
            ),
            step_idle_timeout_seconds=_read_positive_int(
                source, "STEP_IDLE_TIMEOUT_SECONDS", default=300
            ),
            step_no_progress_limit=_read_positive_int(
                source, "STEP_NO_PROGRESS_LIMIT", default=2
            ),
            job_self_heal_max_resets=_read_positive_int(
                source, "JOB_SELF_HEAL_MAX_RESETS", default=1
            ),
        )

    def as_dict(self) -> dict[str, int]:
        """Expose config values for logging/telemetry."""

        return {
            "step_max_attempts": self.step_max_attempts,
            "step_timeout_seconds": self.step_timeout_seconds,
            "step_idle_timeout_seconds": self.step_idle_timeout_seconds,
            "step_no_progress_limit": self.step_no_progress_limit,
            "job_self_heal_max_resets": self.job_self_heal_max_resets,
        }


class FailureSignature:
    """Normalized + scrubbed signature used for no-progress detection."""

    __slots__ = ("value", "fingerprint")

    def __init__(self, value: str) -> None:
        normalized = _collapse_whitespace(value.strip().lower())
        self.value = normalized
        self.fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def matches(self, other: "FailureSignature | None") -> bool:
        return bool(other and self.fingerprint == other.fingerprint)

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return self.value


def build_failure_signature(
    *,
    message: str | None,
    step_id: str | None = None,
    skill_id: str | None = None,
    exit_code: int | None = None,
    failure_hint: str | None = None,
    secret_redactor: SecretRedactor | None = None,
) -> FailureSignature | None:
    """Create a failure signature with consistent formatting and scrubbing."""

    parts: list[str] = []
    if step_id:
        parts.append(f"step:{step_id}")
    if skill_id:
        parts.append(f"skill:{skill_id}")
    if exit_code is not None:
        parts.append(f"exit:{exit_code}")
    if failure_hint:
        parts.append(f"hint:{failure_hint}")
    if message:
        parts.append(message)

    if not parts:
        return None

    payload = _collapse_whitespace(" | ".join(parts))
    redactor = secret_redactor or SecretRedactor.from_environ()
    scrubbed = redactor.scrub(payload).lower()
    return FailureSignature(scrubbed)


@dataclass(slots=True)
class StepAttemptState:
    """Mutable bookkeeping for one step's attempts."""

    step_id: str
    step_index: int
    attempts_consumed: int = 0
    consecutive_no_progress: int = 0
    last_failure_signature: FailureSignature | None = None
    last_diff_hash: str | None = None

    def next_attempt(self, *, max_attempts: int) -> int:
        """Increment the attempt counter, enforcing the configured budget."""

        if self.attempts_consumed >= max_attempts:
            raise AttemptBudgetExceeded(
                f"attempt budget exhausted for {self.step_id} (max={max_attempts})"
            )
        self.attempts_consumed += 1
        return self.attempts_consumed

    def record_failure(
        self,
        *,
        signature: FailureSignature | None,
        diff_hash: str | None,
    ) -> bool:
        """Track failure signature + diff hash; return True when no-progress."""

        matched = bool(
            signature
            and self.last_failure_signature
            and signature.matches(self.last_failure_signature)
            and diff_hash == self.last_diff_hash
        )
        if matched:
            self.consecutive_no_progress += 1
        else:
            self.consecutive_no_progress = 1 if signature else 0
        self.last_failure_signature = signature
        self.last_diff_hash = diff_hash
        return matched

    def reset_no_progress(self) -> None:
        """Reset the no-progress window after a successful attempt."""

        self.consecutive_no_progress = 0
        self.last_failure_signature = None
        self.last_diff_hash = None


@dataclass(slots=True)
class StepAttemptSnapshot:
    """Snapshot persisted for artifacts and telemetry events."""

    step_id: str
    step_index: int
    attempt: int
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    failure_class: FailureClass | None = None
    failure_signature: str | None = None
    failure_signature_hash: str | None = None
    diff_hash: str | None = None
    changed_files: tuple[str, ...] = ()
    strategy: SelfHealStrategy = SelfHealStrategy.NONE


@dataclass(slots=True)
class SelfHealJobState:
    """Job-level controller state (hard reset budget)."""

    resets_consumed: int = 0

    def reserve_hard_reset(self, *, max_resets: int) -> None:
        """Consume one hard reset slot."""

        if self.resets_consumed >= max_resets:
            raise HardResetBudgetExceeded(
                f"hard reset budget exhausted (max={max_resets})"
            )
        self.resets_consumed += 1


class SelfHealController:
    """Coordinates self-heal budgets for steps and jobs."""

    def __init__(
        self,
        *,
        config: SelfHealConfig,
        secret_redactor: SecretRedactor | None = None,
    ) -> None:
        self._config = config
        self._job_state = SelfHealJobState()
        self._active_step: StepAttemptState | None = None
        self._secret_redactor = secret_redactor or SecretRedactor.from_environ(
            placeholder="[REDACTED]"
        )

    @property
    def config(self) -> SelfHealConfig:
        return self._config

    @property
    def active_step(self) -> StepAttemptState | None:
        return self._active_step

    def can_hard_reset(self) -> bool:
        """Return True when additional hard resets remain for the job."""

        return self._job_state.resets_consumed < self._config.job_self_heal_max_resets

    def begin_step(self, *, step_id: str, step_index: int) -> None:
        """Start tracking a new step."""

        self._active_step = StepAttemptState(step_id=step_id, step_index=step_index)

    def new_attempt(self) -> StepAttemptSnapshot:
        """Reserve a new attempt for the active step."""

        if self._active_step is None:
            raise SelfHealError("begin_step must be called before new_attempt")
        attempt_number = self._active_step.next_attempt(
            max_attempts=self._config.step_max_attempts
        )
        return StepAttemptSnapshot(
            step_id=self._active_step.step_id,
            step_index=self._active_step.step_index,
            attempt=attempt_number,
        )

    def reset_after_success(self) -> None:
        """Clear tracking fields when a step succeeds."""

        if self._active_step:
            self._active_step.reset_no_progress()

    def consume_hard_reset(self) -> None:
        """Consume a hard reset budget entry."""

        self._job_state.reserve_hard_reset(
            max_resets=self._config.job_self_heal_max_resets
        )

    def build_failure_signature(
        self,
        *,
        message: str | None,
        step_id: str | None = None,
        skill_id: str | None = None,
        exit_code: int | None = None,
        failure_hint: str | None = None,
    ) -> FailureSignature | None:
        """Helper that uses the controller's redactor for signatures."""

        return build_failure_signature(
            message=message,
            step_id=step_id,
            skill_id=skill_id,
            exit_code=exit_code,
            failure_hint=failure_hint,
            secret_redactor=self._secret_redactor,
        )


def _read_positive_int(
    env: Mapping[str, str],
    key: str,
    *,
    default: int,
) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        raise ValueError(f"{key} must be an integer") from None
    if value < 1:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


class IdleTimeoutWatcher:
    """Async idle timeout watchdog updated by output chunk callbacks."""

    __slots__ = ("_timeout", "_last_pulse", "_stop", "_triggered", "_task")

    def __init__(self, timeout_seconds: int) -> None:
        self._timeout = max(0.0, float(timeout_seconds))
        self._last_pulse = time.monotonic()
        self._stop = asyncio.Event()
        self._triggered = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._timeout <= 0.0 or self._task is not None:
            return
        self._task = asyncio.create_task(self._watch_loop())

    def pulse(self) -> None:
        self._last_pulse = time.monotonic()

    def cancel(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None:
            task.cancel()

    async def wait(self) -> None:
        task = self._task
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    @property
    def triggered(self) -> bool:
        return self._triggered

    async def _watch_loop(self) -> None:
        try:
            while not self._stop.is_set():
                await asyncio.sleep(min(1.0, self._timeout / 4.0))
                if time.monotonic() - self._last_pulse >= self._timeout:
                    self._triggered = True
                    self._stop.set()
        finally:
            self._task = None


class RebuildableWorkspace(Protocol):
    """Defines the expected structure for a workspace that can be rebuilt."""

    repo_dir: Path
    job_root: Path
    execute_log_path: Path
    starting_branch: str
    new_branch: str | None


class HardResetWorkspaceBuilder:
    """Replays successful step patches into a clean workspace clone."""

    def __init__(
        self,
        *,
        run_stage_command: Callable[..., Awaitable[object]],
    ) -> None:
        self._run_stage_command = run_stage_command

    async def rebuild(
        self,
        *,
        repository: str,
        prepared: RebuildableWorkspace,
        resolve_clone_url: Callable[[str], str],
        ensure_working_branch: Callable[..., Awaitable[None]],
        patch_paths: Sequence[Path],
        env: Mapping[str, str] | None = None,
    ) -> None:
        repo_dir: Path = getattr(prepared, "repo_dir")
        job_root: Path = getattr(prepared, "job_root")
        execute_log_path: Path = getattr(prepared, "execute_log_path")
        starting_branch: str = getattr(prepared, "starting_branch")
        new_branch: str | None = getattr(prepared, "new_branch")

        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)

        clone_url = resolve_clone_url(repository)
        try:
            await self._run_stage_command(
                ["git", "clone", "--", clone_url, str(repo_dir)],
                cwd=job_root,
                log_path=execute_log_path,
                env=env,
            )
            await self._run_stage_command(
                ["git", "fetch", "--all", "--prune"],
                cwd=repo_dir,
                log_path=execute_log_path,
                check=False,
                env=env,
            )
            await ensure_working_branch(
                repo_dir=repo_dir,
                starting_branch=starting_branch,
                new_branch=new_branch,
                log_path=execute_log_path,
                env=env,
            )
            for patch_path in patch_paths:
                if not patch_path.exists() or patch_path.stat().st_size == 0:
                    continue
                await self._run_stage_command(
                    [
                        "git",
                        "apply",
                        "--allow-empty",
                        "--whitespace=nowarn",
                        str(patch_path),
                    ],
                    cwd=repo_dir,
                    log_path=execute_log_path,
                    env=env,
                )
        except Exception as exc:  # pragma: no cover - defensive
            raise WorkspaceReplayError(f"hard reset replay failed: {exc}") from exc


def is_failure_retryable(failure_class: FailureClass | None) -> bool:
    """Return True when the failure class supports further retries."""

    return failure_class in {
        FailureClass.TRANSIENT_RUNTIME,
        FailureClass.STUCK_NO_PROGRESS,
    }


__all__ = [
    "AttemptBudgetExceeded",
    "FailureClass",
    "HardResetBudgetExceeded",
    "HardResetWorkspaceBuilder",
    "IdleTimeoutWatcher",
    "NoProgressThresholdExceeded",
    "SelfHealConfig",
    "SelfHealController",
    "SelfHealError",
    "SelfHealStrategy",
    "StepIdleTimeoutExceeded",
    "StepTimeoutExceeded",
    "WorkspaceReplayError",
    "build_failure_signature",
    "is_failure_retryable",
]
