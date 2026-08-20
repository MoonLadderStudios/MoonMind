"""Execution lifecycle ordering and control-plane integration (sections 18, 19).

The generic platform uses canonical Omnigent control-plane aggregates:
- OmnigentSession owns immutable refs, runtime-binding ref, session authority, fencing generations
- OmnigentTurnAttempt owns idempotency, cannot replace plan/binding
- OmnigentObservation records bounded evidence, full payloads remain artifact-backed
- OmnigentCommand journals side effects

Lifecycle order 1-35 must be preserved; retry reuses same plan/binding/generations/skills/session/workspace/host authority.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Execution lifecycle steps - used for validation and observability
LIFECYCLE_STEPS = [
    "validate_workflow_and_step_authority",
    "resolve_agent_profile_snapshot",
    "resolve_harness_catalog_implementation_trust",
    "resolve_agent_source",
    "resolve_skills_snapshot",
    "resolve_credential_binding_set",
    "resolve_provider_profiles",
    "select_materializers",
    "select_host_class_and_launch_policy",
    "compute_class_admission",
    "normalize_model_config",
    "select_execution_realizer_and_support_key",
    "compile_execution_plan_envelope",
    "acquire_provider_leases_deterministic",
    "persist_runtime_binding_with_generations",
    "resolve_host_binding_and_lease",
    "materialize_workspace",
    "materialize_credentials_with_generations",
    "start_or_attach_host",
    "exact_host_harness_attestation",
    "validate_implementation_and_runtimes",
    "validate_exact_host_capabilities_mounts_network_skills",
    "resolve_and_attest_live_model_options",
    "persist_runtime_binding_refs",
    "create_or_reattach_session",
    "persist_session_identity",
    "prepare_and_post_first_message_idempotent",
    "stream_and_normalize_events",
    "route_approvals_intervention_control",
    "harvest_artifacts_evidence_checkpoints",
    "stop_or_drain_session",
    "cleanup_materializer_state",
    "remove_or_release_host",
    "persist_terminal_cleanup_evidence",
    "release_provider_leases_last",
]


def validate_lifecycle_order(completed: list[str]) -> None:
    """Ensure steps were executed in canonical order without skipping required authority steps.

    Validates that the *given order* respects the canonical total order.
    """
    # Map step to its logical position
    pos = {step: idx for idx, step in enumerate(LIFECYCLE_STEPS)}
    # Check given order is increasing in logical order
    last_pos = -1
    seen: set[str] = set()
    mandatory_pre_session = {
        "compile_execution_plan_envelope",
        "acquire_provider_leases_deterministic",
        "persist_runtime_binding_with_generations",
        "resolve_host_binding_and_lease",
        "materialize_workspace",
        "materialize_credentials_with_generations",
        "start_or_attach_host",
        "exact_host_harness_attestation",
        "validate_implementation_and_runtimes",
        "validate_exact_host_capabilities_mounts_network_skills",
        "resolve_and_attest_live_model_options",
        "persist_runtime_binding_refs",
    }
    for step in completed:
        if step not in pos:
            continue
        cur = pos[step]
        if cur <= last_pos:
            raise ValueError(f"lifecycle step out of order: {step} after {LIFECYCLE_STEPS[last_pos]}")
        # Also check authority constraints based on logical positions, not just sorted
        # Lease must happen after plan commitment
        if step == "acquire_provider_leases_deterministic" and "compile_execution_plan_envelope" not in seen:
            raise ValueError("lease acquisition must happen after plan commitment")
        # Session creation forbidden until all mandatory pre-session steps pass
        if step == "create_or_reattach_session":
            missing = mandatory_pre_session - seen
            if missing:
                raise ValueError(f"session creation forbidden until mandatory steps complete: {sorted(missing)}")
        last_pos = cur
        seen.add(step)
    # Ensure cleanup last: if release appears, it must be last in the completed list (ignoring unknown steps)
    if "release_provider_leases_last" in completed:
        # Find last known step
        last_known = None
        for step in reversed(completed):
            if step in pos:
                last_known = step
                break
        if last_known != "release_provider_leases_last":
            raise ValueError("Provider Profile lease release must be last")


class OmnigentSessionAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str = Field(alias="sessionId")
    agentProfileSnapshotRef: str = Field(alias="agentProfileSnapshotRef")
    agentSourceRef: str = Field(alias="agentSourceRef")
    resolvedSkillRefs: dict[str, str] = Field(alias="resolvedSkillRefs")
    executionPlanRef: str = Field(alias="executionPlanRef")
    runtimeBindingRef: str = Field(alias="runtimeBindingRef")
    providerSessionAuthority: str = Field(alias="providerSessionAuthority")
    chatBindingId: str | None = Field(default=None, alias="chatBindingId")
    desiredState: str = Field(alias="desiredState")
    observedState: str = Field(alias="observedState")
    revision: int = Field(ge=1)
    fencingGeneration: int = Field(alias="fencingGeneration")


class OmnigentTurnAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attemptId: str = Field(alias="attemptId")
    sessionId: str = Field(alias="sessionId")
    requestDigest: str = Field(alias="requestDigest")
    # Cannot replace plan or runtime binding, cannot terminalize
    planRef: str = Field(alias="planRef")
    runtimeBindingRef: str = Field(alias="runtimeBindingRef")


class OmnigentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observationId: str = Field(alias="observationId")
    sessionId: str = Field(alias="sessionId")
    boundedEvidence: dict[str, Any] = Field(alias="boundedEvidence")
    artifactRefs: list[str] = Field(default_factory=list, alias="artifactRefs")


class OmnigentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commandId: str = Field(alias="commandId")
    sessionId: str = Field(alias="sessionId")
    kind: str
    payloadRef: str = Field(alias="payloadRef")
    fencingGeneration: int = Field(alias="fencingGeneration")
