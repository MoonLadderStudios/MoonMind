from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.step_attempt_models import STEP_ATTEMPT_CONTENT_TYPE
from moonmind.workflows.temporal.step_attempts import (
    build_step_attempt_manifest_payload,
    step_attempt_id,
    step_attempt_operation_idempotency_key,
)


def test_step_attempt_id_uses_run_scoped_identity() -> None:
    assert (
        step_attempt_id(
            workflow_id="wf-1",
            run_id="run-1",
            logical_step_id="implement",
            attempt=2,
        )
        == "wf-1:run-1:implement:attempt:2"
    )


def test_operation_idempotency_key_includes_attempt_and_operation() -> None:
    key = step_attempt_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=2,
        operation="execute",
    )

    assert key == "wf-1:run-1:implement:attempt:2:execute"
    assert key != step_attempt_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=3,
        operation="execute",
    )
    assert key != step_attempt_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=2,
        operation="manifest_write",
    )


def test_manifest_payload_is_compact_boundary_contract() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    payload = build_step_attempt_manifest_payload(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=1,
        reason="initial_execution",
        status="running",
        updated_at=now,
        summary="Executing plan step",
        execution={
            "kind": "skill",
            "idempotencyKey": "wf-1:run-1:implement:attempt:1:execute",
        },
        input_refs=["artifact://input"],
    )

    assert payload["contentType"] == STEP_ATTEMPT_CONTENT_TYPE
    assert payload["stepAttemptId"] == "wf-1:run-1:implement:attempt:1"
    assert payload["input"] == {"preparedInputRefs": ["artifact://input"]}
    assert payload["execution"]["kind"] == "skill"
    assert payload["outputs"] == {"summary": "Executing plan step"}
