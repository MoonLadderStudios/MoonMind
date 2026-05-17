from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    STEP_ATTEMPT_MANIFEST_CONTENT_TYPE,
    StepAttemptBoundaryResultModel,
    StepAttemptIdentityModel,
    StepAttemptManifestModel,
    StepAttemptSemanticOperationModel,
    build_step_attempt_id,
    build_step_attempt_idempotency_key,
)


def _identity() -> dict[str, object]:
    return {
        "workflowId": "workflow-1",
        "runId": "run-1",
        "logicalStepId": "implement-story",
        "attempt": 2,
    }


def test_step_attempt_identity_builds_deterministic_id() -> None:
    identity = StepAttemptIdentityModel.model_validate(_identity())

    assert identity.workflow_id == "workflow-1"
    assert identity.attempt == 2
    assert build_step_attempt_id(identity) == (
        "workflow-1:run-1:implement-story:attempt:2"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "unbounded_reason"),
        ("status", "unknown_status"),
        ("terminalDisposition", "maybe_ok"),
    ],
)
def test_step_attempt_manifest_rejects_unsupported_canonical_values(
    field: str,
    value: str,
) -> None:
    payload = {
        **_identity(),
        "schemaVersion": "v1",
        "stepAttemptId": "workflow-1:run-1:implement-story:attempt:2",
        "attemptScope": "run",
        "reason": "quality_gate_failed",
        "status": "failed",
        "terminalDisposition": "retryable",
        "input": {"taskInputSnapshotRef": "artifact-input"},
        "context": {"contextBundleRef": "artifact-context"},
        "workspace": {"policy": "continue_from_previous_attempt"},
        "execution": {"kind": "agent_run"},
        "outputs": {"summaryRef": "artifact-summary"},
        "checks": [],
        "sideEffects": {"external": []},
        "dependencyEffects": {"invalidatedLogicalStepIds": []},
        "budget": {"attemptLimit": 3},
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        StepAttemptManifestModel.model_validate(payload)


def test_step_attempt_manifest_accepts_canonical_payload_and_content_type() -> None:
    manifest = StepAttemptManifestModel.model_validate(
        {
            **_identity(),
            "schemaVersion": "v1",
            "stepAttemptId": "workflow-1:run-1:implement-story:attempt:2",
            "attemptScope": "run",
            "lineage": {
                "sourceWorkflowId": "workflow-0",
                "sourceRunId": "run-0",
                "sourceLogicalStepId": "implement-story",
                "sourceAttempt": 1,
                "relationship": "resume_from_failed_step",
                "lineageAttemptOrdinal": 3,
            },
            "reason": "resume_from_failed_step",
            "status": "succeeded",
            "terminalDisposition": "accepted",
            "input": {"taskInputSnapshotRef": "artifact-input"},
            "context": {"contextBundleRef": "artifact-context"},
            "workspace": {"policy": "apply_previous_diff_to_clean_baseline"},
            "execution": {"kind": "agent_run", "childWorkflowId": "child-1"},
            "outputs": {"summaryRef": "artifact-summary"},
            "checks": [{"kind": "unit", "status": "passed"}],
            "sideEffects": {"git": {"disposition": "accepted"}},
            "dependencyEffects": {"invalidatedLogicalStepIds": []},
            "budget": {"attemptLimit": 3, "remainingAttempts": 1},
        }
    )

    assert STEP_ATTEMPT_MANIFEST_CONTENT_TYPE == (
        "application/vnd.moonmind.step-attempt+json;version=1"
    )
    assert manifest.lineage is not None
    assert manifest.lineage.source_attempt == 1
    assert manifest.model_dump(by_alias=True)["terminalDisposition"] == "accepted"


def test_step_attempt_manifest_accepts_compact_evidence_refs() -> None:
    manifest = StepAttemptManifestModel.model_validate(
        {
            **_identity(),
            "schemaVersion": "v1",
            "stepAttemptId": "workflow-1:run-1:implement-story:attempt:2",
            "attemptScope": "run",
            "reason": "runtime_recovered",
            "status": "running",
            "input": {"taskInputSnapshotRef": "artifact-input"},
            "context": {"contextBundleRef": "artifact-context"},
            "workspace": {
                "policy": "continue_from_previous_attempt",
                "sourceAttempt": {
                    "workflowId": "workflow-1",
                    "runId": "run-1",
                    "logicalStepId": "implement-story",
                    "attempt": 1,
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
            "budget": {"attemptLimit": 3, "remainingAttempts": 1},
        }
    )

    serialized = manifest.model_dump(by_alias=True)
    assert serialized["execution"]["diagnosticsRef"] == "artifact-diagnostics"
    assert serialized["outputs"]["diffRef"] == "artifact-diff"
    assert serialized["sideEffects"]["git"]["evidenceRef"] == (
        "artifact-git-side-effect"
    )


def test_step_attempt_manifest_rejects_large_inline_evidence() -> None:
    payload = {
        **_identity(),
        "schemaVersion": "v1",
        "stepAttemptId": "workflow-1:run-1:implement-story:attempt:2",
        "attemptScope": "run",
        "reason": "runtime_recovered",
        "status": "running",
        "input": {"taskInputSnapshotRef": "artifact-input"},
        "context": {"contextBundleRef": "artifact-context"},
        "workspace": {"policy": "continue_from_previous_attempt"},
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
        "budget": {"attemptLimit": 3},
    }

    with pytest.raises(ValidationError, match="compact refs"):
        StepAttemptManifestModel.model_validate(payload)


def test_step_attempt_idempotency_key_uses_attempt_identity_and_operation() -> None:
    identity = StepAttemptIdentityModel.model_validate(_identity())

    key = build_step_attempt_idempotency_key(identity, "manifest")

    assert key == "workflow-1:run-1:implement-story:2:manifest"
    assert build_step_attempt_idempotency_key(identity, "gate:unit") != key
    changed_attempt = identity.model_copy(update={"attempt": 3})
    assert build_step_attempt_idempotency_key(changed_attempt, "manifest") != key


def test_step_attempt_boundary_result_is_compact_and_typed() -> None:
    result = StepAttemptBoundaryResultModel.model_validate(
        {
            "identity": _identity(),
            "manifestArtifactRef": "artifact-step-attempt-2",
            "idempotencyKey": "workflow-1:run-1:implement-story:2:manifest",
            "summary": "Attempt manifest created.",
        }
    )

    serialized = result.model_dump(by_alias=True)
    assert serialized["manifestArtifactRef"] == "artifact-step-attempt-2"
    assert "manifest" not in serialized
    assert "payload" not in serialized


def test_step_attempt_semantic_operation_keeps_retry_reattempt_resume_distinct() -> None:
    retry = StepAttemptSemanticOperationModel.model_validate({"kind": "retry"})
    reattempt = StepAttemptSemanticOperationModel.model_validate({"kind": "reattempt"})
    resume = StepAttemptSemanticOperationModel.model_validate({"kind": "resume"})

    assert {retry.kind, reattempt.kind, resume.kind} == {
        "retry",
        "reattempt",
        "resume",
    }
