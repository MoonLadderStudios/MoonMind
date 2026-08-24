"""Frozen-generation admission and shadow contract for the Omnigent session
supervisor (``MoonMind.OmnigentSession``).

Source issue: MoonLadderStudios/MoonMind#3712.

New sessions are admitted to the ``MoonMind.OmnigentSession`` supervisor only
under an explicit frozen feature generation and exact canary allowlists. The
returned :class:`SupervisorAdmissionSnapshot` is immutable input evidence
computed before workflow creation; an admitted workflow keeps the generation
recorded in its snapshot, so changing the deployed generation never
reinterprets already-admitted workflows.

Contract invariants (issue #3712 "Feature and canary controls"):

* Allowlists are matched by exact membership, never substring.
* The admission snapshot records the selected generation and canary decision.
* Disabling the flag blocks new admissions only. It never affects replay,
  cancellation, cleanup, or read-only diagnostics for already-admitted
  sessions (those are separate controls owned by
  :mod:`moonmind.omnigent.session_supervisor_rollback`).
* Shadow mode computes and records the decision but is never permitted to issue
  provider, host, workspace, publication, cleanup, or lease mutations
  (``side_effects_allowed`` is ``False`` in shadow).
* Canary and general selection use the same execution contract and differ only
  in admission policy (the allowlist scoping recorded on the snapshot).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The single canonical supervisor workflow type new sessions are admitted to.
# Identity is a stable string, never a provider-specific synonym or alias.
OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE = "MoonMind.OmnigentSession"
SUPERVISOR_ADMISSION_POLICY_VERSION = "moonmind.omnigent-session-supervisor-admission/v1"
DISABLED_GENERATION = "disabled"

AdmissionMode = Literal["denied", "shadow", "live"]

# Ordered reason chain. The first failing gate wins so the reason code is stable
# and deterministic for a given (policy, readiness, request) triple.
SUPERVISOR_DISABLED = "supervisor_disabled"
GENERATION_DISABLED = "generation_disabled"
SUPERVISOR_WORKFLOW_NOT_REGISTERED = "supervisor_workflow_not_registered"
COMPILED_INTENT_INCOMPLETE = "compiled_intent_incomplete"
CANONICAL_SCHEMA_NOT_READY = "canonical_schema_not_ready"
EXACT_ARTIFACT_CONFORMANCE_FAILED = "exact_artifact_conformance_failed"
PROVIDER_CAPABILITY_NOT_READY = "provider_capability_not_ready"
RUNTIME_CAPABILITY_NOT_READY = "runtime_capability_not_ready"
ROLLBACK_SUPPORT_INACTIVE = "rollback_support_inactive"
HISTORICAL_READ_SUPPORT_INACTIVE = "historical_read_support_inactive"
DEPLOYMENT_GENERATION_MISMATCH = "deployment_generation_mismatch"
OWNER_NOT_ALLOWLISTED = "owner_not_allowlisted"
EXECUTION_PROFILE_NOT_ALLOWLISTED = "execution_profile_not_allowlisted"
LAUNCH_POLICY_NOT_ALLOWLISTED = "launch_policy_not_allowlisted"
PROVIDER_PROFILE_NOT_ALLOWLISTED = "provider_profile_not_allowlisted"
ELIGIBLE = "eligible"


class SupervisorRolloutPolicy(BaseModel):
    """Operator-configured admission policy, bound before workflow creation."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    shadow: bool = Field(False, alias="shadow")
    generation: str = Field(DISABLED_GENERATION, alias="generation")
    allowed_owner_ids: frozenset[str] = Field(
        default_factory=frozenset, alias="allowedOwnerIds"
    )
    allowed_execution_profile_refs: frozenset[str] = Field(
        default_factory=frozenset, alias="allowedExecutionProfileRefs"
    )
    allowed_launch_policy_refs: frozenset[str] = Field(
        default_factory=frozenset, alias="allowedLaunchPolicyRefs"
    )
    allowed_provider_profile_ids: frozenset[str] = Field(
        default_factory=frozenset, alias="allowedProviderProfileIds"
    )
    policy_version: str = Field(
        SUPERVISOR_ADMISSION_POLICY_VERSION, alias="policyVersion"
    )

    @property
    def canary_scoped(self) -> bool:
        """Whether any exact allowlist narrows admission below the generation."""

        return bool(
            self.allowed_owner_ids
            or self.allowed_execution_profile_refs
            or self.allowed_launch_policy_refs
            or self.allowed_provider_profile_ids
        )


class SupervisorReadiness(BaseModel):
    """Deployment readiness evidence required before the supervisor may admit."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    deployment_generation: str = Field(alias="deploymentGeneration")
    # The production ``MoonMind.OmnigentSession`` Temporal workflow must actually
    # be registered and wired into the launch boundary before any session is
    # admitted. Until that integration lands, this evidence is False and
    # admission fails closed rather than exposing settings that silently admit
    # nothing (issue #3712 "Register and invoke the supervisor workflow").
    supervisor_workflow_registered: bool = Field(
        alias="supervisorWorkflowRegistered"
    )
    compiled_intent_ready: bool = Field(alias="compiledIntentReady")
    canonical_schema_ready: bool = Field(alias="canonicalSchemaReady")
    exact_artifact_conformance_passed: bool = Field(
        alias="exactArtifactConformancePassed"
    )
    provider_capability_ready: bool = Field(alias="providerCapabilityReady")
    runtime_capability_ready: bool = Field(alias="runtimeCapabilityReady")
    rollback_support_active: bool = Field(alias="rollbackSupportActive")
    historical_read_support_active: bool = Field(alias="historicalReadSupportActive")


class SupervisorAdmissionRequest(BaseModel):
    """The scope of one new-session admission request."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    owner_id: str | None = Field(None, alias="ownerId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")
    launch_policy_ref: str | None = Field(None, alias="launchPolicyRef")
    provider_profile_id: str | None = Field(None, alias="providerProfileId")


class SupervisorAdmissionSnapshot(BaseModel):
    """Replay-safe admission decision recorded on the new session.

    Workflows must never re-read the live policy: they carry this immutable
    snapshot. ``generation`` is frozen here so a later generation change cannot
    reinterpret an already-admitted workflow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    admitted: bool
    mode: AdmissionMode
    eligible: bool
    reason_code: str = Field(alias="reasonCode")
    generation: str
    workflow_type: str = Field(
        OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE, alias="workflowType"
    )
    canary_scoped: bool = Field(alias="canaryScoped")
    side_effects_allowed: bool = Field(alias="sideEffectsAllowed")
    shadow_recorded: bool = Field(alias="shadowRecorded")
    owner_id: str | None = Field(None, alias="ownerId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")
    launch_policy_ref: str | None = Field(None, alias="launchPolicyRef")
    provider_profile_id: str | None = Field(None, alias="providerProfileId")
    policy_version: str = Field(
        SUPERVISOR_ADMISSION_POLICY_VERSION, alias="policyVersion"
    )

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def _csv_set(value: object) -> frozenset[str]:
    return frozenset(
        item.strip() for item in str(value or "").split(",") if item.strip()
    )


def supervisor_rollout_policy_from_settings(
    feature_flags: object,
) -> SupervisorRolloutPolicy:
    """Bind operator feature flags to the canonical admission policy.

    A blank or ``disabled`` generation fails admission closed. Allowlists are
    parsed as exact-match sets.
    """

    generation = str(
        getattr(feature_flags, "omnigent_session_supervisor_generation", "")
        or ""
    ).strip() or DISABLED_GENERATION
    return SupervisorRolloutPolicy(
        enabled=bool(
            getattr(feature_flags, "omnigent_session_supervisor_enabled", False)
        ),
        shadow=bool(
            getattr(feature_flags, "omnigent_session_supervisor_shadow", False)
        ),
        generation=generation,
        allowedOwnerIds=_csv_set(
            getattr(feature_flags, "omnigent_session_supervisor_allowed_owner_ids", "")
        ),
        allowedExecutionProfileRefs=_csv_set(
            getattr(
                feature_flags,
                "omnigent_session_supervisor_allowed_execution_profile_refs",
                "",
            )
        ),
        allowedLaunchPolicyRefs=_csv_set(
            getattr(
                feature_flags,
                "omnigent_session_supervisor_allowed_launch_policy_refs",
                "",
            )
        ),
        allowedProviderProfileIds=_csv_set(
            getattr(
                feature_flags,
                "omnigent_session_supervisor_allowed_provider_profile_ids",
                "",
            )
        ),
    )


def evaluate_supervisor_admission(
    *,
    policy: SupervisorRolloutPolicy,
    readiness: SupervisorReadiness,
    request: SupervisorAdmissionRequest,
) -> SupervisorAdmissionSnapshot:
    """Return a bounded, deterministic admission snapshot.

    The decision fails closed on the first unmet gate. Shadow mode never yields
    ``side_effects_allowed`` even when every gate passes.
    """

    reason = ELIGIBLE
    if not policy.enabled:
        reason = SUPERVISOR_DISABLED
    elif not policy.generation or policy.generation == DISABLED_GENERATION:
        reason = GENERATION_DISABLED
    elif not readiness.supervisor_workflow_registered:
        reason = SUPERVISOR_WORKFLOW_NOT_REGISTERED
    elif not readiness.compiled_intent_ready:
        reason = COMPILED_INTENT_INCOMPLETE
    elif not readiness.canonical_schema_ready:
        reason = CANONICAL_SCHEMA_NOT_READY
    elif not readiness.exact_artifact_conformance_passed:
        reason = EXACT_ARTIFACT_CONFORMANCE_FAILED
    elif not readiness.provider_capability_ready:
        reason = PROVIDER_CAPABILITY_NOT_READY
    elif not readiness.runtime_capability_ready:
        reason = RUNTIME_CAPABILITY_NOT_READY
    elif not readiness.rollback_support_active:
        reason = ROLLBACK_SUPPORT_INACTIVE
    elif not readiness.historical_read_support_active:
        reason = HISTORICAL_READ_SUPPORT_INACTIVE
    elif readiness.deployment_generation != policy.generation:
        reason = DEPLOYMENT_GENERATION_MISMATCH
    elif policy.allowed_owner_ids and request.owner_id not in policy.allowed_owner_ids:
        reason = OWNER_NOT_ALLOWLISTED
    elif (
        policy.allowed_execution_profile_refs
        and request.execution_profile_ref not in policy.allowed_execution_profile_refs
    ):
        reason = EXECUTION_PROFILE_NOT_ALLOWLISTED
    elif (
        policy.allowed_launch_policy_refs
        and request.launch_policy_ref not in policy.allowed_launch_policy_refs
    ):
        reason = LAUNCH_POLICY_NOT_ALLOWLISTED
    elif (
        policy.allowed_provider_profile_ids
        and request.provider_profile_id not in policy.allowed_provider_profile_ids
    ):
        reason = PROVIDER_PROFILE_NOT_ALLOWLISTED

    eligible = reason == ELIGIBLE
    if not eligible:
        mode: AdmissionMode = "denied"
    elif policy.shadow:
        mode = "shadow"
    else:
        mode = "live"

    return SupervisorAdmissionSnapshot(
        admitted=mode == "live",
        mode=mode,
        eligible=eligible,
        reasonCode=reason,
        generation=policy.generation,
        canaryScoped=policy.canary_scoped,
        sideEffectsAllowed=mode == "live",
        shadowRecorded=mode == "shadow",
        ownerId=request.owner_id,
        executionProfileRef=request.execution_profile_ref,
        launchPolicyRef=request.launch_policy_ref,
        providerProfileId=request.provider_profile_id,
        policyVersion=policy.policy_version,
    )


__all__ = [
    "OMNIGENT_SESSION_SUPERVISOR_WORKFLOW_TYPE",
    "SUPERVISOR_ADMISSION_POLICY_VERSION",
    "SUPERVISOR_WORKFLOW_NOT_REGISTERED",
    "DISABLED_GENERATION",
    "AdmissionMode",
    "SupervisorRolloutPolicy",
    "SupervisorReadiness",
    "SupervisorAdmissionRequest",
    "SupervisorAdmissionSnapshot",
    "supervisor_rollout_policy_from_settings",
    "evaluate_supervisor_admission",
]
