"""Credential-safe collection authority for provider account usage.

``ProviderAccountUsageService`` coordinates refreshes in the API process; this
module is the common interface the collection executor and the provider
adapters share. A refresh reaches the credential in exactly one of two ways:

* **Active owner.** While a runtime binding, generic host lease, or ledger
  lease uses the credential home, only that owner may read it. The adapter
  asks the owner through its existing transport; when the owner cannot answer,
  the attempt is deferred. Nothing starts a second credential-writing process.
* **Idle probe.** Otherwise the executor takes a non-blocking, exclusive
  ``usage_observation`` grant from the Provider Profile manager and runs the
  adapter's bounded probe in the approved runtime image.

Adapters supply provider behavior only: how to ask an owner, which command
reads usage, how to parse it, and an optional native recovery action. They
never choose profile, generation, owner, or attempt: :func:`bind_owner_report`
binds those from trusted state, so a caller-supplied ID or a token-bearing
payload cannot establish authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from moonmind.provider_profiles.account_usage import (
    AccountIdentityInput,
    AccountUsageFailureCode,
    AccountUsageObservation,
    ObservationAuthority,
    UsageInput,
)

#: An idle probe command may run no longer than this; the container is killed
#: from inside before the manager's usage-observation lease lifetime ends.
MAX_PROBE_SECONDS = 120
MAX_PROBE_OUTPUT_BYTES = 65_536


class UsageCollectionRoute(StrEnum):
    ACTIVE_OWNER = "active_owner"
    IDLE_PROBE = "idle_probe"


class UsageCollectionStatus(StrEnum):
    """Bounded outcome an executor step returns across durable boundaries."""

    OBSERVED = "observed"
    DEFERRED = "deferred"
    FAILED = "failed"
    #: The attempt, generation, or grant no longer matches; nothing was written.
    OBSOLETE = "obsolete"
    NOT_SUPPORTED = "not_supported"


def credential_resource_key(*, runtime_id: str, volume_ref: str) -> str:
    """Return a non-secret digest naming one enrolled credential home."""

    material = "\x1f".join((runtime_id.strip(), volume_ref.strip()))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"credential-home:{digest}"


@dataclass(frozen=True)
class CredentialOwner:
    """One current consumer of a credential home, resolved from trusted state.

    ``kind`` is ``runtime_binding`` for a generic Omnigent host (its owner
    transport may answer) or ``ledger_lease`` for any other held or
    cleanup-requested Provider Profile lease (it has no usage transport).
    """

    kind: str
    profile_id: str
    owner_ref: str
    host_lease_ref: str | None = None
    workflow_id: str | None = None


@dataclass(frozen=True)
class UsageCollectionTarget:
    """The trusted identity one collection attempt is bound to."""

    profile_id: str
    credential_generation: int
    provider_id: str
    runtime_id: str
    attempt_id: str
    deadline_at: datetime


@dataclass(frozen=True)
class OwnerUsageRequest:
    """Compact, secret-free request an adapter forwards to an active owner."""

    target: UsageCollectionTarget
    owner: CredentialOwner


class OwnerUsageReport(BaseModel):
    """What an owner or probe may report: account facts and usage, nothing else.

    No profile, generation, attempt, or authority field exists here, so an
    owner cannot redirect its report; unknown fields are rejected so raw
    provider bodies and tokens cannot ride along.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,127}$"
    )
    workspace_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,127}$"
    )
    identity: AccountIdentityInput | None = None
    usage: UsageInput | None = None


def bind_owner_report(
    target: UsageCollectionTarget, report: OwnerUsageReport
) -> AccountUsageObservation:
    """Bind a report to the attempt the trusted boundary admitted.

    The service then fences it on generation, attempt ownership, and the
    profile's bound account before anything is written.
    """

    return AccountUsageObservation(
        profile_id=target.profile_id,
        credential_generation=target.credential_generation,
        provider_id=target.provider_id,
        runtime_id=target.runtime_id,
        authority=ObservationAuthority(
            kind="refresh_attempt", attempt_id=target.attempt_id
        ),
        account_id=report.account_id,
        workspace_id=report.workspace_id,
        identity=report.identity,
        usage=report.usage,
    )


def bind_native_owner_event(
    *,
    owner_lease: Mapping[str, Any],
    provider_id: str,
    runtime_id: str,
    report: OwnerUsageReport,
) -> AccountUsageObservation:
    """Bind an unsolicited owner event (for example Claude status-line quota).

    ``owner_lease`` is the owner's held ledger lease as the trusted transport
    resolved it, never the event's own claims: its profile, credential
    generation, and expiry become the observation's authority.
    """

    expires_at = owner_lease.get("expiresAt") or owner_lease.get("expires_at")
    return AccountUsageObservation(
        profile_id=str(owner_lease["profileId"]),
        credential_generation=int(owner_lease["credentialGeneration"]),
        provider_id=provider_id,
        runtime_id=runtime_id,
        authority=ObservationAuthority(
            kind="native_owner", owner_expires_at=expires_at
        ),
        account_id=report.account_id,
        workspace_id=report.workspace_id,
        identity=report.identity,
        usage=report.usage,
    )


@dataclass(frozen=True)
class UsageProbePlan:
    """One bounded command run inside the approved runtime image.

    ``writes_credentials`` is set only for an adapter's compatibility-tested
    native recovery action. It mounts the enrolled home writable, which the
    executor allows only under the exclusive idle grant; every other probe
    mounts it read-only.
    """

    argv: tuple[str, ...]
    writes_credentials: bool = False
    timeout_seconds: int = 60
    env: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or any(not isinstance(arg, str) for arg in self.argv):
            raise ValueError("usage probe argv must be non-empty strings")
        if not 1 <= self.timeout_seconds <= MAX_PROBE_SECONDS:
            raise ValueError(
                f"usage probe timeout must be 1..{MAX_PROBE_SECONDS} seconds"
            )


@dataclass(frozen=True)
class ProbeResult:
    """An adapter's parse of one probe run: a report or a typed failure.

    ``recovery_required`` means the stored access token expired; the executor
    then runs the adapter's recovery action once, still under the grant.
    """

    report: OwnerUsageReport | None = None
    failure: AccountUsageFailureCode | None = None
    retry_after: Any = None
    recovery_required: bool = False

    def __post_init__(self) -> None:
        if (self.report is None) == (
            self.failure is None and not self.recovery_required
        ):
            raise ValueError("probe result needs exactly one of report or failure")


@dataclass(frozen=True)
class IdleProbeContext:
    target: UsageCollectionTarget
    credential_home_path: str


class UsageCollectorAdapter(Protocol):
    """Provider-specific collection, plugged in by the Codex and Claude adapters."""

    runtime_id: str

    def supports(self, *, provider_id: str) -> bool:
        """Synchronous, side-effect-free capability check."""

    async def request_from_owner(
        self, request: OwnerUsageRequest
    ) -> OwnerUsageReport | None:
        """Ask the active owner through its existing transport, or ``None``.

        ``None`` means the active mode cannot provide an observation now; the
        attempt is deferred with cached data kept.
        """

    def idle_probe(self, context: IdleProbeContext) -> UsageProbePlan | None:
        """Return the read-only usage command, or ``None`` when unavailable."""

    def recovery_probe(self, context: IdleProbeContext) -> UsageProbePlan | None:
        """Return the native token-recovery action, or ``None`` if unsupported."""

    def parse_probe(
        self, plan: UsageProbePlan, exit_code: int, stdout: bytes
    ) -> ProbeResult:
        """Translate bounded probe output into a report or a typed failure."""


@dataclass(frozen=True)
class UsageCollectorRegistry:
    adapters: tuple[UsageCollectorAdapter, ...] = field(default_factory=tuple)

    def get(self, runtime_id: str, provider_id: str) -> UsageCollectorAdapter | None:
        for adapter in self.adapters:
            if adapter.runtime_id == runtime_id and adapter.supports(
                provider_id=provider_id
            ):
                return adapter
        return None


def default_usage_collectors() -> UsageCollectorRegistry:
    """Adapters available in this build."""

    from moonmind.provider_profiles.claude_account_usage import ClaudeUsageCollector

    return UsageCollectorRegistry((ClaudeUsageCollector(),))


async def resolve_credential_consumers(
    profile_ids: Iterable[str],
    *,
    runtime_bindings: Any,
    host_leases: Any,
    active_ledger_leases: Iterable[Mapping[str, Any]],
) -> list[CredentialOwner]:
    """Read every current consumer of the credential home these profiles share.

    Generic runtime bindings with a live host lease come first because their
    owner may answer a usage request; held or cleanup-requested ledger leases
    follow. An old OAuth binding record alone is never treated as a consumer
    or as proof of idleness. This is read-only: it never drains, migrates, or
    stops anything.
    """

    from moonmind.omnigent.generic_host_janitor import list_profile_host_consumers

    owners: list[CredentialOwner] = []
    for profile_id in sorted(set(profile_ids)):
        for binding in await list_profile_host_consumers(
            profile_id, runtime_bindings=runtime_bindings, host_leases=host_leases
        ):
            owner = (getattr(binding, "phaseResults", None) or {}).get("owner") or {}
            owners.append(
                CredentialOwner(
                    kind="runtime_binding",
                    profile_id=profile_id,
                    owner_ref=str(binding.bindingId),
                    host_lease_ref=str(binding.hostLeaseRef),
                    workflow_id=owner.get("workflowId"),
                )
            )
    for lease in active_ledger_leases:
        owners.append(
            CredentialOwner(
                kind="ledger_lease",
                profile_id=str(lease["profile_id"]),
                owner_ref=str(lease["lease_id"]),
                workflow_id=lease.get("workflow_id"),
            )
        )
    return owners


__all__ = [
    "MAX_PROBE_OUTPUT_BYTES",
    "MAX_PROBE_SECONDS",
    "CredentialOwner",
    "IdleProbeContext",
    "OwnerUsageReport",
    "OwnerUsageRequest",
    "ProbeResult",
    "UsageCollectionRoute",
    "UsageCollectionStatus",
    "UsageCollectionTarget",
    "UsageCollectorAdapter",
    "UsageCollectorRegistry",
    "UsageProbePlan",
    "bind_native_owner_event",
    "bind_owner_report",
    "credential_resource_key",
    "default_usage_collectors",
    "resolve_credential_consumers",
]
