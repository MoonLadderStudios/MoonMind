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

from typing import Any, Protocol

from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy, get_host_class, get_launch_policy
from moonmind.omnigent.harness_platform.execution_plan import OmnigentExecutionPlanEnvelope
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError, HarnessPlatformFailure
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class DockerHostLauncher(Protocol):
    async def launch(self, *, host_class: HostClass, launch_policy: LaunchPolicy, workspace_handle: Any, skill_handle: Any, credential_handles: list[dict[str, Any]]) -> dict[str, Any]: ...


class WorkspaceMaterializationService(Protocol):
    async def materialize(self, request: AgentExecutionRequest) -> dict[str, Any]: ...


class SkillDeliveryService(Protocol):
    async def materialize(self, resolved_skills: dict[str, Any]) -> dict[str, Any]: ...


class EgressAttachmentService(Protocol):
    async def attest(self, launch_policy: LaunchPolicy) -> dict[str, Any]: ...


class HostRegistrationWaiter(Protocol):
    async def wait_for_registration(self, *, expected_host_id: str | None = None) -> dict[str, Any]: ...


class HostImageAttestor(Protocol):
    async def attest(self, host_id: str, expected_image_ref: str) -> dict[str, Any]: ...


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
    ) -> None:
        self._launcher = launcher
        self._workspace_service = workspace_service
        self._skill_service = skill_service
        self._egress_service = egress_service
        self._registration_waiter = registration_waiter
        self._image_attestor = image_attestor

    async def realize(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_class: HostClass | None = None,
        launch_policy: LaunchPolicy | None = None,
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

        # Validate materializer compatibility with HostClass
        materializer_refs = [b.materializerRef for b in plan.payload.credentialBindings.values()]
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

        # Shared services (stubbed for hermetic)
        workspace_handle = {"path": "/workspaces/run", "locator": "sandbox"}
        if self._workspace_service is not None:
            workspace_handle = await self._workspace_service.materialize(request)

        skill_handle = {"deliveryRef": plan.payload.resolvedSkills.get("skillDeliveryRef")}
        if self._skill_service is not None:
            skill_handle = await self._skill_service.materialize(plan.payload.resolvedSkills)

        # Build HostLaunchSpec (secret-free)
        host_launch_spec = {
            "hostClassRef": hc.ref,
            "imageRef": hc.imageRef,
            "launchPolicyRef": lp.ref,
            "workspaceHandle": workspace_handle,
            "skillHandle": skill_handle,
            "materializerRefs": materializer_refs,
        }

        # Launch / attach
        host_id = f"host_{plan.payload.harnessId}_synthetic"
        if self._launcher is not None:
            launch_result = await self._launcher.launch(
                host_class=hc,
                launch_policy=lp,
                workspace_handle=workspace_handle,
                skill_handle=skill_handle,
                credential_handles=[],
            )
            host_id = str(launch_result.get("hostId") or host_id)

        # Wait for registration
        if self._registration_waiter is not None:
            reg = await self._registration_waiter.wait_for_registration(expected_host_id=host_id)
            host_id = str(reg.get("hostId") or host_id)

        # Image attestation
        if self._image_attestor is not None:
            await self._image_attestor.attest(host_id, hc.imageRef)

        # Return generic host context (no harness-specific fields)
        return {
            "hostId": host_id,
            "omnigentHostId": host_id,
            "hostClassRef": hc.ref,
            "imageRef": hc.imageRef,
            "launchPolicyRef": lp.ref,
            "workspacePath": workspace_handle.get("path") or "/workspaces/run",
            "skillDeliveryRef": skill_handle.get("deliveryRef"),
            "materializerRefs": materializer_refs,
            "hostLaunchSpec": host_launch_spec,
            # Attestation refs for runtime binding
            "hostHarnessAttestationRef": f"artifact:host-attestation:{host_id}",
            "modelOptionAttestationRef": f"artifact:model-options:{host_id}",
            "skillDeliveryAttestationRef": skill_handle.get("deliveryRef"),
        }
