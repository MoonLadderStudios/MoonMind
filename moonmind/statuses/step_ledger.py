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


__all__ = ["STEP_LEDGER_STATUS_VALUES", "StepLedgerStatus", "StepLedgerStatusValue"]
