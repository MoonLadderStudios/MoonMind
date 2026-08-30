"""Narrow ports the profile-bound execution use case depends on.

Source issue: MoonLadderStudios/MoonMind#3711.

The replay-visible ``codex-profile-bound@1`` coordinator needs several
unrelated capabilities: the Provider Profile authority it is bound to, the
persisted execution policy snapshot, the durable attempt ordinal of the
surrounding runtime, and four separate host capabilities (preparation,
workspace publication, provider session inspection, and host release). Each is a
separate port with a typed signature so a Postgres adapter, a Temporal adapter,
a Docker host adapter, and a hermetic test double stay independently
substitutable; none of them is folded into one broad "execution runtime"
interface, and none accepts an untyped ``**kwargs`` payload.

The port surface is pure: no SQLAlchemy model, FastAPI request, Temporal SDK
symbol, or provider-native payload crosses it. Adapters live in
:mod:`moonmind.omnigent.execution_adapters`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# Host release is owned by the host-port module; the coordinator consumes that
# one definition rather than declaring a second protocol with the same name.
from moonmind.omnigent.host_ports import OmnigentHostReleasePort


class ExecutionPolicyAuthorityUnavailableError(RuntimeError):
    """The persisted policy snapshot could not be resolved for a launch.

    Adapters normalize their storage-native conflict vocabulary into this
    failure so the coordinator never catches a persistence exception type.
    """


class ProviderProfileAuthority(BaseModel):
    """The bounded, non-secret Provider Profile authority a launch consumes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(alias="profileId")
    runtime_id: str = Field(alias="runtimeId")
    credential_generation: int = Field(0, alias="credentialGeneration", ge=0)
    cooldown_after_429_seconds: int = Field(0, alias="cooldownAfter429Seconds", ge=0)
    launch_ready: bool = Field(alias="launchReady")


@runtime_checkable
class ProviderProfileAuthorityPort(Protocol):
    """Resolve the durable Provider Profile authority bound to a run."""

    async def resolve(self, profile_id: str) -> ProviderProfileAuthority: ...


@runtime_checkable
class ExecutionPolicyAuthorityPort(Protocol):
    """Resolve the immutable runtime policy snapshot for a launch policy ref."""

    async def resolve_runtime_snapshot(self, policy_ref: str) -> dict[str, Any]: ...


@runtime_checkable
class ExecutionAttemptPort(Protocol):
    """Report the durable attempt ordinal of the surrounding execution."""

    def current_attempt(self) -> int: ...


@runtime_checkable
class OmnigentHostPreparationPort(Protocol):
    """Realize one profile-bound host and return its preflight evidence.

    The coordinator owns ordering and evidence; the adapter owns the container,
    network, mount, and credential-volume details behind this one operation.
    """

    async def prepare_host(
        self,
        *,
        binding: Any,
        host_lease: Any,
        workspace_key: str,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        resolved_skillset_ref: str | None = None,
        artifact_gateway: Any | None = None,
        evidence_request: Any | None = None,
        cleanup_authority_store: Any | None = None,
        target_repository: str = "",
        required_capabilities: tuple[str, ...] = (),
        execution_fanout_authorization: Mapping[str, Any] | None = None,
        github_token: str | None = None,
        github_mutation_required: bool = False,
        effective_launch: Mapping[str, Any] | None = None,
        repository_source: str = "",
        repository_provider: str = "",
        repository_connection_ref: str = "",
        repository_client_evidence: Mapping[str, str] | None = None,
        starting_branch: str | None = None,
        target_branch: str | None = None,
        checkout_commit: str | None = None,
        restore_input_refs: tuple[str, ...] = (),
        workspace_checkpoint_restore_ref: str | None = None,
        attachment_refs: tuple[str, ...] = (),
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentWorkspacePublicationPort(Protocol):
    """Publish the authoritative workspace before host capacity is released."""

    async def publish_workspace(
        self,
        *,
        workspace_locator: Mapping[str, Any],
        current_workflow_id: str,
        current_step_execution_id: str,
        publication_identity: str,
        publish_mode: str,
        base_branch: str | None,
        repository: str,
        github_token: str | None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class OmnigentProviderSessionInspectionPort(Protocol):
    """Read bounded terminal-answer evidence for one provider session."""

    async def inspect_session_completion(self, session_id: str) -> dict[str, Any]: ...


@runtime_checkable
class ProfileBoundHostPorts(
    OmnigentHostPreparationPort,
    OmnigentWorkspacePublicationPort,
    OmnigentProviderSessionInspectionPort,
    OmnigentHostReleasePort,
    Protocol,
):
    """The four separate host capabilities the legacy Codex coordinator needs.

    This is a declaration of a dependency *set*, not a fifth capability: each
    member above is independently substitutable and independently contract
    tested, so an adapter may implement host release without implementing host
    preparation. Nothing may be added here that is not already one of the four.
    """


@runtime_checkable
class ProfileBoundHostBindingPort(Protocol):
    """Durable host binding and fenced host-lease authority for one profile."""

    async def get_binding_for_profile(self, profile_id: str) -> Any: ...


__all__ = [
    "ExecutionAttemptPort",
    "OmnigentHostPreparationPort",
    "OmnigentProviderSessionInspectionPort",
    "OmnigentWorkspacePublicationPort",
    "ProfileBoundHostBindingPort",
    "ProfileBoundHostPorts",
    "ExecutionPolicyAuthorityPort",
    "ExecutionPolicyAuthorityUnavailableError",
    "ProviderProfileAuthority",
    "ProviderProfileAuthorityPort",
]
