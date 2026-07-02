"""Step execution artifact/projection status domain values."""

from __future__ import annotations

import enum
from typing import Literal

from moonmind.statuses.step_ledger import StepLedgerStatus


class StepExecutionArtifactStatus(str, enum.Enum):
    """Statuses retained in Step Execution artifact/projection records."""

    PENDING = "pending"
    PREPARING = "preparing"
    EXECUTING = "executing"
    RUNNING = "running"
    CHECKING = "checking"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


StepExecutionArtifactStatusValue = Literal[
    "pending",
    "preparing",
    "executing",
    "running",
    "checking",
    "completed",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
    "superseded",
]
StepExecutionStatus = StepExecutionArtifactStatusValue
StepExecutionReason = Literal[
    "initial_execution",
    "quality_gate_failed",
    "tests_failed",
    "runtime_recovered",
    "recover_from_failed_step",
    "remediation_context",
    "operator_requested",
    "dependency_invalidated",
    "policy_revalidation",
]
StepExecutionTerminalDisposition = Literal[
    "accepted",
    "retryable",
    "blocked",
    "needs_human",
    "discarded",
    "superseded",
    "failed_unrecoverable",
    "failed_with_remaining_work",
]

STEP_EXECUTION_ARTIFACT_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in StepExecutionArtifactStatus
)
STEP_EXECUTION_STATUSES = STEP_EXECUTION_ARTIFACT_STATUS_VALUES

STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS: dict[
    StepExecutionArtifactStatus, StepLedgerStatus
] = {
    StepExecutionArtifactStatus.PENDING: StepLedgerStatus.PENDING,
    StepExecutionArtifactStatus.PREPARING: StepLedgerStatus.EXECUTING,
    StepExecutionArtifactStatus.EXECUTING: StepLedgerStatus.EXECUTING,
    StepExecutionArtifactStatus.RUNNING: StepLedgerStatus.EXECUTING,
    StepExecutionArtifactStatus.CHECKING: StepLedgerStatus.REVIEWING,
    StepExecutionArtifactStatus.COMPLETED: StepLedgerStatus.COMPLETED,
    StepExecutionArtifactStatus.SUCCEEDED: StepLedgerStatus.COMPLETED,
    StepExecutionArtifactStatus.FAILED: StepLedgerStatus.FAILED,
    StepExecutionArtifactStatus.BLOCKED: StepLedgerStatus.AWAITING_EXTERNAL,
    StepExecutionArtifactStatus.CANCELED: StepLedgerStatus.CANCELED,
    StepExecutionArtifactStatus.SUPERSEDED: StepLedgerStatus.SKIPPED,
}


def step_execution_artifact_status_to_ledger_status(
    status: StepExecutionArtifactStatus | str,
) -> StepLedgerStatus:
    """Map a Step Execution artifact status to the operator-facing ledger status."""

    artifact_status = (
        status
        if isinstance(status, StepExecutionArtifactStatus)
        else StepExecutionArtifactStatus(status)
    )
    return STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS[artifact_status]


__all__ = [
    "STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS",
    "STEP_EXECUTION_ARTIFACT_STATUS_VALUES",
    "STEP_EXECUTION_STATUSES",
    "StepExecutionArtifactStatus",
    "StepExecutionArtifactStatusValue",
    "StepExecutionReason",
    "StepExecutionStatus",
    "StepExecutionTerminalDisposition",
    "step_execution_artifact_status_to_ledger_status",
]
