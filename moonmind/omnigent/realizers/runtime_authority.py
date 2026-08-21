"""Provider Profile and credential authority for the generic realizer.

This infrastructure adapter performs the side effects selected by an admitted
plan. It is separate from the harness-neutral realizer so provider transport,
database access and credential materialization do not leak into lifecycle
coordination.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from moonmind.omnigent.harness_platform.credential_bindings import (
    deterministic_lease_order,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.materializers import materialize_credential
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


@dataclass(frozen=True, slots=True)
class AcquiredRuntimeAuthority:
    provider_leases: dict[str, dict[str, Any]]
    credential_handles: tuple[dict[str, Any], ...]
    leases: tuple[CredentialLease, ...]


class ProviderProfileRuntimeAuthority:
    """Acquire exact generations and return only secret-free runtime handles."""

    def __init__(
        self,
        *,
        session_factory: Any,
        lease_client: ProviderProfileLeaseClient | None = None,
        credential_materializer: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        if lease_client is None:
            from moonmind.workflows.temporal.client import TemporalClientAdapter

            lease_client = ProviderProfileLeaseClient(TemporalClientAdapter())
        self._lease_client = lease_client
        self._credential_materializer = credential_materializer

    def assert_ready(self, plan: OmnigentExecutionPlanEnvelope) -> None:
        """Reject secret-bearing plans until a trusted materializer is wired."""

        from moonmind.omnigent.harness_platform.materializers import (
            get_materializer,
        )

        missing = sorted(
            {
                str(binding["materializerRef"])
                for binding in plan.payload.credentialBindings.values()
                if get_materializer(str(binding["materializerRef"])).requiredSecretRoles
                and self._credential_materializer is None
            }
        )
        if missing:
            raise HarnessPlatformError(
                "trusted credential materialization is not composed for: "
                + ", ".join(missing),
                code=(
                    HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
                ),
            )

    @staticmethod
    def _validate_command_authority(command_authority: dict[str, Any]) -> None:
        required = {
            "commandId",
            "claimToken",
            "sessionId",
            "turnAttemptId",
            "expectedSessionRevision",
            "fencingGeneration",
        }
        if required - set(command_authority):
            raise HarnessPlatformError(
                "Provider Profile side effect lacks canonical command authority",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )

    async def acquire(
        self,
        *,
        request: AgentExecutionRequest,
        plan: OmnigentExecutionPlanEnvelope,
        command_authority: dict[str, Any],
    ) -> AcquiredRuntimeAuthority:
        self._validate_command_authority(command_authority)
        from api_service.db.models import ManagedAgentProviderProfile

        bindings_by_profile: dict[str, list[tuple[str, Any]]] = {}
        for slot, binding in plan.payload.credentialBindings.items():
            profile_ref = str(binding.get("providerProfileRef") or "").strip()
            if not profile_ref:
                raise HarnessPlatformError(
                    f"credential slot {slot} has no Provider Profile authority",
                    code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
                )
            bindings_by_profile.setdefault(profile_ref, []).append((slot, binding))

        provider_leases: dict[str, dict[str, Any]] = {}
        handles: list[dict[str, Any]] = []
        acquired: list[CredentialLease] = []
        try:
            for profile_ref in deterministic_lease_order(list(bindings_by_profile)):
                async with self._session_factory() as session:
                    profile = await session.get(
                        ManagedAgentProviderProfile, profile_ref
                    )
                    if profile is None or not profile.enabled:
                        raise HarnessPlatformError(
                            f"Provider Profile {profile_ref} is unavailable",
                            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                        )
                    runtime_id = str(
                        getattr(profile.runtime_id, "value", profile.runtime_id)
                    )

                for slot, binding in sorted(bindings_by_profile[profile_ref]):
                    owner_id = deterministic_lease_owner_id(
                        profile_id=profile_ref,
                        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                        workflow_id=request.correlation_id,
                        step_execution_id=request.idempotency_key,
                        idempotency_key=f"{request.idempotency_key}:{slot}",
                    )
                    lease = await self._lease_client.acquire_execution_lease(
                        runtime_id=runtime_id,
                        profile_id=profile_ref,
                        owner_id=owner_id,
                        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
                        metadata={
                            "executionPlanRef": plan.planRef,
                            "credentialSlot": slot,
                            **command_authority,
                        },
                    )
                    acquired.append(lease)
                    # Read the acquired generation only after the lease is held.
                    # The plan fixes the Provider Profile identity; the runtime
                    # binding fixes the generation observed under that lease.
                    async with self._session_factory() as session:
                        acquired_profile = await session.get(
                            ManagedAgentProviderProfile, profile_ref
                        )
                        if acquired_profile is None or not acquired_profile.enabled:
                            raise HarnessPlatformError(
                                f"Provider Profile {profile_ref} became unavailable",
                                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                            )
                        acquired_runtime_id = str(
                            getattr(
                                acquired_profile.runtime_id,
                                "value",
                                acquired_profile.runtime_id,
                            )
                        )
                        if acquired_runtime_id != runtime_id:
                            raise HarnessPlatformError(
                                f"Provider Profile {profile_ref} changed runtime while acquiring authority",
                                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                            )
                        credential_generation = int(
                            acquired_profile.credential_generation
                        )
                    materializer_ref = str(binding["materializerRef"])
                    from moonmind.omnigent.harness_platform.materializers import (
                        get_materializer,
                    )

                    materializer = get_materializer(materializer_ref)
                    if materializer.requiredSecretRoles:
                        if self._credential_materializer is None:
                            raise HarnessPlatformError(
                                "trusted credential materialization is unavailable",
                                code=(
                                    HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE
                                ),
                            )
                        handle = await self._credential_materializer.materialize(
                            profile=acquired_profile,
                            binding=dict(binding),
                            provider_lease_ref=lease.lease_id,
                            credential_generation=credential_generation,
                            execution_plan_ref=plan.planRef,
                            command_authority=dict(command_authority),
                        )
                    else:
                        handle = materialize_credential(
                            materializer_ref=materializer_ref,
                            provider_profile_ref=profile_ref,
                            provider_lease_ref=lease.lease_id,
                            credential_generation=credential_generation,
                        )
                    if not isinstance(handle, dict) or any(
                        key in handle
                        for key in (
                            "apiKey",
                            "api_key",
                            "secret",
                            "secretBody",
                            "token",
                        )
                    ):
                        raise HarnessPlatformError(
                            "credential materializer returned an unsafe runtime handle",
                            code=(
                                HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED
                            ),
                        )
                    handle = {**handle, "credentialSlot": slot}
                    handles.append(handle)
                    provider_leases[slot] = {
                        "providerProfileRef": profile_ref,
                        "providerLeaseRef": lease.lease_id,
                        "credentialGeneration": credential_generation,
                        "credentialRuntimeRef": handle["credentialRuntimeRef"],
                    }
        except Exception:
            for lease in reversed(acquired):
                await self._lease_client.release_lease(lease)
            raise

        return AcquiredRuntimeAuthority(
            provider_leases=provider_leases,
            credential_handles=tuple(handles),
            leases=tuple(acquired),
        )

    async def release(
        self,
        authority: AcquiredRuntimeAuthority,
        *,
        command_authority: dict[str, Any],
    ) -> None:
        self._validate_command_authority(command_authority)
        for lease in reversed(authority.leases):
            await self._lease_client.release_lease(lease)


__all__ = ["AcquiredRuntimeAuthority", "ProviderProfileRuntimeAuthority"]
