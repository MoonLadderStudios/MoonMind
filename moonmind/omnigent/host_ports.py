"""Narrow host-realization ports and the pure host launch contract.

Source issue: MoonLadderStudios/MoonMind#3711.

``GenericOmnigentHostRuntime`` coordinates one typed use case — realize and
attest an exact host — and must not depend on Docker, Compose, filesystem, or
provider transport implementations. Each collaborator therefore has its own
narrow port here rather than one broad "host runtime" or "host client"
interface, so a Docker adapter, a Compose adapter, and a hermetic test double
are separately substitutable and separately testable.

The ports are structural (:class:`typing.Protocol`), so adapters satisfy them
without importing this module and stay usable outside MoonMind.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass, LaunchPolicy
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class HostLaunchSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(
        "moonmind.omnigent-host-launch.v1", alias="schemaVersion"
    )
    executionPlanRef: str = Field(alias="executionPlanRef")
    stepExecutionId: str = Field(
        alias="stepExecutionId",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,510}[A-Za-z0-9]$",
    )
    runtimeBindingId: str = Field(alias="runtimeBindingId")
    hostLeaseRef: str = Field(alias="hostLeaseRef")
    hostLeaseGeneration: int = Field(alias="hostLeaseGeneration", ge=1)
    hostClassRef: str = Field(alias="hostClassRef")
    imageRef: str = Field(alias="imageRef")
    serverEndpointRef: str = Field(alias="serverEndpointRef")
    serverUrl: str = Field(alias="serverUrl")
    networkRef: str = Field(alias="networkRef")
    limits: dict[str, int]
    runtime: dict[str, Any]
    correlationName: str = Field(alias="correlationName")
    workspaceAttachment: dict[str, Any] = Field(alias="workspaceAttachment")
    skillAttachment: dict[str, Any] = Field(alias="skillAttachment")
    toolAttachments: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple, alias="toolAttachments"
    )
    credentialAttachments: tuple[dict[str, Any], ...] = Field(
        default_factory=tuple, alias="credentialAttachments"
    )
    githubCredentialAttachment: dict[str, Any] | None = Field(
        None, alias="githubCredentialAttachment"
    )
    controlAttachment: dict[str, Any] | None = Field(None, alias="controlAttachment")
    stateAttachment: dict[str, Any] = Field(alias="stateAttachment")
    labels: dict[str, str]

    @model_validator(mode="after")
    def secret_free(self) -> "HostLaunchSpec":
        forbidden = {"secret", "password", "token", "apikey", "api_key", "key"}
        payload = self.model_dump(by_alias=True, mode="json")

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if str(key).lower() in forbidden:
                        raise ValueError(f"HostLaunchSpec contains forbidden key {key}")
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return self


def host_correlation_identity(host_lease_ref: str) -> str:
    digest = hashlib.sha256(host_lease_ref.encode("utf-8")).hexdigest()[:24]
    return f"mm-host-{digest}"

@runtime_checkable
class OmnigentWorkspaceMaterializationPort(Protocol):
    """Materialize the run-owned workspace attachment and its cleanup ref."""

    async def materialize(
        self,
        request: AgentExecutionRequest,
        *,
        mutation: Any,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentSkillDeliveryPort(Protocol):
    """Project the resolved Skill snapshot into a run-owned, cleanable mount."""

    async def anticipated_attachment(
        self, resolved_skills: Any, *, owner_ref: str
    ) -> dict[str, Any]: ...

    async def materialize(
        self, resolved_skills: Any, *, owner_ref: str
    ) -> dict[str, Any]: ...

    async def cleanup(self, attachment: dict[str, Any]) -> None: ...


@runtime_checkable
class OmnigentMountedToolPort(Protocol):
    """Materialize plan-selected mounted tool attachments."""

    async def materialize(self, resolved_tools: Any) -> Any: ...


@runtime_checkable
class OmnigentGithubCredentialPort(Protocol):
    """Materialize and clean up run-owned repository credentials."""

    def anticipated_attachment(
        self, resolved_tools: Any, *, owner_ref: str
    ) -> dict[str, Any] | None: ...

    async def materialize(
        self,
        *,
        request: AgentExecutionRequest,
        resolved_tools: Any,
        owner_ref: str,
        writer_image_ref: str,
        runtime_uid: int,
        runtime_gid: int,
    ) -> dict[str, Any] | None: ...

    async def cleanup(self, attachment: dict[str, Any]) -> None: ...


@runtime_checkable
class OmnigentEgressAttestationPort(Protocol):
    """Attest the enforced egress profile the host will be attached to."""

    async def attest(
        self, *, request: AgentExecutionRequest, launch_policy: LaunchPolicy
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentHostLauncherPort(Protocol):
    """Start one exact host from a secret-free launch spec."""

    server_url: str

    async def launch(
        self,
        *,
        spec: HostLaunchSpec,
        host_class: HostClass,
        launch_policy: LaunchPolicy,
        credential_handles: list[dict[str, Any]],
        runtime_environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentRuntimeEnvironmentPort(Protocol):
    """Build lease-scoped runtime capabilities at the infrastructure boundary."""

    def build(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        host_lease_ref: str,
        launch_policy: LaunchPolicy,
    ) -> Mapping[str, str]: ...


@runtime_checkable
class OmnigentHostRegistrationPort(Protocol):
    """Wait for the launched host to register its harness with the endpoint."""

    async def wait_for_registration(
        self,
        *,
        correlation_name: str,
        harness_id: str,
        credentialless: bool = False,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentHostAttestationPort(Protocol):
    """Produce exact-host attestation evidence for a launched host."""

    async def attest(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        spec: HostLaunchSpec,
        host_class: HostClass,
        launch_result: dict[str, Any],
        registration: dict[str, Any],
        credential_handles: list[dict[str, Any]],
        egress_attestation: dict[str, Any],
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentHostCleanupPort(Protocol):
    """Release the host container and its run-owned volumes."""

    async def cleanup(
        self,
        *,
        container_name: str,
        host_lease_ref: str,
        host_lease_generation: int,
        state_volume_ref: str,
        control_volume_ref: str | None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentHostContainerInventoryPort(Protocol):
    """Observe and reclaim label-owned host containers and their volumes.

    Orphan discovery and reclamation are a separate concern from realizing a
    host for a run: the janitor needs them without any ability to prepare a
    host, publish a workspace, or read a provider session. Every operation is
    ownership-scoped — an unlabeled or foreign container is refused, never
    silently reclaimed.
    """

    async def container_exists(self, container_name: str) -> bool: ...

    async def list_managed_containers(self) -> list[str]: ...

    async def managed_container_host_lease_ref(
        self, container_name: str
    ) -> str | None: ...

    async def remove_container(self, container_name: str) -> None: ...

    async def assert_container_owned(
        self, *, container_name: str, lease_id: str
    ) -> None: ...


@runtime_checkable
class OmnigentHostReleasePort(Protocol):
    """Release one host's capacity and publish its terminal cleanup evidence."""

    async def stop_host(
        self,
        *,
        binding: Any,
        host_lease: Any,
        effective_launch: Mapping[str, Any] | None = None,
        egress_evidence: Mapping[str, Any] | None = None,
        launch_evidence_ref: str | None = None,
        evidence_request: Any | None = None,
        artifact_gateway: Any | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentStaticHostReleasePort(Protocol):
    """Stop the static credential consumer when no host lease is active.

    Separate from :class:`OmnigentHostReleasePort`: releasing a leased host and
    stopping the deployment's static credential consumer are different
    authorities with different preconditions.
    """

    async def stop_static_host(self, *, binding: Any | None = None) -> None: ...


@runtime_checkable
class OmnigentHostReclamationPorts(
    OmnigentHostContainerInventoryPort,
    OmnigentHostReleasePort,
    OmnigentStaticHostReleasePort,
    Protocol,
):
    """The two capabilities a janitor needs: observe orphans, release capacity.

    A dependency-set declaration, not a third capability. The janitor must not
    be able to prepare a host, publish a workspace, or read a provider session,
    so it depends on these ports rather than on a concrete host runtime class.
    """


__all__ = [
    "HostLaunchSpec",
    "OmnigentEgressAttestationPort",
    "OmnigentGithubCredentialPort",
    "OmnigentHostAttestationPort",
    "OmnigentHostCleanupPort",
    "OmnigentHostContainerInventoryPort",
    "OmnigentHostReclamationPorts",
    "OmnigentHostReleasePort",
    "OmnigentStaticHostReleasePort",
    "OmnigentHostLauncherPort",
    "OmnigentHostRegistrationPort",
    "OmnigentMountedToolPort",
    "OmnigentSkillDeliveryPort",
    "OmnigentWorkspaceMaterializationPort",
    "host_correlation_identity",
]
