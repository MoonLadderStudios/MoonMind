"""MoonMind workflow lifecycle status domain values."""

from __future__ import annotations

import enum

from moonmind.statuses.close_status import TemporalExecutionCloseStatus
from moonmind.statuses.compat import normalize_workflow_state_alias


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


WORKFLOW_STATE_VALUES: frozenset[str] = frozenset(
    item.value for item in MoonMindWorkflowState
)

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
    """Parse a canonical workflow state, accepting only explicit compat aliases."""

    return MoonMindWorkflowState(normalize_workflow_state_alias(raw))


def workflow_state_to_close_status(
    state: MoonMindWorkflowState | str,
) -> TemporalExecutionCloseStatus:
    """Return the close status for a terminal workflow lifecycle state."""

    if isinstance(state, MoonMindWorkflowState):
        workflow_state = state
    else:
        workflow_state = coerce_workflow_state(state)
    return WORKFLOW_STATE_TO_CLOSE_STATUS[workflow_state]


__all__ = [
    "NON_TERMINAL_WORKFLOW_STATES",
    "OPERATOR_SIGNAL_ALLOWED_WORKFLOW_STATES",
    "PRE_WORKFLOW_STATES",
    "RUNNING_WORKFLOW_STATES",
    "TERMINAL_WORKFLOW_STATES",
    "WORKFLOW_STATE_TO_CLOSE_STATUS",
    "WORKFLOW_STATE_VALUES",
    "MoonMindWorkflowState",
    "coerce_workflow_state",
    "workflow_state_to_close_status",
]
