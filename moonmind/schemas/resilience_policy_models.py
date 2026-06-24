"""Typed, versioned ResiliencePolicy contract (MM-880).

MM-880: Compile and persist versioned ResiliencePolicy envelopes per workflow run
and step execution so failure review never requires reconstructing behavior from
scattered constants, environment variables, provider profiles, and workflow
defaults.

This module defines a single, deterministic, artifact-friendly contract that
captures the resilience values that govern a run *before* step execution begins:

* attempts / retry budgets
* wall-clock and idle timeouts
* no-progress handling
* provider cooldowns
* checkpoint requirements
* side-effect idempotency
* outbound scanning
* observability
* cost attribution

The envelope is intentionally compact: large details are carried as artifact
references and secrets are carried as references only, preserving Temporal
payload discipline (see ``temporal_payload_policy``). ``compile_resilience_policy``
is a pure, deterministic builder that fails fast on missing or unsupported
values so the policy can be validated at workflow/activity boundaries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .temporal_payload_policy import (
    MAX_TEMPORAL_METADATA_REF_CHARS,
    validate_compact_temporal_mapping,
)

RESILIENCE_POLICY_SCHEMA_VERSION = "v1"
RESILIENCE_POLICY_CONTENT_TYPE = (
    "application/vnd.moonmind.resilience-policy+json;version=1"
)

# Mirror of ``StepExecutionCheckpointBoundary`` (temporal_models). Duplicated as a
# local Literal so this contract stays import-light and self-contained.
ResilienceCheckpointBoundary = Literal[
    "after_prepare",
    "before_execution",
    "after_execution",
    "after_gate",
    "before_publication",
    "before_recovery_restoration",
]

ResilienceIdempotencyStrategy = Literal[
    "step_execution_operation",
    "agent_execution_request",
]


class ResiliencePolicyError(ValueError):
    """Raised when a resilience policy cannot be compiled or validated.

    Used for fail-fast handling of missing or unsupported policy values at
    workflow/activity boundaries.
    """


def _compact_reference(value: Any, *, field_name: str) -> str | None:
    """Validate that a value is a compact *reference*, never inline content.

    References must be short, single-line strings. This keeps large details
    artifact-backed and prevents inlining payloads into the envelope.
    """

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
        raise ResiliencePolicyError(
            f"{field_name} must be a compact reference, not inline content"
        )
    return candidate


def _contains_secret_like_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if any(
                marker in key_text
                for marker in ("secret", "token", "password", "credential", "api_key")
            ):
                return True
            if _contains_secret_like_key(nested):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_contains_secret_like_key(item) for item in value)
    return False


class ResilienceAttemptsPolicy(BaseModel):
    """Attempt and self-heal reset budgets governing retries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    step_max_attempts: int = Field(..., alias="stepMaxAttempts", ge=1)
    step_no_progress_limit: int = Field(..., alias="stepNoProgressLimit", ge=1)
    job_self_heal_max_resets: int = Field(..., alias="jobSelfHealMaxResets", ge=0)


class ResilienceTimeoutsPolicy(BaseModel):
    """Wall-clock and idle timeouts governing step execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    step_timeout_seconds: int = Field(..., alias="stepTimeoutSeconds", ge=1)
    step_idle_timeout_seconds: int = Field(..., alias="stepIdleTimeoutSeconds", ge=1)


class ResilienceProviderCooldownPolicy(BaseModel):
    """Provider cooldown / rate-limit values captured for the run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    cooldown_after_429_seconds: int = Field(
        ..., alias="cooldownAfter429Seconds", ge=0
    )
    provider_profile_id: str | None = Field(None, alias="providerProfileId")
    rate_limit_policy: dict[str, Any] = Field(
        default_factory=dict, alias="rateLimitPolicy"
    )
    rate_limit_policy_ref: str | None = Field(None, alias="rateLimitPolicyRef")

    @field_validator("provider_profile_id", mode="before")
    @classmethod
    def _normalize_profile_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("rate_limit_policy_ref", mode="before")
    @classmethod
    def _validate_rate_limit_ref(cls, value: Any) -> str | None:
        return _compact_reference(value, field_name="rateLimitPolicyRef")

    @field_validator("rate_limit_policy", mode="after")
    @classmethod
    def _validate_rate_limit_policy(cls, value: dict[str, Any]) -> dict[str, Any]:
        compact = validate_compact_temporal_mapping(
            value or {},
            field_name="rateLimitPolicy",
        )
        if _contains_secret_like_key(compact):
            raise ResiliencePolicyError(
                "rateLimitPolicy must not contain raw credential keys"
            )
        return compact


class ResilienceCheckpointPolicy(BaseModel):
    """Checkpoint requirements governing where evidence must exist."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    checkpoint_required: bool = Field(..., alias="checkpointRequired")
    required_boundaries: tuple[ResilienceCheckpointBoundary, ...] = Field(
        default=(),
        alias="requiredBoundaries",
    )

    @model_validator(mode="after")
    def _required_boundaries_present(self) -> "ResilienceCheckpointPolicy":
        if self.checkpoint_required and not self.required_boundaries:
            raise ResiliencePolicyError(
                "checkpointRequired policy must declare at least one boundary"
            )
        # Reject duplicate boundaries so the policy is unambiguous.
        if len(set(self.required_boundaries)) != len(self.required_boundaries):
            raise ResiliencePolicyError(
                "requiredBoundaries must not contain duplicates"
            )
        return self


class ResilienceIdempotencyPolicy(BaseModel):
    """Side-effect idempotency requirements governing replays."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    side_effect_idempotency_required: bool = Field(
        ..., alias="sideEffectIdempotencyRequired"
    )
    key_strategy: ResilienceIdempotencyStrategy = Field(..., alias="keyStrategy")


class ResilienceOutboundScanPolicy(BaseModel):
    """Outbound scanning policy governing egress redaction/blocking."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    high_security_mode: bool = Field(..., alias="highSecurityMode")
    block_on_finding: bool = Field(..., alias="blockOnFinding")


class ResilienceObservabilityPolicy(BaseModel):
    """Observability surfaces enabled for the run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    live_logs_timeline_enabled: bool = Field(..., alias="liveLogsTimelineEnabled")
    structured_history_enabled: bool = Field(..., alias="structuredHistoryEnabled")


class ResilienceCostAttributionPolicy(BaseModel):
    """Cost attribution dimensions captured for the run."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_id: str | None = Field(None, alias="runtimeId")
    model: str | None = Field(None, alias="model")
    effort: str | None = Field(None, alias="effort")
    cost_center: str | None = Field(None, alias="costCenter")
    budget_ref: str | None = Field(None, alias="budgetRef")

    @field_validator("runtime_id", "model", "effort", "cost_center", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("budget_ref", mode="before")
    @classmethod
    def _validate_budget_ref(cls, value: Any) -> str | None:
        return _compact_reference(value, field_name="budgetRef")


class ResiliencePolicyRef(BaseModel):
    """Compact, versioned reference to a persisted ResiliencePolicy envelope.

    This is the shape attached to workflow memo and step execution manifests so
    every step execution can be traced to the policy values that governed it
    without carrying the full envelope in Temporal payloads.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    policy_id: str = Field(..., alias="policyId", min_length=1)
    policy_version: int = Field(..., alias="policyVersion", ge=1)
    digest: str = Field(..., alias="digest", min_length=1)
    content_type: str = Field(
        RESILIENCE_POLICY_CONTENT_TYPE,
        alias="contentType",
    )
    envelope_ref: str | None = Field(None, alias="envelopeRef")

    @field_validator("envelope_ref", mode="before")
    @classmethod
    def _validate_envelope_ref(cls, value: Any) -> str | None:
        return _compact_reference(value, field_name="envelopeRef")


class ResiliencePolicyEnvelope(BaseModel):
    """Versioned, deterministic resilience policy attached to a workflow run.

    The envelope carries a content digest computed deterministically from the
    governing values (independent of ``compiled_at`` / run identity) so the same
    policy values always produce the same ``policy_id`` and ``digest``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field(
        RESILIENCE_POLICY_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    content_type: Literal[RESILIENCE_POLICY_CONTENT_TYPE] = Field(
        RESILIENCE_POLICY_CONTENT_TYPE,
        alias="contentType",
    )
    policy_version: int = Field(..., alias="policyVersion", ge=1)
    policy_id: str | None = Field(None, alias="policyId")
    digest: str | None = Field(None, alias="digest")
    compiled_at: datetime = Field(..., alias="compiledAt")
    workflow_id: str | None = Field(None, alias="workflowId")
    run_id: str | None = Field(None, alias="runId")

    attempts: ResilienceAttemptsPolicy
    timeouts: ResilienceTimeoutsPolicy
    provider_cooldown: ResilienceProviderCooldownPolicy = Field(
        ..., alias="providerCooldown"
    )
    checkpoints: ResilienceCheckpointPolicy
    idempotency: ResilienceIdempotencyPolicy
    outbound_scanning: ResilienceOutboundScanPolicy = Field(
        ..., alias="outboundScanning"
    )
    observability: ResilienceObservabilityPolicy
    cost_attribution: ResilienceCostAttributionPolicy = Field(
        ..., alias="costAttribution"
    )

    # Optional artifact ref for verbose, forensic-only details (kept out of the
    # compact envelope to preserve Temporal payload discipline).
    details_ref: str | None = Field(None, alias="detailsRef")
    # Secrets are references only; raw secret material must never be embedded.
    secret_refs: dict[str, str] = Field(default_factory=dict, alias="secretRefs")

    @field_validator("workflow_id", "run_id", mode="before")
    @classmethod
    def _normalize_optional_identity(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("details_ref", mode="before")
    @classmethod
    def _validate_details_ref(cls, value: Any) -> str | None:
        return _compact_reference(value, field_name="detailsRef")

    def _fingerprint_payload(self) -> dict[str, Any]:
        """Canonical, identity-independent content used to derive the digest."""

        return {
            "schemaVersion": self.schema_version,
            "policyVersion": self.policy_version,
            "attempts": self.attempts.model_dump(by_alias=True, mode="json"),
            "timeouts": self.timeouts.model_dump(by_alias=True, mode="json"),
            "providerCooldown": self.provider_cooldown.model_dump(
                by_alias=True, mode="json"
            ),
            "checkpoints": self.checkpoints.model_dump(by_alias=True, mode="json"),
            "idempotency": self.idempotency.model_dump(by_alias=True, mode="json"),
            "outboundScanning": self.outbound_scanning.model_dump(
                by_alias=True, mode="json"
            ),
            "observability": self.observability.model_dump(
                by_alias=True, mode="json"
            ),
            "costAttribution": self.cost_attribution.model_dump(
                by_alias=True, mode="json"
            ),
            "detailsRef": self.details_ref,
            "secretRefs": {key: self.secret_refs[key] for key in sorted(self.secret_refs)},
        }

    def compute_digest(self) -> str:
        """Return the deterministic sha256 digest of the governing values."""

        encoded = json.dumps(
            self._fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def _finalize(self) -> "ResiliencePolicyEnvelope":
        # Secrets must be references only (never raw secret material).
        for key, value in self.secret_refs.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                raise ResiliencePolicyError("secretRefs keys must be non-empty")
            candidate = str(value).strip()
            _compact_reference(candidate, field_name=f"secretRefs[{normalized_key}]")
            if "://" not in candidate and not candidate.startswith(("secret:", "${")):
                raise ResiliencePolicyError(
                    "secretRefs must contain references, not raw secret values"
                )

        expected_digest = self.compute_digest()
        if self.digest is None:
            self.digest = expected_digest
        elif self.digest != expected_digest:
            raise ResiliencePolicyError(
                "resilience policy digest does not match its content"
            )

        expected_id = f"resilience-policy-{expected_digest[:16]}"
        if self.policy_id is None:
            self.policy_id = expected_id
        elif self.policy_id != expected_id:
            raise ResiliencePolicyError(
                "resilience policy policyId does not match its content digest"
            )

        # Enforce compact-metadata / artifact-ref discipline for the whole
        # envelope: no raw bytes, bounded size, JSON serializable.
        validate_compact_temporal_mapping(
            self.model_dump(by_alias=True, mode="json"),
            field_name="resiliencePolicy",
        )
        return self

    def compact_ref(self, *, envelope_ref: str | None = None) -> ResiliencePolicyRef:
        """Return the compact reference for memo/step-execution attachment."""

        if not self.policy_id or not self.digest:
            # _finalize always populates these; guard defensively.
            raise ResiliencePolicyError("envelope must be finalized before referencing")
        return ResiliencePolicyRef(
            policyId=self.policy_id,
            policyVersion=self.policy_version,
            digest=self.digest,
            contentType=self.content_type,
            envelopeRef=envelope_ref,
        )


def compile_resilience_policy(
    *,
    compiled_at: datetime,
    attempts: Mapping[str, Any] | ResilienceAttemptsPolicy,
    timeouts: Mapping[str, Any] | ResilienceTimeoutsPolicy,
    provider_cooldown: Mapping[str, Any] | ResilienceProviderCooldownPolicy,
    checkpoints: Mapping[str, Any] | ResilienceCheckpointPolicy,
    idempotency: Mapping[str, Any] | ResilienceIdempotencyPolicy,
    outbound_scanning: Mapping[str, Any] | ResilienceOutboundScanPolicy,
    observability: Mapping[str, Any] | ResilienceObservabilityPolicy,
    cost_attribution: Mapping[str, Any] | ResilienceCostAttributionPolicy,
    policy_version: int = 1,
    workflow_id: str | None = None,
    run_id: str | None = None,
    details_ref: str | None = None,
    secret_refs: Mapping[str, str] | None = None,
) -> ResiliencePolicyEnvelope:
    """Deterministically compile a versioned ResiliencePolicy envelope.

    Inputs are explicit resolved values: the builder performs no environment or
    provider-manager inference, so the resulting envelope is the single
    deterministic source of resilience values for the run. Missing or
    unsupported values fail fast with :class:`ResiliencePolicyError`.
    """

    required_sections: dict[str, Any] = {
        "attempts": attempts,
        "timeouts": timeouts,
        "providerCooldown": provider_cooldown,
        "checkpoints": checkpoints,
        "idempotency": idempotency,
        "outboundScanning": outbound_scanning,
        "observability": observability,
        "costAttribution": cost_attribution,
    }
    for name, value in required_sections.items():
        if value is None:
            raise ResiliencePolicyError(
                f"resilience policy requires '{name}' values"
            )

    try:
        return ResiliencePolicyEnvelope(
            policyVersion=policy_version,
            compiledAt=compiled_at,
            workflowId=workflow_id,
            runId=run_id,
            attempts=attempts,
            timeouts=timeouts,
            providerCooldown=provider_cooldown,
            checkpoints=checkpoints,
            idempotency=idempotency,
            outboundScanning=outbound_scanning,
            observability=observability,
            costAttribution=cost_attribution,
            detailsRef=details_ref,
            secretRefs=dict(secret_refs or {}),
        )
    except ResiliencePolicyError:
        raise
    except ValidationError as exc:
        raise ResiliencePolicyError(
            f"resilience policy failed validation: {exc.errors()}"
        ) from exc


__all__ = [
    "RESILIENCE_POLICY_CONTENT_TYPE",
    "RESILIENCE_POLICY_SCHEMA_VERSION",
    "ResilienceAttemptsPolicy",
    "ResilienceCheckpointBoundary",
    "ResilienceCheckpointPolicy",
    "ResilienceCostAttributionPolicy",
    "ResilienceIdempotencyPolicy",
    "ResilienceIdempotencyStrategy",
    "ResilienceObservabilityPolicy",
    "ResilienceOutboundScanPolicy",
    "ResiliencePolicyEnvelope",
    "ResiliencePolicyError",
    "ResiliencePolicyRef",
    "ResilienceProviderCooldownPolicy",
    "ResilienceTimeoutsPolicy",
    "compile_resilience_policy",
]
