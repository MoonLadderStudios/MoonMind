"""Pure builder for the incident reconstruction manifest (MM-884).

Every failed run emits one incident reconstruction manifest before terminal
failure is reported. The manifest correlates the resilience policy, the
provider/profile/credential source and sanitized provider failure event, the
failed logical step, progress signals, workspace changes, accepted/blocked side
effects, the checkpoint restore candidate, cost-attribution settings plus
observed cost where available, trace spans across every boundary, and durable
log/artifact references -- all joined by one stable correlation (trace) id.

The builder is a pure function over compact, ref-only state so it can be
exercised at the workflow boundary and unit tested directly. It never embeds
large or unsafe content: it links the already-emitted recovery manifest, run
summary, step manifests, and logs rather than duplicating them, and it copies
only sanitized, structured provider fields (never raw provider ``reason`` text).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from moonmind.schemas.incident_reconstruction_models import (
    INCIDENT_EVIDENCE_KINDS,
    IncidentCostAttributionModel,
    IncidentEvidenceItemModel,
    IncidentProviderContextModel,
    IncidentReconstructionManifestModel,
    IncidentTraceContextModel,
    IncidentTraceRefModel,
    IncidentTraceSpanModel,
)
from moonmind.schemas.resilience_policy_models import ResiliencePolicyRef
from moonmind.schemas.temporal_models import FailedRunRecoveryManifestModel
from moonmind.workflows.provider_failures import sanitized_summary_for_class

# Observed-cost key aliases accepted from agent result outputs. The canonical
# forms mirror ``ModelCostEstimate.to_metadata`` (moonmind/billing/costs.py).
_OBSERVED_TOKEN_KEYS = {
    "input_tokens": ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens"),
    "output_tokens": (
        "outputTokens",
        "output_tokens",
        "completionTokens",
        "completion_tokens",
    ),
    "total_tokens": ("totalTokens", "total_tokens"),
}
_OBSERVED_COST_KEYS = (
    "costEstimateUsd",
    "cost_estimate_usd",
    "estimatedCostUsd",
    "estimated_cost_usd",
    "costUsd",
    "cost_usd",
    "totalCostUsd",
    "total_cost_usd",
)
_OBSERVED_PRICING_SOURCE_KEYS = ("pricingSource", "pricing_source")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def derive_incident_trace_id(workflow_id: str, run_id: str) -> str:
    """Return a deterministic, replay-stable correlation id for a run.

    Derived purely from the workflow/run identity so the same run always
    produces the same trace id everywhere it is referenced (step manifests,
    spans, projections).
    """

    workflow_text = _text(workflow_id) or "unknown-workflow"
    run_text = _text(run_id) or "unknown-run"
    digest = hashlib.sha256(f"{workflow_text}:{run_text}".encode("utf-8")).hexdigest()
    return f"trace-{digest[:32]}"


def _derive_span_id(trace_id: str, boundary: str, *, suffix: str | None = None) -> str:
    seed = f"{trace_id}:{boundary}"
    if suffix:
        seed = f"{seed}:{suffix}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"span-{boundary}-{digest[:16]}"


def build_incident_trace_context(
    *,
    workflow_id: str,
    run_id: str,
    external_correlation_id: str | None = None,
    parent_run_id: str | None = None,
) -> IncidentTraceContextModel:
    """Build the stable trace context for a run."""

    return IncidentTraceContextModel(
        traceId=derive_incident_trace_id(workflow_id, run_id),
        workflowId=workflow_id,
        runId=run_id,
        externalCorrelationId=_text(external_correlation_id),
        parentRunId=_text(parent_run_id),
    )


def build_incident_trace_ref(
    *,
    workflow_id: str,
    run_id: str,
    logical_step_id: str | None = None,
    execution_ordinal: int | None = None,
) -> IncidentTraceRefModel:
    """Build the compact trace ref stamped onto a step-execution manifest."""

    trace_id = derive_incident_trace_id(workflow_id, run_id)
    span_suffix = None
    if logical_step_id:
        span_suffix = logical_step_id
        if execution_ordinal:
            span_suffix = f"{logical_step_id}:{execution_ordinal}"
    span_id = _derive_span_id(trace_id, "step_manifest", suffix=span_suffix)
    return IncidentTraceRefModel(
        traceId=trace_id,
        workflowId=workflow_id,
        runId=run_id,
        spanId=span_id,
        logicalStepId=_text(logical_step_id),
        executionOrdinal=_positive_int(execution_ordinal),
    )


def _normalize_policy_ref(
    policy_ref: Mapping[str, Any] | ResiliencePolicyRef | None,
) -> ResiliencePolicyRef | None:
    if policy_ref is None:
        return None
    if isinstance(policy_ref, ResiliencePolicyRef):
        return policy_ref
    if isinstance(policy_ref, Mapping):
        return ResiliencePolicyRef.model_validate(dict(policy_ref))
    return None


def _build_provider_context(
    *,
    provider_failure: Mapping[str, Any] | None,
    provider_profile_id: str | None,
    runtime_id: str | None,
    credential_source: str | None,
) -> IncidentProviderContextModel | None:
    failure = provider_failure if isinstance(provider_failure, Mapping) else {}

    def _field(*keys: str) -> str | None:
        for key in keys:
            value = _text(failure.get(key))
            if value:
                return value
        return None

    provider_error_class = _field("providerErrorClass", "provider_error_class")
    sanitized_summary = _field("sanitizedSummary", "sanitized_summary")
    if sanitized_summary is None and provider_error_class is not None:
        # Never copy raw provider ``reason`` text; derive a raw-text-free summary.
        sanitized_summary = sanitized_summary_for_class(provider_error_class)

    context = IncidentProviderContextModel(
        providerProfileId=_text(provider_profile_id),
        runtimeId=_text(runtime_id),
        credentialSource=_text(credential_source),
        providerErrorClass=provider_error_class,
        providerErrorCode=_field("providerErrorCode", "provider_error_code"),
        retryRecommendation=_field("retryRecommendation", "retry_recommendation"),
        retryAfterSeconds=_non_negative_int(
            failure.get("retryAfterSeconds", failure.get("retry_after_seconds"))
        ),
        resetAt=_field("resetAt", "reset_at"),
        quotaScope=_field("quotaScope", "quota_scope"),
        credentialScope=_field("credentialScope", "credential_scope"),
        providerRequestId=_field("providerRequestId", "provider_request_id"),
        sanitizedSummary=sanitized_summary,
        rawErrorRef=_field("rawErrorRef", "raw_error_ref"),
    )
    has_signal = any(
        value is not None
        for value in (
            context.provider_profile_id,
            context.runtime_id,
            context.credential_source,
            context.provider_error_class,
            context.provider_error_code,
            context.retry_recommendation,
            context.retry_after_seconds,
            context.reset_at,
            context.quota_scope,
            context.credential_scope,
            context.provider_request_id,
            context.sanitized_summary,
            context.raw_error_ref,
        )
    )
    return context if has_signal else None


def _build_cost(
    *,
    cost_attribution_settings: Mapping[str, Any] | None,
    observed_cost: Mapping[str, Any] | None,
) -> IncidentCostAttributionModel | None:
    settings = (
        cost_attribution_settings
        if isinstance(cost_attribution_settings, Mapping)
        else {}
    )

    def _setting(*keys: str) -> str | None:
        for key in keys:
            value = _text(settings.get(key))
            if value:
                return value
        return None

    runtime_id = _setting("runtimeId", "runtime_id")
    model = _setting("model")
    effort = _setting("effort")
    cost_center = _setting("costCenter", "cost_center")
    budget_ref = _setting("budgetRef", "budget_ref")

    observed = observed_cost if isinstance(observed_cost, Mapping) else {}

    def _observed_token(field: str) -> int | None:
        for key in _OBSERVED_TOKEN_KEYS[field]:
            value = _non_negative_int(observed.get(key))
            if value is not None:
                return value
        return None

    input_tokens = _observed_token("input_tokens")
    output_tokens = _observed_token("output_tokens")
    total_tokens = _observed_token("total_tokens")
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    cost_estimate_usd: float | None = None
    for key in _OBSERVED_COST_KEYS:
        cost_estimate_usd = _non_negative_float(observed.get(key))
        if cost_estimate_usd is not None:
            break
    pricing_source: str | None = None
    for key in _OBSERVED_PRICING_SOURCE_KEYS:
        pricing_source = _text(observed.get(key))
        if pricing_source:
            break

    observed_available = any(
        value is not None
        for value in (input_tokens, output_tokens, total_tokens, cost_estimate_usd)
    )
    has_settings = any(
        value is not None
        for value in (runtime_id, model, effort, cost_center, budget_ref)
    )
    if not observed_available and not has_settings:
        return None

    return IncidentCostAttributionModel(
        runtimeId=runtime_id,
        model=model,
        effort=effort,
        costCenter=cost_center,
        budgetRef=budget_ref,
        observedAvailable=observed_available,
        inputTokens=input_tokens if observed_available else None,
        outputTokens=output_tokens if observed_available else None,
        totalTokens=total_tokens if observed_available else None,
        costEstimateUsd=cost_estimate_usd if observed_available else None,
        pricingSource=pricing_source if observed_available else None,
        unavailableReason=None if observed_available else "observed_cost_not_reported",
    )


def _build_trace_spans(
    *,
    trace_id: str,
    failed_logical_step_id: str | None,
    provider: IncidentProviderContextModel | None,
    recovery_manifest_ref: str | None,
    logs_ref: str | None,
    artifact_refs: Mapping[str, str],
) -> list[IncidentTraceSpanModel]:
    """Build one span per correlated boundary, all sharing the run trace id."""

    run_summary_ref = _text(artifact_refs.get("runSummary"))
    step_manifest_ref = _text(artifact_refs.get("failedStepManifest")) or _text(
        artifact_refs.get("stepManifest")
    )
    provider_ref = provider.raw_error_ref if provider is not None else None
    provider_name = provider.provider_error_class if provider is not None else None
    # The run-summary artifact is written *after* the incident manifest (before
    # terminal failure), so fall back to any other durable ref for the artifact
    # boundary span when it is not yet available.
    artifact_span_ref = (
        run_summary_ref
        or _text(recovery_manifest_ref)
        or next((value for value in artifact_refs.values() if _text(value)), None)
    )

    span_specs: tuple[tuple[str, str | None, str | None], ...] = (
        ("api", "api.request", None),
        ("workflow", "workflow.run", run_summary_ref),
        (
            "activity",
            f"activity.{failed_logical_step_id}" if failed_logical_step_id else "activity",
            step_manifest_ref,
        ),
        ("provider", provider_name or "provider", provider_ref),
        ("side_effect", "side_effects", recovery_manifest_ref),
        ("log", "logs", logs_ref),
        ("artifact", "artifacts", artifact_span_ref),
        (
            "step_manifest",
            failed_logical_step_id or "step_manifest",
            step_manifest_ref,
        ),
    )
    spans: list[IncidentTraceSpanModel] = []
    for boundary, name, artifact_ref in span_specs:
        spans.append(
            IncidentTraceSpanModel(
                boundary=boundary,
                spanId=_derive_span_id(trace_id, boundary),
                traceId=trace_id,
                name=name,
                artifactRef=artifact_ref,
                status="present" if artifact_ref else "correlated",
            )
        )
    return spans


def _evidence_item(
    kind: str,
    *,
    present: bool,
    artifact_ref: str | None = None,
    summary: str | None = None,
    absent_reason_code: str | None = None,
) -> IncidentEvidenceItemModel:
    return IncidentEvidenceItemModel(
        kind=kind,
        present=present,
        artifactRef=artifact_ref if present else None,
        summary=summary if present else None,
        reasonCode=None if present else absent_reason_code,
    )


def build_incident_reconstruction_manifest(
    *,
    workflow_id: str,
    run_id: str,
    created_at: datetime,
    external_correlation_id: str | None = None,
    parent_run_id: str | None = None,
    policy_ref: Mapping[str, Any] | ResiliencePolicyRef | None = None,
    provider_profile_id: str | None = None,
    runtime_id: str | None = None,
    credential_source: str | None = None,
    provider_failure: Mapping[str, Any] | None = None,
    cost_attribution_settings: Mapping[str, Any] | None = None,
    observed_cost: Mapping[str, Any] | None = None,
    recovery_manifest: FailedRunRecoveryManifestModel | None = None,
    recovery_manifest_ref: str | None = None,
    failure_diagnostic: Mapping[str, Any] | None = None,
    progress_summary: Mapping[str, Any] | None = None,
    workspace_changes: Sequence[Mapping[str, Any]] | None = None,
    logs_ref: str | None = None,
    artifact_refs: Mapping[str, str] | None = None,
    control_stop: Mapping[str, Any] | None = None,
) -> IncidentReconstructionManifestModel:
    """Build the incident reconstruction manifest for a failed run.

    Correlates every failure-evidence category under one stable trace id. The
    side-effect dispositions and checkpoint restore candidate are reused from the
    already-built failed-run recovery manifest (MM-881) so the incident path does
    not recompute or duplicate them; the recovery manifest is linked by ref.
    """

    trace = build_incident_trace_context(
        workflow_id=workflow_id,
        run_id=run_id,
        external_correlation_id=external_correlation_id,
        parent_run_id=parent_run_id,
    )
    artifact_refs = {
        str(key).strip(): _text(value)
        for key, value in dict(artifact_refs or {}).items()
        if _text(value)
    }
    artifact_refs = {key: value for key, value in artifact_refs.items() if key and value}

    diagnostic = failure_diagnostic if isinstance(failure_diagnostic, Mapping) else {}
    failure_stage = _text(diagnostic.get("stage"))
    failure_category = _text(diagnostic.get("category"))
    failed_logical_step_id = _text(diagnostic.get("stepId"))
    failed_execution_ordinal: int | None = None

    side_effect_dispositions: list[dict[str, Any]] = []
    checkpoint: dict[str, Any] | None = None
    if recovery_manifest is not None:
        failure_stage = failure_stage or recovery_manifest.failure_stage
        failure_category = failure_category or recovery_manifest.failure_category
        failed_logical_step_id = (
            failed_logical_step_id or recovery_manifest.failed_logical_step_id
        )
        failed_execution_ordinal = recovery_manifest.failed_execution_ordinal
        side_effect_dispositions = [
            disposition.model_dump(by_alias=True, mode="json", exclude_none=True)
            for disposition in recovery_manifest.side_effect_dispositions
        ]
        checkpoint = recovery_manifest.recovery_eligibility.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )

    normalized_policy_ref = _normalize_policy_ref(policy_ref)
    provider = _build_provider_context(
        provider_failure=provider_failure,
        provider_profile_id=provider_profile_id,
        runtime_id=runtime_id,
        credential_source=credential_source,
    )
    cost = _build_cost(
        cost_attribution_settings=cost_attribution_settings,
        observed_cost=observed_cost,
    )

    progress = dict(progress_summary) if isinstance(progress_summary, Mapping) else {}
    workspace_change_list = [
        dict(change)
        for change in (workspace_changes or [])
        if isinstance(change, Mapping)
    ]

    trace_spans = _build_trace_spans(
        trace_id=trace.trace_id,
        failed_logical_step_id=failed_logical_step_id,
        provider=provider,
        recovery_manifest_ref=recovery_manifest_ref,
        logs_ref=logs_ref,
        artifact_refs=artifact_refs,
    )

    evidence = [
        _evidence_item(
            "policy",
            present=normalized_policy_ref is not None,
            artifact_ref=(
                normalized_policy_ref.envelope_ref if normalized_policy_ref else None
            ),
            summary=(
                normalized_policy_ref.policy_id if normalized_policy_ref else None
            ),
            absent_reason_code="no_resilience_policy_compiled",
        ),
        _evidence_item(
            "provider",
            present=provider is not None,
            summary=provider.sanitized_summary if provider is not None else None,
            absent_reason_code="no_provider_failure_event",
        ),
        _evidence_item(
            "failed_step",
            present=bool(failed_logical_step_id),
            summary=failed_logical_step_id,
            absent_reason_code="no_failed_step_identified",
        ),
        _evidence_item(
            "progress",
            present=bool(progress),
            absent_reason_code="no_progress_recorded",
        ),
        _evidence_item(
            "workspace_changes",
            present=bool(workspace_change_list),
            absent_reason_code="no_workspace_changes_recorded",
        ),
        _evidence_item(
            "side_effects",
            present=bool(side_effect_dispositions),
            artifact_ref=recovery_manifest_ref,
            absent_reason_code="no_side_effects_recorded",
        ),
        _evidence_item(
            "checkpoint",
            present=checkpoint is not None,
            artifact_ref=recovery_manifest_ref,
            absent_reason_code="no_recovery_manifest",
        ),
        _evidence_item(
            "cost",
            present=cost is not None,
            absent_reason_code="no_cost_attribution",
        ),
        _evidence_item(
            "trace",
            present=True,
            summary=trace.trace_id,
        ),
        _evidence_item(
            "logs",
            present=bool(_text(logs_ref)),
            artifact_ref=_text(logs_ref),
            absent_reason_code="no_logs_ref",
        ),
        _evidence_item(
            "artifacts",
            present=bool(artifact_refs),
            artifact_ref=next(iter(artifact_refs.values()), None),
            absent_reason_code="no_artifact_refs",
        ),
    ]

    # Defensive: never let a category silently drop.
    assert {item.kind for item in evidence} == set(INCIDENT_EVIDENCE_KINDS)

    return IncidentReconstructionManifestModel(
        trace=trace,
        createdAt=created_at,
        failureStage=failure_stage,
        failureCategory=failure_category,
        failedLogicalStepId=failed_logical_step_id,
        failedExecutionOrdinal=_positive_int(failed_execution_ordinal),
        controlStop=dict(control_stop) if isinstance(control_stop, Mapping) else None,
        policyRef=normalized_policy_ref,
        provider=provider,
        cost=cost,
        sideEffectDispositions=side_effect_dispositions,
        checkpoint=checkpoint,
        recoveryManifestRef=_text(recovery_manifest_ref),
        progress=progress,
        workspaceChanges=workspace_change_list,
        traceSpans=trace_spans,
        logsRef=_text(logs_ref),
        artifactRefs=artifact_refs,
        evidence=evidence,
    )


__all__ = [
    "build_incident_reconstruction_manifest",
    "build_incident_trace_context",
    "build_incident_trace_ref",
    "derive_incident_trace_id",
]
