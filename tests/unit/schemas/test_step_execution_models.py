from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.step_execution_models import (
    STEP_EXECUTION_CONTENT_TYPE,
    StepExecutionIdentityModel,
    StepExecutionManifestModel,
)
from moonmind.schemas.temporal_models import (
    EnvironmentDiagnosticReferenceModel,
    EvidenceRefStatusModel,
    RecoveryEligibilityDiagnosticModel,
    StepEvidenceSummaryModel,
)


def test_step_execution_identity_requires_run_scoped_positive_execution_ordinal() -> None:
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


def test_execution_contract_keeps_retry_reexecution_and_recovery_terms_distinct() -> None:
    first = StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
    )
    reexecute = StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=2,
    )
    resumed = StepExecutionManifestModel(
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

    assert first.step_execution_id != reexecute.step_execution_id
    assert resumed.execution_ordinal == 1
    assert resumed.lineage["sourceExecutionOrdinal"] == 2


def test_evidence_ref_status_distinguishes_available_missing_invalid_and_skipped() -> None:
    available = EvidenceRefStatusModel(
        category="checkpoint",
        status="available",
        boundary="before_execution",
        artifactRef="artifact://checkpoint/before",
        label="Before execution checkpoint",
    )
    missing = EvidenceRefStatusModel(
        category="checkpoint",
        status="missing",
        boundary="before_execution",
        reasonCode="missing_required_checkpoint_boundary",
    )
    invalid = EvidenceRefStatusModel(
        category="retrieval",
        status="invalid",
        artifactRef="artifact://retrieval/manifest",
        reasonCode="manifest_invalid",
    )
    skipped = EvidenceRefStatusModel(
        category="memory",
        status="skipped",
        reasonCode="memory_not_requested",
    )

    assert available.artifact_ref == "artifact://checkpoint/before"
    assert missing.artifact_ref is None
    assert invalid.reason_code == "manifest_invalid"
    assert skipped.status == "skipped"

    with pytest.raises(ValidationError):
        EvidenceRefStatusModel(category="checkpoint", status="available")

    with pytest.raises(ValidationError):
        EvidenceRefStatusModel(category="checkpoint", status="future_status")


def test_recovery_eligibility_diagnostic_is_fail_closed_and_typed() -> None:
    eligible = RecoveryEligibilityDiagnosticModel(
        eligible=True,
        defaultAction="resume_from_checkpoint",
        requiredBoundary="before_execution",
        checkpointRef="artifact://checkpoint/before",
        operatorGuidance="resume",
        evidence=[
            {
                "category": "checkpoint",
                "status": "available",
                "boundary": "before_execution",
                "artifactRef": "artifact://checkpoint/before",
            }
        ],
    )

    assert eligible.default_action == "resume_from_checkpoint"
    assert eligible.disabled_reason_code is None

    disabled = RecoveryEligibilityDiagnosticModel(
        eligible=False,
        defaultAction="full_retry",
        disabledReasonCode="missing_required_checkpoint_boundary",
        requiredBoundary="before_execution",
        operatorGuidance="full_retry",
        evidence=[
            {
                "category": "checkpoint",
                "status": "missing",
                "boundary": "before_execution",
                "reasonCode": "missing_required_checkpoint_boundary",
            }
        ],
    )

    assert disabled.disabled_reason_code == "missing_required_checkpoint_boundary"

    with pytest.raises(ValidationError):
        RecoveryEligibilityDiagnosticModel(
            eligible=True,
            defaultAction="full_retry",
            operatorGuidance="full_retry",
            checkpointRef="artifact://checkpoint/before",
        )

    with pytest.raises(ValidationError):
        RecoveryEligibilityDiagnosticModel(
            eligible=False,
            defaultAction="full_retry",
            operatorGuidance="full_retry",
        )


def test_step_evidence_summary_groups_ref_only_categories_and_diagnostics() -> None:
    summary = StepEvidenceSummaryModel(
        logicalStepId="implement",
        executionOrdinal=2,
        checkpointRefsByBoundary={
            "before_execution": {
                "category": "checkpoint",
                "status": "available",
                "boundary": "before_execution",
                "artifactRef": "artifact://checkpoint/before",
            }
        },
        contextBundleRef={
            "category": "context",
            "status": "available",
            "artifactRef": "artifact://context/bundle",
        },
        retrievalManifestRef={
            "category": "retrieval",
            "status": "skipped",
            "reasonCode": "retrieval_not_requested",
        },
        memoryManifestRef={
            "category": "memory",
            "status": "missing",
            "reasonCode": "memory_manifest_missing",
        },
        gateSummary={"verdict": "FULLY_IMPLEMENTED", "summary": "Gate passed"},
        terminalDisposition="accepted",
        sideEffectSummary={
            "status": "available",
            "artifactRefs": {"summary": "artifact://side-effects/summary"},
            "summary": "No external publish side effects.",
        },
        diagnosticRefs=[
            {
                "kind": "provider_lease",
                "status": "available",
                "diagnosticsRef": "artifact://diagnostics/provider-lease",
                "reasonCode": "provider_capacity_unavailable",
                "summary": "Provider capacity unavailable.",
            }
        ],
    )

    dumped = summary.model_dump(by_alias=True)

    assert dumped["checkpointRefsByBoundary"]["before_execution"]["artifactRef"]
    assert dumped["contextBundleRef"]["status"] == "available"
    assert dumped["retrievalManifestRef"]["status"] == "skipped"
    assert dumped["diagnosticRefs"][0]["kind"] == "provider_lease"
    assert "raw" not in json.dumps(dumped).lower()


def test_environment_diagnostic_reference_requires_typed_guidance_without_raw_payload() -> None:
    diagnostic = EnvironmentDiagnosticReferenceModel(
        kind="ghcr",
        status="available",
        diagnosticsRef="artifact://diagnostics/ghcr",
        reasonCode="ghcr_auth_failed",
        summary="GHCR authentication failed; refresh the registry credential.",
    )

    assert diagnostic.kind == "ghcr"
    assert diagnostic.diagnostics_ref == "artifact://diagnostics/ghcr"

    with pytest.raises(ValidationError):
        EnvironmentDiagnosticReferenceModel(
            kind="provider_lease",
            status="available",
            diagnosticsRef="artifact://diagnostics/provider",
            reasonCode="provider_payload",
            summary="provider payload: token=secret",
        )
