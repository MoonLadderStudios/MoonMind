from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.step_attempt_models import (
    STEP_ATTEMPT_CONTENT_TYPE,
    StepAttemptIdentityModel,
    StepAttemptManifestModel,
)


def test_step_attempt_identity_requires_run_scoped_positive_attempt() -> None:
    identity = StepAttemptIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        attempt=2,
    )

    assert identity.step_attempt_id == "wf-1:run-1:implement:attempt:2"

    with pytest.raises(ValidationError):
        StepAttemptIdentityModel(
            workflowId="wf-1",
            runId="run-1",
            logicalStepId="implement",
            attempt=0,
        )

    with pytest.raises(ValidationError):
        StepAttemptIdentityModel(
            workflowId=" ",
            runId="run-1",
            logicalStepId="implement",
            attempt=1,
        )


def test_manifest_serializes_versioned_artifact_contract() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    manifest = StepAttemptManifestModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        attempt=1,
        reason="initial_execution",
        status="running",
        startedAt=now,
        updatedAt=now,
        input={"preparedInputRefs": ["artifact://prepared"]},
        context={"contextBundleRef": "artifact://context"},
        execution={"kind": "skill"},
    )

    payload = manifest.model_dump(by_alias=True)

    assert payload["schemaVersion"] == "v1"
    assert payload["contentType"] == STEP_ATTEMPT_CONTENT_TYPE
    assert payload["stepAttemptId"] == "wf-1:run-1:implement:attempt:1"
    assert payload["attemptScope"] == "run"
    assert payload["terminalDisposition"] is None
    assert payload["input"] == {"preparedInputRefs": ["artifact://prepared"]}
    assert payload["execution"] == {"kind": "skill"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "workflow_retry"),
        ("status", "unknown"),
        ("terminalDisposition", "maybe_ok"),
    ],
)
def test_manifest_rejects_unsupported_reason_status_and_disposition(
    field: str,
    value: str,
) -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    payload = {
        "workflowId": "wf-1",
        "runId": "run-1",
        "logicalStepId": "implement",
        "attempt": 1,
        "reason": "initial_execution",
        "status": "running",
        "startedAt": now,
        "updatedAt": now,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        StepAttemptManifestModel(**payload)


def test_attempt_contract_keeps_retry_reattempt_and_resume_terms_distinct() -> None:
    first = StepAttemptIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        attempt=1,
    )
    reattempt = StepAttemptIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        attempt=2,
    )
    resumed = StepAttemptManifestModel(
        workflowId="wf-2",
        runId="run-2",
        logicalStepId="implement",
        attempt=1,
        reason="resume_from_failed_step",
        status="running",
        startedAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        updatedAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        lineage={
            "sourceWorkflowId": "wf-1",
            "sourceRunId": "run-1",
            "sourceLogicalStepId": "implement",
            "sourceAttempt": 2,
            "lineageAttemptOrdinal": 3,
        },
    )

    assert first.step_attempt_id != reattempt.step_attempt_id
    assert resumed.attempt == 1
    assert resumed.lineage["sourceAttempt"] == 2
