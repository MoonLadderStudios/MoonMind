"""Step execution artifact/projection status domain values."""

from __future__ import annotations

import enum
from typing import Literal

from moonmind.statuses.step_ledger import StepLedgerStatus


class StepExecutionArtifactStatus(str, enum.Enum):
    """Statuses retained in Step Execution artifact/projection records."""

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    CHECKING = "checking"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


StepExecutionArtifactStatusValue = Literal[
    "pending",
    "preparing",
    "running",
    "checking",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
    "superseded",
]

STEP_EXECUTION_ARTIFACT_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in StepExecutionArtifactStatus
)

STEP_EXECUTION_ARTIFACT_STATUS_TO_LEDGER_STATUS: dict[
    StepExecutionArtifactStatus, StepLedgerStatus
] = {
    StepExecutionArtifactStatus.PENDING: StepLedgerStatus.PENDING,
    StepExecutionArtifactStatus.PREPARING: StepLedgerStatus.RUNNING,
    StepExecutionArtifactStatus.RUNNING: StepLedgerStatus.RUNNING,
    StepExecutionArtifactStatus.CHECKING: StepLedgerStatus.REVIEWING,
    StepExecutionArtifactStatus.SUCCEEDED: StepLedgerStatus.SUCCEEDED,
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
    "StepExecutionArtifactStatus",
    "StepExecutionArtifactStatusValue",
    "step_execution_artifact_status_to_ledger_status",
]
