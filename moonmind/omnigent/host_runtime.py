"""Generic Omnigent Host Runtime (Phase 2 extraction).

Shared infrastructure that both the legacy OAuth runtime and the new generic
realizer use. Extracted behind interfaces so Codex behavior remains
byte-for-byte equivalent where practical, and generic host can reuse services
without harness branches.

Services:
- DockerHostLauncher
- WorkspaceMaterializationService
- SkillDeliveryService
- MountedToolService
- EgressAttachmentService
- HostRegistrationWaiter
- HostImageAttestor
- HostCleanupService

The generic runtime consumes HostClass, LaunchPolicy, CredentialRuntimeHandles,
WorkspaceHandle, SkillDeliveryHandle – never runtime_id or harness adapter
dictionary.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
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
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


logger = logging.getLogger(__name__)


class DockerHostLauncher(Protocol):
    async def launch(
        self,
        *,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        workspace_handle: Any,
        skill_handle: Any,
        credential_handles: list[dict[str, Any]],
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class WorkspaceMaterializationService(Protocol):
    async def materialize(
        self,
        request: AgentExecutionRequest,
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class SkillDeliveryService(Protocol):
    async def materialize(
        self,
        resolved_skills: dict[str, Any],
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class EgressAttachmentService(Protocol):
    async def attest(
        self,
        launch_policy: LaunchPolicy,
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class HostRegistrationWaiter(Protocol):
    async def wait_for_registration(
        self,
        *,
        expected_host_id: str | None = None,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class HostImageAttestor(Protocol):
    async def attest(
        self,
        host_id: str,
        expected_image_ref: str,
        *,
        authority: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class HostCleanupService(Protocol):
    async def cleanup(
        self,
        *,
        plan_ref: str,
        runtime_binding_ref: str | None,
        host_id: str | None,
        authority: dict[str, Any],
    ) -> None:
        raise NotImplementedError


class HostRealizationContextService(Protocol):
    async def prepare_realization(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        authority: Mapping[str, Any],
    ) -> None:
        raise NotImplementedError


class GenericOmnigentHostRuntime:
    """Harness-neutral host realization.

    Consumes HostClass + LaunchPolicy + credential handles, not harness-specific
    branches. Validates exact host attestation generically via
    harness_platform/attestation.
    """

    def __init__(
        self,
        *,
        launcher: DockerHostLauncher | None = None,
        workspace_service: WorkspaceMaterializationService | None = None,
        skill_service: SkillDeliveryService | None = None,
        egress_service: EgressAttachmentService | None = None,
        registration_waiter: HostRegistrationWaiter | None = None,
        image_attestor: HostImageAttestor | None = None,
        cleanup_service: HostCleanupService | None = None,
        context_service: HostRealizationContextService | None = None,
    ) -> None:
        self._launcher = launcher
        self._workspace_service = workspace_service
        self._skill_service = skill_service
        self._egress_service = egress_service
        self._registration_waiter = registration_waiter
        self._image_attestor = image_attestor
        self._cleanup_service = cleanup_service
        self._context_service = context_service

    def assert_ready(self) -> None:
        """Fail before command, credential, lease, workspace, or host effects."""

        dependencies = {
            "host launcher": self._launcher,
            "workspace materializer": self._workspace_service,
            "Skill delivery service": self._skill_service,
            "egress attestor": self._egress_service,
            "host registration waiter": self._registration_waiter,
            "host image attestor": self._image_attestor,
            "host cleanup service": self._cleanup_service,
        }
        missing = sorted(name for name, value in dependencies.items() if value is None)
        if missing:
            raise HarnessPlatformError(
                "generic host runtime is not production-ready: " + ", ".join(missing),
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )

    @staticmethod
    def _side_effect_authority(
        *,
        plan_ref: str,
        runtime_binding_ref: str | None,
        command_authority: dict[str, Any],
    ) -> dict[str, Any]:
        required = {
            "commandId",
            "claimToken",
            "sessionId",
            "turnAttemptId",
            "expectedSessionRevision",
            "fencingGeneration",
        }
        missing = sorted(required - set(command_authority))
        if (
            missing
            or not plan_ref.startswith("omnigent-execution-plan:sha256:")
            or not str(runtime_binding_ref or "").startswith(
                "omnigent-runtime-binding:sha256:"
            )
        ):
            raise HarnessPlatformError(
                "generic host side effect lacks stable command or binding authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        return {
            **command_authority,
            "executionPlanRef": plan_ref,
            "runtimeBindingRef": runtime_binding_ref,
        }

    async def realize(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_class: HostClass | None = None,
        launch_policy: LaunchPolicy | None = None,
        credential_handles: list[dict[str, Any]] | None = None,
        runtime_binding_ref: str,
        command_authority: dict[str, Any],
    ) -> dict[str, Any]:
        """Realize an attested Omnigent host for the plan.

        Steps (generic, no harness branches):
        1. Resolve Host Class + launch policy from plan
        2. Materialize workspace (shared service)
        3. Materialize Skills (shared)
        4. Build secret-free HostLaunchSpec
        5. Start/attach host
        6. Wait for exact host registration
        7. Verify image, architecture, Omnigent build, harness implementation,
           vendor runtime, mounts, network
        8. Query host model options and validate selected model
        9. Return host context for session driver
        """
        hc = host_class or get_host_class(plan.payload.hostClassRef)
        lp = launch_policy or get_launch_policy(plan.payload.launchPolicyRef)
        self.assert_ready()
        side_effect_authority = self._side_effect_authority(
            plan_ref=plan.planRef,
            runtime_binding_ref=runtime_binding_ref,
            command_authority=command_authority,
        )
        if self._context_service is not None:
            await self._context_service.prepare_realization(
                request=request,
                plan=plan,
                authority=side_effect_authority,
            )

        # Validate materializer compatibility with HostClass
        materializer_refs = [
            str(binding["materializerRef"])
            for binding in plan.payload.credentialBindings.values()
        ]
        for mat_ref in materializer_refs:
            if not hc.supports_materializer(mat_ref):
                raise HarnessPlatformError(
                    f"materializer {mat_ref} not supported by host class {hc.ref}",
                    code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
                )

        # Validate launch policy allows host class
        if not lp.allows_host_class(hc):
            raise HarnessPlatformError(
                f"policy {lp.ref} incompatible with host class {hc.ref}",
                code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
            )

        if self._workspace_service is None:
            raise HarnessPlatformError(
                "workspace materializer required for generic host realization",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        workspace_handle = await self._workspace_service.materialize(  # type: ignore[union-attr]
            request, authority=side_effect_authority
        )

        if self._skill_service is None:
            raise HarnessPlatformError(
                "Skill delivery service required for generic host realization",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_DELIVERY_MISMATCH,
            )
        skill_handle = await self._skill_service.materialize(  # type: ignore[union-attr]
            plan.payload.resolvedSkills,
            authority=side_effect_authority,
        )

        # Build HostLaunchSpec (secret-free)
        host_launch_spec = {
            "hostClassRef": hc.ref,
            "imageRef": hc.imageRef,
            "launchPolicyRef": lp.ref,
            "workspaceHandle": workspace_handle,
            "skillHandle": skill_handle,
            "materializerRefs": materializer_refs,
            "authority": side_effect_authority,
        }

        # Use provided credential handles or validate (P1 3828196627)
        from moonmind.omnigent.harness_platform.materializers import get_materializer

        if credential_handles is None:
            credential_handles = []
        # Validate required handles before launch
        for mat_ref in materializer_refs:
            try:
                mat = get_materializer(mat_ref)
                if mat.requiredSecretRoles and not any(
                    h.get("materializerRef") == mat_ref for h in credential_handles
                ):
                    raise HarnessPlatformError(
                        f"materializer {mat_ref} requires credential handles before host launch",
                        code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
                    )
            except HarnessPlatformError:
                raise
            except Exception:  # best-effort, ignore
                pass

        # Launch / attach. Hermetic callers inject fakes through this same
        # interface; production code never infers test mode or invents authority.
        if self._launcher is None:
            raise HarnessPlatformError(
                "host launcher required for generic host realization",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        else:
            launch_result = await self._launcher.launch(
                host_class=hc,
                launch_policy=lp,
                workspace_handle=workspace_handle,
                skill_handle=skill_handle,
                credential_handles=credential_handles,
                authority=side_effect_authority,
            )
            launched_host_id = str(launch_result.get("hostId") or "").strip()
            expected_host_name = str(
                launch_result.get("expectedHostName") or ""
            ).strip()
            host_id = launched_host_id
            host_binding_ref = str(
                launch_result.get("hostBindingRef") or ""
            ).strip()
            host_lease_ref = str(
                launch_result.get("hostLeaseRef") or ""
            ).strip()
            host_lease_generation = launch_result.get("hostLeaseGeneration")
            if (
                (not launched_host_id and not expected_host_name)
                or not host_binding_ref
                or not host_lease_ref
                or not isinstance(host_lease_generation, int)
                or host_lease_generation < 1
            ):
                if launched_host_id or expected_host_name:
                    try:
                        await self.cleanup(
                            plan.planRef,
                            runtime_binding_ref,
                            host_id=launched_host_id or None,
                            command_authority=command_authority,
                        )
                    except Exception as cleanup_error:
                        raise HarnessPlatformError(
                            "generic host launch cleanup is deferred",
                            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                        ) from cleanup_error
                raise HarnessPlatformError(
                    "host launcher omitted exact fenced host authority",
                    code=(
                        HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED
                        if not (launched_host_id or expected_host_name)
                        else HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY
                    ),
                )

        try:
            # ``assert_ready`` established both dependencies before launch.
            reg = await self._registration_waiter.wait_for_registration(  # type: ignore[union-attr]
                expected_host_id=launched_host_id or None,
                authority=side_effect_authority,
            )
            host_id = str(reg.get("hostId") or host_id)
            if launched_host_id and host_id != launched_host_id:
                raise HarnessPlatformError(
                    "registered host differs from fenced launch authority",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            raw_attestation = reg.get("attestation")
            if not isinstance(raw_attestation, Mapping):
                raise HarnessPlatformError(
                    "exact host registration omitted harness attestation",
                    code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
                )
            from moonmind.omnigent.harness_platform.attestation import (
                HostHarnessAttestation,
                compute_attestation_ref,
                validate_exact_host_attestation,
            )
            from moonmind.omnigent.harness_platform.catalog import (
                HarnessImplementationIdentity,
            )

            attestation = HostHarnessAttestation.model_validate(raw_attestation)
            if (
                not attestation.attestationRef
                or compute_attestation_ref(attestation)
                != attestation.attestationRef
            ):
                raise HarnessPlatformError(
                    "exact host attestation ref does not match its content",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            observed_implementation = HarnessImplementationIdentity.model_validate(
                attestation.harnessImplementation
            )
            if (
                observed_implementation.implementation_ref()
                != plan.payload.harnessImplementationRef
            ):
                raise HarnessPlatformError(
                    "exact host harness implementation differs from the plan",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            host_entry = next(
                (
                    entry
                    for entry in hc.declaredHarnessImplementations
                    if entry.harnessId == plan.payload.harnessId
                    and entry.implementationRef
                    == plan.payload.harnessImplementationRef
                ),
                None,
            )
            if host_entry is None:
                raise HarnessPlatformError(
                    "Host Class no longer declares the planned implementation",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
                )
            validate_exact_host_attestation(
                attestation,
                expectedHostClassRef=hc.ref,
                expectedImageRef=hc.imageRef,
                expectedOmnigentBuildDigest=hc.omnigentBuildDigest,
                expectedHarnessId=plan.payload.harnessId,
                expectedImplementation=attestation.harnessImplementation,
                requiredCapabilities=list(
                    plan.payload.classAdmissionDecision.get("required") or []
                ),
                expectedArchitecture=(
                    plan.payload.supportIdentity.architecture
                    if plan.payload.supportIdentity is not None
                    else hc.architectures[0]
                ),
                expectedHostId=host_id,
                currentHostLeaseGeneration=host_lease_generation,
                expectedVendorRuntimes=list(host_entry.runtimeDependencies),
            )
            image_evidence = await self._image_attestor.attest(  # type: ignore[union-attr]
                host_id, hc.imageRef, authority=side_effect_authority
            )
            egress_evidence = await self._egress_service.attest(  # type: ignore[union-attr]
                lp, authority=side_effect_authority
            )
            model_evidence = reg.get("modelOptionAttestation")
            required_refs = {
                "hostHarnessAttestationRef": attestation.attestationRef,
                "exactHostCapabilityDecisionRef": reg.get(
                    "exactHostCapabilityDecisionRef"
                ),
                "workspaceResolutionRef": workspace_handle.get("resolutionRef"),
                "modelOptionAttestationRef": (
                    model_evidence.get("attestationRef")
                    if isinstance(model_evidence, Mapping)
                    else None
                ),
                "skillDeliveryAttestationRef": skill_handle.get(
                    "attestationRef"
                ),
                "imageAttestationRef": (
                    image_evidence.get("attestationRef")
                    if isinstance(image_evidence, Mapping)
                    else None
                ),
                "egressAttestationRef": (
                    egress_evidence.get("attestationRef")
                    if isinstance(egress_evidence, Mapping)
                    else None
                ),
            }
            missing_refs = sorted(
                key for key, value in required_refs.items() if not value
            )
            planned_model = plan.payload.modelConfig.qualifiedId
            if (
                missing_refs
                or not isinstance(model_evidence, Mapping)
                or model_evidence.get("available") is not True
                or model_evidence.get("modelId") != planned_model
                or skill_handle.get("deliveryRef")
                != plan.payload.resolvedSkills.get("skillDeliveryRef")
                or not isinstance(image_evidence, Mapping)
                or image_evidence.get("observedImageRef") != hc.imageRef
                or not isinstance(egress_evidence, Mapping)
                or egress_evidence.get("enforced") is not True
            ):
                raise HarnessPlatformError(
                    "exact host evidence is incomplete or differs from the plan: "
                    + ", ".join(missing_refs),
                    code=(
                        HarnessPlatformFailure.OMNIGENT_EXACT_HOST_CAPABILITY_MISMATCH
                    ),
                )
        except Exception:
            # A launcher success transfers cleanup authority even if later
            # registration or attestation fails. Do not strand an unowned host.
            try:
                await self.cleanup(
                    plan.planRef,
                    runtime_binding_ref,
                    host_id=host_id,
                    command_authority=command_authority,
                )
            except Exception as cleanup_error:
                logger.exception(
                    "Generic host cleanup remains pending after realization failure"
                )
                raise HarnessPlatformError(
                    "generic host partial realization cleanup is deferred",
                    code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                ) from cleanup_error
            raise

        # Return generic host context (no harness-specific fields)
        return {
            "hostId": host_id,
            "omnigentHostId": host_id,
            "hostBindingRef": host_binding_ref,
            "hostLeaseRef": host_lease_ref,
            "hostLeaseGeneration": host_lease_generation,
            "hostClassRef": hc.ref,
            "imageRef": hc.imageRef,
            "launchPolicyRef": lp.ref,
            "workspacePath": workspace_handle.get("path") or "/workspaces/run",
            "skillDeliveryRef": skill_handle.get("deliveryRef"),
            "materializerRefs": materializer_refs,
            "hostLaunchSpec": host_launch_spec,
            # Attestation refs for runtime binding
            "hostHarnessAttestationRef": required_refs[
                "hostHarnessAttestationRef"
            ],
            "exactHostCapabilityDecisionRef": required_refs[
                "exactHostCapabilityDecisionRef"
            ],
            "workspaceResolutionRef": required_refs["workspaceResolutionRef"],
            "modelOptionAttestationRef": required_refs[
                "modelOptionAttestationRef"
            ],
            "skillDeliveryAttestationRef": required_refs[
                "skillDeliveryAttestationRef"
            ],
        }

    async def cleanup(
        self,
        plan_ref: str,
        runtime_binding_ref: str | None,
        *,
        host_id: str | None = None,
        command_authority: dict[str, Any],
    ) -> None:
        if self._cleanup_service is None:
            raise HarnessPlatformError(
                "host cleanup service required for generic host realization",
                code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
            )
        authority = self._side_effect_authority(
            plan_ref=plan_ref,
            runtime_binding_ref=runtime_binding_ref,
            command_authority=command_authority,
        )
        await self._cleanup_service.cleanup(
            plan_ref=plan_ref,
            runtime_binding_ref=runtime_binding_ref,
            host_id=host_id,
            authority=authority,
        )
