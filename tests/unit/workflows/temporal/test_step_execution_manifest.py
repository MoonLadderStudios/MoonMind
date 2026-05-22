from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
    StepExecutionBoundaryResultModel,
    StepExecutionIdentityModel,
    StepExecutionManifestModel,
    StepExecutionSemanticOperationModel,
    build_step_execution_id,
    build_step_execution_idempotency_key,
)


def _identity() -> dict[str, object]:
    return {
        "workflowId": "workflow-1",
        "runId": "run-1",
        "logicalStepId": "implement-story",
        "executionOrdinal": 2,
    }


def test_step_execution_identity_builds_deterministic_id() -> None:
    identity = StepExecutionIdentityModel.model_validate(_identity())

    assert identity.workflow_id == "workflow-1"
    assert identity.execution_ordinal == 2
    assert build_step_execution_id(identity) == (
        "workflow-1:run-1:implement-story:execution:2"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "unbounded_reason"),
        ("status", "unknown_status"),
        ("terminalDisposition", "maybe_ok"),
    ],
)
def test_step_execution_manifest_rejects_unsupported_canonical_values(
    field: str,
    value: str,
) -> None:
    payload = {
        **_identity(),
        "schemaVersion": "v1",
        "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
        "executionScope": "run",
        "reason": "quality_gate_failed",
        "status": "failed",
        "terminalDisposition": "retryable",
        "input": {"taskInputSnapshotRef": "artifact-input"},
        "context": {"contextBundleRef": "artifact-context"},
        "workspace": {"policy": "continue_from_previous_execution"},
        "execution": {"kind": "agent_run"},
        "outputs": {"summaryRef": "artifact-summary"},
        "checks": [],
        "sideEffects": {"external": []},
        "dependencyEffects": {"invalidatedLogicalStepIds": []},
        "budget": {"executionLimit": 3},
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        StepExecutionManifestModel.model_validate(payload)


def test_step_execution_manifest_accepts_canonical_payload_and_content_type() -> None:
    manifest = StepExecutionManifestModel.model_validate(
        {
            **_identity(),
            "schemaVersion": "v1",
            "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
            "executionScope": "run",
            "lineage": {
                "sourceWorkflowId": "workflow-0",
                "sourceRunId": "run-0",
                "sourceLogicalStepId": "implement-story",
                "sourceExecutionOrdinal": 1,
                "relationship": "recover_from_failed_step",
                "lineageExecutionOrdinal": 3,
            },
            "reason": "recover_from_failed_step",
            "status": "succeeded",
            "terminalDisposition": "accepted",
            "input": {"taskInputSnapshotRef": "artifact-input"},
            "context": {"contextBundleRef": "artifact-context"},
            "workspace": {"policy": "apply_previous_execution_diff_to_clean_baseline"},
            "execution": {"kind": "agent_run", "childWorkflowId": "child-1"},
            "outputs": {"summaryRef": "artifact-summary"},
            "checks": [{"kind": "unit", "status": "passed"}],
            "sideEffects": {"git": {"disposition": "accepted"}},
            "dependencyEffects": {"invalidatedLogicalStepIds": []},
            "budget": {"executionLimit": 3, "remainingExecutions": 1},
        }
    )

    assert STEP_EXECUTION_MANIFEST_CONTENT_TYPE == (
        "application/vnd.moonmind.step-execution+json;version=1"
    )
    assert manifest.lineage is not None
    assert manifest.lineage.source_execution_ordinal == 1
    assert manifest.model_dump(by_alias=True)["terminalDisposition"] == "accepted"


def test_step_execution_manifest_accepts_compact_evidence_refs() -> None:
    manifest = StepExecutionManifestModel.model_validate(
        {
            **_identity(),
            "schemaVersion": "v1",
            "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
            "executionScope": "run",
            "reason": "runtime_recovered",
            "status": "running",
            "input": {"taskInputSnapshotRef": "artifact-input"},
            "context": {"contextBundleRef": "artifact-context"},
            "workspace": {
                "policy": "continue_from_previous_execution",
                "sourceExecutionOrdinal": {
                    "workflowId": "workflow-1",
                    "runId": "run-1",
                    "logicalStepId": "implement-story",
                    "executionOrdinal": 1,
                },
            },
            "execution": {
                "childWorkflowId": "child-1",
                "childRunId": "child-run-1",
                "diagnosticsRef": "artifact-diagnostics",
            },
            "outputs": {
                "summaryRef": "artifact-summary",
                "diffRef": "artifact-diff",
            },
            "checks": [{"kind": "unit", "status": "passed"}],
            "sideEffects": {
                "git": {
                    "disposition": "accepted",
                    "evidenceRef": "artifact-git-side-effect",
                }
            },
            "dependencyEffects": {"invalidatedLogicalStepIds": []},
            "budget": {"executionLimit": 3, "remainingExecutions": 1},
        }
    )

    serialized = manifest.model_dump(by_alias=True)
    assert serialized["execution"]["diagnosticsRef"] == "artifact-diagnostics"
    assert serialized["outputs"]["diffRef"] == "artifact-diff"
    assert serialized["sideEffects"]["git"]["evidenceRef"] == (
        "artifact-git-side-effect"
    )


def test_step_execution_manifest_rejects_large_inline_evidence() -> None:
    payload = {
        **_identity(),
        "schemaVersion": "v1",
        "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
        "executionScope": "run",
        "reason": "runtime_recovered",
        "status": "running",
        "input": {"taskInputSnapshotRef": "artifact-input"},
        "context": {"contextBundleRef": "artifact-context"},
        "workspace": {"policy": "continue_from_previous_execution"},
        "execution": {"kind": "agent_run"},
        "outputs": {"summaryRef": "artifact-summary"},
        "checks": [],
        "sideEffects": {
            "git": {
                "disposition": "accepted",
                "logText": "x" * 2000,
            }
        },
        "dependencyEffects": {"invalidatedLogicalStepIds": []},
        "budget": {"executionLimit": 3},
    }

    with pytest.raises(ValidationError, match="compact refs"):
        StepExecutionManifestModel.model_validate(payload)


def test_step_execution_manifest_rejects_large_inline_check_evidence() -> None:
    payload = {
        **_identity(),
        "schemaVersion": "v1",
        "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
        "executionScope": "run",
        "reason": "runtime_recovered",
        "status": "running",
        "input": {"taskInputSnapshotRef": "artifact-input"},
        "context": {"contextBundleRef": "artifact-context"},
        "workspace": {"policy": "continue_from_previous_execution"},
        "execution": {"kind": "agent_run"},
        "outputs": {"summaryRef": "artifact-summary"},
        "checks": [{"kind": "unit", "logText": "x" * 2000}],
        "sideEffects": {},
        "dependencyEffects": {"invalidatedLogicalStepIds": []},
        "budget": {"executionLimit": 3},
    }

    with pytest.raises(ValidationError, match="compact refs"):
        StepExecutionManifestModel.model_validate(payload)


def test_step_execution_idempotency_key_uses_execution_identity_and_operation() -> None:
    identity = StepExecutionIdentityModel.model_validate(_identity())

    key = build_step_execution_idempotency_key(identity, "manifest")

    assert key == "workflow-1:run-1:implement-story:2:manifest"
    assert build_step_execution_idempotency_key(identity, "gate:unit") != key
    changed_attempt = identity.model_copy(update={"execution_ordinal": 3})
    assert build_step_execution_idempotency_key(changed_attempt, "manifest") != key


def test_step_execution_boundary_result_is_compact_and_typed() -> None:
    result = StepExecutionBoundaryResultModel.model_validate(
        {
            "identity": _identity(),
            "manifestArtifactRef": "artifact-step-execution-2",
            "idempotencyKey": "workflow-1:run-1:implement-story:2:manifest",
            "summary": "Execution manifest created.",
        }
    )

    serialized = result.model_dump(by_alias=True)
    assert serialized["manifestArtifactRef"] == "artifact-step-execution-2"
    assert "manifest" not in serialized
    assert "payload" not in serialized


def test_step_execution_semantic_operation_keeps_retry_reexecution_recover_distinct() -> None:
    retry = StepExecutionSemanticOperationModel.model_validate({"kind": "retry"})
    reexecution = StepExecutionSemanticOperationModel.model_validate({"kind": "reexecution"})
    recover = StepExecutionSemanticOperationModel.model_validate({"kind": "recover"})

    assert {retry.kind, reexecution.kind, recover.kind} == {
        "retry",
        "reexecution",
        "recover",
    }
