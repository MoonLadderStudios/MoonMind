"""Incident reconstruction contract (MM-884).

MM-884: Complete end-to-end incident reconstruction with trace refs and per-step
cost attribution.

This module defines the single, deterministic, artifact-friendly contract that
correlates *all* the failure evidence a MoonMind operator needs to reconstruct a
hard incident without reading raw worker internals:

* the versioned resilience policy that governed the run/step (MM-880)
* the provider / profile / credential source and the sanitized provider failure
  event (MM-882)
* the failed logical step and its execution ordinal
* progress signals
* workspace changes
* accepted / blocked side effects and the checkpoint restore candidate (MM-881)
* cost-attribution settings plus observed cost where available
* trace spans across the API, workflow, activity, provider, side-effect, log,
  artifact, and step-manifest boundaries, all sharing one stable correlation id
* durable log and artifact references

The contract is intentionally compact: large details are carried as artifact
references and secret-bearing values / raw provider payloads are redacted or
referenced only, preserving Temporal payload discipline (see
``temporal_payload_policy``). The manifest never duplicates large evidence; it
links the durable recovery manifest, run summary, step manifests, and logs.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .resilience_policy_models import ResiliencePolicyRef
from .temporal_payload_policy import (
    MAX_TEMPORAL_METADATA_REF_CHARS,
    validate_compact_temporal_mapping,
)

INCIDENT_RECONSTRUCTION_SCHEMA_VERSION = "v1"
INCIDENT_RECONSTRUCTION_CONTENT_TYPE = (
    "application/vnd.moonmind.incident-reconstruction+json;version=1"
)

# Boundaries a stable correlation id propagates through. These mirror the
# acceptance criterion exactly: "API, workflow, activity, provider, side-effect,
# log, artifact, and step-manifest boundaries".
IncidentTraceBoundary = Literal[
    "api",
    "workflow",
    "activity",
    "provider",
    "side_effect",
    "log",
    "artifact",
    "step_manifest",
]

# Correlated evidence categories a failed run must expose in one reconstruction
# path. Mirrors the acceptance criterion: "policy, provider/profile/credential
# source, failed step, progress signals, workspace changes, accepted/blocked
# side effects, checkpoint restore candidate, cost, trace spans, logs, and
# artifacts".
IncidentEvidenceKind = Literal[
    "policy",
    "provider",
    "failed_step",
    "progress",
    "workspace_changes",
    "side_effects",
    "checkpoint",
    "cost",
    "trace",
    "logs",
    "artifacts",
]

# All evidence kinds the manifest enumerates so a failed run always *names* every
# correlated category (present with a locator, or absent with a reason code).
INCIDENT_EVIDENCE_KINDS: tuple[str, ...] = (
    "policy",
    "provider",
    "failed_step",
    "progress",
    "workspace_changes",
    "side_effects",
    "checkpoint",
    "cost",
    "trace",
    "logs",
    "artifacts",
)

# Tokens that must never appear inline in a bounded summary; raw provider text,
# diffs, logs, and credential material belong in artifact-gated refs only.
_FORBIDDEN_INLINE_TOKENS = (
    "diff --git",
    "raw log",
    "raw logs",
    "raw stdout",
    "raw stderr",
    "provider payload",
    "credential value",
    "token=",
    "password=",
    "-----begin",
)
_SECRET_LIKE_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"AIza[A-Za-z0-9_-]+"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)


def _reject_unsafe_summary(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if any(token in lowered for token in _FORBIDDEN_INLINE_TOKENS):
        raise ValueError("summary must be bounded, redacted, and ref-only")
    if any(pattern.search(value) for pattern in _SECRET_LIKE_PATTERNS):
        raise ValueError("summary must not contain secret-like values")
    return value


def _strip_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _compact_ref(value: Any, *, field_name: str) -> str | None:
    """Validate a value is a compact, single-line reference (never inline content)."""

    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if (
        len(candidate) > MAX_TEMPORAL_METADATA_REF_CHARS
        or "\n" in candidate
        or "\r" in candidate
    ):
        raise ValueError(f"{field_name} must be a compact reference, not inline content")
    return candidate


class IncidentTraceContextModel(BaseModel):
    """Stable correlation identity propagated end to end for one run.

    ``trace_id`` is deterministically derived from the workflow/run identity so
    it is replay-stable and identical everywhere it is referenced (step
    manifests, spans, projections).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    trace_id: str = Field(..., alias="traceId", min_length=1, max_length=120)
    workflow_id: str = Field(..., alias="workflowId", min_length=1, max_length=400)
    run_id: str = Field(..., alias="runId", min_length=1, max_length=400)
    external_correlation_id: str | None = Field(
        None, alias="externalCorrelationId", max_length=400
    )
    parent_run_id: str | None = Field(None, alias="parentRunId", max_length=400)

    @field_validator(
        "trace_id",
        "workflow_id",
        "run_id",
        "external_correlation_id",
        "parent_run_id",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)


class IncidentTraceRefModel(BaseModel):
    """Compact trace reference stamped onto step manifests and incident projections.

    Carries the stable run ``trace_id`` plus an optional per-step ``span_id`` so a
    step manifest can be correlated to the run's incident reconstruction and to
    its trace/log slice through the same correlation id.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    trace_id: str = Field(..., alias="traceId", min_length=1, max_length=120)
    workflow_id: str = Field(..., alias="workflowId", min_length=1, max_length=400)
    run_id: str = Field(..., alias="runId", min_length=1, max_length=400)
    span_id: str | None = Field(None, alias="spanId", max_length=200)
    logical_step_id: str | None = Field(None, alias="logicalStepId", max_length=200)
    execution_ordinal: int | None = Field(None, alias="executionOrdinal", ge=1)

    @field_validator(
        "trace_id",
        "workflow_id",
        "run_id",
        "span_id",
        "logical_step_id",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)


class IncidentTraceSpanModel(BaseModel):
    """One correlated boundary span in the incident trace.

    Every span shares the run ``trace_id`` so policy, provider, side-effect, log,
    artifact, and step-manifest evidence can be joined by correlation id without
    duplicating the evidence itself.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    boundary: IncidentTraceBoundary
    span_id: str = Field(..., alias="spanId", min_length=1, max_length=200)
    trace_id: str = Field(..., alias="traceId", min_length=1, max_length=120)
    name: str | None = Field(None, max_length=200)
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=500)
    status: str | None = Field(None, max_length=60)
    summary: str | None = Field(None, max_length=500)

    @field_validator("span_id", "trace_id", "name", "status", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator("artifact_ref", mode="before")
    @classmethod
    def _validate_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="artifactRef")

    @field_validator("summary")
    @classmethod
    def _reject_unsafe(cls, value: str | None) -> str | None:
        return _reject_unsafe_summary(value)


class IncidentProviderContextModel(BaseModel):
    """Provider / profile / credential source plus the sanitized provider failure.

    Carries only structured, raw-text-free fields. The operator-facing
    ``sanitized_summary`` never contains raw provider text and raw detail is
    referenced through ``raw_error_ref`` (an artifact ref), per the redaction
    acceptance criterion.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider_profile_id: str | None = Field(
        None, alias="providerProfileId", max_length=200
    )
    runtime_id: str | None = Field(None, alias="runtimeId", max_length=200)
    credential_source: str | None = Field(
        None, alias="credentialSource", max_length=200
    )
    provider_error_class: str | None = Field(
        None, alias="providerErrorClass", max_length=120
    )
    provider_error_code: str | None = Field(
        None, alias="providerErrorCode", max_length=60
    )
    retry_recommendation: str | None = Field(
        None, alias="retryRecommendation", max_length=120
    )
    retry_after_seconds: int | None = Field(
        None, alias="retryAfterSeconds", ge=0
    )
    reset_at: str | None = Field(None, alias="resetAt", max_length=120)
    quota_scope: str | None = Field(None, alias="quotaScope", max_length=120)
    credential_scope: str | None = Field(
        None, alias="credentialScope", max_length=200
    )
    provider_request_id: str | None = Field(
        None, alias="providerRequestId", max_length=200
    )
    sanitized_summary: str | None = Field(
        None, alias="sanitizedSummary", max_length=500
    )
    raw_error_ref: str | None = Field(None, alias="rawErrorRef", max_length=500)

    @field_validator(
        "provider_profile_id",
        "runtime_id",
        "credential_source",
        "provider_error_class",
        "provider_error_code",
        "retry_recommendation",
        "reset_at",
        "quota_scope",
        "credential_scope",
        "provider_request_id",
        "sanitized_summary",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator("raw_error_ref", mode="before")
    @classmethod
    def _validate_raw_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="rawErrorRef")

    @field_validator("sanitized_summary")
    @classmethod
    def _reject_unsafe(cls, value: str | None) -> str | None:
        return _reject_unsafe_summary(value)


class IncidentCostAttributionModel(BaseModel):
    """Cost-attribution settings plus observed cost where available.

    The attribution dimensions (``runtime_id``/``model``/``effort``/
    ``cost_center``/``budget_ref``) mirror the resilience cost-attribution policy.
    Observed token/cost values are populated only ``where available`` at runtime;
    when absent, ``observed_available`` stays ``False`` and the numeric fields
    remain ``None`` rather than fabricating a value.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_id: str | None = Field(None, alias="runtimeId", max_length=200)
    model: str | None = Field(None, max_length=200)
    effort: str | None = Field(None, max_length=120)
    cost_center: str | None = Field(None, alias="costCenter", max_length=200)
    budget_ref: str | None = Field(None, alias="budgetRef", max_length=500)

    observed_available: bool = Field(False, alias="observedAvailable")
    input_tokens: int | None = Field(None, alias="inputTokens", ge=0)
    output_tokens: int | None = Field(None, alias="outputTokens", ge=0)
    total_tokens: int | None = Field(None, alias="totalTokens", ge=0)
    cost_estimate_usd: float | None = Field(None, alias="costEstimateUsd", ge=0)
    pricing_source: str | None = Field(None, alias="pricingSource", max_length=120)
    unavailable_reason: str | None = Field(
        None, alias="unavailableReason", max_length=200
    )

    @field_validator(
        "runtime_id",
        "model",
        "effort",
        "cost_center",
        "pricing_source",
        "unavailable_reason",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator("budget_ref", mode="before")
    @classmethod
    def _validate_budget_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="budgetRef")

    @model_validator(mode="after")
    def _validate_observed(self) -> "IncidentCostAttributionModel":
        observed_values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cost_estimate_usd,
        )
        has_observed_value = any(value is not None for value in observed_values)
        if self.observed_available:
            if not has_observed_value:
                raise ValueError(
                    "observed cost requires at least one token or cost value"
                )
        elif has_observed_value:
            raise ValueError(
                "observed cost values require observedAvailable to be true"
            )
        return self


class IncidentEvidenceItemModel(BaseModel):
    """Presence + locator for one correlated incident evidence category."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: IncidentEvidenceKind
    present: bool
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=500)
    summary: str | None = Field(None, max_length=500)
    reason_code: str | None = Field(None, alias="reasonCode", max_length=120)

    @field_validator("summary", "reason_code", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator("artifact_ref", mode="before")
    @classmethod
    def _validate_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="artifactRef")

    @field_validator("summary")
    @classmethod
    def _reject_unsafe(cls, value: str | None) -> str | None:
        return _reject_unsafe_summary(value)

    @model_validator(mode="after")
    def _validate_presence(self) -> "IncidentEvidenceItemModel":
        if not self.present and not self.reason_code:
            raise ValueError("absent incident evidence requires a reasonCode")
        return self


class IncidentControlStopModel(BaseModel):
    """Workflow-owned terminal control evidence without a failed step."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["workflow_gate"] = "workflow_gate"
    reason_code: str = Field(..., alias="reasonCode", max_length=120)
    logical_step_id: str = Field(..., alias="logicalStepId", max_length=200)
    verdict: str | None = Field(None, max_length=120)
    terminal_disposition: str = Field(
        "failed_with_remaining_work", alias="terminalDisposition", max_length=120
    )
    gate_result_ref: str | None = Field(None, alias="gateResultRef", max_length=500)
    verification_ref: str | None = Field(
        None, alias="verificationRef", max_length=500
    )
    remaining_work_ref: str | None = Field(
        None, alias="remainingWorkRef", max_length=500
    )
    workspace_head_ref: str | None = Field(
        None, alias="workspaceHeadRef", max_length=500
    )
    exhausted_budget_dimension: str | None = Field(
        None, alias="exhaustedBudgetDimension", max_length=120
    )
    last_accepted_step_execution_id: str | None = Field(
        None, alias="lastAcceptedStepExecutionId", max_length=300
    )
    publication_feasible: bool = Field(False, alias="publicationFeasible")
    publication_feasibility_reason: str | None = Field(
        None, alias="publicationFeasibilityReason", max_length=120
    )
    publication_attempted: bool = Field(False, alias="publicationAttempted")
    auxiliary_outcomes: dict[str, Any] = Field(
        default_factory=dict, alias="auxiliaryOutcomes"
    )
    review_gate_budget: dict[str, Any] | None = Field(
        None, alias="reviewGateBudget"
    )
    remediation_budget: dict[str, Any] | None = Field(
        None, alias="remediationBudget"
    )

    @field_validator(
        "reason_code",
        "logical_step_id",
        "verdict",
        "terminal_disposition",
        "exhausted_budget_dimension",
        "last_accepted_step_execution_id",
        "publication_feasibility_reason",
        mode="before",
    )
    @classmethod
    def _strip_control_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator(
        "gate_result_ref",
        "verification_ref",
        "remaining_work_ref",
        "workspace_head_ref",
        mode="before",
    )
    @classmethod
    def _validate_control_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="control evidence ref")


class IncidentReconstructionManifestModel(BaseModel):
    """One incident reconstruction path correlating all run failure evidence.

    Emitted before terminal failure is reported. Correlates policy, provider/
    profile/credential source, failed step, progress, workspace changes,
    accepted/blocked side effects, checkpoint restore candidate, cost, trace
    spans, logs, and artifacts under one stable trace id. Large evidence is
    carried as bounded refs (it is never duplicated into workflow history);
    secret-bearing values and raw provider payloads are redacted or
    artifact-gated.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field(
        INCIDENT_RECONSTRUCTION_SCHEMA_VERSION, alias="schemaVersion"
    )
    content_type: Literal[INCIDENT_RECONSTRUCTION_CONTENT_TYPE] = Field(
        INCIDENT_RECONSTRUCTION_CONTENT_TYPE, alias="contentType"
    )
    trace: IncidentTraceContextModel
    created_at: datetime = Field(..., alias="createdAt")
    failure_stage: str | None = Field(None, alias="failureStage", max_length=120)
    failure_category: str | None = Field(None, alias="failureCategory", max_length=120)
    failed_logical_step_id: str | None = Field(
        None, alias="failedLogicalStepId", max_length=200
    )
    failed_execution_ordinal: int | None = Field(
        None, alias="failedExecutionOrdinal", ge=1
    )
    control_stop: IncidentControlStopModel | None = Field(None, alias="controlStop")
    policy_ref: ResiliencePolicyRef | None = Field(None, alias="policyRef")
    provider: IncidentProviderContextModel | None = None
    cost: IncidentCostAttributionModel | None = None
    side_effect_dispositions: list[dict[str, Any]] = Field(
        default_factory=list, alias="sideEffectDispositions"
    )
    checkpoint: dict[str, Any] | None = Field(None)
    recovery_manifest_ref: str | None = Field(None, alias="recoveryManifestRef")
    progress: dict[str, Any] = Field(default_factory=dict)
    workspace_changes: list[dict[str, Any]] = Field(
        default_factory=list, alias="workspaceChanges"
    )
    trace_spans: list[IncidentTraceSpanModel] = Field(
        default_factory=list, alias="traceSpans"
    )
    logs_ref: str | None = Field(None, alias="logsRef")
    artifact_refs: dict[str, str] = Field(default_factory=dict, alias="artifactRefs")
    evidence: list[IncidentEvidenceItemModel] = Field(default_factory=list)

    @field_validator(
        "failure_stage", "failure_category", "failed_logical_step_id", mode="before"
    )
    @classmethod
    def _strip_text(cls, value: Any) -> str | None:
        return _strip_optional_text(value)

    @field_validator("recovery_manifest_ref", mode="before")
    @classmethod
    def _validate_recovery_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="recoveryManifestRef")

    @field_validator("logs_ref", mode="before")
    @classmethod
    def _validate_logs_ref(cls, value: Any) -> str | None:
        return _compact_ref(value, field_name="logsRef")

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _validate_artifact_refs(cls, value: Any) -> dict[str, str]:
        if value is None:
            return {}
        refs: dict[str, str] = {}
        for key, ref in dict(value).items():
            name = str(key).strip()
            compact = _compact_ref(ref, field_name=f"artifactRefs[{name}]")
            if name and compact:
                refs[name] = compact
        return refs

    @model_validator(mode="after")
    def _finalize(self) -> "IncidentReconstructionManifestModel":
        # Every correlated category must be named exactly once so a failed run
        # always *exposes* the full evidence surface (present or, with a reason,
        # absent) rather than silently omitting a category.
        kinds = [item.kind for item in self.evidence]
        if len(kinds) != len(set(kinds)):
            raise ValueError("incident evidence must not list a category twice")
        missing = set(INCIDENT_EVIDENCE_KINDS) - set(kinds)
        if missing:
            raise ValueError(
                "incident evidence must name every correlated category; "
                f"missing: {sorted(missing)}"
            )
        # Enforce compact-metadata / artifact-ref discipline for the whole
        # manifest: bounded size, JSON serializable, no raw bytes.
        validate_compact_temporal_mapping(
            self.model_dump(by_alias=True, mode="json", exclude_none=True),
            field_name="incidentReconstruction",
        )
        return self


__all__ = [
    "INCIDENT_EVIDENCE_KINDS",
    "INCIDENT_RECONSTRUCTION_CONTENT_TYPE",
    "INCIDENT_RECONSTRUCTION_SCHEMA_VERSION",
    "IncidentCostAttributionModel",
    "IncidentControlStopModel",
    "IncidentEvidenceItemModel",
    "IncidentEvidenceKind",
    "IncidentProviderContextModel",
    "IncidentReconstructionManifestModel",
    "IncidentTraceBoundary",
    "IncidentTraceContextModel",
    "IncidentTraceRefModel",
    "IncidentTraceSpanModel",
]
