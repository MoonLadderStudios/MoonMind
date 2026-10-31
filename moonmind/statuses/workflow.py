"""MoonMind workflow lifecycle status domain values."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from typing import Any, Literal, cast, get_args

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
ObjectiveOutcomeValue = Literal[
    "active",
    "succeeded",
    "failed",
    "cancelled",
    "idle",
    "verification_blocked",
    "unknown",
]


def workflow_objective_outcome(
    *,
    state: str,
    memo: Mapping[str, Any],
    finish_summary: Mapping[str, Any] | None,
) -> ObjectiveOutcomeValue | None:
    """Project the workflow objective separately from its last agent turn."""
    primary = finish_summary.get("primaryOutcome") if finish_summary else None
    if (
        state == "failed"
        and isinstance(primary, Mapping)
        and primary.get("kind") == "workflow_gate"
        and primary.get("terminalDisposition") == "failed_with_remaining_work"
    ):
        # A verifier can finish its turn successfully while rejecting the
        # enclosing objective. Its settled memo must not erase that rejection.
        return "failed"
    value = memo.get("objectiveOutcome")
    outcome = (
        cast(ObjectiveOutcomeValue, value)
        if isinstance(value, str) and value in get_args(ObjectiveOutcomeValue)
        else None
    )
    if outcome not in {None, "active"}:
        return outcome
    canonical = finish_summary.get("agentResult") if finish_summary else None
    terminal_agent = state in {
        "completed",
        "failed",
        "canceled",
        "no_commit",
    } and isinstance(canonical, Mapping)
    if state == "completed" or terminal_agent:
        context = finish_summary.get("publishContext") if finish_summary else None
        final = (
            context.get("objectiveOutcome") if isinstance(context, Mapping) else None
        )
        if (
            isinstance(final, str)
            and final not in {"active", "unknown"}
            and final in get_args(ObjectiveOutcomeValue)
        ):
            return cast(ObjectiveOutcomeValue, final)
    if terminal_agent:
        final = canonical.get("objectiveOutcome")
        if final in get_args(ObjectiveOutcomeValue) and final != "active":
            return cast(ObjectiveOutcomeValue, final)
    return outcome


def merge_terminal_result_summary(
    previous: Mapping[str, Any] | None,
    incoming: dict[str, Any],
    *,
    run_id: str | None,
) -> tuple[dict[str, Any], bool]:
    """Keep terminal facts within the existing finish-summary projection.

    Only the admitted run/Step execution order can supersede a terminal result.
    A newer observation in that same execution can add evidence, never retract
    confirmed work or replace the original failure with a reporting error.
    The second value says whether this observation may replace the primary
    terminal disposition, including its separately indexed outcome code.
    """
    old = previous or {}
    before, after = old.get("agentResult"), incoming.get("agentResult")
    prior_order, order = old.get("resultOrder"), incoming.get("resultOrder")
    if (
        isinstance(order, Mapping)
        and run_id
        and order.get("observerRunId", order.get("runId")) != run_id
    ):
        return dict(old), False
    if not isinstance(before, Mapping):
        return incoming, True
    if not isinstance(after, Mapping):
        return (
            {
                **incoming,
                **{
                    key: old[key]
                    for key in (
                        "finishOutcome",
                        "failureSummary",
                        "failure",
                        "publish",
                        "publishContext",
                    )
                    if key in old
                },
                "agentResult": dict(before),
                "resultOrder": old.get("resultOrder"),
            },
            False,
        )
    if isinstance(prior_order, Mapping):
        if not isinstance(order, Mapping):
            return dict(old), False
        if order.get("runId") != prior_order.get("runId"):
            return (
                (incoming, True) if order.get("runId") == run_id else (dict(old), False)
            )

        def position(value: Mapping[str, Any]) -> tuple[int, int, int]:
            return tuple(
                int(value.get(key) or 0)
                for key in ("stepIndex", "executionOrdinal", "revision")
            )

        try:
            prior_position, next_position = position(prior_order), position(order)
        except (TypeError, ValueError):
            return dict(old), False
        if next_position < prior_position:
            return dict(old), False
        if next_position[:2] > prior_position[:2]:
            return incoming, True  # A genuinely newer admitted Step execution.
        if order.get("logicalStepId") != prior_order.get("logicalStepId"):
            return dict(old), False
    merged = {**after}
    for key in (
        "executionState",
        "objectiveOutcome",
        "publicationState",
        "outputAvailability",
        "failureId",
        "failureClass",
        "providerErrorCode",
        "retryDisposition",
        "retryRecommendation",
        "providerFailure",
        "diagnosticsRef",
        "failureCause",
        "retryAction",
    ):
        value = before.get(key)
        if value not in (None, "unknown", "delivery_unknown") and (
            before.get("observation") == "confirmed"
            or key in {"objectiveOutcome", "publicationState", "outputAvailability"}
        ):
            merged[key] = value
    if before.get("observation") == "confirmed":
        merged["observation"] = "confirmed"
        if before.get("objectiveOutcome") in {"succeeded", "idle"}:
            # A reporting error after confirmed success is secondary, including
            # when a legacy observer incorrectly labels it as the primary error.
            for key in (
                "failureId",
                "failureClass",
                "providerErrorCode",
                "providerFailure",
                "retryDisposition",
                "retryRecommendation",
                "failureCause",
                "retryAction",
            ):
                merged[key] = before.get(key)
    merged["outputRefs"] = list(
        dict.fromkeys([*before.get("outputRefs", []), *after.get("outputRefs", [])])
    )
    merged["diagnosticRefs"] = list(
        dict.fromkeys(
            [*before.get("diagnosticRefs", []), *after.get("diagnosticRefs", [])]
        )
    )
    merged["secondaryErrors"] = list(before.get("secondaryErrors") or [])
    for error in after.get("secondaryErrors") or []:
        if error not in merged["secondaryErrors"]:
            merged["secondaryErrors"].append(error)
    summary = {**incoming, "agentResult": merged}
    replace_terminal = before.get("observation") != "confirmed"
    if not replace_terminal:
        for key in (
            "finishOutcome",
            "failureSummary",
            "failure",
            "publish",
            "publishContext",
        ):
            if key in old:
                summary[key] = old[key]
    return summary, replace_terminal


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
    "ObjectiveOutcomeValue",
    "coerce_workflow_state",
    "simplified_temporal_status",
    "workflow_objective_outcome",
    "workflow_state_to_close_status",
]
