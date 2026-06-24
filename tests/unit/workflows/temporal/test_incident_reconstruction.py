"""Unit tests for the incident reconstruction builder (MM-884).

Covers the acceptance criteria for "Complete end-to-end incident reconstruction
with trace refs and per-step cost attribution":

- A failed run exposes correlated evidence for policy, provider/profile/
  credential source, failed step, progress, workspace changes, accepted/blocked
  side effects, checkpoint restore candidate, cost, trace spans, logs, and
  artifacts in one reconstruction path.
- Trace ids propagate through every boundary and are stable/replay-safe.
- Raw provider payloads are referenced (never inlined) and secret-bearing text
  is rejected.
- Tests cover a hard incident with both accepted and blocked side effects plus a
  provider failure event.
"""

from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.incident_reconstruction_models import (
    INCIDENT_EVIDENCE_KINDS,
    INCIDENT_RECONSTRUCTION_CONTENT_TYPE,
)
from moonmind.workflows.temporal.incident_reconstruction import (
    build_incident_reconstruction_manifest,
    build_incident_trace_ref,
    derive_incident_trace_id,
)
from moonmind.workflows.temporal.recovery_manifest import (
    build_failed_run_recovery_manifest,
)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)

_POLICY_REF = {
    "policyId": "resilience-policy-abc",
    "policyVersion": 1,
    "digest": "d" * 64,
    "contentType": "application/vnd.moonmind.resilience-policy+json;version=1",
    "envelopeRef": "artifact://policy/env-1",
}


def _hard_incident_recovery_manifest():
    """A failed publish step with one accepted and one blocked side effect."""

    return build_failed_run_recovery_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        step_ledger_rows=[
            {
                "logicalStepId": "prepare",
                "status": "succeeded",
                "executionOrdinal": 1,
                "terminalDisposition": "accepted",
                "title": "Prepare workspace",
            },
            {
                "logicalStepId": "publish",
                "status": "failed",
                "executionOrdinal": 2,
                "title": "Publish PR",
            },
        ],
        terminal_dispositions={"prepare": "accepted"},
        checkpoint_refs_by_boundary={
            "publish": {"after_gate": "artifact://checkpoint/after-gate"}
        },
        side_effect_records={
            "publish": [
                {
                    "class": "workspace_mutation",
                    "operation": "edit-file",
                    "disposition": "accepted",
                },
                {
                    "class": "publication",
                    "operation": "open-pr",
                    "disposition": "blocked",
                    "target": "repo#1",
                },
            ]
        },
        failure_diagnostic={
            "stepId": "publish",
            "stage": "publishing",
            "category": "integration_error",
        },
    )


def _provider_failure_event() -> dict:
    return {
        "providerErrorClass": "rate_limit",
        "providerErrorCode": "429",
        "retryRecommendation": "retry_after_cooldown",
        "retryAfterSeconds": 30,
        "providerRequestId": "req-123",
        "rawErrorRef": "artifact://provider/raw-1",
        "sanitizedSummary": (
            "Provider rate limit reached; the run will retry after a profile cooldown."
        ),
    }


def _build_hard_incident():
    recovery = _hard_incident_recovery_manifest()
    return build_incident_reconstruction_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        external_correlation_id="ext-abc",
        policy_ref=_POLICY_REF,
        provider_profile_id="profile-9",
        runtime_id="codex_cli",
        credential_source="managed_secret:openai",
        provider_failure=_provider_failure_event(),
        cost_attribution_settings={
            "runtimeId": "codex_cli",
            "model": "gpt-4o",
            "effort": "high",
            "costCenter": "team-x",
            "budgetRef": "artifact://budget/1",
        },
        observed_cost={
            "inputTokens": 1200,
            "outputTokens": 800,
            "costEstimateUsd": 0.05,
            "pricingSource": "built_in",
        },
        recovery_manifest=recovery,
        recovery_manifest_ref="artifact://recovery/manifest-1",
        failure_diagnostic={
            "stepId": "publish",
            "stage": "publishing",
            "category": "integration_error",
        },
        progress_summary={"total": 2, "succeeded": 1, "failed": 1},
        workspace_changes=[
            {"logicalStepId": "publish", "terminalDisposition": "blocked"}
        ],
        logs_ref="artifact://logs/spool-1",
        artifact_refs={
            "runSummary": "artifact://reports/run_summary",
            "failedStepManifest": "artifact://reports/step/publish",
        },
    )


def test_hard_incident_exposes_all_correlated_evidence():
    manifest = _build_hard_incident()
    present = {item.kind: item.present for item in manifest.evidence}
    # A failed run exposes correlated evidence for EVERY category.
    assert set(present) == set(INCIDENT_EVIDENCE_KINDS)
    assert all(present.values()), present


def test_hard_incident_correlates_accepted_and_blocked_side_effects():
    manifest = _build_hard_incident()
    dispositions = {
        (d["class"], d["disposition"]) for d in manifest.side_effect_dispositions
    }
    assert ("workspace_mutation", "accepted") in dispositions
    assert ("publication", "blocked") in dispositions


def test_hard_incident_correlates_provider_failure_event():
    manifest = _build_hard_incident()
    assert manifest.provider is not None
    assert manifest.provider.provider_error_class == "rate_limit"
    assert manifest.provider.provider_request_id == "req-123"
    # Raw provider detail is referenced, never inlined.
    assert manifest.provider.raw_error_ref == "artifact://provider/raw-1"


def test_hard_incident_blocks_checkpoint_resume_on_blocked_side_effect():
    manifest = _build_hard_incident()
    assert manifest.checkpoint is not None
    # The blocked publication side effect keeps checkpoint resume ineligible.
    assert manifest.checkpoint["eligible"] is False
    assert manifest.checkpoint["disabledReasonCode"] == "side_effect_blocked"


def test_trace_id_propagates_through_every_boundary():
    manifest = _build_hard_incident()
    trace_id = derive_incident_trace_id("wf-1", "run-1")
    assert manifest.trace.trace_id == trace_id
    boundaries = {span.boundary for span in manifest.trace_spans}
    assert boundaries == {
        "api",
        "workflow",
        "activity",
        "provider",
        "side_effect",
        "log",
        "artifact",
        "step_manifest",
    }
    # Every span shares the one stable run trace id.
    assert all(span.trace_id == trace_id for span in manifest.trace_spans)


def test_trace_id_is_deterministic_and_replay_stable():
    first = derive_incident_trace_id("wf-1", "run-1")
    second = derive_incident_trace_id("wf-1", "run-1")
    assert first == second
    assert derive_incident_trace_id("wf-1", "run-2") != first


def test_step_trace_ref_shares_run_trace_id():
    ref = build_incident_trace_ref(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="publish",
        execution_ordinal=2,
    )
    assert ref.trace_id == derive_incident_trace_id("wf-1", "run-1")
    assert ref.logical_step_id == "publish"
    assert ref.execution_ordinal == 2
    assert ref.span_id is not None


def test_cost_includes_settings_and_observed_value():
    manifest = _build_hard_incident()
    assert manifest.cost is not None
    assert manifest.cost.runtime_id == "codex_cli"
    assert manifest.cost.cost_center == "team-x"
    assert manifest.cost.observed_available is True
    assert manifest.cost.total_tokens == 2000
    assert manifest.cost.cost_estimate_usd == 0.05


def test_cost_observed_absent_when_runtime_did_not_report():
    recovery = _hard_incident_recovery_manifest()
    manifest = build_incident_reconstruction_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        cost_attribution_settings={"runtimeId": "codex_cli", "model": "gpt-4o"},
        observed_cost=None,
        recovery_manifest=recovery,
        recovery_manifest_ref="artifact://recovery/manifest-1",
        artifact_refs={"recoveryManifest": "artifact://recovery/manifest-1"},
    )
    assert manifest.cost is not None
    assert manifest.cost.observed_available is False
    assert manifest.cost.total_tokens is None
    assert manifest.cost.unavailable_reason == "observed_cost_not_reported"


def test_manifest_links_durable_recovery_manifest_without_duplicating():
    manifest = _build_hard_incident()
    assert manifest.recovery_manifest_ref == "artifact://recovery/manifest-1"
    side_effects_evidence = next(
        item for item in manifest.evidence if item.kind == "side_effects"
    )
    assert side_effects_evidence.artifact_ref == "artifact://recovery/manifest-1"


def test_provider_failure_raw_reason_text_is_dropped():
    # An in-flight envelope that still carries a raw ``reason`` must not leak it.
    recovery = _hard_incident_recovery_manifest()
    manifest = build_incident_reconstruction_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        provider_failure={
            "providerErrorClass": "auth",
            "reason": "traceback with ghp_SECRETSECRETSECRETSECRETSECRET token",
        },
        recovery_manifest=recovery,
        recovery_manifest_ref="artifact://recovery/manifest-1",
        artifact_refs={"recoveryManifest": "artifact://recovery/manifest-1"},
    )
    assert manifest.provider is not None
    dumped = manifest.provider.model_dump(by_alias=True, mode="json")
    assert "reason" not in dumped
    # A raw-text-free summary is derived from the class instead.
    assert manifest.provider.sanitized_summary is not None
    assert "ghp_" not in manifest.provider.sanitized_summary


def test_manifest_serializes_with_expected_content_type():
    manifest = _build_hard_incident()
    dumped = manifest.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert dumped["contentType"] == INCIDENT_RECONSTRUCTION_CONTENT_TYPE
    assert dumped["failedLogicalStepId"] == "publish"
    assert dumped["failedExecutionOrdinal"] == 2


def test_minimal_incident_still_names_every_category_as_absent():
    manifest = build_incident_reconstruction_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
    )
    present = {item.kind: item.present for item in manifest.evidence}
    # Only trace is always present; the rest are named absent with reason codes.
    assert present["trace"] is True
    absent = {kind for kind, is_present in present.items() if not is_present}
    assert "policy" in absent and "provider" in absent and "cost" in absent
    for item in manifest.evidence:
        if not item.present:
            assert item.reason_code is not None
