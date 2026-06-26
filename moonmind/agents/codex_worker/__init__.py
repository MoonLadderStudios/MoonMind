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
    from moonmind.workflows.temporal.runtime.self_heal import (
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
    from moonmind.utils.cli import CliVerificationError
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
    "FailureClass": "moonmind.workflows.temporal.runtime.self_heal",
    "HardResetWorkspaceBuilder": "moonmind.workflows.temporal.runtime.self_heal",
    "IdleTimeoutWatcher": "moonmind.workflows.temporal.runtime.self_heal",
    "SelfHealConfig": "moonmind.workflows.temporal.runtime.self_heal",
    "SelfHealController": "moonmind.workflows.temporal.runtime.self_heal",
    "SelfHealStrategy": "moonmind.workflows.temporal.runtime.self_heal",
    "StepIdleTimeoutExceeded": "moonmind.workflows.temporal.runtime.self_heal",
    "StepTimeoutExceeded": "moonmind.workflows.temporal.runtime.self_heal",
    "WorkspaceReplayError": "moonmind.workflows.temporal.runtime.self_heal",
    "is_failure_retryable": "moonmind.workflows.temporal.runtime.self_heal",
    "CliVerificationError": "moonmind.utils.cli",
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
