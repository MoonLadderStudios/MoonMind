from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.step_execution_models import (
    STEP_EXECUTION_CONTENT_TYPE,
    MemoryApplicationResultManifest,
    MemoryPolicyDecisionManifest,
    MemoryProposalManifest,
    MemorySideEffectSummary,
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


def _source() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
    )


@pytest.mark.parametrize(
    "state",
    [
        "proposed",
        "accepted_for_run_context",
        "applied_to_repo",
        "rejected",
        "superseded",
    ],
)
def test_memory_effect_state_accepts_exact_allowed_values(state: str) -> None:
    proposal = MemoryProposalManifest(
        proposalId="proposal-1",
        source=_source(),
        target="memory://run",
        state=state,
        kind="codebase_pattern",
        reason="candidate_codebase_pattern",
        contentRef="artifact://memory/proposal-1",
        evidenceRefs=["artifact://evidence/1"],
        createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )

    assert proposal.state == state


@pytest.mark.parametrize("state", ["", "PROPOSED", "unknown", " applied_to_repo "])
def test_memory_effect_state_rejects_blank_unknown_or_differently_cased_values(
    state: str,
) -> None:
    with pytest.raises(ValidationError):
        MemoryProposalManifest(
            proposalId="proposal-1",
            source=_source(),
            target="memory://run",
            state=state,
            kind="codebase_pattern",
            reason="candidate_codebase_pattern",
            contentRef="artifact://memory/proposal-1",
            evidenceRefs=["artifact://evidence/1"],
            createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )


def test_memory_proposal_manifest_requires_source_refs_and_supersession_linkage() -> None:
    replacement = MemoryProposalManifest(
        proposalId="proposal-2",
        source=_source(),
        target="memory://run",
        state="superseded",
        kind="operator_instruction",
        reason="replacement_available",
        contentRef="artifact://memory/proposal-2",
        evidenceRefs=["artifact://evidence/replacement"],
        supersedesProposalRef="artifact://memory/proposal-1",
        createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )

    assert replacement.supersedes_proposal_ref == "artifact://memory/proposal-1"

    with pytest.raises(ValidationError):
        MemoryProposalManifest(
            proposalId="proposal-3",
            source=_source(),
            target="memory://run",
            state="proposed",
            kind="operator_instruction",
            reason="missing_content_ref",
            contentRef=" ",
            evidenceRefs=["artifact://evidence/1"],
            createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )


def test_memory_policy_decision_and_application_results_fail_closed() -> None:
    blocked = MemoryPolicyDecisionManifest(
        decisionId="decision-1",
        proposalRef="artifact://memory/proposal-1",
        source=_source(),
        target="repo://AGENTS.md",
        reason="terminal_disposition_not_accepted",
        decision="blocked",
        decisionRef="artifact://memory/decision-1",
        evidenceRefs=["artifact://gate/disposition"],
        gateStatus={"terminalDisposition": "failed", "publicationGate": False},
        createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )
    result = MemoryApplicationResultManifest(
        applicationId="application-1",
        proposalRef="artifact://memory/proposal-1",
        decisionRef=blocked.decision_ref,
        source=_source(),
        target="repo://AGENTS.md",
        outcome="blocked",
        failureReason="terminal_disposition_not_accepted",
        createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    )

    assert blocked.decision == "blocked"
    assert result.outcome == "blocked"

    with pytest.raises(ValidationError):
        MemoryPolicyDecisionManifest(
            decisionId="decision-2",
            proposalRef="artifact://memory/proposal-1",
            source=_source(),
            target="repo://AGENTS.md",
            reason="unknown_policy",
            decision="allow_maybe",
            decisionRef="artifact://memory/decision-2",
            evidenceRefs=["artifact://gate/disposition"],
            gateStatus={"terminalDisposition": "accepted"},
            createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )

    with pytest.raises(ValidationError):
        MemoryApplicationResultManifest(
            applicationId="application-2",
            proposalRef="artifact://memory/proposal-1",
            decisionRef="artifact://memory/decision-2",
            source=_source(),
            target="repo://AGENTS.md",
            outcome="applied",
            createdAt=datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"reason": "diff --git a/file b/file"},
        {"reason": "raw stdout from provider"},
        {"reason": "token=secret-value"},
        {"inlineContent": "provider payload"},
    ],
)
def test_memory_manifests_reject_unsafe_inline_content(payload: dict[str, str]) -> None:
    base = {
        "proposalId": "proposal-1",
        "source": _source(),
        "target": "memory://run",
        "state": "proposed",
        "kind": "codebase_pattern",
        "reason": "candidate_codebase_pattern",
        "contentRef": "artifact://memory/proposal-1",
        "evidenceRefs": ["artifact://evidence/1"],
        "createdAt": datetime(2026, 5, 17, 12, 0, tzinfo=UTC),
    }

    with pytest.raises(ValidationError):
        MemoryProposalManifest(**{**base, **payload})


def test_memory_side_effect_summary_is_ref_only_and_source_identified() -> None:
    summary = MemorySideEffectSummary(
        state="accepted_for_run_context",
        target="memory://run",
        reason="policy_approved_for_later_attempts",
        proposalRef="artifact://memory/proposal-1",
        decisionRef="artifact://memory/decision-1",
        applicationResultRef="artifact://memory/application-1",
        source=_source(),
    )

    assert summary.source.logical_step_id == "implement"

    with pytest.raises(ValidationError):
        MemorySideEffectSummary(
            state="accepted_for_run_context",
            target="memory://run",
            reason="provider payload should not inline",
            proposalRef="artifact://memory/proposal-1",
            decisionRef="artifact://memory/decision-1",
            applicationResultRef="artifact://memory/application-1",
            source=_source(),
        )


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
