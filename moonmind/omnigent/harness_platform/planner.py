"""Omnigent Execution Planner - pure pre-host planning (sections 5, 15, 16, 19).

The planner is pure wrt provider side effects. It consumes:
- Agent Profile snapshot (v2)
- Pinned harness catalog + trust record
- Resolved Skill snapshot + delivery
- Credential-binding set version/digest
- Provider Profile compatibility
- Materializer registry
- Host Class registry
- Launch policy
- Model config -> modelConfigDigest
- Execution realizer selection (trusted planner, not workflow-authored)

It emits a canonical secret-free OmnigentExecutionPlanPayload envelope before
any provider lease or host mutation.

Lifecycle ordering preserved (19 steps 1-13).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from moonmind.omnigent.harness_platform.agent_profile import (
    OmnigentAgentProfileV2,
    validate_agent_profile,
)
from moonmind.omnigent.harness_platform.attestation import HostHarnessAttestation
from moonmind.omnigent.harness_platform.capabilities import (
    ClassAdmissionDecision,
    compute_class_admission,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessTrustRecord,
    TrustState,
    is_launchable_trust,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBindingSet,
    validate_binding_set_for_plan,
    parse_binding_set_ref,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    OmnigentExecutionPlanPayload,
    ModelConfig,
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HostClass,
    LaunchPolicy,
    get_host_class,
    get_launch_policy,
    validate_policy_for_host_class,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    validate_binding_materializer,
)
from moonmind.omnigent.harness_platform.skills import (
    ResolvedSkillSet,
    validate_skill_refs_for_plan,
)
from moonmind.omnigent.harness_platform.support import (
    compute_required_capabilities_digest,
    compute_support_combination_key,
    SupportKeyPayload,
)


# Trusted realizer selection - never workflow-authored, never overridable via Agent Profile settings
def select_execution_realizer(
    *,
    harness_id: str,
    agent_profile: OmnigentAgentProfileV2,
    is_codex: bool = False,
) -> str:
    """Select versioned execution realizer (section 6: executionRealizerRef is trusted planner only)."""
    # Codex may use explicit codex-profile-bound@1 for preservation until cutover
    # New harnesses use generic-omnigent-host@1
    # This selection is deterministic planning, not runtime fallback
    if harness_id == "codex-native" and is_codex:
        # Default to existing realizer for Codex preservation; generic requires explicit qualification
        # For now, planner selects generic if requested via profile metadata? But spec says realizer not workflow-authored
        # We select generic only if harness is not codex or if explicitly allowed after parity
        return "codex-profile-bound@1"
    return "generic-omnigent-host@1"


def compile_execution_plan(
    *,
    agent_profile: dict[str, Any] | OmnigentAgentProfileV2,
    harness_catalog: HarnessCatalogSnapshot,
    trust_record: HarnessTrustRecord,
    resolved_skills: dict[str, Any] | ResolvedSkillSet,
    credential_binding_set: CredentialBindingSet,
    host_class_ref: str,
    launch_policy_ref: str,
    model_qualified_id: str | None,
    model_effort: str | None,
    model_route_ref: str | None,
    model_normalized_options: dict[str, Any],
    workflow_requirements: list[str] | None = None,
    bridge_capabilities: dict[str, bool] | None = None,
    workspace_intent_ref: str = "workspace-intent:sha256:" + "a" * 64,
    policy_snapshot_ref: str = "omnigent-policy:sha256:" + "b" * 64,
    capture_policy_ref: str | None = None,
    execution_realizer_ref: str | None = None,
) -> OmnigentExecutionPlanEnvelope:
    # 1. Validate agent profile (resolve snapshot)
    profile = validate_agent_profile(agent_profile)

    # 2-3. Resolve catalog + trust
    if trust_record.harnessId != profile.harness.id:
        raise HarnessPlatformError(
            "trust record harness mismatch",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    if not is_launchable_trust(trust_record.trustState):
        raise HarnessPlatformError(
            f"harness {profile.harness.id} is not launchable: {trust_record.trustState}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED,
        )
    # Ensure harness exists in catalog
    catalog_harness_ids = {h.id for h in harness_catalog.harnesses}
    if profile.harness.id not in catalog_harness_ids:
        raise HarnessPlatformError(
            f"harness {profile.harness.id} unknown in catalog",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    # Catalog freshness is assumed caller validated via assert_catalog_fresh

    # 4. Agent source already validated via profile parsing (discriminated)

    # 5. Resolved skills
    skills = validate_skill_refs_for_plan(resolved_skills)

    # 6-7. Credential binding set
    required_slots = [s.id for s in profile.credentialSlots if not s.optional]
    validate_binding_set_for_plan(binding_set=credential_binding_set, required_slots=required_slots)
    # Validate each binding's materializer exists and is compatible with host class later

    # 8. Materializers + 9. Host Class + launch policy class-level admission
    host_class = get_host_class(host_class_ref)
    launch_policy = get_launch_policy(launch_policy_ref)

    # Host class must declare the harness implementation
    if not host_class.declares_harness(profile.harness.id, profile.harness.implementationRef):
        raise HarnessPlatformError(
            f"host class {host_class.ref} does not declare harness {profile.harness.id}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )

    # Policy must allow host class and integration mode
    # Derive integration mode from catalog capabilities
    harness_record = next(h for h in harness_catalog.harnesses if h.id == profile.harness.id)
    integration_mode = harness_record.capabilities.integrationMode or "native-server"
    materializer_refs = [b.materializerRef for b in credential_binding_set.bindings.values()]
    validate_policy_for_host_class(
        policy=launch_policy,
        host_class=host_class,
        harness_integration_mode=integration_mode,
        materializer_refs=materializer_refs,
    )

    # Validate each materializer supports harness and host mode
    host_mode_for_materializer = launch_policy.hostMode
    # Normalize legacy modes
    if host_mode_for_materializer == "on_demand_docker":
        host_mode_for_materializer = "on-demand"
    elif host_mode_for_materializer == "static_compose":
        host_mode_for_materializer = "static-connected"
    for slot, binding in credential_binding_set.bindings.items():
        validate_binding_materializer(
            materializer_ref=binding.materializerRef,
            harness_implementation_ref=profile.harness.implementationRef,
            host_mode=host_mode_for_materializer,
        )

    # 10. Class-level capability admission
    wf_reqs = workflow_requirements or []
    prof_reqs = {
        "required": list(profile.requirements.harness.required) + list(profile.requirements.moonmind.required) + list(profile.requirements.host.required),
        "preferred": list(profile.requirements.harness.preferred),
    }
    catalog_caps: dict[str, Any] = {}
    for h in harness_catalog.harnesses:
        if h.id == profile.harness.id:
            caps = h.capabilities.model_dump(by_alias=True, mode="json")
            catalog_caps = {k: v for k, v in caps.items() if v is not None}
            break
    host_caps: dict[str, bool] = {k: bool(v) for k, v in host_class.features.items()}
    materializer_caps: dict[str, bool] = {}
    for ref in materializer_refs:
        try:
            mat = get_materializer(ref)
            # materializers don't directly expose capability booleans; assume compatible
            materializer_caps[ref] = True
        except Exception:
            pass
    bridge_caps = bridge_capabilities or {}
    policy_caps = list(launch_policy.controlCapabilities)

    class_decision = compute_class_admission(
        workflow_requirements=wf_reqs,
        profile_requirements=prof_reqs,
        catalog_capabilities=catalog_caps,
        host_class_capabilities=host_caps,
        materializer_capabilities=materializer_caps,
        bridge_capabilities=bridge_caps,
        launch_policy_capabilities=policy_caps,
    )

    # 11. Normalize model config + digest
    model_digest = compute_model_config_digest(
        qualifiedId=model_qualified_id,
        effort=model_effort,
        routeRef=model_route_ref,
        normalizedOptions=model_normalized_options,
    )

    # 12. Select execution realizer + compute support combination key
    realizer = execution_realizer_ref or select_execution_realizer(
        harness_id=profile.harness.id,
        agent_profile=profile,
        is_codex=(profile.harness.id == "codex-native"),
    )
    # Validate realizer exists
    from moonmind.omnigent.harness_platform.support import validate_realizer
    try:
        validate_realizer(realizer)
    except Exception as exc:
        raise HarnessPlatformError(
            f"execution realizer {realizer} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
        ) from exc

    required_caps_digest = compute_required_capabilities_digest(list(class_decision.requiredSatisfied))

    support_payload = SupportKeyPayload.model_validate(
        {
            "omnigentServerBuildRef": harness_catalog.omnigentBuildDigest,
            "omnigentHostBuildRef": host_class.omnigentBuildDigest,
            "harnessImplementationRef": profile.harness.implementationRef,
            "vendorRuntimeRefs": [],
            "agentSourceRef": profile.source.model_dump(by_alias=True, mode="json").get("upstreamId") or profile.source.model_dump(by_alias=True, mode="json").get("importedAgentId") or "unknown",
            "materializerRefs": sorted(materializer_refs),
            "providerCompatibilityClass": credential_binding_set.bindingSetId,
            "hostClassRef": host_class.ref,
            "architecture": host_class.architectures[0] if host_class.architectures else "linux/amd64",
            "launchPolicyRef": launch_policy.ref,
            "modelConfigDigest": model_digest,
            "executionRealizerRef": realizer,
            "requiredCapabilitiesDigest": required_caps_digest,
        }
    )
    support_key = compute_support_combination_key(support_payload)

    # Build plan payload
    runtime_validation_requirements = (
        "exact-harness-implementation",
        "exact-vendor-runtime",
        "exact-network-egress",
        "exact-skill-delivery",
        "live-model-option",
    )

    # Agent source already normalized via profile.source
    agent_source_dict = profile.source.model_dump(by_alias=True, mode="json")

    plan_payload = OmnigentExecutionPlanPayload.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
            "endpointRef": profile.endpointRef,
            "agentProfileSnapshotRef": profile.snapshot_ref(),
            "harnessCatalogRef": harness_catalog.catalogRef,
            "harnessId": profile.harness.id,
            "harnessImplementationRef": profile.harness.implementationRef,
            "agentSource": agent_source_dict,
            "credentialBindingSetRef": credential_binding_set.ref,
            "credentialBindings": {slot: b.model_dump(by_alias=True, mode="json") for slot, b in credential_binding_set.bindings.items()},
            "hostClassRef": host_class.ref,
            "launchPolicyRef": launch_policy.ref,
            "executionRealizerRef": realizer,
            "model": {
                "qualifiedId": model_qualified_id,
                "effort": model_effort,
                "routeRef": model_route_ref,
                "normalizedOptions": model_normalized_options,
                "modelConfigDigest": model_digest,
            },
            "resolvedSkills": skills.model_dump(by_alias=True, mode="json"),
            "classAdmissionDecision": class_decision.model_dump(by_alias=True, mode="json"),
            "runtimeValidationRequirements": runtime_validation_requirements,
            "workspaceIntentRef": workspace_intent_ref,
            "capturePolicyRef": capture_policy_ref,
            "policySnapshotRef": policy_snapshot_ref,
            "supportCombinationKey": support_key,
        }
    )

    # 13. Compile, canonicalize, hash, persist envelope
    envelope = create_execution_plan_envelope(plan_payload)
    return envelope
