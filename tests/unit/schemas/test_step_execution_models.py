from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.step_execution_models import (
    STEP_EXECUTION_CONTENT_TYPE,
    StepExecutionIdentityModel,
    StepExecutionManifestModel,
)


def test_step_execution_identity_requires_run_scoped_positive_attempt() -> None:
    identity = StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=2,
    )

    assert identity.step_execution_id == "wf-1:run-1:implement:execution:2"

    with pytest.raises(ValidationError):
        StepExecutionIdentityModel(
            workflowId="wf-1",
            runId="run-1",
            logicalStepId="implement",
            executionOrdinal=0,
        )

    with pytest.raises(ValidationError):
        StepExecutionIdentityModel(
            workflowId=" ",
            runId="run-1",
            logicalStepId="implement",
            executionOrdinal=1,
        )


def test_manifest_serializes_versioned_artifact_contract() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    manifest = StepExecutionManifestModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
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
    assert payload["contentType"] == STEP_EXECUTION_CONTENT_TYPE
    assert payload["stepExecutionId"] == "wf-1:run-1:implement:execution:1"
    assert payload["executionScope"] == "run"
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
        "executionOrdinal": 1,
        "reason": "initial_execution",
        "status": "running",
        "startedAt": now,
        "updatedAt": now,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        StepExecutionManifestModel(**payload)


def test_execution_contract_keeps_retry_reexecution_and_recover_terms_distinct() -> None:
    first = StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
    )
    reexecution = StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=2,
    )
    recoverd = StepExecutionManifestModel(
        workflowId="wf-2",
        runId="run-2",
        logicalStepId="implement",
        executionOrdinal=1,
        reason="recover_from_failed_step",
        status="running",
        startedAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        updatedAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        lineage={
            "sourceWorkflowId": "wf-1",
            "sourceRunId": "run-1",
            "sourceLogicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "lineageExecutionOrdinal": 3,
        },
    )

    assert first.step_execution_id != reexecution.step_execution_id
    assert recoverd.execution_ordinal == 1
    assert recoverd.lineage["sourceExecutionOrdinal"] == 2
