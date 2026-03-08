"""Codex remote worker daemon package."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moonmind.agents.codex_worker.handlers import (
        ArtifactUpload,
        CodexExecHandler,
        CodexSkillPayload,
        CodexWorkerHandlerError,
        WorkerExecutionResult,
    )
    from moonmind.agents.codex_worker.metrics import WorkerMetrics
    from moonmind.agents.codex_worker.self_heal import (
        FailureClass,
        HardResetWorkspaceBuilder,
        IdleTimeoutWatcher,
        SelfHealConfig,
        SelfHealController,
        SelfHealStrategy,
        StepIdleTimeoutExceeded,
        StepTimeoutExceeded,
        WorkspaceReplayError,
        is_failure_retryable,
    )
    from moonmind.agents.codex_worker.utils import CliVerificationError
    from moonmind.agents.codex_worker.worker import (
        ClaimedJob,
        CodexWorker,
        CodexWorkerConfig,
        QueueApiClient,
        QueueClientError,
    )


__all__ = [
    "ArtifactUpload",
    "CliVerificationError",
    "ClaimedJob",
    "CodexExecHandler",
    "CodexSkillPayload",
    "CodexWorker",
    "CodexWorkerConfig",
    "CodexWorkerHandlerError",
    "QueueApiClient",
    "QueueClientError",
    "WorkerExecutionResult",
    "FailureClass",
    "HardResetWorkspaceBuilder",
    "IdleTimeoutWatcher",
    "SelfHealConfig",
    "SelfHealController",
    "SelfHealStrategy",
    "StepIdleTimeoutExceeded",
    "StepTimeoutExceeded",
    "WorkspaceReplayError",
    "is_failure_retryable",
    "WorkerMetrics",
]

_EXPORT_MAP = {
    "ArtifactUpload": "moonmind.agents.codex_worker.handlers",
    "CodexExecHandler": "moonmind.agents.codex_worker.handlers",
    "CodexWorkerHandlerError": "moonmind.agents.codex_worker.handlers",
    "CodexSkillPayload": "moonmind.agents.codex_worker.handlers",
    "WorkerExecutionResult": "moonmind.agents.codex_worker.handlers",
    "WorkerMetrics": "moonmind.agents.codex_worker.metrics",
    "FailureClass": "moonmind.agents.codex_worker.self_heal",
    "HardResetWorkspaceBuilder": "moonmind.agents.codex_worker.self_heal",
    "IdleTimeoutWatcher": "moonmind.agents.codex_worker.self_heal",
    "SelfHealConfig": "moonmind.agents.codex_worker.self_heal",
    "SelfHealController": "moonmind.agents.codex_worker.self_heal",
    "SelfHealStrategy": "moonmind.agents.codex_worker.self_heal",
    "StepIdleTimeoutExceeded": "moonmind.agents.codex_worker.self_heal",
    "StepTimeoutExceeded": "moonmind.agents.codex_worker.self_heal",
    "WorkspaceReplayError": "moonmind.agents.codex_worker.self_heal",
    "is_failure_retryable": "moonmind.agents.codex_worker.self_heal",
    "CliVerificationError": "moonmind.agents.codex_worker.utils",
    "ClaimedJob": "moonmind.agents.codex_worker.worker",
    "CodexWorker": "moonmind.agents.codex_worker.worker",
    "CodexWorkerConfig": "moonmind.agents.codex_worker.worker",
    "QueueApiClient": "moonmind.agents.codex_worker.worker",
    "QueueClientError": "moonmind.agents.codex_worker.worker",
}


def __getattr__(name: str):  # pragma: no cover - trivial lazy-import dispatch
    if name in _EXPORT_MAP:
        module = import_module(_EXPORT_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
