"""MoonMind workflow lifecycle status domain values."""

from __future__ import annotations

import enum
from typing import Literal

from moonmind.statuses.close_status import TemporalExecutionCloseStatus

class MoonMindWorkflowState(str, enum.Enum):
    """Domain lifecycle states exposed through Temporal Visibility mm_state."""

    SCHEDULED = "scheduled"
    INITIALIZING = "initializing"
    WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"
    PLANNING = "planning"
    AWAITING_SLOT = "awaiting_slot"
    EXECUTING = "executing"
    AWAITING_EXTERNAL = "awaiting_external"
    PROPOSALS = "proposals"
    FINALIZING = "finalizing"
    NO_COMMIT = "no_commit"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


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

WORKFLOW_STATE_VALUES: frozenset[str] = frozenset(
    item.value for item in MoonMindWorkflowState
)
WORKFLOW_LIFECYCLE_STATES = WORKFLOW_STATE_VALUES

PRE_WORKFLOW_STATES: frozenset[MoonMindWorkflowState] = frozenset(
    {
        MoonMindWorkflowState.SCHEDULED,
        MoonMindWorkflowState.INITIALIZING,
        MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
        MoonMindWorkflowState.PLANNING,
        MoonMindWorkflowState.AWAITING_SLOT,
    }
)

TERMINAL_WORKFLOW_STATES: frozenset[MoonMindWorkflowState] = frozenset(
    {
        MoonMindWorkflowState.NO_COMMIT,
        MoonMindWorkflowState.COMPLETED,
        MoonMindWorkflowState.FAILED,
        MoonMindWorkflowState.CANCELED,
    }
)
ACTIVE_WORKFLOW_STATES = WORKFLOW_STATE_VALUES - {
    item.value for item in TERMINAL_WORKFLOW_STATES
}
TEMPORAL_CLOSE_STATUSES = frozenset(
    {"completed", "failed", "canceled", "terminated", "timed_out", "continued_as_new"}
)
TEMPORAL_CLOSE_TO_SIMPLIFIED_STATUS: dict[str | None, SimplifiedTemporalStatus] = {
    None: "running",
    "completed": "completed",
    "canceled": "canceled",
    "failed": "failed",
    "terminated": "failed",
    "timed_out": "failed",
    "continued_as_new": "running",
}

NON_TERMINAL_WORKFLOW_STATES: frozenset[MoonMindWorkflowState] = frozenset(
    {
        MoonMindWorkflowState.SCHEDULED,
        MoonMindWorkflowState.INITIALIZING,
        MoonMindWorkflowState.PLANNING,
        MoonMindWorkflowState.EXECUTING,
        MoonMindWorkflowState.AWAITING_EXTERNAL,
        MoonMindWorkflowState.FINALIZING,
    }
)

OPERATOR_SIGNAL_ALLOWED_WORKFLOW_STATES: frozenset[MoonMindWorkflowState] = frozenset(
    {
        MoonMindWorkflowState.SCHEDULED,
        MoonMindWorkflowState.INITIALIZING,
        MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
        MoonMindWorkflowState.PLANNING,
        MoonMindWorkflowState.AWAITING_SLOT,
        MoonMindWorkflowState.EXECUTING,
        MoonMindWorkflowState.PROPOSALS,
        MoonMindWorkflowState.AWAITING_EXTERNAL,
        MoonMindWorkflowState.FINALIZING,
    }
)

RUNNING_WORKFLOW_STATES: frozenset[MoonMindWorkflowState] = frozenset(
    {
        MoonMindWorkflowState.PLANNING,
        MoonMindWorkflowState.EXECUTING,
        MoonMindWorkflowState.AWAITING_EXTERNAL,
        MoonMindWorkflowState.FINALIZING,
    }
)

WORKFLOW_STATE_TO_CLOSE_STATUS: dict[
    MoonMindWorkflowState, TemporalExecutionCloseStatus
] = {
    MoonMindWorkflowState.NO_COMMIT: TemporalExecutionCloseStatus.COMPLETED,
    MoonMindWorkflowState.COMPLETED: TemporalExecutionCloseStatus.COMPLETED,
    MoonMindWorkflowState.FAILED: TemporalExecutionCloseStatus.FAILED,
    MoonMindWorkflowState.CANCELED: TemporalExecutionCloseStatus.CANCELED,
}


def coerce_workflow_state(raw: str) -> MoonMindWorkflowState:
    """Parse a canonical workflow state without accepting legacy aliases."""

    return MoonMindWorkflowState(str(raw).strip().lower())


def workflow_state_to_close_status(
    state: MoonMindWorkflowState | str,
) -> TemporalExecutionCloseStatus:
    """Return the close status for a terminal workflow lifecycle state."""

    if isinstance(state, MoonMindWorkflowState):
        workflow_state = state
    else:
        workflow_state = coerce_workflow_state(state)
    return WORKFLOW_STATE_TO_CLOSE_STATUS[workflow_state]


def simplified_temporal_status(
    close_status: str | None,
) -> SimplifiedTemporalStatus:
    return TEMPORAL_CLOSE_TO_SIMPLIFIED_STATUS.get(close_status, "failed")


__all__ = [
    "ACTIVE_WORKFLOW_STATES",
    "NON_TERMINAL_WORKFLOW_STATES",
    "OPERATOR_SIGNAL_ALLOWED_WORKFLOW_STATES",
    "PRE_WORKFLOW_STATES",
    "RUNNING_WORKFLOW_STATES",
    "SimplifiedTemporalStatus",
    "TEMPORAL_CLOSE_STATUSES",
    "TEMPORAL_CLOSE_TO_SIMPLIFIED_STATUS",
    "TERMINAL_WORKFLOW_STATES",
    "TemporalCloseStatus",
    "WORKFLOW_STATE_TO_CLOSE_STATUS",
    "WORKFLOW_STATE_VALUES",
    "WORKFLOW_LIFECYCLE_STATES",
    "WorkflowLifecycleState",
    "MoonMindWorkflowState",
    "coerce_workflow_state",
    "simplified_temporal_status",
    "workflow_state_to_close_status",
]
