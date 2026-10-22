"""Plan a retry of a closed ``MoonMind.UserWorkflow`` run from one of its steps.

A retry resets the run's Temporal history to the workflow task that started
the chosen step. Temporal replays every earlier event, so completed steps,
their results and the workflow state they produced are kept exactly as
recorded; the chosen step and everything after it run again on the installed
MoonMind code. Saved Omnigent workspaces reach the retried step through the
same restore references the original attempt used.

The run workflow upserts ``mm_current_step_order`` into its memo in the
workflow task that starts each plan step, so the first memo change to an order
marks that step's start in history. Planning is pure: callers supply the
history events and the step ledger rows. The replay check runs the installed
workflow code over the kept history before anything is reset.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from temporalio.api.enums.v1 import EventType, WorkflowTaskFailedCause
from temporalio.api.history.v1 import HistoryEvent
from temporalio.client import WorkflowHistory

STEP_ORDER_MEMO_KEY = "mm_current_step_order"
STEP_ID_MEMO_KEY = "mm_current_step_id"

RETRYABLE_CLOSE_STATES = frozenset({"failed", "canceled", "terminated", "timed_out"})


class RetryFromStepUnavailable(ValueError):
    """A retry cannot be planned; ``reason`` is a stable, user-facing code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class StepBoundary:
    """The workflow task that first started one plan step."""

    order: int
    workflow_task_completed_event_id: int
    workflow_task_started_event_id: int
    logical_step_id: str | None = None


@dataclass(frozen=True)
class RetryCandidate:
    """A step the run started, so a retry can restart from it."""

    logical_step_id: str
    order: int
    title: str | None
    status: str | None


@dataclass(frozen=True)
class RetryFromStepPlan:
    """Where to reset the run and what a replay pre-check must accept."""

    logical_step_id: str
    order: int
    title: str | None
    reset_event_id: int
    replay_until_event_id: int


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def step_boundaries(
    events: Iterable[HistoryEvent],
    decode_memo_value: Callable[[Any], Any],
    *,
    ledger_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[int, StepBoundary]:
    """Bind recorded starts to logical steps, including older dynamic plans.

    Execution position can differ from ledger order after loop materialization.
    New histories name the step in memo; retained histories carry its trusted
    identity in the AgentRun child request recorded after that start.
    """

    started_by_completed: dict[int, int] = {}
    boundaries: dict[int, StepBoundary] = {}
    current_order: int | None = None
    for event in events:
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED:
            attributes = event.workflow_task_completed_event_attributes
            started_by_completed[event.event_id] = int(attributes.started_event_id)
            continue
        if (
            event.event_type
            == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        ):
            child = event.start_child_workflow_execution_initiated_event_attributes
            if (
                child.workflow_type.name == "MoonMind.AgentRun"
                and current_order in boundaries
            ):
                for payload in child.input.payloads:
                    try:
                        request = decode_memo_value(payload)
                    except Exception:
                        continue
                    metadata = (
                        request.get("step_execution") or request.get("stepExecution")
                        if isinstance(request, Mapping)
                        else None
                    )
                    identity = (
                        _text(
                            metadata.get("logical_step_id")
                            or metadata.get("logicalStepId")
                        )
                        if isinstance(metadata, Mapping)
                        else None
                    )
                    if identity:
                        boundary = boundaries[current_order]
                        if (
                            boundary.logical_step_id
                            and boundary.logical_step_id != identity
                        ):
                            raise RetryFromStepUnavailable(
                                "history_ambiguous",
                                "The recorded step identities conflict; this run cannot safely restart from a step.",
                            )
                        boundaries[current_order] = replace(
                            boundary, logical_step_id=identity
                        )
            continue
        if event.event_type != EventType.EVENT_TYPE_WORKFLOW_PROPERTIES_MODIFIED:
            continue
        attributes = event.workflow_properties_modified_event_attributes
        payload = attributes.upserted_memo.fields.get(STEP_ORDER_MEMO_KEY)
        if payload is None:
            continue
        try:
            order = _positive_int(decode_memo_value(payload))
        except Exception:
            order = None
        if order is None or order == current_order:
            continue
        current_order = order
        if order in boundaries:
            continue
        completed_id = int(attributes.workflow_task_completed_event_id)
        started_id = started_by_completed.get(completed_id)
        if started_id is None:
            continue
        boundaries[order] = StepBoundary(
            order=order,
            workflow_task_completed_event_id=completed_id,
            workflow_task_started_event_id=started_id,
            logical_step_id=(
                _text(
                    decode_memo_value(attributes.upserted_memo.fields[STEP_ID_MEMO_KEY])
                )
                if STEP_ID_MEMO_KEY in attributes.upserted_memo.fields
                else None
            ),
        )
    if ledger_rows:
        orders = {
            str(row.get("logicalStepId")): order
            for order, row in _ledger_rows_by_order(ledger_rows).items()
        }
        return {
            orders.get(boundary.logical_step_id, order): replace(
                boundary, order=orders.get(boundary.logical_step_id, order)
            )
            for order, boundary in boundaries.items()
            if not boundary.logical_step_id or boundary.logical_step_id in orders
        }
    return boundaries


def _ledger_rows_by_order(
    ledger_rows: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    rows: dict[int, Mapping[str, Any]] = {}
    for row in ledger_rows:
        if not isinstance(row, Mapping):
            continue
        order = _positive_int(row.get("order"))
        if order is not None and str(row.get("logicalStepId") or "").strip():
            rows.setdefault(order, row)
    return rows


def _text(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate or None


def default_retry_order(
    ledger_rows: Sequence[Mapping[str, Any]],
    boundaries: Mapping[int, StepBoundary],
    *,
    failed_step_id: str | None = None,
) -> int | None:
    """Return the step a retry should start from by default.

    That is the earliest failed step: the one the run records, or a failed
    ledger row before it. A plan that continued past an earlier failure keeps
    that failure in any history reset after it, so the retried run would fail
    again. Without a failure, it is the last step the run started.
    """

    rows_by_order = _ledger_rows_by_order(ledger_rows)
    failed_orders = [
        order
        for order, row in rows_by_order.items()
        if order in boundaries
        and (
            str(row.get("status") or "").strip().lower() == "failed"
            or (failed_step_id and row.get("logicalStepId") == failed_step_id)
        )
    ]
    if failed_orders:
        return min(failed_orders)
    started = [order for order in boundaries if order in rows_by_order]
    return max(started) if started else None


def retry_candidates(
    ledger_rows: Sequence[Mapping[str, Any]],
    boundaries: Mapping[int, StepBoundary],
    *,
    failed_step_id: str | None = None,
) -> list[RetryCandidate]:
    """Return the steps a retry may start from, earliest first.

    A step qualifies when the run started it and it is not later than the
    default step; steps the run never reached have no recorded start.
    """

    default_order = default_retry_order(
        ledger_rows, boundaries, failed_step_id=failed_step_id
    )
    if default_order is None:
        return []
    rows_by_order = _ledger_rows_by_order(ledger_rows)
    candidates: list[RetryCandidate] = []
    for order in sorted(boundaries):
        row = rows_by_order.get(order)
        if row is None or order > default_order:
            continue
        candidates.append(
            RetryCandidate(
                logical_step_id=str(row.get("logicalStepId")).strip(),
                order=order,
                title=_text(row.get("title")),
                status=_text(row.get("status")),
            )
        )
    return candidates


def plan_retry_from_step(
    *,
    events: Iterable[HistoryEvent],
    ledger_rows: Sequence[Mapping[str, Any]],
    decode_memo_value: Callable[[Any], Any],
    requested_step_id: str | None = None,
    failed_step_id: str | None = None,
) -> RetryFromStepPlan:
    """Choose the reset point for a retry, or explain why there is none."""

    boundaries = step_boundaries(events, decode_memo_value, ledger_rows=ledger_rows)
    candidates = retry_candidates(
        ledger_rows, boundaries, failed_step_id=failed_step_id
    )
    if not candidates:
        raise RetryFromStepUnavailable(
            "no_step_started",
            "The run failed before it started a plan step; rerun it instead.",
        )
    requested = _text(requested_step_id)
    if requested is None:
        chosen = candidates[-1]
    else:
        chosen = next(
            (item for item in candidates if item.logical_step_id == requested),
            None,
        )
        if chosen is None:
            raise RetryFromStepUnavailable(
                "step_not_retryable",
                f"Step {requested} did not start in this run before it stopped, "
                "so the run cannot restart from it.",
            )
    boundary = boundaries[chosen.order]
    return RetryFromStepPlan(
        logical_step_id=chosen.logical_step_id,
        order=chosen.order,
        title=chosen.title,
        reset_event_id=boundary.workflow_task_completed_event_id,
        replay_until_event_id=boundary.workflow_task_started_event_id,
    )


def retry_reset_reason(logical_step_id: str, request_id: str) -> str:
    """The reset reason Temporal records; it names the request that made it."""

    reason = f"MoonMind retry from step {logical_step_id}"
    return f"{reason} (request {request_id})" if request_id else reason


def accepted_retry_step(
    *,
    successor_events: Iterable[HistoryEvent],
    source_events: Iterable[HistoryEvent],
    ledger_rows: Sequence[Mapping[str, Any]],
    source_run_id: str,
    request_id: str,
    decode_memo_value: Callable[[Any], Any],
) -> RetryCandidate | None:
    """Return the step a successor run restarted for this request, if it did.

    A reset run begins by failing the workflow task at the reset point, naming
    the base run and the reason. A retry Temporal accepted before MoonMind
    saved its response is recognized by that event, so the same request can
    return the run instead of reporting the run as changed.
    """

    if not request_id:
        return None
    reset_task = None
    for event in successor_events:
        # The kept prefix can hold the resets that created earlier runs; the
        # last one created this run.
        if (
            event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED
            and event.workflow_task_failed_event_attributes.cause
            == WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_RESET_WORKFLOW
        ):
            reset_task = event.workflow_task_failed_event_attributes
    if reset_task is None or reset_task.base_run_id != source_run_id:
        return None
    rows_by_order = _ledger_rows_by_order(ledger_rows)
    for order, boundary in step_boundaries(
        source_events, decode_memo_value, ledger_rows=ledger_rows
    ).items():
        row = rows_by_order.get(order)
        if row is None:
            continue
        logical_step_id = str(row.get("logicalStepId")).strip()
        if boundary.workflow_task_started_event_id == int(
            reset_task.started_event_id
        ) and reset_task.failure.message == retry_reset_reason(
            logical_step_id, request_id
        ):
            return RetryCandidate(
                logical_step_id=logical_step_id,
                order=order,
                title=_text(row.get("title")),
                status=_text(row.get("status")),
            )
    return None


def _replay_workflows() -> list[type]:
    """The installed workflow classes a retried run will execute."""

    from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

    return [MoonMindUserWorkflow]


async def replay_failure_before_reset(
    history: WorkflowHistory,
    *,
    until_event_id: int,
) -> str | None:
    """Replay the history a retry keeps on the installed workflow code.

    Returns ``None`` when the kept prefix replays deterministically, otherwise
    the replay failure. A reset run whose prefix the installed code cannot
    replay would stall on its first workflow task instead of retrying.
    """

    from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )

    prefix = WorkflowHistory(
        history.workflow_id,
        [event for event in history.events if event.event_id <= until_event_id],
    )
    result = await Replayer(
        workflows=_replay_workflows(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(prefix, raise_on_replay_failure=False)
    if result.replay_failure is None:
        return None
    return str(result.replay_failure)[:1000]


__all__ = [
    "RETRYABLE_CLOSE_STATES",
    "STEP_ORDER_MEMO_KEY",
    "RetryCandidate",
    "RetryFromStepPlan",
    "RetryFromStepUnavailable",
    "StepBoundary",
    "accepted_retry_step",
    "default_retry_order",
    "plan_retry_from_step",
    "replay_failure_before_reset",
    "retry_candidates",
    "retry_reset_reason",
    "step_boundaries",
]
