"""Step ledger status domain and explicit artifact-status conversions."""

from __future__ import annotations

from typing import Literal

from moonmind.statuses.step_execution import StepExecutionStatus

StepLedgerStatus = Literal[
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

STEP_LEDGER_STATUSES = frozenset(
    {
        "pending",
        "ready",
        "running",
        "awaiting_external",
        "reviewing",
        "succeeded",
        "failed",
        "skipped",
        "canceled",
    }
)

STEP_EXECUTION_TO_LEDGER_STATUS: dict[StepExecutionStatus, StepLedgerStatus] = {
    "pending": "pending",
    "preparing": "running",
    "running": "running",
    "checking": "reviewing",
    "succeeded": "succeeded",
    "failed": "failed",
    "blocked": "awaiting_external",
    "canceled": "canceled",
    "superseded": "skipped",
}


def step_execution_to_ledger_status(
    status: StepExecutionStatus,
) -> StepLedgerStatus:
    return STEP_EXECUTION_TO_LEDGER_STATUS.get(status, "pending")
