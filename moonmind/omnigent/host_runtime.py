"""Concrete, harness-neutral Omnigent host realization boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy
from moonmind.omnigent.host_services.attestation import DockerOmnigentHostAttestor
from moonmind.omnigent.host_services.cleanup import DockerOmnigentHostCleanupService
from moonmind.omnigent.host_services.egress import OmnigentEgressService
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.host_services.launcher import (
    DockerOmnigentHostLauncher,
    HostLaunchSpec,
    host_correlation_identity,
)
from moonmind.omnigent.host_services.mounted_tools import OmnigentMountedToolService
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.omnigent.host_services.skills import OmnigentSkillDeliveryService
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


@dataclass(frozen=True)
class PreparedHostInputs:
    workspace_attachment: dict[str, Any]
    skill_attachment: dict[str, Any]
    tool_attachments: tuple[dict[str, Any], ...]
    egress_attestation: dict[str, Any]
    github_credential_attachment: dict[str, Any] | None = None

    @property
    def cleanup_refs(self) -> tuple[str, ...]:
        values = [
            self.workspace_attachment.get("cleanupRef"),
            self.skill_attachment.get("cleanupRef"),
            *(item.get("cleanupRef") for item in self.tool_attachments),
            (
                self.github_credential_attachment.get("cleanupRef")
                if self.github_credential_attachment is not None
                else None
            ),
            self.egress_attestation.get("cleanupRef"),
        ]
        return tuple(str(item) for item in values if item)


class GenericOmnigentHostRuntime:
    """Materialize and attest an exact host using only plan-selected data."""

    def __init__(
        self,
        *,
        launcher: DockerOmnigentHostLauncher,
        workspace_service: OmnigentWorkspaceMaterializer,
        skill_service: OmnigentSkillDeliveryService,
        tool_service: OmnigentMountedToolService,
        github_credential_service: OmnigentGithubCredentialService,
        egress_service: OmnigentEgressService,
        registration_waiter: OmnigentHostRegistrationService,
        host_attestor: DockerOmnigentHostAttestor,
        cleanup_service: DockerOmnigentHostCleanupService,
    ) -> None:
        dependencies = (
            launcher,
            workspace_service,
            skill_service,
            tool_service,
            github_credential_service,
            egress_service,
            registration_waiter,
            host_attestor,
            cleanup_service,
        )
        if any(item is None for item in dependencies):
            raise HarnessPlatformError(
                "generic host runtime dependencies are incomplete",
                code=HarnessPlatformFailure.OMNIGENT_GENERIC_REALIZER_NOT_READY,
            )
        self._launcher = launcher
        self._workspace = workspace_service
        self._skills = skill_service
        self._tools = tool_service
        self._github_credentials = github_credential_service
        self._egress = egress_service
        self._registration = registration_waiter
        self._attestor = host_attestor
        self._cleanup = cleanup_service

    async def prepare(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        authority_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> PreparedHostInputs:
        workspace = await self._workspace.materialize(
            request,
            mutation=plan.payload.workspaceMutation,
            runtime_uid=int(host_class.runtime.get("uid", 1000)),
            runtime_gid=int(host_class.runtime.get("gid", 1000)),
        )
        if authority_sink is not None:
            await authority_sink({"kind": "workspace", **workspace})
        # Skill snapshots may be shared, but their mutable run projection and
        # cleanup authority must remain owned by this execution.
        anticipated_skills = await self._skills.anticipated_attachment(
            plan.payload.resolvedSkills,
            owner_ref=request.idempotency_key,
        )
        if authority_sink is not None:
            # Persist deterministic cleanup authority before the first
            # projection filesystem mutation so worker loss remains janitor-safe.
            await authority_sink({"kind": "skills", **anticipated_skills})
        skills = await self._skills.materialize(
            plan.payload.resolvedSkills,
            owner_ref=request.idempotency_key,
        )
        if skills != anticipated_skills:
            raise HarnessPlatformError(
                "Skill materialization changed its anticipated cleanup authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        omnigent_parameters = (
            request.parameters.get("omnigent")
            if isinstance(request.parameters, dict)
            else None
        )
        if (
            isinstance(omnigent_parameters, dict)
            and "toolAttachments" in omnigent_parameters
        ):
            raise HarnessPlatformError(
                "workflow-authored tool attachments are not accepted",
                code=HarnessPlatformFailure.OMNIGENT_HOST_LAUNCH_FAILED,
            )
        tools = tuple(await self._tools.materialize(plan.payload.resolvedTools))
        if authority_sink is not None:
            for tool in tools:
                await authority_sink({"kind": "tool", **tool})
        anticipated_github = self._github_credentials.anticipated_attachment(
            plan.payload.resolvedTools,
            owner_ref=request.idempotency_key,
        )
        if anticipated_github is not None and authority_sink is not None:
            await authority_sink(
                {"kind": "github_credentials", **anticipated_github}
            )
        github_credentials = await self._github_credentials.materialize(
            request=request,
            resolved_tools=plan.payload.resolvedTools,
            owner_ref=request.idempotency_key,
            writer_image_ref=host_class.imageRef,
            runtime_uid=int(host_class.runtime.get("uid", 1000)),
            runtime_gid=int(host_class.runtime.get("gid", 1000)),
        )
        if github_credentials != anticipated_github:
            raise HarnessPlatformError(
                "GitHub credential materialization changed its anticipated authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        egress = await self._egress.attest(request=request, launch_policy=launch_policy)
        if authority_sink is not None:
            await authority_sink({"kind": "egress", **egress})
        return PreparedHostInputs(
            workspace_attachment=workspace,
            skill_attachment=skills,
            tool_attachments=tools,
            github_credential_attachment=github_credentials,
            egress_attestation=egress,
        )

    async def realize(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        runtime_binding_id: str,
        host_lease_ref: str,
        host_lease_generation: int,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        prepared: PreparedHostInputs,
        credential_handles: list[dict[str, Any]],
        authority_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if (
            host_class.ref != plan.payload.hostClassRef
            or launch_policy.ref != plan.payload.launchPolicyRef
        ):
            raise HarnessPlatformError(
                "runtime Host Class or launch policy differs from the immutable plan",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        credential_attachments = tuple(
            attachment
            for handle in credential_handles
            for attachment in handle.get("attachments", [])
        )
        correlation = host_correlation_identity(host_lease_ref)
        state_digest = hashlib.sha256(
            f"{runtime_binding_id}\0{host_lease_ref}".encode("utf-8")
        ).hexdigest()[:32]
        state_volume = f"mm-omnigent-state-{state_digest}"
        labels = {
            "moonmind.owner": "generic-omnigent-host",
            "moonmind.execution_plan_ref": plan.planRef,
            "moonmind.runtime_binding_id": runtime_binding_id,
            "moonmind.host_lease_ref": host_lease_ref,
            "moonmind.host_lease_generation": str(host_lease_generation),
            "moonmind.egress.profile": str(prepared.egress_attestation["profileRef"]),
            "moonmind.egress.profile_digest": str(
                prepared.egress_attestation["profileDigest"]
            ),
            "moonmind.egress.applied_rule_digest": str(
                prepared.egress_attestation["appliedRuleDigest"]
            ),
        }
        spec = HostLaunchSpec.model_validate(
            {
                "executionPlanRef": plan.planRef,
                "runtimeBindingId": runtime_binding_id,
                "hostLeaseRef": host_lease_ref,
                "hostLeaseGeneration": host_lease_generation,
                "hostClassRef": host_class.ref,
                "imageRef": host_class.imageRef,
                "serverEndpointRef": plan.payload.endpointRef,
                "serverUrl": self._launcher.server_url,
                "networkRef": prepared.egress_attestation["networkRef"],
                "limits": launch_policy.limits,
                "runtime": host_class.runtime,
                "correlationName": correlation,
                "workspaceAttachment": prepared.workspace_attachment,
                "skillAttachment": prepared.skill_attachment,
                "toolAttachments": list(prepared.tool_attachments),
                "githubCredentialAttachment": (
                    prepared.github_credential_attachment
                ),
                "credentialAttachments": list(credential_attachments),
                "controlAttachment": (
                    self._launcher.control_attachment(host_lease_ref)
                    if hasattr(self._launcher, "control_attachment")
                    else None
                ),
                "stateAttachment": {
                    "kind": "volume",
                    "sourceRef": state_volume,
                    "targetPath": "/home/app/.omnigent",
                    "accessMode": "read-write",
                },
                "labels": labels,
            }
        )
        anticipated_authority = {
            "kind": "host",
            "containerName": correlation,
            "stateVolumeRef": state_volume,
            "controlVolumeRef": (
                spec.controlAttachment.get("sourceRef")
                if spec.controlAttachment is not None
                else None
            ),
            "hostCleanupRef": f"host-cleanup:{correlation}",
            "stateCleanupRef": f"state-cleanup:{state_volume}",
            "hostLeaseRef": host_lease_ref,
            "launchGeneration": host_lease_generation,
        }
        # Persist the deterministic cleanup authority before the first launch
        # mutation. Cleanup is idempotent when a resource was never created.
        if authority_sink is not None:
            await authority_sink(anticipated_authority)
        launch = await self._launcher.launch(
            spec=spec,
            host_class=host_class,
            launch_policy=launch_policy,
            credential_handles=credential_handles,
        )
        try:
            registration = await self._registration.wait_for_registration(
                correlation_name=correlation,
                harness_id=plan.payload.harnessId,
            )
            attestations = await self._attestor.attest(
                request=request,
                plan=plan,
                spec=spec,
                host_class=host_class,
                launch_result=launch,
                registration=registration,
                credential_handles=credential_handles,
                egress_attestation=prepared.egress_attestation,
            )
        except BaseException:
            await self._cleanup.cleanup(
                container_name=launch["containerName"],
                host_lease_ref=host_lease_ref,
                host_lease_generation=host_lease_generation,
                state_volume_ref=launch["stateVolumeRef"],
                control_volume_ref=launch.get("controlVolumeRef"),
            )
            raise
        return {
            "hostId": registration["omnigentHostId"],
            "omnigentHostId": registration["omnigentHostId"],
            "containerName": launch["containerName"],
            "stateVolumeRef": launch["stateVolumeRef"],
            "controlVolumeRef": launch.get("controlVolumeRef"),
            "hostClassRef": host_class.ref,
            "launchPolicyRef": launch_policy.ref,
            "workspacePath": "/workspaces/run",
            "hostLaunchSpec": spec.model_dump(by_alias=True, mode="json"),
            "hostCleanupRef": launch["hostCleanupRef"],
            "stateCleanupRef": launch["stateCleanupRef"],
            **attestations,
        }

    async def cleanup(
        self,
        *,
        host_context: dict[str, Any],
        host_lease_ref: str,
        host_lease_generation: int,
    ) -> dict[str, Any]:
        return await self._cleanup.cleanup(
            container_name=str(host_context["containerName"]),
            host_lease_ref=host_lease_ref,
            host_lease_generation=host_lease_generation,
            state_volume_ref=str(host_context["stateVolumeRef"]),
            control_volume_ref=(
                str(host_context["controlVolumeRef"])
                if host_context.get("controlVolumeRef")
                else None
            ),
        )

    async def cleanup_prepared(self, prepared: PreparedHostInputs) -> None:
        # Workspace and tool bindings refer to authoritative caller-owned
        # paths, while egress is deployment-owned. Only the run-owned Skill
        # projection is removed here.
        await self._skills.cleanup(prepared.skill_attachment)
        if prepared.github_credential_attachment is not None:
            await self._github_credentials.cleanup(
                prepared.github_credential_attachment
            )

    async def cleanup_authorities(
        self, authorities: tuple[str | dict[str, Any], ...]
    ) -> None:
        for authority in reversed(authorities):
            if isinstance(authority, dict) and authority.get("kind") == "skills":
                await self._skills.cleanup(authority)
            elif (
                isinstance(authority, dict)
                and authority.get("kind") == "github_credentials"
            ):
                await self._github_credentials.cleanup(authority)


__all__ = ["GenericOmnigentHostRuntime", "PreparedHostInputs"]
