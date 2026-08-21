"""Normal-product Omnigent execution authority admission.

This module is the single pre-side-effect compiler for
MoonLadderStudios/MoonMind#3701.  It converts the immutable Agent Profile
snapshot selected by the API into one secret-free, digest-addressed execution
plan.  Harness differences are registrations consumed as data; execution
activities must load the resulting plan ref and never repeat this selection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    create_binding_set,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    get_host_class,
    get_launch_policy,
    validate_policy_for_host_class,
)
from moonmind.omnigent.harness_platform.materializers import (
    validate_binding_materializer,
)
from moonmind.omnigent.harness_platform.planner import select_execution_realizer
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_required_capabilities_digest,
    compute_support_combination_key,
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _content_ref(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{_digest(value).removeprefix('sha256:')}"


@dataclass(frozen=True, slots=True)
class HarnessAdmissionRegistration:
    """Trusted catalog-to-realization registration used at admission."""

    harness_id: str
    implementation: HarnessImplementationIdentity
    host_class_ref: str
    integration_mode: str
    provider_runtime_id: str
    materializer_ref: str
    accepted_auth_model: str
    model_route_ref: str
    catalog_capabilities: tuple[str, ...]


def _implementation(digest_character: str) -> HarnessImplementationIdentity:
    return HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + digest_character * 64,
            "pluginEntryPoint": None,
        }
    )


# Adding a harness changes this catalog registration plus the existing Host
# Class, materializer, Provider Profile compatibility, policy and conformance
# registrations.  Session lifecycle code contains no harness-name branches.
HARNESS_ADMISSION_REGISTRATIONS: dict[str, HarnessAdmissionRegistration] = {
    "codex-native": HarnessAdmissionRegistration(
        harness_id="codex-native",
        implementation=_implementation("e"),
        host_class_ref="omnigent-codex-current@1",
        integration_mode="native-server",
        provider_runtime_id="codex_cli",
        materializer_ref="codex-oauth-home@1",
        accepted_auth_model="oauth_volume",
        model_route_ref="openai",
        catalog_capabilities=("interrupt", "streaming"),
    ),
    "opencode-native": HarnessAdmissionRegistration(
        harness_id="opencode-native",
        implementation=_implementation("a"),
        host_class_ref="omnigent-opencode@1",
        integration_mode="native-server",
        provider_runtime_id="opencode_go",
        materializer_ref="opencode-auth-json@1",
        accepted_auth_model="own-auth",
        model_route_ref="opencode-go",
        catalog_capabilities=("interrupt", "streaming"),
    ),
}

NORMAL_PRODUCT_HARNESS_IDS = frozenset(HARNESS_ADMISSION_REGISTRATIONS)
NORMAL_PRODUCT_PROVIDER_RUNTIME_IDS = frozenset(
    registration.provider_runtime_id
    for registration in HARNESS_ADMISSION_REGISTRATIONS.values()
)


def _mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HarnessPlatformError(
            f"{field_name} must be an object",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    return value


def _text(value: Any, *, field_name: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise HarnessPlatformError(
            f"{field_name} is required",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    return candidate


def _profile_snapshot_ref(snapshot: Mapping[str, Any]) -> str:
    return _content_ref("omnigent-agent-profile", snapshot)


def _registered_implementation(
    *,
    snapshot: Mapping[str, Any],
    registration: HarnessAdmissionRegistration,
) -> HarnessImplementationIdentity:
    upstream = snapshot.get("upstreamSnapshot")
    upstream = upstream if isinstance(upstream, Mapping) else {}
    raw = upstream.get("harnessImplementation")
    implementation = (
        HarnessImplementationIdentity.model_validate(raw)
        if isinstance(raw, Mapping)
        else registration.implementation
    )
    if (
        implementation.implementation_ref()
        != registration.implementation.implementation_ref()
    ):
        raise HarnessPlatformError(
            "Agent Profile harness implementation conflicts with the trusted registration",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    return implementation


def _agent_source(
    snapshot: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, Any]:
    source = _mapping(document.get("source"), field_name="agentProfile.document.source")
    upstream_snapshot = snapshot.get("upstreamSnapshot")
    upstream_snapshot = (
        dict(upstream_snapshot) if isinstance(upstream_snapshot, Mapping) else {}
    )
    bundle_ref = str(source.get("bundleArtifactRef") or "").strip()
    if bundle_ref:
        return {
            "kind": "bundle",
            "bundleArtifactRef": bundle_ref,
            "bundleDigest": _text(
                source.get("bundleDigest"),
                field_name="agentProfile.document.source.bundleDigest",
            ),
            "importReceiptRef": _text(
                source.get("importReceiptRef"),
                field_name="agentProfile.document.source.importReceiptRef",
            ),
            "importedAgentId": _text(
                snapshot.get("agentId"), field_name="agentProfile.agentId"
            ),
            "importedAgentVersion": str(source.get("upstreamVersion") or "1"),
            "importedContentDigest": _digest(upstream_snapshot or source),
        }
    return {
        "kind": "upstream",
        "upstreamId": _text(
            snapshot.get("agentId") or source.get("upstreamId"),
            field_name="agentProfile.agentId",
        ),
        "upstreamVersion": str(source.get("upstreamVersion") or "unversioned"),
        "upstreamSnapshotDigest": _digest(upstream_snapshot or source),
    }


def _resolved_skill_authority(
    snapshot: Mapping[str, Any], workflow_parameters: Mapping[str, Any]
) -> dict[str, Any]:
    workflow = workflow_parameters.get("workflow")
    workflow = workflow if isinstance(workflow, Mapping) else {}
    selection = {
        "profileSkills": list(
            _mapping(snapshot.get("document"), field_name="agentProfile.document").get(
                "skills", []
            )
            or []
        ),
        "workflowSkills": workflow.get("skills"),
        "steps": [
            {
                "id": step.get("id"),
                "skills": step.get("skills"),
                "skill": step.get("skill"),
            }
            for step in (workflow.get("steps") or [])
            if isinstance(step, Mapping)
        ],
    }
    resolved_ref = str(
        workflow_parameters.get("resolvedSkillsetRef")
        or workflow_parameters.get("resolved_skillset_ref")
        or ""
    ).strip()
    selection_digest = _digest(selection)
    return {
        "resolvedSkillSetRef": resolved_ref or None,
        "resolvedSkillSetDigest": selection_digest,
        "skillDeliveryRef": _content_ref(
            "skill-delivery", {"selectionDigest": selection_digest}
        ),
        "selectionDigest": selection_digest,
    }


def compile_normal_product_execution_plan(
    *,
    agent_profile_snapshot: Mapping[str, Any],
    workflow_parameters: Mapping[str, Any],
    workflow_id: str,
) -> OmnigentExecutionPlanEnvelope:
    """Compile the one immutable execution authority for a normal create.

    The compiler is pure with respect to provider, lease, credential, host and
    workspace side effects.  It may inspect only trusted, code-owned
    registrations and the already-resolved immutable Agent Profile snapshot.
    """

    snapshot = dict(agent_profile_snapshot)
    document = _mapping(snapshot.get("document"), field_name="agentProfile.document")
    harness_id = _text(
        document.get("harness"), field_name="agentProfile.document.harness"
    )
    registration = HARNESS_ADMISSION_REGISTRATIONS.get(harness_id)
    if registration is None:
        raise HarnessPlatformError(
            f"harness {harness_id} has no normal-product admission registration",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )

    implementation = _registered_implementation(
        snapshot=snapshot, registration=registration
    )
    host_class = get_host_class(registration.host_class_ref)
    if not host_class.declares_harness(harness_id, implementation.implementation_ref()):
        raise HarnessPlatformError(
            f"Host Class {host_class.ref} does not declare {harness_id}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )

    launch_policy_ref = _text(
        snapshot.get("launchPolicyRef"), field_name="agentProfile.launchPolicyRef"
    )
    allowed = document.get("execution")
    allowed = allowed if isinstance(allowed, Mapping) else {}
    allowed_refs = tuple(allowed.get("allowedLaunchPolicyRefs") or ())
    if launch_policy_ref not in allowed_refs:
        raise HarnessPlatformError(
            f"launch policy {launch_policy_ref} is outside the Agent Profile allowlist",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    launch_policy = get_launch_policy(launch_policy_ref)
    validate_policy_for_host_class(
        policy=launch_policy,
        host_class=host_class,
        harness_integration_mode=registration.integration_mode,
        materializer_refs=[registration.materializer_ref],
    )
    validate_binding_materializer(
        materializer_ref=registration.materializer_ref,
        harness_implementation_ref=implementation.implementation_ref(),
        host_mode=(
            "on-demand"
            if launch_policy.hostMode == "on_demand_docker"
            else (
                "static-connected"
                if launch_policy.hostMode == "static_compose"
                else launch_policy.hostMode
            )
        ),
    )

    required_capabilities = sorted(
        {
            str(value).strip()
            for value in (
                list(document.get("requiredCapabilities") or [])
                + list(workflow_parameters.get("requiredCapabilities") or [])
            )
            if str(value).strip()
        }
    )
    available_capabilities = {
        *registration.catalog_capabilities,
        *(name for name, enabled in host_class.features.items() if enabled),
        *launch_policy.controlCapabilities,
    }
    missing = sorted(set(required_capabilities) - available_capabilities)
    if missing:
        raise HarnessPlatformError(
            f"required capabilities are unavailable: {missing}",
            code=HarnessPlatformFailure.OMNIGENT_CAPABILITY_REQUIRED_UNSUPPORTED,
        )

    provider_profile_ref = _text(
        snapshot.get("providerProfileRef"),
        field_name="agentProfile.providerProfileRef",
    )
    binding_set = create_binding_set(
        bindingSetId="admission-"
        + hashlib.sha256(
            f"{snapshot.get('profileId')}:{provider_profile_ref}".encode()
        ).hexdigest()[:20],
        version=int(snapshot.get("version") or 1),
        bindings={
            "primary-model": {
                "providerProfileRef": provider_profile_ref,
                "materializerRef": registration.materializer_ref,
            }
        },
    )

    model_document = document.get("model")
    model_document = model_document if isinstance(model_document, Mapping) else {}
    model_id = (
        str(
            workflow_parameters.get("model") or model_document.get("model") or ""
        ).strip()
        or None
    )
    if model_id is None:
        raise HarnessPlatformError(
            "an explicit model is required before Omnigent host acquisition",
            code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
        )
    effort = (
        str(
            workflow_parameters.get("effort") or model_document.get("effort") or ""
        ).strip()
        or None
    )
    normalized_options = dict(model_document.get("settings") or {})
    model_digest = compute_model_config_digest(
        qualifiedId=model_id,
        effort=effort,
        routeRef=registration.model_route_ref,
        normalizedOptions=normalized_options,
    )
    realizer_ref = select_execution_realizer(
        harness_id=harness_id,
        is_codex=harness_id == "codex-native",
    )

    agent_source = _agent_source(snapshot, document)
    upstream_snapshot = snapshot.get("upstreamSnapshot")
    catalog_authority = {
        "endpointRef": document.get("endpointRef"),
        "observedAt": (
            upstream_snapshot.get("catalogObservedAt")
            if isinstance(upstream_snapshot, Mapping)
            else None
        ),
        "harnessId": harness_id,
        "implementation": implementation.model_dump(by_alias=True, mode="json"),
        "hostBuildDigest": host_class.omnigentBuildDigest,
    }
    catalog_ref = _content_ref("omnigent-harness-catalog", catalog_authority)
    agent_source_ref = _content_ref("agent-source", agent_source)
    provider_requirements = document.get("providerRequirements")
    provider_requirements = (
        provider_requirements if isinstance(provider_requirements, Mapping) else {}
    )
    provider_compatibility_class = _canonical_json(
        {
            "runtimeId": provider_requirements.get("runtimeId"),
            "credentialSource": provider_requirements.get("credentialSource"),
            "materializationMode": provider_requirements.get("materializationMode"),
        }
    )
    vendor_runtime_refs = tuple(
        sorted(
            f"{dependency.get('name')}@{dependency.get('version')}#{dependency.get('digest')}"
            for entry in host_class.declaredHarnessImplementations
            if entry.harnessId == harness_id
            for dependency in entry.runtimeDependencies
        )
    )
    support_identity = SupportKeyPayload.model_validate(
        {
            "omnigentServerBuildRef": host_class.omnigentBuildDigest,
            "omnigentHostBuildRef": host_class.imageRef,
            "harnessImplementationRef": implementation.implementation_ref(),
            "vendorRuntimeRefs": vendor_runtime_refs,
            "agentSourceRef": agent_source_ref,
            "materializerRefs": [registration.materializer_ref],
            "providerCompatibilityClass": provider_compatibility_class,
            "hostClassRef": host_class.ref,
            "architecture": host_class.architectures[0],
            "launchPolicyRef": launch_policy.ref,
            "modelConfigDigest": model_digest,
            "executionRealizerRef": realizer_ref,
            "requiredCapabilitiesDigest": compute_required_capabilities_digest(
                required_capabilities
            ),
        }
    )
    support_key = compute_support_combination_key(support_identity)
    workspace_intent = {
        "workflowId": workflow_id,
        "repository": workflow_parameters.get("repository"),
        "workspace": document.get("workspace"),
    }
    policy_authority = {
        "profilePolicyRef": snapshot.get("policyRef"),
        "profileDigest": snapshot.get("digest"),
        "launchPolicyRef": launch_policy.ref,
    }
    resolved_skills = _resolved_skill_authority(snapshot, workflow_parameters)

    return create_execution_plan_envelope(
        {
            "schemaVersion": "moonmind.omnigent-execution-plan-payload.v1",
            "endpointRef": _text(
                document.get("endpointRef"),
                field_name="agentProfile.document.endpointRef",
            ),
            "agentProfileSnapshotRef": _profile_snapshot_ref(snapshot),
            "harnessCatalogRef": catalog_ref,
            "harnessId": harness_id,
            "harnessImplementationRef": implementation.implementation_ref(),
            "agentSource": agent_source,
            "credentialBindingSetRef": binding_set.ref,
            "credentialBindings": {
                slot: binding.model_dump(by_alias=True, mode="json")
                for slot, binding in binding_set.bindings.items()
            },
            "hostClassRef": host_class.ref,
            "launchPolicyRef": launch_policy.ref,
            "executionRealizerRef": realizer_ref,
            "model": {
                "qualifiedId": model_id,
                "effort": effort,
                "routeRef": registration.model_route_ref,
                "normalizedOptions": normalized_options,
                "modelConfigDigest": model_digest,
            },
            "resolvedSkills": resolved_skills,
            "classAdmissionDecision": {
                "required": required_capabilities,
                "requiredSatisfied": required_capabilities,
                "preferredSatisfied": [],
                "preferredMissing": [],
            },
            "runtimeValidationRequirements": [
                "exact-harness-implementation",
                "exact-vendor-runtime",
                "exact-network-egress",
                "exact-skill-delivery",
                "live-model-option",
            ],
            "workspaceIntentRef": _content_ref("workspace-intent", workspace_intent),
            "capturePolicyRef": _content_ref(
                "omnigent-capture-policy", document.get("capture") or {}
            ),
            "policySnapshotRef": _content_ref("omnigent-policy", policy_authority),
            "supportCombinationKey": support_key,
            "supportIdentity": support_identity.model_dump(by_alias=True, mode="json"),
        }
    )


async def compile_and_persist_execution_authority(
    session: Any,
    *,
    agent_profile_snapshot: Mapping[str, Any],
    workflow_parameters: Mapping[str, Any],
    workflow_id: str,
) -> OmnigentExecutionPlanEnvelope:
    """Compile and flush the plan in the caller's create transaction."""

    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

    plan = compile_normal_product_execution_plan(
        agent_profile_snapshot=agent_profile_snapshot,
        workflow_parameters=workflow_parameters,
        workflow_id=workflow_id,
    )
    return await DbExecutionPlanStore.persist_in_session(session, plan)


__all__ = [
    "NORMAL_PRODUCT_HARNESS_IDS",
    "NORMAL_PRODUCT_PROVIDER_RUNTIME_IDS",
    "HARNESS_ADMISSION_REGISTRATIONS",
    "HarnessAdmissionRegistration",
    "compile_and_persist_execution_authority",
    "compile_normal_product_execution_plan",
]
