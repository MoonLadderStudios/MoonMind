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

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from moonmind.omnigent.harness_platform.agent_profile import (
    OmnigentAgentProfileV2,
    validate_agent_profile,
)
from moonmind.omnigent.harness_platform.capabilities import compute_class_admission
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessTrustRecord,
    is_launchable_trust,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBindingSet,
    validate_binding_set_for_plan,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    OmnigentExecutionPlanPayload,
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
    SupportKeyPayload,
    compute_required_capabilities_digest,
    compute_support_combination_key,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only import
    from moonmind.omnigent.session_supervisor_rollback import (
        RollbackExerciseDecision,
        RollbackExerciseRecord,
    )


# Trusted realizer selection - never workflow-authored, never overridable via Agent Profile settings
def select_execution_realizer(
    *,
    harness_id: str,
    is_codex: bool = False,
) -> str:
    """Select versioned execution realizer (section 6: executionRealizerRef is trusted planner only).

    The routing rule is capability-derived, not harness-name enumeration:
    ``codex-native`` keeps the retained profile-bound realizer until the exact
    shared-image Codex row passes its conformance gates (#3832). That operator
    qualification is the one gate (``generic_codex_qualified``); there is no
    silent fallback — an explicit generic selection before qualification fails
    fast, and the same plan always reconciles to the same realizer.
    ``claude-native`` follows the same fail-closed contract (#3831): the
    generic realizer is returned only after ``generic_claude_qualified`` opts
    in; otherwise planning fails fast instead of advertising an unqualified
    combination. All other harnesses own the generic realizer directly.
    """

    if harness_id == "codex-native" and is_codex:
        from moonmind.omnigent.settings import generic_codex_qualified

        if generic_codex_qualified():
            return "generic-omnigent-host@1"
        return "codex-profile-bound@1"
    if harness_id == "claude-native":
        from moonmind.omnigent.harness_platform.failures import (
            HarnessPlatformError as _HarnessPlatformError,
        )
        from moonmind.omnigent.harness_platform.failures import (
            HarnessPlatformFailure as _HarnessPlatformFailure,
        )
        from moonmind.omnigent.settings import generic_claude_qualified

        if generic_claude_qualified():
            return "generic-omnigent-host@1"
        raise _HarnessPlatformError(
            "execution realizer generic-omnigent-host@1 is not qualified "
            "for claude-native in this deployment; explicit generic "
            "selection is fail-closed",
            code=_HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
        )
    return "generic-omnigent-host@1"


def _configured_legacy_rollback_generation() -> str | None:
    """Read the operator-selected rollback generation for legacy re-admission.

    Trusted planner input only: it comes from deployment settings, never from an
    Agent Profile or workflow payload.
    """

    from moonmind.config.settings import settings
    from moonmind.omnigent.session_supervisor_rollback import (
        legacy_rollback_generation_from_settings,
    )

    return legacy_rollback_generation_from_settings(settings.feature_flags)


def _rollback_exercise_decision_for_plan(
    *,
    retirement_path_id: str,
    agent_profile_ref: str | None,
    host_class_ref: str | None,
    materializer_refs: Sequence[str],
    execution_realizer_ref: str | None,
    model_qualified_id: str | None,
    launch_policy_ref: str | None,
    host_mode: str | None,
    architecture: str | None,
    owner_cohort: str | None,
    records: Sequence[RollbackExerciseRecord] | None,
) -> RollbackExerciseDecision:
    """Evaluate recorded rollback evidence against this plan's exact scope.

    Fail-closed on every axis: a dimension this plan cannot name exactly, and a
    missing or non-matching record, both leave the path un-exercised and
    therefore closed to new work. ``RollbackScope`` rejects a blank dimension,
    so a scope that cannot be built is reported as unsatisfied rather than
    widened.
    """

    from datetime import datetime, timezone

    from moonmind.omnigent.session_supervisor_rollback import (
        RollbackExerciseDecision,
        RollbackScope,
        evaluate_rollback_exercise,
    )

    # Exactly one materializer may be named; a plan with several has no single
    # exercised materializer scope.
    unique_materializers = sorted({str(ref) for ref in materializer_refs if ref})
    materializer_ref = (
        unique_materializers[0] if len(unique_materializers) == 1 else None
    )
    try:
        scope = RollbackScope(
            agentProfileRef=str(agent_profile_ref or ""),
            hostClassRef=str(host_class_ref or ""),
            materializerRef=str(materializer_ref or ""),
            executionRealizerRef=str(execution_realizer_ref or ""),
            modelQualifiedId=str(model_qualified_id or ""),
            launchPolicyRef=str(launch_policy_ref or ""),
            hostMode=str(host_mode or ""),
            architecture=str(architecture or ""),
            ownerCohort=str(owner_cohort or ""),
        )
    except ValueError:
        return RollbackExerciseDecision(
            retirementPathId=retirement_path_id,
            satisfied=False,
            reasonCode="rollback_scope_incomplete",
        )

    return evaluate_rollback_exercise(
        retirement_path_id=retirement_path_id,
        scope=scope,
        records=records or (),
        now=datetime.now(timezone.utc),
    )


def compile_execution_plan(
    *,
    agent_profile: dict[str, Any] | OmnigentAgentProfileV2,
    harness_catalog: HarnessCatalogSnapshot,
    freshness_catalog: HarnessCatalogSnapshot | None = None,
    trust_record: HarnessTrustRecord,
    resolved_skills: dict[str, Any] | ResolvedSkillSet,
    credential_binding_set: CredentialBindingSet,
    host_class_ref: str,
    host_class: HostClass | None = None,
    launch_policy_ref: str,
    launch_policy: LaunchPolicy | None = None,
    model_qualified_id: str | None,
    model_effort: str | None,
    model_route_ref: str | None,
    model_normalized_options: dict[str, Any],
    workflow_requirements: list[str] | None = None,
    bridge_capabilities: dict[str, bool] | None = None,
    platform_capabilities: dict[str, bool] | None = None,
    workspace_intent_ref: str | None = None,
    policy_snapshot_ref: str | None = None,
    policy_snapshot_digest: str | None = None,
    effective_launch_snapshot_ref: str | None = None,
    effective_launch_snapshot_digest: str | None = None,
    host_image_ref: str | None = None,
    omnigent_host_build_digest: str | None = None,
    host_architecture: str | None = None,
    capture_policy_ref: str | None = None,
    execution_realizer_ref: str | None = None,
    execution_authority: dict[str, Any] | None = None,
    agent_profile_snapshot_ref: str | None = None,
    rollback_generation: str | None = None,
    rollback_owner_cohort: str | None = None,
    rollback_exercise_records: Sequence[RollbackExerciseRecord] | None = None,
) -> OmnigentExecutionPlanEnvelope:
    # 1. Validate agent profile (resolve snapshot)
    profile = validate_agent_profile(agent_profile)

    # 2-3. Resolve catalog + trust
    if trust_record.harnessId != profile.harness.id:
        raise HarnessPlatformError(
            "trust record harness mismatch",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    # Trust must bind to exact selected implementation (profile, trust, and catalog must agree)
    if trust_record.implementationRef != profile.harness.implementationRef:
        raise HarnessPlatformError(
            f"trust record implementation mismatch: {trust_record.implementationRef} != {profile.harness.implementationRef}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    # Verify catalog row's implementation digest matches trust (via implementationRef)
    catalog_harness = next(
        (h for h in harness_catalog.harnesses if h.id == profile.harness.id), None
    )
    if catalog_harness is not None:
        expected_impl_ref = catalog_harness.implementation.implementation_ref()
        if expected_impl_ref != profile.harness.implementationRef:
            raise HarnessPlatformError(
                f"catalog implementation mismatch: {expected_impl_ref} != {profile.harness.implementationRef}",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
    if not is_launchable_trust(trust_record.trustState):
        raise HarnessPlatformError(
            f"harness {profile.harness.id} is not launchable: {trust_record.trustState}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED,
        )
    # Bind catalog to endpoint: profile and catalog must be from same endpoint
    if profile.endpointRef != harness_catalog.endpointRef:
        raise HarnessPlatformError(
            f"catalog endpoint mismatch: {harness_catalog.endpointRef} != {profile.endpointRef}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
        )
    if profile.harness.catalogRef != harness_catalog.catalogRef:
        raise HarnessPlatformError(
            f"catalog ref mismatch: {profile.harness.catalogRef} != {harness_catalog.catalogRef}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE,
        )
    # Ensure harness exists in catalog
    catalog_harness_ids = {h.id for h in harness_catalog.harnesses}
    if profile.harness.id not in catalog_harness_ids:
        raise HarnessPlatformError(
            f"harness {profile.harness.id} unknown in catalog",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    # Enforce catalog freshness at shared pre-lease planning boundary
    try:
        from moonmind.omnigent.harness_platform.catalog import (
            assert_catalog_fresh as _assert_fresh,
        )
        from moonmind.omnigent.harness_platform.catalog import (
            assert_catalog_refresh_attests as _assert_refresh,
        )

        if freshness_catalog is None:
            _assert_fresh(harness_catalog)
        else:
            _assert_refresh(
                authority=harness_catalog,
                observation=freshness_catalog,
                harness_id=profile.harness.id,
                implementation_ref=profile.harness.implementationRef,
            )
    except HarnessPlatformError:
        raise
    except Exception as exc:
        raise HarnessPlatformError(
            str(exc), code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_STALE
        ) from exc

    # 4. Agent source already validated via profile parsing (discriminated)

    # 5. Resolved skills
    skills = validate_skill_refs_for_plan(resolved_skills)

    # 6-7. Credential binding set
    required_slots = [s.id for s in profile.credentialSlots if not s.optional]
    declared_slots = [s.id for s in profile.credentialSlots]
    validate_binding_set_for_plan(
        binding_set=credential_binding_set,
        required_slots=required_slots,
        declared_slots=declared_slots,
    )
    # Validate each binding's materializer exists and is compatible with host class later

    # 8. Materializers + 9. Host Class + launch policy class-level admission
    selected_host_class = host_class or get_host_class(host_class_ref)
    if selected_host_class.ref != host_class_ref:
        raise HarnessPlatformError(
            f"selected Host Class {selected_host_class.ref} does not match {host_class_ref}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    selected_launch_policy = launch_policy or get_launch_policy(launch_policy_ref)
    if selected_launch_policy.ref != launch_policy_ref:
        raise HarnessPlatformError(
            f"selected launch policy {selected_launch_policy.ref} does not match "
            f"{launch_policy_ref}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    # Reject launch policies outside the profile allowlist
    if (
        profile.allowedLaunchPolicyRefs
        and launch_policy_ref not in profile.allowedLaunchPolicyRefs
    ):
        raise HarnessPlatformError(
            f"launch policy {launch_policy_ref} not in profile allowlist {profile.allowedLaunchPolicyRefs}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )

    # Host class must declare the harness implementation
    if not selected_host_class.declares_harness(
        profile.harness.id, profile.harness.implementationRef
    ):
        raise HarnessPlatformError(
            f"host class {selected_host_class.ref} does not declare harness {profile.harness.id}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )

    # Policy must allow host class and integration mode
    # Derive integration mode from catalog capabilities
    harness_record = next(
        h for h in harness_catalog.harnesses if h.id == profile.harness.id
    )
    integration_mode = harness_record.capabilities.integrationMode or "native-server"
    materializer_refs = [
        b.materializerRef for b in credential_binding_set.bindings.values()
    ]
    validate_policy_for_host_class(
        policy=selected_launch_policy,
        host_class=selected_host_class,
        harness_integration_mode=integration_mode,
        materializer_refs=materializer_refs,
    )

    # Validate each materializer supports harness and host mode
    host_mode_for_materializer = selected_launch_policy.hostMode
    # Normalize legacy modes
    if host_mode_for_materializer == "on_demand_docker":
        host_mode_for_materializer = "on-demand"
    elif host_mode_for_materializer == "static_compose":
        host_mode_for_materializer = "static-connected"
    for slot, binding in credential_binding_set.bindings.items():
        # Enforce slot auth constraints: materializer auth model and provider must be accepted by profile slot
        slot_decl = next((s for s in profile.credentialSlots if s.id == slot), None)
        if slot_decl is not None:
            mat = get_materializer(binding.materializerRef)
            # Check acceptedAuthModels
            if slot_decl.acceptedAuthModels and not any(
                am in mat.acceptedAuthModels for am in slot_decl.acceptedAuthModels
            ):
                raise HarnessPlatformError(
                    f"materializer {binding.materializerRef} auth model {mat.acceptedAuthModels} not in slot {slot} accepted {slot_decl.acceptedAuthModels}",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
            # Check acceptedProviderIds via binding's providerProfileRef? we need to resolve provider profile? For now, ensure materializer's auth model matches slot
            # Provider ID check would require Provider Profile lookup; defer to lease acquisition if not available
        validate_binding_materializer(
            materializer_ref=binding.materializerRef,
            harness_implementation_ref=profile.harness.implementationRef,
            harness_id=profile.harness.id,
            host_mode=host_mode_for_materializer,
        )

    # 10. Class-level capability admission
    wf_reqs = workflow_requirements or []
    prof_reqs = {
        "required": list(profile.requirements.harness.required)
        + list(profile.requirements.moonmind.required)
        + list(profile.requirements.host.required),
        "preferred": list(profile.requirements.harness.preferred),
    }
    catalog_caps: dict[str, Any] = {}
    for h in harness_catalog.harnesses:
        if h.id == profile.harness.id:
            caps = h.capabilities.model_dump(by_alias=True, mode="json")
            catalog_caps = {k: v for k, v in caps.items() if v is not None}
            break
    host_caps: dict[str, bool] = {
        k: bool(v) for k, v in selected_host_class.features.items()
    }
    materializer_caps: dict[str, bool] = {}
    for ref in materializer_refs:
        try:
            get_materializer(ref)
            # materializers don't directly expose capability booleans; assume compatible if allowlisted
            materializer_caps[ref] = True
        except Exception as exc:
            # Materializer not found is handled as incompatible during admission; keep empty for now
            _ = exc
            continue
    bridge_caps = bridge_capabilities or {}
    policy_caps = list(selected_launch_policy.controlCapabilities)

    class_decision = compute_class_admission(
        workflow_requirements=wf_reqs,
        profile_requirements=prof_reqs,
        catalog_capabilities=catalog_caps,
        host_class_capabilities=host_caps,
        materializer_capabilities=materializer_caps,
        bridge_capabilities=bridge_caps,
        launch_policy_capabilities=policy_caps,
        platform_capabilities=platform_capabilities,
    )

    # 11. Normalize model config + digest
    model_digest = compute_model_config_digest(
        qualifiedId=model_qualified_id,
        effort=model_effort,
        routeRef=model_route_ref,
        normalizedOptions=model_normalized_options,
    )

    # 12. Select execution realizer + compute support combination key
    # Realizer is trusted planner selection, never workflow-authored; ignore caller override
    # For tests that explicitly request a realizer for codex preservation, allow only if compatible
    # Otherwise select via trusted policy
    trusted_realizer = select_execution_realizer(
        harness_id=profile.harness.id,
        is_codex=(profile.harness.id == "codex-native"),
    )
    if (
        execution_realizer_ref is not None
        and execution_realizer_ref != trusted_realizer
    ):
        # Only allow codex-profile-bound@1 for codex harness explicitly via trusted path
        generic_codex_requested = (
            profile.harness.id == "codex-native"
            and execution_realizer_ref == "generic-omnigent-host@1"
        )
        generic_claude_requested = (
            profile.harness.id == "claude-native"
            and execution_realizer_ref == "generic-omnigent-host@1"
        )
        if generic_codex_requested:
            from moonmind.omnigent.settings import generic_codex_qualified

            if not generic_codex_qualified():
                raise HarnessPlatformError(
                    "execution realizer generic-omnigent-host@1 is not qualified "
                    "for codex-native in this deployment; explicit generic "
                    "selection is fail-closed",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
                )
        elif generic_claude_requested:
            from moonmind.omnigent.settings import generic_claude_qualified

            if not generic_claude_qualified():
                raise HarnessPlatformError(
                    "execution realizer generic-omnigent-host@1 is not qualified "
                    "for claude-native in this deployment; explicit generic "
                    "selection is fail-closed",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
                )
        elif not (
            profile.harness.id == "codex-native"
            and execution_realizer_ref == "codex-profile-bound@1"
        ):
            raise HarnessPlatformError(
                f"execution realizer {execution_realizer_ref} not selectable for harness {profile.harness.id}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            )
        realizer = execution_realizer_ref
    else:
        realizer = trusted_realizer
    # Validate realizer exists
    try:
        from moonmind.omnigent.harness_platform.support import (
            validate_realizer as _validate_realizer,
        )

        _validate_realizer(realizer)
    except Exception as exc:
        raise HarnessPlatformError(
            f"execution realizer {realizer} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
        ) from exc

    # Retirement admission (#3835 required work 2). Plan compilation is the one
    # new-admission boundary for every client: a trusted planner default, an
    # explicit ``execution_realizer_ref`` from an alternate API client, a
    # schedule, and a preset all resolve here. Once the code-owned retirement
    # class for a realizer stops admitting new work, no new plan may select it,
    # while already-recorded plans keep executing, cancelling, cleaning up, and
    # reading normally. A ``rollback_only`` class re-admits new work only under
    # the exact rollback generation the operator has explicitly selected.
    from moonmind.omnigent.legacy_retirement import (
        LegacyAdmissionRejected,
        assert_new_admission_allowed,
        retirement_record_for_surface,
    )

    retirement_row = retirement_record_for_surface(f"realizer:{realizer}")
    if retirement_row is not None:
        generation = rollback_generation
        exercise_decision = None
        if retirement_row.requires_rollback_generation:
            if generation is None:
                generation = _configured_legacy_rollback_generation()
            # A rollback generation is a single global string. Re-admission also
            # needs a fresh, successful rollback exercise recorded for *this*
            # exact plan scope, so one allowlisted generation cannot re-admit
            # every profile, Host Class, materializer, model, launch policy,
            # host mode, architecture, and owner cohort using the path.
            exercise_decision = _rollback_exercise_decision_for_plan(
                retirement_path_id=retirement_row.path_id,
                agent_profile_ref=agent_profile_snapshot_ref,
                host_class_ref=host_class_ref,
                materializer_refs=materializer_refs,
                execution_realizer_ref=realizer,
                model_qualified_id=model_qualified_id,
                launch_policy_ref=launch_policy_ref,
                host_mode=selected_launch_policy.hostMode,
                # The architecture the plan will actually run on, resolved the
                # same way the support key resolves it below.
                architecture=(
                    host_architecture
                    or (
                        selected_host_class.architectures[0]
                        if selected_host_class.architectures
                        else None
                    )
                ),
                owner_cohort=rollback_owner_cohort,
                records=rollback_exercise_records,
            )
        try:
            assert_new_admission_allowed(
                retirement_row.path_id,
                rollback_generation=generation,
                rollback_exercise=exercise_decision,
            )
        except LegacyAdmissionRejected as exc:
            raise HarnessPlatformError(
                str(exc),
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
            ) from exc

    required_caps_digest = compute_required_capabilities_digest(
        list(class_decision.requiredSatisfied)
    )

    # Include exact vendor runtime refs from Host Class entry for support key differentiation
    vendor_refs = []
    for entry in selected_host_class.declaredHarnessImplementations:
        if (
            entry.harnessId == profile.harness.id
            and entry.implementationRef == profile.harness.implementationRef
        ):
            for dep in entry.runtimeDependencies:
                # dep is dict with name, version, and optional digest.
                # Packs pin the vendor version identity while the launched
                # image attests the installed runtime; bind the support key
                # to the exact host image digest when no per-runtime digest
                # is recorded so rebuilt images cannot reuse prior evidence.
                # Never emit a literal "#None" suffix.
                name = dep.get("name")
                version = dep.get("version")
                digest = dep.get("digest")
                if digest:
                    vendor_refs.append(f"{name}@{version}#{digest}")
                else:
                    image_ref = str(selected_host_class.imageRef or "")
                    image_digest = (
                        image_ref.split("@", 1)[1]
                        if "@" in image_ref
                        else ""
                    )
                    if image_digest:
                        vendor_refs.append(f"{name}@{version}#{image_digest}")
                    else:
                        vendor_refs.append(f"{name}@{version}")
            break
    # Full agent source identity (not just ID) to differentiate bundles with same ID but different version/content
    agent_source_full = profile.source.model_dump(by_alias=True, mode="json")
    # Use full source digest for support key to differentiate bundles
    import hashlib as _hashlib
    import json as _json

    agent_source_ref = _json.dumps(
        agent_source_full, sort_keys=True, separators=(",", ":")
    )
    agent_source_ref = (
        "agent-source:sha256:" + _hashlib.sha256(agent_source_ref.encode()).hexdigest()
    )
    support_architecture = (
        host_architecture
        or (
            selected_host_class.architectures[0]
            if selected_host_class.architectures
            else "linux/amd64"
        )
    )
    if support_architecture not in selected_host_class.architectures:
        raise HarnessPlatformError(
            f"selected Host Class {selected_host_class.ref} does not support "
            f"architecture {support_architecture}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )

    support_payload = SupportKeyPayload.model_validate(
        {
            "omnigentServerBuildRef": harness_catalog.omnigentBuildDigest,
            "omnigentHostBuildRef": selected_host_class.omnigentBuildDigest,
            "harnessImplementationRef": profile.harness.implementationRef,
            "vendorRuntimeRefs": sorted(vendor_refs),
            "agentSourceRef": agent_source_ref,
            "materializerRefs": sorted(materializer_refs),
            "providerCompatibilityClass": credential_binding_set.bindingSetId,
            "hostClassRef": selected_host_class.ref,
            "architecture": support_architecture,
            "launchPolicyRef": selected_launch_policy.ref,
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
    if workspace_intent_ref is None:
        workspace_payload = _json.dumps(
            profile.workspace, sort_keys=True, separators=(",", ":")
        ).encode()
        workspace_intent_ref = (
            "workspace-intent:sha256:" + _hashlib.sha256(workspace_payload).hexdigest()
        )
    if policy_snapshot_ref is None:
        policy_payload = _json.dumps(
            selected_launch_policy.model_dump(by_alias=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        policy_snapshot_ref = (
            "omnigent-policy:sha256:" + _hashlib.sha256(policy_payload).hexdigest()
        )

    plan_payload = OmnigentExecutionPlanPayload.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
            "authority": execution_authority,
            "endpointRef": profile.endpointRef,
            "agentProfileSnapshotRef": (
                agent_profile_snapshot_ref or profile.snapshot_ref()
            ),
            "harnessCatalogRef": harness_catalog.catalogRef,
            "harnessId": profile.harness.id,
            "harnessImplementationRef": profile.harness.implementationRef,
            "agentSource": agent_source_dict,
            "credentialBindingSetRef": credential_binding_set.ref,
            "credentialBindings": {
                slot: binding.model_dump(by_alias=True, mode="json")
                for slot, binding in credential_binding_set.bindings.items()
            },
            "hostClassRef": selected_host_class.ref,
            "hostImageRef": host_image_ref,
            "omnigentHostBuildDigest": omnigent_host_build_digest,
            "hostArchitecture": host_architecture,
            "launchPolicyRef": selected_launch_policy.ref,
            "executionRealizerRef": realizer,
            "model": {
                "qualifiedId": model_qualified_id,
                "effort": model_effort,
                "routeRef": model_route_ref,
                "normalizedOptions": model_normalized_options,
                "modelConfigDigest": model_digest,
            },
            "resolvedSkills": skills.model_dump(by_alias=True, mode="json"),
            "resolvedTools": {
                "toolDeliveryRef": "tool-delivery:sha256:"
                + _hashlib.sha256(
                    _json.dumps(
                        {
                            "agentProfileSnapshotRef": profile.snapshot_ref(),
                            "tools": sorted(profile.tools),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "tools": sorted(profile.tools),
            },
            "classAdmissionDecision": class_decision.model_dump(
                by_alias=True, mode="json"
            ),
            "runtimeValidationRequirements": runtime_validation_requirements,
            "workspaceIntentRef": workspace_intent_ref,
            "workspaceMutation": str(
                profile.workspace.get("mutation") or "allowed"
            ),
            "capturePolicyRef": capture_policy_ref,
            "capturePolicy": dict(profile.capture),
            "policySnapshotRef": policy_snapshot_ref,
            "policySnapshotDigest": policy_snapshot_digest,
            "effectiveLaunchSnapshotRef": effective_launch_snapshot_ref,
            "effectiveLaunchSnapshotDigest": effective_launch_snapshot_digest,
            "supportCombinationKey": support_key,
            "supportIdentity": support_payload.model_dump(
                by_alias=True, mode="json"
            ),
        }
    )

    # 13. Compile, canonicalize, hash, persist envelope
    envelope = create_execution_plan_envelope(plan_payload)
    return envelope
