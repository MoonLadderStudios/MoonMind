"""Step ledger status domain values."""

from __future__ import annotations

import enum
from typing import Literal


class StepLedgerStatus(str, enum.Enum):
    """Canonical operator-facing step ledger statuses."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_EXTERNAL = "awaiting_external"
    REVIEWING = "reviewing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


StepLedgerStatusValue = Literal[
    "pending",
    "ready",
    "running",
    "awaiting_external",
    "reviewing",
    "succeeded",
    "failed",
    "skipped",
    "canceled",
]

STEP_LEDGER_STATUS_VALUES: frozenset[str] = frozenset(
    item.value for item in StepLedgerStatus
)
STEP_LEDGER_STATUSES = STEP_LEDGER_STATUS_VALUES

STEP_EXECUTION_TO_LEDGER_STATUS: dict[str, StepLedgerStatus] = {
    "pending": StepLedgerStatus.PENDING,
    "preparing": StepLedgerStatus.RUNNING,
    "running": StepLedgerStatus.RUNNING,
    "checking": StepLedgerStatus.REVIEWING,
    "succeeded": StepLedgerStatus.SUCCEEDED,
    "failed": StepLedgerStatus.FAILED,
    "blocked": StepLedgerStatus.AWAITING_EXTERNAL,
    "canceled": StepLedgerStatus.CANCELED,
    "superseded": StepLedgerStatus.SKIPPED,
}


def step_execution_to_ledger_status(status: str) -> StepLedgerStatus:
    return STEP_EXECUTION_TO_LEDGER_STATUS.get(str(status), StepLedgerStatus.PENDING)


__all__ = [
    "STEP_EXECUTION_TO_LEDGER_STATUS",
    "STEP_LEDGER_STATUSES",
    "STEP_LEDGER_STATUS_VALUES",
    "StepLedgerStatus",
    "StepLedgerStatusValue",
    "step_execution_to_ledger_status",
]
