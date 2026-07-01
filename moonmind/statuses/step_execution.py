"""Step execution artifact status domain."""

from __future__ import annotations

from typing import Literal

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

StepExecutionStatus = Literal[
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

STEP_EXECUTION_STATUSES = frozenset(
    {
        "pending",
        "preparing",
        "running",
        "checking",
        "succeeded",
        "failed",
        "blocked",
        "canceled",
        "superseded",
    }
)

