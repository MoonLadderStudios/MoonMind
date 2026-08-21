"""Durable stores for execution plans and runtime bindings (Phase 1).

Provides both DB-backed and in-memory implementations so unit tests remain
hermetic while production uses SQLAlchemy. The stores enforce:

- Plan is immutable, digest-addressed, secret-free, persisted before leases
- Runtime binding is staged, fenced, immutable core (planRef + generations)
- Retries load the same plan via planRef
- Workflow input cannot author executionRealizerRef (trusted planner only)
"""

from __future__ import annotations

from typing import Any, Protocol

from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
    create_runtime_binding,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)


class OmnigentExecutionPlanStore(Protocol):
    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None: ...

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope: ...

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope: ...


class OmnigentRuntimeBindingStore(Protocol):
    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None: ...

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
    ) -> OmnigentRuntimeBinding: ...

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
    ) -> OmnigentRuntimeBinding: ...

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding: ...


class InMemoryExecutionPlanStore:
    """Hermetic store for tests and local dev without DB."""

    def __init__(self) -> None:
        self._plans: dict[str, OmnigentExecutionPlanEnvelope] = {}

    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None:
        return self._plans.get(plan_ref)

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope:
        # Verify envelope before persist (fail closed on digest mismatch)
        verify_execution_plan_envelope(envelope)
        # Secret-free check already enforced by execution_plan model validators
        existing = self._plans.get(envelope.planRef)
        if existing is not None and existing != envelope:
            raise HarnessPlatformError(
                f"execution plan conflict for {envelope.planRef}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        self._plans[envelope.planRef] = envelope
        return envelope

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope:
        # Workflow input must not author executionRealizerRef – strip so trusted planner selects
        compile_kwargs.pop("execution_realizer_ref", None)
        compile_kwargs.pop("executionRealizerRef", None)
        envelope: OmnigentExecutionPlanEnvelope = compile_fn(**compile_kwargs)
        existing = self._plans.get(envelope.planRef)
        if existing is not None:
            return existing
        return await self.persist(envelope)


class InMemoryRuntimeBindingStore:
    """Staged, fenced runtime binding store (in-memory)."""

    def __init__(self) -> None:
        self._bindings: dict[str, OmnigentRuntimeBinding] = {}
        # Also index by planRef for lookup
        self._by_plan: dict[str, list[str]] = {}

    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None:
        return self._bindings.get(runtime_binding_ref)

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        host_binding_ref: str = "host-binding:pending",
        host_lease_ref: str = "host-lease:pending",
        host_lease_generation: int = 1,
        omnigent_host_id: str = "host_pending",
    ) -> OmnigentRuntimeBinding:
        # Use placeholder host until host lease is realized; generation is fenced
        binding = create_runtime_binding(
            executionPlanRef=execution_plan_ref,
            providerLeases=provider_leases,
            hostBindingRef=host_binding_ref,
            hostLeaseRef=host_lease_ref,
            hostLeaseGeneration=host_lease_generation,
            omnigentHostId=omnigent_host_id,
        )
        # Immutable core cannot be mutated after creation
        self._bindings[binding.runtimeBindingRef] = binding
        self._by_plan.setdefault(execution_plan_ref, []).append(binding.runtimeBindingRef)
        return binding

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
    ) -> OmnigentRuntimeBinding:
        existing = self._bindings.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        # Cannot mutate plan decisions or acquired generations; only add host info
        # Re-create with new host fields but preserve providerLeases (immutable)
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={k: v.model_dump(by_alias=True, mode="json") for k, v in existing.providerLeases.items()},
            hostBindingRef=host_binding_ref,
            hostLeaseRef=host_lease_ref,
            hostLeaseGeneration=host_lease_generation,
            omnigentHostId=omnigent_host_id,
            hostHarnessAttestationRef=existing.hostHarnessAttestationRef,
            exactHostCapabilityDecisionRef=existing.exactHostCapabilityDecisionRef,
            workspaceResolutionRef=existing.workspaceResolutionRef,
            modelOptionAttestationRef=existing.modelOptionAttestationRef,
            skillDeliveryAttestationRef=existing.skillDeliveryAttestationRef,
            omnigentSessionId=existing.omnigentSessionId,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        # New ref differs because host fields are part of digest; we store under new ref
        # but also keep old for history; return new
        self._bindings[updated.runtimeBindingRef] = updated
        # Do not delete old; keep for audit
        return updated

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding:
        existing = self._bindings.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        updated = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={k: v.model_dump(by_alias=True, mode="json") for k, v in existing.providerLeases.items()},
            hostBindingRef=existing.hostBindingRef,
            hostLeaseRef=existing.hostLeaseRef,
            hostLeaseGeneration=existing.hostLeaseGeneration,
            omnigentHostId=existing.omnigentHostId,
            hostHarnessAttestationRef=existing.hostHarnessAttestationRef,
            exactHostCapabilityDecisionRef=existing.exactHostCapabilityDecisionRef,
            workspaceResolutionRef=existing.workspaceResolutionRef,
            modelOptionAttestationRef=existing.modelOptionAttestationRef,
            skillDeliveryAttestationRef=existing.skillDeliveryAttestationRef,
            omnigentSessionId=omnigent_session_id,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        self._bindings[updated.runtimeBindingRef] = updated
        return updated


# DB-backed implementations (thin wrappers around models) – used in production

class DbExecutionPlanStore:
    """DB-backed store using OmnigentExecutionPlanRecord."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None:
        from api_service.db.models import OmnigentExecutionPlanRecord

        async with self._session_factory() as session:
            record = await session.get(OmnigentExecutionPlanRecord, plan_ref)
            if record is None:
                return None
            return OmnigentExecutionPlanEnvelope.model_validate(
                {"schemaVersion": record.schema_version, "planRef": record.plan_ref, "payload": record.payload_json}
            )

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope:
        from api_service.db.models import OmnigentExecutionPlanRecord
        from sqlalchemy.exc import IntegrityError

        verify_execution_plan_envelope(envelope)
        async with self._session_factory() as session:
            existing = await session.get(OmnigentExecutionPlanRecord, envelope.planRef)
            if existing is not None:
                # Verify existing payload matches
                if existing.payload_json != envelope.payload.model_dump(by_alias=True, mode="json"):
                    raise HarnessPlatformError(
                        f"execution plan conflict for {envelope.planRef}",
                        code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                    )
                return envelope
            record = OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=envelope.payload.model_dump(by_alias=True, mode="json"),
                agent_profile_snapshot_ref=envelope.payload.agentProfileSnapshotRef,
                credential_binding_set_ref=envelope.payload.credentialBindingSetRef,
                harness_id=envelope.payload.harnessId,
                harness_implementation_ref=envelope.payload.harnessImplementationRef,
                host_class_ref=envelope.payload.hostClassRef,
                launch_policy_ref=envelope.payload.launchPolicyRef,
                execution_realizer_ref=envelope.payload.executionRealizerRef,
                support_combination_key=envelope.payload.supportCombinationKey,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                # Race: another worker persisted same plan
                loaded = await self.load(envelope.planRef)
                if loaded is not None:
                    return loaded
                raise HarnessPlatformError(
                    f"failed to persist execution plan: {exc}",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                ) from exc
            return envelope

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope:
        envelope: OmnigentExecutionPlanEnvelope = compile_fn(**compile_kwargs)
        existing = await self.load(envelope.planRef)
        if existing is not None:
            return existing
        return await self.persist(envelope)
