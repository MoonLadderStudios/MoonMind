"""Contract tests for the incident reconstruction manifest (MM-884)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.incident_reconstruction_models import (
    INCIDENT_EVIDENCE_KINDS,
    INCIDENT_RECONSTRUCTION_CONTENT_TYPE,
    IncidentCostAttributionModel,
    IncidentEvidenceItemModel,
    IncidentProviderContextModel,
    IncidentReconstructionManifestModel,
    IncidentTraceContextModel,
    IncidentTraceRefModel,
    IncidentTraceSpanModel,
)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _trace() -> IncidentTraceContextModel:
    return IncidentTraceContextModel(
        traceId="trace-abc",
        workflowId="wf-1",
        runId="run-1",
    )


def _full_evidence() -> list[IncidentEvidenceItemModel]:
    return [
        IncidentEvidenceItemModel(kind=kind, present=False, reasonCode=f"no_{kind}")
        for kind in INCIDENT_EVIDENCE_KINDS
    ]


def _manifest(**overrides) -> IncidentReconstructionManifestModel:
    kwargs: dict = dict(
        trace=_trace(),
        createdAt=_NOW,
        evidence=_full_evidence(),
    )
    kwargs.update(overrides)
    return IncidentReconstructionManifestModel(**kwargs)


def test_manifest_round_trips_and_sets_content_type():
    manifest = _manifest()
    dumped = manifest.model_dump(by_alias=True, mode="json")
    assert dumped["contentType"] == INCIDENT_RECONSTRUCTION_CONTENT_TYPE
    assert dumped["schemaVersion"] == "v1"
    assert dumped["trace"]["traceId"] == "trace-abc"
    # Every correlated category is named exactly once.
    assert {item["kind"] for item in dumped["evidence"]} == set(INCIDENT_EVIDENCE_KINDS)


def test_manifest_preserves_workflow_control_stop_without_failed_step():
    manifest = _manifest(
        controlStop={
            "kind": "workflow_gate",
            "reasonCode": "remediation_budget_exhausted",
            "logicalStepId": "verify-remediation-6",
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "remainingWorkRef": "artifact://remaining/final",
            "remediationAttempt": 6,
            "remediationMaxAttempts": 6,
            "remediationAttemptsConsumed": 6,
        }
    )
    dumped = manifest.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert manifest.failed_logical_step_id is None
    assert dumped["controlStop"]["reasonCode"] == "remediation_budget_exhausted"
    assert dumped["controlStop"]["remediationAttemptsConsumed"] == 6


def test_manifest_requires_every_evidence_category():
    partial = [
        IncidentEvidenceItemModel(kind="policy", present=False, reasonCode="x"),
        IncidentEvidenceItemModel(kind="provider", present=False, reasonCode="y"),
    ]
    with pytest.raises(ValidationError) as exc:
        _manifest(evidence=partial)
    assert "every correlated category" in str(exc.value)


def test_manifest_rejects_duplicate_evidence_category():
    duplicated = _full_evidence() + [
        IncidentEvidenceItemModel(kind="policy", present=False, reasonCode="dup")
    ]
    with pytest.raises(ValidationError) as exc:
        _manifest(evidence=duplicated)
    assert "twice" in str(exc.value)


def test_absent_evidence_requires_reason_code():
    with pytest.raises(ValidationError):
        IncidentEvidenceItemModel(kind="policy", present=False)


def test_present_evidence_allows_locator_without_reason_code():
    item = IncidentEvidenceItemModel(
        kind="logs", present=True, artifactRef="artifact://logs/1"
    )
    assert item.reason_code is None
    assert item.artifact_ref == "artifact://logs/1"


def test_evidence_summary_rejects_unsafe_inline_content():
    with pytest.raises(ValidationError):
        IncidentEvidenceItemModel(
            kind="provider",
            present=True,
            summary="diff --git a/x b/x",
        )


def test_provider_context_rejects_secret_like_summary():
    with pytest.raises(ValidationError):
        IncidentProviderContextModel(
            providerErrorClass="auth",
            sanitizedSummary="leaked ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )


def test_provider_context_keeps_structured_fields():
    provider = IncidentProviderContextModel(
        providerProfileId="profile-9",
        providerErrorClass="rate_limit",
        providerErrorCode="429",
        retryAfterSeconds=30,
        rawErrorRef="artifact://provider/raw-1",
        sanitizedSummary="Provider rate limit reached; the run will retry after a profile cooldown.",
    )
    assert provider.provider_error_class == "rate_limit"
    assert provider.retry_after_seconds == 30
    assert provider.raw_error_ref == "artifact://provider/raw-1"


def test_cost_observed_requires_value_when_available():
    with pytest.raises(ValidationError):
        IncidentCostAttributionModel(observedAvailable=True)


def test_cost_observed_value_requires_available_flag():
    with pytest.raises(ValidationError):
        IncidentCostAttributionModel(observedAvailable=False, totalTokens=10)


def test_cost_settings_only_is_valid():
    cost = IncidentCostAttributionModel(
        runtimeId="codex_cli",
        model="gpt-4o",
        observedAvailable=False,
        unavailableReason="observed_cost_not_reported",
    )
    assert cost.observed_available is False
    assert cost.total_tokens is None


def test_cost_observed_value_is_valid():
    cost = IncidentCostAttributionModel(
        observedAvailable=True,
        inputTokens=100,
        outputTokens=50,
        totalTokens=150,
        costEstimateUsd=0.01,
        pricingSource="built_in",
    )
    assert cost.observed_available is True
    assert cost.cost_estimate_usd == 0.01


def test_trace_span_requires_compact_artifact_ref():
    with pytest.raises(ValidationError):
        IncidentTraceSpanModel(
            boundary="provider",
            spanId="span-1",
            traceId="trace-abc",
            artifactRef="line-one\nline-two",
        )


def test_trace_ref_round_trips():
    ref = IncidentTraceRefModel(
        traceId="trace-abc",
        workflowId="wf-1",
        runId="run-1",
        spanId="span-step-1",
        logicalStepId="publish",
        executionOrdinal=2,
    )
    dumped = ref.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert dumped["traceId"] == "trace-abc"
    assert dumped["logicalStepId"] == "publish"
    assert dumped["executionOrdinal"] == 2


def test_manifest_rejects_inline_recovery_ref_with_newline():
    with pytest.raises(ValidationError):
        _manifest(recoveryManifestRef="artifact://x\nartifact://y")
