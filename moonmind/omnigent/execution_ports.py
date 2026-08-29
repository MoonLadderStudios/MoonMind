"""Narrow ports the profile-bound execution use case depends on.

Source issue: MoonLadderStudios/MoonMind#3711.

The replay-visible ``codex-profile-bound@1`` coordinator needs three unrelated
capabilities: the Provider Profile authority it is bound to, the persisted
execution policy snapshot, and the durable attempt ordinal of the surrounding
runtime. Each is a separate port so a Postgres adapter, a Temporal adapter, and
a hermetic test double stay independently substitutable; none of them is folded
into one broad "execution runtime" interface.

The port surface is pure: no SQLAlchemy model, FastAPI request, Temporal SDK
symbol, or provider-native payload crosses it. Adapters live in
:mod:`moonmind.omnigent.execution_adapters`.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


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
class ProfileBoundHostRuntimePort(Protocol):
    """Prepare, publish from, inspect, and release one profile-bound host.

    The legacy Codex coordinator owns ordering and evidence, not the container,
    network, mount, or credential-volume details behind these operations.
    """

    async def prepare_host(self, **kwargs: Any) -> Any: ...

    async def publish_workspace(self, **kwargs: Any) -> dict[str, Any]: ...

    async def inspect_session_completion(self, session_id: str) -> dict[str, Any]: ...

    async def stop_host(self, **kwargs: Any) -> dict[str, Any]: ...


@runtime_checkable
class ProfileBoundHostBindingPort(Protocol):
    """Durable host binding and fenced host-lease authority for one profile."""

    async def get_binding_for_profile(self, profile_id: str) -> Any: ...


__all__ = [
    "ExecutionAttemptPort",
    "ProfileBoundHostBindingPort",
    "ProfileBoundHostRuntimePort",
    "ExecutionPolicyAuthorityPort",
    "ExecutionPolicyAuthorityUnavailableError",
    "ProviderProfileAuthority",
    "ProviderProfileAuthorityPort",
]
