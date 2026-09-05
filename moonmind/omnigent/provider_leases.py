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
    async def acquire_execution_lease(self, **kwargs: Any) -> CredentialLease:
        raise NotImplementedError

    async def inspect_lease(self, lease: CredentialLease) -> dict[str, Any]:
        raise NotImplementedError

    async def release_lease(self, lease: CredentialLease) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class AcquiredProviderLease:
    slot: str
    provider_profile_ref: str
    capacity_scope_ref: str
    provider_lease_ref: str
    credential_generation: int
    lease: CredentialLease
    #: True when the launching workflow admitted this capacity and therefore
    #: owns its release (MoonLadderStudios/MoonMind#3878 invariant 10). This
    #: Activity must never release a lease it does not own.
    owned_by_workflow: bool = False

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
            "leaseOwnerIsWorkflow": self.owned_by_workflow,
            # MoonLadderStudios/MoonMind#3879: carry the grant generation into
            # the persisted handle so janitor recovery releases the exact grant
            # this binding owns, not whatever the owner ID holds later.
            "leaseFencingGeneration": self.lease.fencing_generation,
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
        admitted_capacity: Any | None = None,
    ) -> tuple[AcquiredProviderLease, ...]:
        """Acquire provider capacity in deterministic Provider Profile order.

        When ``admitted_capacity`` is present the launching workflow already
        queued for and owns this capacity (MoonLadderStudios/MoonMind#3878
        invariant 6). Acquisition then confirms the workflow-owned lease and
        returns immediately, so no wait for capacity happens inside this
        long-running Activity, and release authority stays with the workflow.
        """

        from api_service.db.models import ManagedAgentProviderProfile

        by_profile: dict[str, list[str]] = {}
        for slot, binding in plan.payload.credentialBindings.items():
            by_profile.setdefault(binding.providerProfileRef, []).append(slot)
        if admitted_capacity is not None:
            admitted_ref = str(
                getattr(admitted_capacity, "provider_profile_ref", "")
            ).strip()
            if sorted(by_profile) != [admitted_ref]:
                # The workflow admitted capacity for one exact profile; a plan
                # that selects another (or several) must fail closed rather
                # than silently acquire unadmitted capacity.
                raise HarnessPlatformError(
                    "admitted provider capacity does not match the plan's "
                    "selected Provider Profiles",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
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
                owned_by_workflow = admitted_capacity is not None
                if owned_by_workflow:
                    owner_id = str(
                        getattr(admitted_capacity, "lease_owner_id", "")
                    ).strip()
                    if not owner_id:
                        raise HarnessPlatformError(
                            "admitted provider capacity is missing its lease owner",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
                        )
                else:
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
                        # A workflow-owned grant needs no Activity liveness
                        # check: the requesting workflow *is* the owner the
                        # manager already tracks.
                        owner_is_workflow=owned_by_workflow,
                        metadata={
                            "capacityScopeRef": capacity_scope_ref,
                            "executionPlanRef": plan.planRef,
                            # Activity-owned grants are liveness-checked
                            # against this owning workflow by the manager.
                            "workflowId": (
                                owner_id if owned_by_workflow else workflow_id
                            ),
                        },
                    )
                    if owned_by_workflow and not lease.already_held:
                        # The workflow said it holds this capacity, and the
                        # manager disagrees. Accepting the fresh grant would
                        # double-count the ledger and leave two releasers.
                        raise HarnessPlatformError(
                            "workflow-admitted provider capacity is no longer "
                            f"held for {profile_ref}",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
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
                    # Only release a lease this Activity actually created. A
                    # workflow-owned lease that was confirmed here belongs to
                    # the workflow, which releases it after cleanup completes.
                    if lease is not None and not (
                        owned_by_workflow and lease.already_held
                    ):
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
                            owned_by_workflow=owned_by_workflow,
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
            if acquired.owned_by_workflow:
                # The launching workflow admitted this capacity and releases it
                # last, after the Activity's own cleanup completes
                # (MoonLadderStudios/MoonMind#3878 invariant 10).
                continue
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
            if value.get("leaseOwnerIsWorkflow") is True:
                # Janitor recovery must not release capacity a live workflow
                # owns. The manager's lease eviction is the safety net if that
                # workflow is gone (#3878 invariants 10 and 11).
                seen.add(lease_id)
                continue
            try:
                purpose = CredentialLeasePurpose(str(value["leasePurpose"]))
                raw_fence = value.get("leaseFencingGeneration")
                reconstructed.append(
                    CredentialLease(
                        profile_id=str(value["providerProfileRef"]),
                        runtime_id=str(value["runtimeId"]),
                        lease_id=lease_id,
                        owner_id=str(value["leaseOwnerId"]),
                        purpose=purpose,
                        # A binding written before fenced grants carries no
                        # generation; the manager honours an unfenced release
                        # so recovery of those handles still frees capacity.
                        fencing_generation=(
                            int(raw_fence) if raw_fence is not None else None
                        ),
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
