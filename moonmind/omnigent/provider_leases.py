"""Provider Profile capacity acquisition for generic Omnigent execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, NoReturn, Protocol

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


def _parse_lease_timestamp(value: Any) -> datetime | None:
    """Parse a manager lease timestamp, treating anything unusable as absent."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class ProviderLeaseClient(Protocol):
    async def acquire_execution_lease(self, **kwargs: Any) -> CredentialLease:
        raise NotImplementedError

    async def inspect_lease(self, lease: CredentialLease) -> dict[str, Any]:
        raise NotImplementedError

    async def release_lease(self, lease: CredentialLease) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class _AdmittedLeaseFence:
    """The durable identity one admitted grant must positively match."""

    owner_id: str
    plan_ref: str
    step_execution_id: str
    idempotency_key: str
    credential_generation: int | None


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
        """Bind provider capacity in deterministic Provider Profile order.

        When ``admitted_capacity`` is present the launching workflow already
        queued for and owns this capacity (MoonLadderStudios/MoonMind#3878
        invariant 6). This method then *consumes* that grant by inspection
        only (MoonLadderStudios/MoonMind#3880): it never calls an acquiring
        client, so it can neither grant new capacity nor wait for a
        replacement, and release authority stays with the workflow. A missing,
        revoked, expired, wrong-plan, wrong-owner or stale-generation ticket
        fails closed before any host or credential side effect; the deliberate
        re-admission that follows belongs to the workflow owner.

        Without ``admitted_capacity`` this is the pre-#3878 shape: the Activity
        acquires and releases its own Activity-owned leases.
        """

        by_profile: dict[str, list[str]] = {}
        for slot, binding in plan.payload.credentialBindings.items():
            by_profile.setdefault(binding.providerProfileRef, []).append(slot)
        if admitted_capacity is not None:
            return await self._consume_admitted_capacity(
                plan=plan,
                by_profile=by_profile,
                admitted_capacity=admitted_capacity,
            )
        return await self._acquire_activity_owned(
            plan=plan,
            by_profile=by_profile,
            workflow_id=workflow_id,
            step_execution_id=step_execution_id,
            idempotency_key=idempotency_key,
        )

    async def _consume_admitted_capacity(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        by_profile: dict[str, list[str]],
        admitted_capacity: Any,
    ) -> tuple[AcquiredProviderLease, ...]:
        """Positively establish the workflow-admitted identity, or fail closed."""

        owner_id = str(getattr(admitted_capacity, "lease_owner_id", "")).strip()
        if not owner_id:
            raise HarnessPlatformError(
                "admitted provider capacity is missing its lease owner",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
            )
        admitted_profiles = tuple(getattr(admitted_capacity, "profiles", ()) or ())
        admitted_refs = sorted(
            str(getattr(item, "provider_profile_ref", "")).strip()
            for item in admitted_profiles
        )
        if not admitted_refs or "" in admitted_refs:
            raise HarnessPlatformError(
                "admitted provider capacity names no Provider Profile",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
            )
        if sorted(by_profile) != admitted_refs:
            # The workflow admitted an exact profile set; a plan that selects
            # another must fail closed rather than bind unadmitted capacity.
            raise HarnessPlatformError(
                "admitted provider capacity does not match the plan's "
                "selected Provider Profiles",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        admitted_plan_ref = str(
            getattr(admitted_capacity, "execution_plan_ref", "") or ""
        ).strip()
        if admitted_plan_ref and admitted_plan_ref != plan.planRef:
            raise HarnessPlatformError(
                "admitted provider capacity was granted for another execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        step_execution_id = str(
            getattr(admitted_capacity, "step_execution_id", "") or ""
        ).strip()
        idempotency_key = str(
            getattr(admitted_capacity, "idempotency_key", "") or ""
        ).strip()

        acquired: list[AcquiredProviderLease] = []
        for profile_ref in admitted_refs:  # deterministic Provider Profile order
            admitted_profile = next(
                item
                for item in admitted_profiles
                if str(getattr(item, "provider_profile_ref", "")).strip()
                == profile_ref
            )
            runtime_id = str(
                getattr(admitted_profile, "provider_runtime_id", "")
            ).strip()
            if not runtime_id:
                raise HarnessPlatformError(
                    "admitted provider capacity is missing its capacity ledger "
                    f"for {profile_ref}",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
                )
            # A handle, never a request: ``inspect_lease`` reads the manager's
            # ledger and cannot reserve, queue for, or extend capacity.
            lease = CredentialLease(
                profile_id=profile_ref,
                runtime_id=runtime_id,
                lease_id=owner_id,
                owner_id=owner_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                already_held=True,
            )
            admitted_generation = getattr(
                admitted_profile, "credential_generation", None
            )
            inspection = await self._leases.inspect_lease(lease)
            self._assert_admitted_lease_matches(
                inspection,
                profile_ref=profile_ref,
                fence=_AdmittedLeaseFence(
                    owner_id=owner_id,
                    # The ticket's own binding, not the plan being executed: a
                    # v1 ticket bound no plan, and a v2 ticket already matched
                    # above.
                    plan_ref=admitted_plan_ref,
                    step_execution_id=step_execution_id,
                    idempotency_key=idempotency_key,
                    credential_generation=admitted_generation,
                ),
            )
            capacity_scope_ref = str(
                getattr(admitted_profile, "capacity_scope_ref", None)
                or f"provider-profile:{profile_ref}"
            )
            generation = await self._fenced_credential_generation(
                profile_ref=profile_ref,
                admitted_generation=admitted_generation,
            )
            for slot in sorted(by_profile[profile_ref]):
                acquired.append(
                    AcquiredProviderLease(
                        slot=slot,
                        provider_profile_ref=profile_ref,
                        capacity_scope_ref=capacity_scope_ref,
                        provider_lease_ref=(
                            f"provider-profile-lease:{lease.lease_id}"
                        ),
                        credential_generation=generation,
                        lease=lease,
                        owned_by_workflow=True,
                    )
                )
        return tuple(acquired)

    @staticmethod
    def _assert_admitted_lease_matches(
        inspection: Any,
        *,
        profile_ref: str,
        fence: "_AdmittedLeaseFence",
    ) -> None:
        """Require complete, matching, live evidence for the admitted grant.

        Absence is never acceptance: an empty or malformed inspection payload,
        a lease held by another owner or profile, an expired lease, or a lease
        granted for a different plan, step or request all fail closed
        (MoonLadderStudios/MoonMind#3880 AC2). Every field compared here is
        persisted on the durable lease row, so a manager restart preserves the
        fence instead of silently weakening it.
        """

        def _reject(reason: str) -> NoReturn:
            raise HarnessPlatformError(
                "workflow-admitted provider capacity is not usable for "
                f"{profile_ref}: {reason}",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_LEASE_UNAVAILABLE,
            )

        if not isinstance(inspection, Mapping) or not inspection:
            _reject("lease inspection returned no evidence")
        if inspection.get("active") is not True:
            _reject("the manager does not report an active lease")
        if str(inspection.get("profile_id") or "").strip() != profile_ref:
            _reject("the lease is held against another Provider Profile")
        inspected_owner = str(
            inspection.get("ownerId") or inspection.get("owner_id") or ""
        ).strip()
        if inspected_owner != fence.owner_id:
            _reject("the lease is held by another owner")
        if inspection.get("ownerIsWorkflow") is not True:
            _reject("the lease is not workflow-owned")
        expires_at = _parse_lease_timestamp(inspection.get("expiresAt"))
        if expires_at is None:
            _reject("the lease carries no usable expiry")
        if expires_at <= datetime.now(UTC):
            _reject("the lease has expired")
        for label, expected, observed in (
            (
                "execution plan",
                fence.plan_ref,
                str(inspection.get("executionPlanRef") or "").strip(),
            ),
            (
                "step execution",
                fence.step_execution_id,
                str(inspection.get("stepExecutionId") or "").strip(),
            ),
            (
                "request identity",
                fence.idempotency_key,
                str(inspection.get("idempotencyKey") or "").strip(),
            ),
        ):
            if not expected:
                # A retained v1 ticket never bound this identity, so there is
                # nothing to establish; the checks above still apply.
                continue
            if observed != expected:
                _reject(f"the lease was granted for another {label}")
        if fence.credential_generation is not None:
            granted_generation = inspection.get("credentialGeneration")
            if granted_generation is not None and (
                int(granted_generation) != int(fence.credential_generation)
            ):
                _reject(
                    "the lease was granted against another credential generation"
                )

    async def _fenced_credential_generation(
        self,
        *,
        profile_ref: str,
        admitted_generation: Any,
    ) -> int:
        """Return the generation this handoff is fenced to.

        The workflow recorded the generation it admitted against. Reading a
        fresh one here would silently adopt a rotation that happened while the
        run queued; comparing instead makes rotation an explicit, typed
        re-admission (MoonLadderStudios/MoonMind#3880).
        """

        from api_service.db.models import ManagedAgentProviderProfile

        async with self._session_factory() as session:
            profile = await session.get(ManagedAgentProviderProfile, profile_ref)
            if profile is None:
                raise HarnessPlatformError(
                    f"Provider Profile {profile_ref} no longer exists",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
            auth_state = getattr(profile.auth_state, "value", profile.auth_state)
            if not profile.enabled or auth_state != "connected":
                raise HarnessPlatformError(
                    f"Provider Profile {profile_ref} is not launch ready",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
            current_generation = int(profile.credential_generation)
        if admitted_generation is None:
            # Retained v1 tickets never recorded a generation; the sticky
            # generation is the one observed at consumption, as before.
            return current_generation
        if int(admitted_generation) != current_generation:
            raise HarnessPlatformError(
                "Provider Profile credentials rotated after capacity was "
                f"admitted for {profile_ref}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        return current_generation

    async def _acquire_activity_owned(
        self,
        *,
        plan: OmnigentExecutionPlanEnvelope,
        by_profile: dict[str, list[str]],
        workflow_id: str,
        step_execution_id: str,
        idempotency_key: str,
    ) -> tuple[AcquiredProviderLease, ...]:
        """Acquire Activity-owned capacity in deterministic profile order."""

        from api_service.db.models import ManagedAgentProviderProfile

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
                        owner_is_workflow=False,
                        metadata={
                            "capacityScopeRef": capacity_scope_ref,
                            "executionPlanRef": plan.planRef,
                            # Activity-owned grants are liveness-checked
                            # against this owning workflow by the manager.
                            "workflowId": workflow_id,
                        },
                    )
                    inspection = await self._leases.inspect_lease(lease)
                    if not isinstance(inspection, Mapping) or (
                        inspection.get("active") is not True
                    ):
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
                            owned_by_workflow=False,
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
