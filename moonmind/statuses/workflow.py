"""Workflow lifecycle, Temporal close, and simplified Temporal status domains."""

from __future__ import annotations

from typing import Literal

WorkflowLifecycleState = Literal[
    "scheduled",
    "initializing",
    "waiting_on_dependencies",
    "planning",
    "awaiting_slot",
    "executing",
    "awaiting_external",
    "proposals",
    "finalizing",
    "no_commit",
    "completed",
    "failed",
    "canceled",
]

TemporalCloseStatus = Literal[
    "completed",
    "failed",
    "canceled",
    "terminated",
    "timed_out",
    "continued_as_new",
]

SimplifiedTemporalStatus = Literal["running", "completed", "failed", "canceled"]

WORKFLOW_LIFECYCLE_STATES = frozenset(
    {
        "scheduled",
        "initializing",
        "waiting_on_dependencies",
        "planning",
        "awaiting_slot",
        "executing",
        "awaiting_external",
        "proposals",
        "finalizing",
        "no_commit",
        "completed",
        "failed",
        "canceled",
    }
)

TERMINAL_WORKFLOW_STATES = frozenset({"no_commit", "completed", "failed", "canceled"})
ACTIVE_WORKFLOW_STATES = WORKFLOW_LIFECYCLE_STATES - TERMINAL_WORKFLOW_STATES

TEMPORAL_CLOSE_STATUSES = frozenset(
    {"completed", "failed", "canceled", "terminated", "timed_out", "continued_as_new"}
)

TEMPORAL_CLOSE_TO_SIMPLIFIED_STATUS = {
    None: "running",
    "completed": "completed",
    "canceled": "canceled",
    "failed": "failed",
    "terminated": "failed",
    "timed_out": "failed",
    "continued_as_new": "running",
}


def simplified_temporal_status(
    close_status: str | None,
) -> SimplifiedTemporalStatus:
    return TEMPORAL_CLOSE_TO_SIMPLIFIED_STATUS.get(close_status, "failed")  # type: ignore[return-value]

