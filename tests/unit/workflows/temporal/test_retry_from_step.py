"""Retry-from-step planning over recorded run history."""

from __future__ import annotations

import pytest
from temporalio.api.enums.v1 import EventType, WorkflowTaskFailedCause
from temporalio.api.history.v1 import HistoryEvent

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.retry_from_step import (
    STEP_ID_MEMO_KEY,
    STEP_ORDER_MEMO_KEY,
    RetryFromStepUnavailable,
    accepted_retry_step,
    plan_retry_from_step,
    retry_candidates,
    retry_reset_reason,
    step_boundaries,
)

_CONVERTER = MOONMIND_TEMPORAL_DATA_CONVERTER.payload_converter


def _decode(payload):
    return _CONVERTER.from_payload(payload)


class _History:
    """Build the event shapes the run workflow records around step starts."""

    def __init__(self) -> None:
        self.events: list[HistoryEvent] = []

    def _next_id(self) -> int:
        return len(self.events) + 1

    def workflow_task(self) -> int:
        scheduled = HistoryEvent(
            event_id=self._next_id(),
            event_type=EventType.EVENT_TYPE_WORKFLOW_TASK_SCHEDULED,
        )
        self.events.append(scheduled)
        started = HistoryEvent(
            event_id=self._next_id(),
            event_type=EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED,
        )
        self.events.append(started)
        completed = HistoryEvent(
            event_id=self._next_id(),
            event_type=EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED,
        )
        completed.workflow_task_completed_event_attributes.started_event_id = (
            started.event_id
        )
        completed.workflow_task_completed_event_attributes.scheduled_event_id = (
            scheduled.event_id
        )
        self.events.append(completed)
        return completed.event_id

    def memo(self, completed_id: int, **fields) -> None:
        event = HistoryEvent(
            event_id=self._next_id(),
            event_type=EventType.EVENT_TYPE_WORKFLOW_PROPERTIES_MODIFIED,
        )
        attributes = event.workflow_properties_modified_event_attributes
        attributes.workflow_task_completed_event_id = completed_id
        for key, value in fields.items():
            attributes.upserted_memo.fields[key].CopyFrom(_CONVERTER.to_payload(value))
        self.events.append(event)


def _reset_failure(
    base_run_id: str, started_event_id: int, message: str
) -> HistoryEvent:
    event = HistoryEvent(event_type=EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED)
    attributes = event.workflow_task_failed_event_attributes
    attributes.cause = WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_RESET_WORKFLOW
    attributes.base_run_id = base_run_id
    attributes.started_event_id = started_event_id
    attributes.failure.message = message
    return event


def _rows(*statuses: str) -> list[dict]:
    return [
        {
            "logicalStepId": f"step-{order}",
            "order": order,
            "title": f"Step {order}",
            "status": status,
        }
        for order, status in enumerate(statuses, start=1)
    ]


def _three_step_failure() -> tuple[_History, dict[int, int]]:
    """Step 1 and 2 completed, step 2 waited for capacity, step 3 failed."""

    history = _History()
    history.memo(history.workflow_task(), summary="Execution initialized.")
    wt_by_order: dict[int, int] = {}
    for order in (1, 2, 3):
        wt = history.workflow_task()
        wt_by_order[order] = wt
        history.memo(
            wt,
            **{STEP_ORDER_MEMO_KEY: order, "summary": f"Executing plan step {order}/4"},
        )
        if order == 2:
            # Capacity waits repeat the same order; they are not step starts.
            history.memo(
                history.workflow_task(),
                **{STEP_ORDER_MEMO_KEY: 2, "summary": "waiting"},
            )
    history.memo(
        history.workflow_task(), **{STEP_ORDER_MEMO_KEY: 3, "summary": "failed"}
    )
    return history, wt_by_order


def test_step_boundaries_use_the_first_task_that_started_each_step() -> None:
    history, wt_by_order = _three_step_failure()

    boundaries = step_boundaries(history.events, _decode)

    assert sorted(boundaries) == [1, 2, 3]
    for order, boundary in boundaries.items():
        assert boundary.workflow_task_completed_event_id == wt_by_order[order]
        assert boundary.workflow_task_started_event_id == wt_by_order[order] - 1


def test_default_plan_restarts_the_failed_step() -> None:
    history, wt_by_order = _three_step_failure()

    plan = plan_retry_from_step(
        events=history.events,
        ledger_rows=_rows("completed", "completed", "failed", "pending"),
        decode_memo_value=_decode,
    )

    assert plan.logical_step_id == "step-3"
    assert plan.reset_event_id == wt_by_order[3]
    assert plan.replay_until_event_id == wt_by_order[3] - 1


@pytest.mark.parametrize(
    "identity_source",
    ["memo", "child_request", "child_request_alias", "child_request_field_names"],
)
def test_dynamic_steps_use_recorded_identity_when_execution_and_ledger_order_differ(
    identity_source,
):
    history = _History()
    task = history.workflow_task()
    history.memo(
        task,
        **{
            STEP_ORDER_MEMO_KEY: 3,
            **({STEP_ID_MEMO_KEY: "remediate-6"} if identity_source == "memo" else {}),
        },
    )
    child = HistoryEvent(
        event_id=history._next_id(),
        event_type=EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED,
    )
    child.start_child_workflow_execution_initiated_event_attributes.workflow_type.name = (
        "MoonMind.AgentRun"
    )
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        correlationId="test-run",
        idempotencyKey="test-attempt-6",
        stepExecution={
            "logicalStepId": "remediate-6",
            "stepExecutionId": "test-workflow:test-run:remediate-6:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
            "executionOrdinal": 1,
            "runId": "test-run",
            "workflowId": "test-workflow",
        },
    )
    if identity_source in {"child_request_alias", "child_request_field_names"}:
        request = request.model_dump(by_alias=identity_source == "child_request_alias")
    child.start_child_workflow_execution_initiated_event_attributes.input.payloads.append(
        _CONVERTER.to_payload(request)
    )
    history.events.append(child)
    rows = [
        {"logicalStepId": "earlier-verifier", "order": 3, "status": "completed"},
        {"logicalStepId": "remediate-6", "order": 6, "status": "failed"},
    ]

    plan = plan_retry_from_step(
        events=history.events,
        ledger_rows=rows,
        decode_memo_value=_decode,
        failed_step_id="remediate-6",
    )

    assert (plan.logical_step_id, plan.reset_event_id) == ("remediate-6", task)
    assert plan.order == 6
    accepted = accepted_retry_step(
        successor_events=[
            _reset_failure(
                "source-run", task - 1, retry_reset_reason("remediate-6", "retry-6")
            )
        ],
        source_events=history.events,
        ledger_rows=rows,
        source_run_id="source-run",
        request_id="retry-6",
        decode_memo_value=_decode,
    )
    assert accepted.logical_step_id == "remediate-6"


def test_operator_can_restart_from_an_earlier_started_step() -> None:
    history, wt_by_order = _three_step_failure()

    plan = plan_retry_from_step(
        events=history.events,
        ledger_rows=_rows("completed", "completed", "failed", "pending"),
        decode_memo_value=_decode,
        requested_step_id="step-2",
    )

    assert (plan.logical_step_id, plan.reset_event_id) == ("step-2", wt_by_order[2])


def test_failed_step_from_the_failure_diagnostic_wins_over_ledger_status() -> None:
    history, wt_by_order = _three_step_failure()

    plan = plan_retry_from_step(
        events=history.events,
        # A workflow-level failure can leave the ledger without a failed row.
        ledger_rows=_rows("completed", "completed", "completed", "pending"),
        decode_memo_value=_decode,
        failed_step_id="step-2",
    )

    assert plan.logical_step_id == "step-2"


def test_default_plan_restarts_the_earliest_unresolved_failure() -> None:
    history, wt_by_order = _three_step_failure()
    # A CONTINUE plan kept going after step 1 failed; a reset after step 1
    # would replay that failure and leave the retried run failed again.
    rows = _rows("failed", "completed", "failed", "pending")

    for failed_step_id in (None, "step-3"):
        plan = plan_retry_from_step(
            events=history.events,
            ledger_rows=rows,
            decode_memo_value=_decode,
            failed_step_id=failed_step_id,
        )
        assert (plan.logical_step_id, plan.reset_event_id) == ("step-1", wt_by_order[1])

    assert [
        item.logical_step_id
        for item in retry_candidates(
            rows, step_boundaries(history.events, _decode), failed_step_id="step-3"
        )
    ] == ["step-1"]


def test_without_a_failed_row_the_last_started_step_is_restarted() -> None:
    history, wt_by_order = _three_step_failure()

    plan = plan_retry_from_step(
        events=history.events,
        ledger_rows=_rows("completed", "completed", "completed", "pending"),
        decode_memo_value=_decode,
    )

    assert plan.logical_step_id == "step-3"


def test_steps_the_run_never_started_are_not_candidates() -> None:
    history, _ = _three_step_failure()
    rows = _rows("completed", "completed", "failed", "pending")

    candidates = retry_candidates(rows, step_boundaries(history.events, _decode))

    assert [item.logical_step_id for item in candidates] == [
        "step-1",
        "step-2",
        "step-3",
    ]
    with pytest.raises(RetryFromStepUnavailable) as raised:
        plan_retry_from_step(
            events=history.events,
            ledger_rows=rows,
            decode_memo_value=_decode,
            requested_step_id="step-4",
        )
    assert raised.value.reason == "step_not_retryable"


def test_a_run_that_never_started_a_step_cannot_restart_from_one() -> None:
    history = _History()
    history.memo(history.workflow_task(), summary="Planning execution strategy.")

    with pytest.raises(RetryFromStepUnavailable) as raised:
        plan_retry_from_step(
            events=history.events,
            ledger_rows=_rows("pending"),
            decode_memo_value=_decode,
        )

    assert raised.value.reason == "no_step_started"


def test_a_retry_accepted_before_its_response_was_saved_is_recognized() -> None:
    history, wt_by_order = _three_step_failure()
    rows = _rows("completed", "completed", "failed", "pending")
    started = wt_by_order[3] - 1
    successor = [
        # The failed run was itself a retry of an earlier run.
        _reset_failure("run-0", 2, retry_reset_reason("step-1", "retry-0")),
        _reset_failure("run-1", started, retry_reset_reason("step-3", "retry-1")),
    ]

    def accepted(events, *, source_run_id="run-1", request_id="retry-1"):
        return accepted_retry_step(
            successor_events=events,
            source_events=history.events,
            ledger_rows=rows,
            source_run_id=source_run_id,
            request_id=request_id,
            decode_memo_value=_decode,
        )

    step = accepted(successor)
    assert step is not None
    assert (step.logical_step_id, step.order) == ("step-3", 3)
    # Another request, another base run, or a run created by something else
    # is a changed run, never this request's result.
    assert accepted(successor, request_id="retry-2") is None
    assert accepted(successor, source_run_id="run-x") is None
    assert accepted(successor[:1]) is None
    assert accepted([]) is None
