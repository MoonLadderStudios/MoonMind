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

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.execution_profiles import validate_effective_launch_snapshot
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    compute_attestation_ref,
    validate_exact_host_attestation,
    validate_opencode_exact_host_preflight,
)
from moonmind.omnigent.harness_platform.capabilities import (
    ClassAdmissionDecision,
    ExactHostCapabilityDecision,
    compute_class_admission_ref,
    compute_exact_host_capability_decision_ref,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessImplementationIdentity,
    HarnessTrustRecord,
)
from moonmind.omnigent.harness_platform.credential_bindings import CredentialBindingSet
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import (
    get_host_class,
    get_opencode_host_class,
)
from moonmind.omnigent.harness_platform.materializers import (
    cleanup_opencode_auth,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
    create_runtime_binding,
)
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet


_GENERIC_REALIZER_REF = "generic-omnigent-host@1"
_GENERIC_ATTACH_SCHEMA = "moonmind.omnigent-generic-harness-attach.v1"
_STOCK_HOST_CATALOG_CONTRACT = "omnigent.http.host-catalog.v1"
_RUNTIME_OBSERVATION_SCHEMA = "moonmind.omnigent-host-runtime-observation.v1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class GenericHarnessAttachContract(BaseModel):
    """Planner-owned immutable input consumed at the host attach boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal[
        "moonmind.omnigent-generic-harness-attach.v1"
    ] = Field(_GENERIC_ATTACH_SCHEMA, alias="schemaVersion")
    host_catalog_contract: Literal[
        "omnigent.http.host-catalog.v1"
    ] = Field(_STOCK_HOST_CATALOG_CONTRACT, alias="hostCatalogContract")
    execution_plan: OmnigentExecutionPlanEnvelope = Field(alias="executionPlan")
    harness_implementation: HarnessImplementationIdentity = Field(
        alias="harnessImplementation"
    )

    @model_validator(mode="after")
    def validate_join(self) -> "GenericHarnessAttachContract":
        plan = self.execution_plan
        if plan.payload.executionRealizerRef != _GENERIC_REALIZER_REF:
            raise ValueError("attach contract requires the generic host realizer")
        if (
            self.harness_implementation.implementation_ref()
            != plan.payload.harnessImplementationRef
        ):
            raise ValueError("attach contract harness implementation is stale")
        return self


class GenericHostRuntimeObservation(BaseModel):
    """Selected output of the production workload/image/network attestor."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal[
        "moonmind.omnigent-host-runtime-observation.v1"
    ] = Field(_RUNTIME_OBSERVATION_SCHEMA, alias="schemaVersion")
    workload_image_ref: str = Field(alias="workloadImageRef", min_length=1)
    workload_image_digest: str = Field(alias="workloadImageDigest", min_length=1)
    architecture: str = Field(min_length=1)
    attachment_identity: str = Field(alias="attachmentIdentity", min_length=1)
    network_identity: str = Field(alias="networkIdentity", min_length=1)
    endpoint_identity: str = Field(alias="endpointIdentity", min_length=1)
    validation_result: Literal["passed"] = Field(alias="validationResult")
    validated_at: datetime = Field(alias="validatedAt")

    @model_validator(mode="after")
    def validate_observation(self) -> "GenericHostRuntimeObservation":
        if not _DIGEST_RE.fullmatch(self.workload_image_digest):
            raise ValueError("workload image digest is invalid")
        if self.validated_at.tzinfo is None:
            raise ValueError("runtime observation timestamp must be timezone-aware")
        return self

    @classmethod
    def from_workload_evidence(
        cls, evidence: Mapping[str, Any]
    ) -> "GenericHostRuntimeObservation":
        fields = {
            "workloadImageRef": evidence.get("workloadImageRef"),
            "workloadImageDigest": evidence.get("workloadImageDigest"),
            "architecture": evidence.get("architecture"),
            "attachmentIdentity": evidence.get("attachmentIdentity"),
            "networkIdentity": evidence.get("networkIdentity"),
            "endpointIdentity": evidence.get("endpointIdentity"),
            "validationResult": evidence.get("validationResult"),
            "validatedAt": evidence.get("validatedAt"),
        }
        return cls.model_validate(fields)


class StockHostCatalogEntry(BaseModel):
    """Exact supported projection of stock Omnigent ``GET /v1/hosts``."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_version: Literal["omnigent.http.host-catalog-entry.v1"] = Field(
        "omnigent.http.host-catalog-entry.v1", alias="schemaVersion"
    )
    host_id: str = Field(alias="host_id", min_length=1)
    name: str = Field(min_length=1)
    owner: str | None = None
    status: Literal["online", "offline"]
    sandbox_provider: str | None = None
    configured_harnesses: dict[str, bool | str] | None = None
    gateway_inference: dict[str, bool] | None = None

    @classmethod
    def from_stock(cls, raw: Mapping[str, Any]) -> "StockHostCatalogEntry":
        payload = dict(raw)
        payload["schemaVersion"] = "omnigent.http.host-catalog-entry.v1"
        return cls.model_validate(payload)

    def ready_harnesses(self) -> set[str]:
        ready_values = {"true", "ready", "available", "authenticated"}
        return {
            name
            for name, readiness in (self.configured_harnesses or {}).items()
            if readiness is True
            or str(readiness).strip().lower() in ready_values
        }


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


def create_generic_harness_attach_contract(
    *,
    execution_plan: OmnigentExecutionPlanEnvelope | Mapping[str, Any],
    harness_implementation: HarnessImplementationIdentity | Mapping[str, Any],
) -> GenericHarnessAttachContract:
    """Create the versioned, secret-free contract used by host attachment."""

    plan = (
        execution_plan
        if isinstance(execution_plan, OmnigentExecutionPlanEnvelope)
        else verify_execution_plan_envelope(dict(execution_plan))
    )
    implementation = (
        harness_implementation
        if isinstance(harness_implementation, HarnessImplementationIdentity)
        else HarnessImplementationIdentity.model_validate(harness_implementation)
    )
    return GenericHarnessAttachContract(
        executionPlan=plan,
        harnessImplementation=implementation,
    )


def create_generic_harness_attach_contract_from_execution_intent(
    *,
    execution_plan: Any | None,
    harness_implementation: Any | None,
    provider_profile_id: str,
) -> GenericHarnessAttachContract | None:
    """Resolve the planner-owned generic attach input from compiled intent.

    The compiled execution intent is the immutable handoff between the trusted
    planner and both production launch coordinators.  A generic plan and its
    exact implementation identity are an atomic pair: accepting either one on
    its own would let a launch lose the authority needed at ``prepare_host``.
    The selected Provider Profile must also be one of the plan's pre-lease
    credential bindings before the contract can become launch authority.

    Codex and other existing realizers omit both values and retain their
    established launch path unchanged.
    """

    if execution_plan is None and harness_implementation is None:
        return None
    if not isinstance(execution_plan, Mapping) or not isinstance(
        harness_implementation, Mapping
    ):
        raise ValueError(
            "generic execution intent requires both execution plan and "
            "harness implementation"
        )

    contract = create_generic_harness_attach_contract(
        execution_plan=execution_plan,
        harness_implementation=harness_implementation,
    )
    planned_profiles = {
        str(binding.get("providerProfileRef") or "").strip()
        for binding in contract.execution_plan.payload.credentialBindings.values()
        if isinstance(binding, Mapping)
    }
    selected_profile = str(provider_profile_id or "").strip()
    if not selected_profile or selected_profile not in planned_profiles:
        raise ValueError(
            "generic execution plan does not select the launch Provider Profile"
        )
    return contract


def bind_generic_harness_attach_contract(
    *,
    effective_launch: Mapping[str, Any],
    attach_contract: GenericHarnessAttachContract | Mapping[str, Any],
) -> dict[str, Any]:
    """Digest-bind a planner-owned generic attach contract to a launch."""

    validate_effective_launch_snapshot(effective_launch)
    contract = (
        attach_contract
        if isinstance(attach_contract, GenericHarnessAttachContract)
        else GenericHarnessAttachContract.model_validate(attach_contract)
    )
    plan = contract.execution_plan
    policy_authority = effective_launch.get("policyAuthority")
    if plan.payload.endpointRef != str(effective_launch.get("endpointRef") or ""):
        raise ValueError("generic execution plan endpoint does not match the launch")
    if plan.payload.launchPolicyRef != str(
        effective_launch.get("launchPolicyRef") or ""
    ):
        raise ValueError("generic execution plan policy does not match the launch")
    if (
        not isinstance(policy_authority, Mapping)
        or plan.payload.policySnapshotRef
        != str(policy_authority.get("snapshotRef") or "")
    ):
        raise ValueError("generic execution plan policy snapshot is stale")
    if plan.payload.harnessId != str(effective_launch.get("harness") or ""):
        raise ValueError("generic execution plan harness does not match the launch")
    host_class = get_host_class(plan.payload.hostClassRef)
    if host_class.imageRef != str(effective_launch.get("hostImageRef") or ""):
        raise ValueError("generic execution plan host class image is stale")
    if not host_class.declares_harness(
        plan.payload.harnessId,
        contract.harness_implementation.implementation_ref(),
    ):
        raise ValueError("generic execution plan harness is absent from its host class")

    launch = dict(effective_launch)
    launch.pop("snapshotRef", None)
    launch.update(
        {
            "executionPlanRef": plan.planRef,
            "executionRealizerRef": plan.payload.executionRealizerRef,
            "genericHarnessAttachContract": contract.model_dump(
                by_alias=True, mode="json"
            ),
        }
    )
    canonical = json.dumps(launch, sort_keys=True, separators=(",", ":"))
    launch["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        canonical.encode()
    ).hexdigest()
    validate_effective_launch_snapshot(launch)
    return launch


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
    attach_contract: GenericHarnessAttachContract | Mapping[str, Any],
    host_catalog_entry: StockHostCatalogEntry | Mapping[str, Any],
    workload_evidence: GenericHostRuntimeObservation | Mapping[str, Any],
    effective_launch: Mapping[str, Any],
    host_binding_ref: str,
    host_lease_ref: str,
    host_lease_generation: int,
    provider_profile_id: str,
    provider_lease_ref: str,
    credential_generation: int,
    credential_runtime_ref: str,
    current_host_id: str,
    current_session_id: str | None = None,
    now: datetime | None = None,
    max_attestation_age_seconds: int = 600,
) -> dict[str, Any]:
    """Construct generic facade authority from production-owned evidence.

    The immutable plan and implementation arrive in the digest-bound attach
    contract. The exact stock catalog supplies host identity/readiness, while
    the workload attestor supplies image, architecture, and network evidence.
    MoonMind alone joins those inputs to the acquired lease fences.
    """

    if effective_launch.get("executionRealizerRef") != _GENERIC_REALIZER_REF:
        raise ValueError("generic harness authority requires the generic realizer")
    if isinstance(host_lease_generation, bool) or host_lease_generation < 1:
        raise ValueError("generic host lease generation must be fenced")
    if not str(credential_runtime_ref or "").strip():
        raise ValueError("generic provider lease omitted its credential runtime ref")

    try:
        contract = (
            attach_contract
            if isinstance(attach_contract, GenericHarnessAttachContract)
            else GenericHarnessAttachContract.model_validate(attach_contract)
        )
        stock_host = (
            host_catalog_entry
            if isinstance(host_catalog_entry, StockHostCatalogEntry)
            else StockHostCatalogEntry.from_stock(host_catalog_entry)
        )
        observation = (
            workload_evidence
            if isinstance(workload_evidence, GenericHostRuntimeObservation)
            else GenericHostRuntimeObservation.from_workload_evidence(
                workload_evidence
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("generic host attach evidence is malformed") from exc

    launch_contract = effective_launch.get("genericHarnessAttachContract")
    if not isinstance(launch_contract, Mapping) or contract != (
        GenericHarnessAttachContract.model_validate(launch_contract)
    ):
        raise ValueError("generic host attach contract is missing or stale")
    plan = contract.execution_plan
    launch_plan_ref = str(effective_launch.get("executionPlanRef") or "").strip()
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
    if stock_host.status != "online" or stock_host.host_id != current_host_id:
        raise ValueError("generic stock host identity is stale")
    if plan.payload.harnessId not in stock_host.ready_harnesses():
        raise ValueError("generic stock host does not advertise the planned harness")

    observed_at = observation.validated_at.astimezone(UTC)
    age_seconds = ((now or datetime.now(UTC)) - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_attestation_age_seconds:
        raise ValueError("generic host runtime observation is stale or future-dated")

    host_class = get_host_class(plan.payload.hostClassRef)
    launch_image_ref = str(effective_launch.get("hostImageRef") or "")
    if (
        host_class.imageRef != launch_image_ref
        or observation.workload_image_ref != launch_image_ref
    ):
        raise ValueError("generic host image does not match immutable authority")
    normalized_architectures = {
        item.removeprefix("linux/") for item in host_class.architectures
    }
    if observation.architecture.removeprefix("linux/") not in normalized_architectures:
        raise ValueError("generic host architecture is not admitted")

    implementation = contract.harness_implementation
    matching_entries = [
        entry
        for entry in host_class.declaredHarnessImplementations
        if entry.harnessId == plan.payload.harnessId
        and entry.implementationRef == implementation.implementation_ref()
    ]
    if len(matching_entries) != 1:
        raise ValueError("generic host class does not declare the planned harness")
    host_entry = matching_entries[0]
    class_decision = ClassAdmissionDecision.model_validate(
        plan.payload.classAdmissionDecision
    )
    controls = {
        str(value).strip() for value in effective_launch.get("controlCapabilities", ())
    }
    feature_map = {
        "workspace.bind": "workspaceBind",
        "repository.read": "git",
        "repository.mutation": "git",
        "git": "git",
        "artifact.capture": "mountedTools",
    }
    attested_capabilities: dict[str, bool] = {}
    for capability in class_decision.requiredSatisfied:
        feature = feature_map.get(capability, capability)
        attested_capabilities[capability] = bool(
            capability in controls or host_class.features.get(feature) is True
        )

    attestation_payload: dict[str, Any] = {
        "hostId": stock_host.host_id,
        "hostClassRef": host_class.ref,
        "hostImageRef": observation.workload_image_ref,
        "omnigentVersion": host_class.omnigentVersion,
        "omnigentBuildDigest": host_class.omnigentBuildDigest,
        "harnessId": plan.payload.harnessId,
        "harnessImplementation": implementation.model_dump(
            by_alias=True, mode="json"
        ),
        "runtimeDependencies": list(host_entry.runtimeDependencies),
        "configured": True,
        "capabilities": attested_capabilities,
        "architecture": observation.architecture,
        "attestationGeneration": host_lease_generation,
        "observedAt": observed_at,
    }
    attestation = HostHarnessAttestation.model_validate(attestation_payload)
    attestation_payload["attestationRef"] = compute_attestation_ref(attestation)
    attestation = HostHarnessAttestation.model_validate(attestation_payload)
    try:
        validate_exact_host_attestation(
            attestation,
            expectedHostClassRef=host_class.ref,
            expectedImageRef=launch_image_ref,
            expectedOmnigentBuildDigest=host_class.omnigentBuildDigest,
            expectedHarnessId=plan.payload.harnessId,
            expectedImplementation=implementation.model_dump(
                by_alias=True, mode="json"
            ),
            requiredCapabilities=list(class_decision.requiredSatisfied),
            expectedHostId=current_host_id,
            currentHostLeaseGeneration=host_lease_generation,
            expectedVendorRuntimes=list(host_entry.runtimeDependencies),
            now=now,
            max_age_seconds=max_attestation_age_seconds,
        )
    except Exception as exc:
        raise ValueError("generic exact-host attestation was rejected") from exc

    decision = ExactHostCapabilityDecision.model_validate(
        {
            "classAdmissionRef": compute_class_admission_ref(class_decision),
            "exactHostAttested": True,
            "requiredSatisfied": list(class_decision.requiredSatisfied),
            "missingRequired": [],
            "degraded": list(class_decision.degraded),
        }
    )
    provider_leases = {
        slot: {
            "providerProfileRef": provider_profile_id,
            "providerLeaseRef": provider_lease_ref,
            "credentialGeneration": credential_generation,
            "credentialRuntimeRef": credential_runtime_ref,
        }
        for slot, declared in plan.payload.credentialBindings.items()
        if str(declared.get("providerProfileRef") or "") == provider_profile_id
    }
    if not provider_leases:
        raise ValueError("generic execution plan has no acquired provider lease")
    runtime_binding = create_runtime_binding(
        executionPlanRef=plan.planRef,
        providerLeases=provider_leases,
        hostBindingRef=host_binding_ref,
        hostLeaseRef=host_lease_ref,
        hostLeaseGeneration=host_lease_generation,
        omnigentHostId=current_host_id,
        hostHarnessAttestationRef=attestation.attestationRef,
        exactHostCapabilityDecisionRef=(
            compute_exact_host_capability_decision_ref(decision)
        ),
        omnigentSessionId=current_session_id,
    )

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
    "GenericHarnessAttachContract",
    "GenericHostRuntimeObservation",
    "StockHostCatalogEntry",
    "bind_generic_harness_attach_contract",
    "compile_opencode_execution_plan",
    "create_generic_harness_attach_contract",
    "create_generic_harness_attach_contract_from_execution_intent",
    "build_generic_harness_authority",
    "build_preflight_generic_harness_authority",
    "materialize_opencode_credential_for_host",
    "preflight_opencode_host",
    "verify_and_cleanup_opencode_credential",
]
