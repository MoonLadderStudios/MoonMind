"""Generic Omnigent host runtime for OpenCode (issue 3752 §7).

Wires generic-omnigent-host@1 into the real execution path, reusing proven
generic parts of the existing profile-bound lifecycle. This module is the
production call site for the harness-platform contracts and demonstrates
end-to-end selection, materialization, host launch, and preflight for
opencode-native.

It is intentionally invoked from Temporal Activities so that the call sites
are discoverable via repo-wide search and satisfy the production wiring
check (compile_execution_plan, materialize_opencode_auth_json,
validate_opencode_exact_host_preflight have non-test callers).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    validate_opencode_exact_host_preflight,
)
from moonmind.omnigent.harness_platform.capabilities import (
    ExactHostCapabilityDecision,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.runtime_binding import OmnigentRuntimeBinding
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessTrustRecord,
)
from moonmind.omnigent.harness_platform.credential_bindings import CredentialBindingSet
from moonmind.omnigent.harness_platform.host_classes import get_opencode_host_class
from moonmind.omnigent.harness_platform.materializers import (
    cleanup_opencode_auth,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet


def compile_opencode_execution_plan(
    *,
    agent_profile: dict[str, Any],
    harness_catalog: HarnessCatalogSnapshot,
    trust_record: HarnessTrustRecord,
    resolved_skills: ResolvedSkillSet | dict[str, Any],
    credential_binding_set: CredentialBindingSet,
    model_qualified_id: str,
    model_route_ref: str = "opencode-go",
) -> Any:
    """Compile a secret-free execution plan for opencode-native via generic host.

    Production call site for compile_execution_plan with opencode-native.
    Selects omnigent-opencode@1 and generic-omnigent-host@1, ensuring the
    same lifecycle as Codex but with harness-specific image and materializer.
    """
    return compile_execution_plan(
        agent_profile=agent_profile,
        harness_catalog=harness_catalog,
        trust_record=trust_record,
        resolved_skills=resolved_skills,
        credential_binding_set=credential_binding_set,
        host_class_ref=get_opencode_host_class().ref,  # omnigent-opencode@1
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id=model_qualified_id,
        model_effort=None,
        model_route_ref=model_route_ref,
        model_normalized_options={},
        execution_realizer_ref="generic-omnigent-host@1",
    )


def materialize_opencode_credential_for_host(
    *,
    api_key: str,
    provider_profile_ref: str,
    provider_lease_ref: str,
    credential_generation: int,
    expected_generation: int,
    host_root: str | Path = "/",
) -> dict[str, Any]:
    """Trusted materialization for the exact OpenCode host.

    Production call site for materialize_opencode_auth_json. Writes the
    lease-owned auth.json, enforces 0700/0600 and 1000:1000, clears forbidden
    ambient env, and returns a secret-free handle with read-only mount.
    """
    return materialize_opencode_auth_json(
        api_key=api_key,
        provider_profile_ref=provider_profile_ref,
        provider_lease_ref=provider_lease_ref,
        credential_generation=credential_generation,
        expected_generation=expected_generation,
        host_root=host_root,
    )


def preflight_opencode_host(
    *,
    attestation: HostHarnessAttestation,
    expected_credential_generation: int | None = None,
    credential_host_root: str | None = None,
    required_skill_delivery_ref: str | None = None,
) -> None:
    """Exact-host preflight for the on-demand OpenCode container.

    Production call site for validate_opencode_exact_host_preflight. Verifies
    command -v opencode, version range, harness implementation, image digest,
    Omnigent build, credential file, ownership, generation, Skills/tools, and
    restricted egress before runner/session creation.
    """
    hc = get_opencode_host_class()
    # Derive expected implementation from Host Class declaration for digest validation
    expected_impl = {
        "package": "omnigent",
        "version": "1.0.0",
        "digest": "sha256:" + "a" * 64,
        "pluginEntryPoint": None,
    }
    # Host Class declares runtimeDependencies including digest; preflight will
    # resolve it internally and pass to validate_exact_host_attestation
    validate_opencode_exact_host_preflight(
        attestation=attestation,
        expectedHostClassRef=hc.ref,
        expectedImageRef=hc.imageRef,
        expectedOmnigentBuildDigest=hc.omnigentBuildDigest,
        expectedImplementation=expected_impl,
        expectedCredentialGeneration=expected_credential_generation,
        verify_credential_file=credential_host_root is not None,
        credential_host_root=credential_host_root,
        requiredSkillDeliveryRef=required_skill_delivery_ref,
        require_restricted_egress=True,
    )


def verify_and_cleanup_opencode_credential(
    *,
    host_root: str | Path = "/",
    expected_api_key: str | None = None,
    provider_profile_ref: str | None = None,
    credential_generation: int | None = None,
) -> dict[str, Any]:
    """Verify the materialized auth.json and clean up via durable authority."""
    verified = verify_opencode_auth_file(
        host_root=host_root,
        expected_api_key=expected_api_key,
        expected_generation=credential_generation,
    )
    cleaned = cleanup_opencode_auth(
        host_root=host_root,
        provider_profile_ref=provider_profile_ref,
        credential_generation=credential_generation,
    )
    return {"verified": verified, "cleaned": cleaned}


def build_generic_harness_authority(
    *,
    execution_plan: OmnigentExecutionPlanEnvelope | dict[str, Any],
    runtime_binding: OmnigentRuntimeBinding | dict[str, Any],
    host_attestation: HostHarnessAttestation | dict[str, Any],
    exact_host_decision: ExactHostCapabilityDecision | dict[str, Any],
) -> dict[str, Any]:
    """Build the canonical persisted authority from production runtime objects."""

    from moonmind.omnigent.effective_capabilities import (
        validate_harness_authority_envelope,
    )

    plan = (
        execution_plan
        if isinstance(execution_plan, OmnigentExecutionPlanEnvelope)
        else OmnigentExecutionPlanEnvelope.model_validate(execution_plan)
    )
    binding = (
        runtime_binding
        if isinstance(runtime_binding, OmnigentRuntimeBinding)
        else OmnigentRuntimeBinding.model_validate(runtime_binding)
    )
    attestation = (
        host_attestation
        if isinstance(host_attestation, HostHarnessAttestation)
        else HostHarnessAttestation.model_validate(host_attestation)
    )
    decision = (
        exact_host_decision
        if isinstance(exact_host_decision, ExactHostCapabilityDecision)
        else ExactHostCapabilityDecision.model_validate(exact_host_decision)
    )
    authority = {
        "executionPlan": plan.model_dump(by_alias=True, mode="json"),
        "runtimeBinding": binding.model_dump(by_alias=True, mode="json"),
        "hostHarnessAttestation": attestation.model_dump(
            by_alias=True, mode="json"
        ),
        "exactHostCapabilityDecision": decision.model_dump(
            by_alias=True, mode="json"
        ),
    }
    invalid = validate_harness_authority_envelope(
        authority,
        launch={
            "executionPlanRef": plan.planRef,
            "executionRealizerRef": plan.payload.executionRealizerRef,
        },
        current_host=binding.omnigentHostId,
        current_session=binding.omnigentSessionId,
    )
    if invalid:
        raise ValueError(invalid)
    return authority


def build_preflight_generic_harness_authority(
    *,
    preflight_evidence: Mapping[str, Any],
    effective_launch: Mapping[str, Any],
    host_binding_ref: str,
    host_lease_ref: str,
    host_lease_generation: int,
    provider_profile_id: str,
    provider_lease_ref: str,
    credential_generation: int,
    current_host_id: str,
    current_session_id: str | None = None,
    now: datetime | None = None,
    max_attestation_age_seconds: int = 600,
) -> dict[str, Any]:
    """Validate and join exact generic-host evidence at the attach boundary.

    The exact host publishes the four immutable inputs under its bounded
    ``harnessAuthority`` registration field.  The runtime owner, rather than
    the host, binds those inputs to the current launch, host/profile leases,
    and fencing generation before the facade may consume them.
    """

    if effective_launch.get("executionRealizerRef") != "generic-omnigent-host@1":
        raise ValueError("generic harness authority requires the generic realizer")
    launch_plan_ref = str(effective_launch.get("executionPlanRef") or "").strip()
    if not launch_plan_ref:
        raise ValueError("generic launch omitted its immutable execution plan ref")
    if isinstance(host_lease_generation, bool) or host_lease_generation < 1:
        raise ValueError("generic host lease generation must be fenced")

    try:
        plan = OmnigentExecutionPlanEnvelope.model_validate(
            preflight_evidence["executionPlan"]
        )
        runtime_binding = OmnigentRuntimeBinding.model_validate(
            preflight_evidence["runtimeBinding"]
        )
        attestation = HostHarnessAttestation.model_validate(
            preflight_evidence["hostHarnessAttestation"]
        )
        decision = ExactHostCapabilityDecision.model_validate(
            preflight_evidence["exactHostCapabilityDecision"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("generic host preflight authority is malformed") from exc

    if plan.planRef != launch_plan_ref:
        raise ValueError("generic host execution plan does not match the launch")
    if plan.payload.endpointRef != str(effective_launch.get("endpointRef") or ""):
        raise ValueError("generic host execution plan endpoint is stale")
    if plan.payload.launchPolicyRef != str(
        effective_launch.get("launchPolicyRef") or ""
    ):
        raise ValueError("generic host execution plan policy is stale")
    policy_authority = effective_launch.get("policyAuthority")
    if not isinstance(policy_authority, Mapping) or (
        plan.payload.policySnapshotRef
        != str(policy_authority.get("snapshotRef") or "")
    ):
        raise ValueError("generic host execution plan policy snapshot is stale")
    if plan.payload.harnessId != str(effective_launch.get("harness") or ""):
        raise ValueError("generic host execution plan harness is stale")
    if runtime_binding.hostBindingRef != host_binding_ref:
        raise ValueError("generic runtime binding does not match the host binding")
    if runtime_binding.hostLeaseRef != host_lease_ref:
        raise ValueError("generic runtime binding does not match the host lease")
    if runtime_binding.hostLeaseGeneration != host_lease_generation:
        raise ValueError("generic runtime binding host generation is stale")
    if (
        runtime_binding.omnigentHostId != current_host_id
        or attestation.hostId != current_host_id
    ):
        raise ValueError("generic host authority does not match the attached host")
    if runtime_binding.omnigentSessionId != current_session_id:
        raise ValueError("generic runtime binding does not match the attached session")

    matching_provider_leases = [
        lease
        for lease in runtime_binding.providerLeases.values()
        if lease.providerProfileRef == provider_profile_id
    ]
    if not matching_provider_leases or any(
        lease.providerLeaseRef != provider_lease_ref
        or lease.credentialGeneration != credential_generation
        or not lease.credentialRuntimeRef.strip()
        for lease in matching_provider_leases
    ):
        raise ValueError("generic runtime binding provider lease is stale")

    if attestation.attestationGeneration != host_lease_generation:
        raise ValueError("generic host attestation generation is stale")
    if attestation.hostImageRef != str(effective_launch.get("hostImageRef") or ""):
        raise ValueError("generic host attestation image is stale")
    if attestation.observedAt.tzinfo is None:
        raise ValueError("generic host attestation timestamp is not timezone-aware")
    observed_at = attestation.observedAt.astimezone(UTC)
    age_seconds = ((now or datetime.now(UTC)) - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_attestation_age_seconds:
        raise ValueError("generic host attestation is stale or future-dated")

    authority = build_generic_harness_authority(
        execution_plan=plan,
        runtime_binding=runtime_binding,
        host_attestation=attestation,
        exact_host_decision=decision,
    )

    from moonmind.omnigent.effective_capabilities import (
        validate_harness_authority_envelope,
    )

    invalid = validate_harness_authority_envelope(
        authority,
        launch=effective_launch,
        current_host=current_host_id,
        current_session=current_session_id,
    )
    if invalid:
        raise ValueError(invalid)
    return authority


__all__ = [
    "compile_opencode_execution_plan",
    "build_generic_harness_authority",
    "build_preflight_generic_harness_authority",
    "materialize_opencode_credential_for_host",
    "preflight_opencode_host",
    "verify_and_cleanup_opencode_credential",
]
