"""Step ledger status domain values."""

from __future__ import annotations

import enum
from typing import Literal


class StepLedgerStatus(str, enum.Enum):
    """Canonical operator-facing step ledger statuses."""

    PENDING = "pending"
    READY = "ready"
    EXECUTING = "executing"
    AWAITING_EXTERNAL = "awaiting_external"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


StepLedgerStatusValue = Literal[
    "pending",
    "ready",
    "executing",
    "awaiting_external",
    "reviewing",
    "completed",
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
    "preparing": StepLedgerStatus.EXECUTING,
    "executing": StepLedgerStatus.EXECUTING,
    "running": StepLedgerStatus.EXECUTING,
    "checking": StepLedgerStatus.REVIEWING,
    "completed": StepLedgerStatus.COMPLETED,
    "succeeded": StepLedgerStatus.COMPLETED,
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
