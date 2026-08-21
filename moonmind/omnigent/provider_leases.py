"""Provider Profile capacity acquisition for generic Omnigent execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    deterministic_lease_owner_id,
)


class ProviderLeaseClient(Protocol):
    async def acquire_execution_lease(self, **kwargs: Any) -> CredentialLease: ...

    async def inspect_lease(self, lease: CredentialLease) -> dict[str, Any]: ...

    async def release_lease(self, lease: CredentialLease) -> None: ...


@dataclass(frozen=True)
class AcquiredProviderLease:
    slot: str
    provider_profile_ref: str
    capacity_scope_ref: str
    provider_lease_ref: str
    credential_generation: int
    lease: CredentialLease

    def runtime_binding_value(
        self, *, credential_runtime_ref: str = "pending"
    ) -> dict[str, Any]:
        return {
            "providerProfileRef": self.provider_profile_ref,
            "capacityScopeRef": self.capacity_scope_ref,
            "providerLeaseRef": self.provider_lease_ref,
            "runtimeId": self.lease.runtime_id,
            "leaseOwnerId": self.lease.owner_id,
            "leasePurpose": self.lease.purpose.value,
            "credentialGeneration": self.credential_generation,
            "credentialRuntimeRef": credential_runtime_ref,
        }


class OmnigentProviderLeaseCoordinator:
    def __init__(
        self, *, session_factory: Any, lease_client: ProviderLeaseClient
    ) -> None:
        self._session_factory = session_factory
        self._leases = lease_client

    async def acquire_all(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        workflow_id: str,
        step_execution_id: str,
        idempotency_key: str,
    ) -> tuple[AcquiredProviderLease, ...]:
        from api_service.db.models import ManagedAgentProviderProfile

        by_profile: dict[str, list[str]] = {}
        for slot, binding in plan.payload.credentialBindings.items():
            by_profile.setdefault(binding["providerProfileRef"], []).append(slot)
        acquired: list[AcquiredProviderLease] = []
        try:
            for profile_ref in sorted(by_profile):
                async with self._session_factory() as session:
                    profile = await session.get(
                        ManagedAgentProviderProfile, profile_ref
                    )
                    if profile is None:
                        raise HarnessPlatformError(
                            f"Provider Profile {profile_ref} no longer exists",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                        )
                    auth_state = getattr(
                        profile.auth_state, "value", profile.auth_state
                    )
                    if not profile.enabled or auth_state != "connected":
                        raise HarnessPlatformError(
                            f"Provider Profile {profile_ref} is not launch ready",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                        )
                    runtime_id = profile.runtime_id
                    capacity_scope_ref = profile.capacity_scope_ref
                owner_id = deterministic_lease_owner_id(
                    profile_id=profile_ref,
                    purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                    workflow_id=workflow_id,
                    step_execution_id=step_execution_id,
                    idempotency_key=idempotency_key,
                )
                lease: CredentialLease | None = None
                try:
                    lease = await self._leases.acquire_execution_lease(
                        runtime_id=runtime_id,
                        profile_id=profile_ref,
                        owner_id=owner_id,
                        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                        metadata={
                            "capacityScopeRef": capacity_scope_ref,
                            "executionPlanRef": plan.planRef,
                        },
                    )
                    inspection = await self._leases.inspect_lease(lease)
                    if inspection.get("active") is False:
                        raise HarnessPlatformError(
                            f"Provider Profile lease is not active for {profile_ref}",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
                        )
                    # Reload after the capacity handoff. This generation becomes
                    # sticky and is persisted before any secret is resolved.
                    async with self._session_factory() as session:
                        leased_profile = await session.get(
                            ManagedAgentProviderProfile, profile_ref
                        )
                        if leased_profile is None:
                            raise HarnessPlatformError(
                                f"Provider Profile {profile_ref} disappeared after lease acquisition",
                                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                            )
                        generation = int(leased_profile.credential_generation)
                except Exception as exc:
                    if lease is not None:
                        try:
                            await self._leases.release_lease(lease)
                        except Exception as release_exc:
                            raise HarnessPlatformError(
                                f"Provider Profile lease cleanup failed for {profile_ref}",
                                code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
                            ) from release_exc
                    if isinstance(exc, HarnessPlatformError):
                        raise
                    raise HarnessPlatformError(
                        f"Provider Profile capacity is unavailable for {profile_ref}",
                        code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
                    ) from exc
                assert lease is not None
                for slot in sorted(by_profile[profile_ref]):
                    acquired.append(
                        AcquiredProviderLease(
                            slot=slot,
                            provider_profile_ref=profile_ref,
                            capacity_scope_ref=capacity_scope_ref,
                            provider_lease_ref=f"provider-profile-lease:{lease.lease_id}",
                            credential_generation=generation,
                            lease=lease,
                        )
                    )
            return tuple(acquired)
        except BaseException:
            await self.release_all(acquired)
            raise

    async def release_all(
        self, leases: list[AcquiredProviderLease] | tuple[AcquiredProviderLease, ...]
    ) -> None:
        released: set[str] = set()
        for acquired in reversed(tuple(leases)):
            if acquired.lease.lease_id in released:
                continue
            await self._leases.release_lease(acquired.lease)
            released.add(acquired.lease.lease_id)

    async def release_from_binding(
        self, provider_leases: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Release persisted lease handles without resolving any credential."""

        reconstructed: list[CredentialLease] = []
        seen: set[str] = set()
        for value in provider_leases.values():
            lease_ref = str(value.get("providerLeaseRef") or "")
            lease_id = lease_ref.removeprefix("provider-profile-lease:")
            if not lease_id or lease_id in seen:
                continue
            try:
                purpose = CredentialLeasePurpose(str(value["leasePurpose"]))
                reconstructed.append(
                    CredentialLease(
                        profile_id=str(value["providerProfileRef"]),
                        runtime_id=str(value["runtimeId"]),
                        lease_id=lease_id,
                        owner_id=str(value["leaseOwnerId"]),
                        purpose=purpose,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise HarnessPlatformError(
                    "persisted Provider Profile lease authority is incomplete",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                ) from exc
            seen.add(lease_id)
        for lease in reversed(reconstructed):
            await self._leases.release_lease(lease)


__all__ = ["AcquiredProviderLease", "OmnigentProviderLeaseCoordinator"]
