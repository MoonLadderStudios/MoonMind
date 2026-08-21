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
    async def load(self, plan_ref: str) -> OmnigentExecutionPlanEnvelope | None: pass  # noqa

    async def load_or_compile(
        self,
        *,
        compile_fn: Any,
        compile_kwargs: dict[str, Any],
    ) -> OmnigentExecutionPlanEnvelope: pass  # noqa

    async def persist(self, envelope: OmnigentExecutionPlanEnvelope) -> OmnigentExecutionPlanEnvelope: pass  # noqa


class OmnigentRuntimeBindingStore(Protocol):
    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None: pass  # noqa

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        credential_handles: dict[str, dict[str, Any]] | None = None,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def latest_for_plan(
        self, execution_plan_ref: str
    ) -> OmnigentRuntimeBinding | None: pass  # noqa

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str | None = None,
        exact_host_capability_decision_ref: str | None = None,
        workspace_resolution_ref: str | None = None,
        model_option_attestation_ref: str | None = None,
        skill_delivery_attestation_ref: str | None = None,
    ) -> OmnigentRuntimeBinding: pass  # noqa

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding: pass  # noqa


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
        credential_handles: dict[str, dict[str, Any]] | None = None,
    ) -> OmnigentRuntimeBinding:
        existing = await self.latest_for_plan(execution_plan_ref)
        if existing is not None:
            expected = {
                slot: value.model_dump(by_alias=True, mode="json")
                for slot, value in existing.providerLeases.items()
            }
            if expected != provider_leases:
                raise HarnessPlatformError(
                    "runtime binding provider lease authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            expected_cleanup_refs = tuple(
                sorted(
                    str(handle.get("cleanupRef"))
                    for handle in (credential_handles or {}).values()
                    if handle.get("cleanupRef")
                )
            )
            if existing.cleanupAuthorityRefs != expected_cleanup_refs:
                raise HarnessPlatformError(
                    "runtime binding cleanup authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return existing
        binding = create_runtime_binding(
            executionPlanRef=execution_plan_ref,
            providerLeases=provider_leases,
            cleanupAuthorityRefs=sorted(
                str(handle.get("cleanupRef"))
                for handle in (credential_handles or {}).values()
                if handle.get("cleanupRef")
            ),
        )
        # Immutable core cannot be mutated after creation
        self._bindings[binding.runtimeBindingRef] = binding
        self._by_plan.setdefault(execution_plan_ref, []).append(binding.runtimeBindingRef)
        return binding

    async def latest_for_plan(
        self, execution_plan_ref: str
    ) -> OmnigentRuntimeBinding | None:
        refs = self._by_plan.get(execution_plan_ref) or []
        return self._bindings.get(refs[-1]) if refs else None

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str | None = None,
        exact_host_capability_decision_ref: str | None = None,
        workspace_resolution_ref: str | None = None,
        model_option_attestation_ref: str | None = None,
        skill_delivery_attestation_ref: str | None = None,
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
            hostHarnessAttestationRef=host_harness_attestation_ref,
            exactHostCapabilityDecisionRef=exact_host_capability_decision_ref,
            workspaceResolutionRef=workspace_resolution_ref,
            modelOptionAttestationRef=model_option_attestation_ref,
            skillDeliveryAttestationRef=skill_delivery_attestation_ref,
            omnigentSessionId=existing.omnigentSessionId,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        # New ref differs because host fields are part of digest; we store under new ref
        # but also keep old for history; return new
        self._bindings[updated.runtimeBindingRef] = updated
        self._by_plan.setdefault(existing.executionPlanRef, []).append(
            updated.runtimeBindingRef
        )
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
        self._by_plan.setdefault(existing.executionPlanRef, []).append(
            updated.runtimeBindingRef
        )
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

    @staticmethod
    async def persist_in_session(
        session: Any,
        envelope: OmnigentExecutionPlanEnvelope,
    ) -> OmnigentExecutionPlanEnvelope:
        """Flush a plan inside an API caller's admission transaction.

        The caller commits the execution record and plan together before
        Temporal start.  This avoids a window in which a workflow can launch
        without the immutable authority it names.
        """

        from api_service.db.models import OmnigentExecutionPlanRecord

        verify_execution_plan_envelope(envelope)
        existing = await session.get(OmnigentExecutionPlanRecord, envelope.planRef)
        payload = envelope.payload.model_dump(by_alias=True, mode="json")
        if existing is not None:
            if existing.payload_json != payload:
                raise HarnessPlatformError(
                    f"execution plan conflict for {envelope.planRef}",
                    code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
                )
            return envelope
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=envelope.planRef,
                schema_version=envelope.schemaVersion,
                payload_json=payload,
                agent_profile_snapshot_ref=envelope.payload.agentProfileSnapshotRef,
                credential_binding_set_ref=envelope.payload.credentialBindingSetRef,
                harness_id=envelope.payload.harnessId,
                harness_implementation_ref=envelope.payload.harnessImplementationRef,
                host_class_ref=envelope.payload.hostClassRef,
                launch_policy_ref=envelope.payload.launchPolicyRef,
                execution_realizer_ref=envelope.payload.executionRealizerRef,
                support_combination_key=envelope.payload.supportCombinationKey,
            )
        )
        await session.flush()
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


class DbRuntimeBindingStore:
    """DB-backed immutable-stage runtime binding journal."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _from_record(record: Any) -> OmnigentRuntimeBinding:
        attestations = dict(record.attestation_refs_json or {})
        return OmnigentRuntimeBinding.model_validate(
            {
                "schemaVersion": "moonmind.omnigent-runtime-binding.v1",
                "runtimeBindingRef": record.runtime_binding_ref,
                "executionPlanRef": record.execution_plan_ref,
                "providerLeases": record.provider_leases_json or {},
                "hostBindingRef": record.host_binding_ref,
                "hostLeaseRef": record.host_lease_ref,
                "hostLeaseGeneration": record.host_lease_generation,
                "omnigentHostId": record.omnigent_host_id,
                "hostHarnessAttestationRef": attestations.get(
                    "hostHarnessAttestationRef"
                ),
                "exactHostCapabilityDecisionRef": attestations.get(
                    "exactHostCapabilityDecisionRef"
                ),
                "workspaceResolutionRef": attestations.get(
                    "workspaceResolutionRef"
                ),
                "modelOptionAttestationRef": attestations.get(
                    "modelOptionAttestationRef"
                ),
                "skillDeliveryAttestationRef": attestations.get(
                    "skillDeliveryAttestationRef"
                ),
                "omnigentSessionId": record.session_id,
                "cleanupAuthorityRefs": record.cleanup_authority_refs_json or [],
            }
        )

    async def get(self, runtime_binding_ref: str) -> OmnigentRuntimeBinding | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord

        async with self._session_factory() as session:
            record = await session.get(
                OmnigentRuntimeBindingRecord, runtime_binding_ref
            )
            return self._from_record(record) if record is not None else None

    async def latest_for_plan(
        self, execution_plan_ref: str
    ) -> OmnigentRuntimeBinding | None:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy import case, select

        async with self._session_factory() as session:
            record = (
                await session.execute(
                    select(OmnigentRuntimeBindingRecord)
                    .where(
                        OmnigentRuntimeBindingRecord.execution_plan_ref
                        == execution_plan_ref
                    )
                    .order_by(
                        case(
                            (
                                OmnigentRuntimeBindingRecord.state
                                == "session_bound",
                                3,
                            ),
                            (
                                OmnigentRuntimeBindingRecord.state
                                == "host_acquired",
                                2,
                            ),
                            else_=1,
                        ).desc(),
                        OmnigentRuntimeBindingRecord.created_at.desc(),
                        OmnigentRuntimeBindingRecord.revision.desc(),
                        OmnigentRuntimeBindingRecord.runtime_binding_ref.desc(),
                    )
                    .limit(1)
                )
            ).scalars().first()
            return self._from_record(record) if record is not None else None

    async def _persist(
        self,
        binding: OmnigentRuntimeBinding,
        *,
        state: str,
        credential_handles: dict[str, Any] | None = None,
    ) -> OmnigentRuntimeBinding:
        from api_service.db.models import OmnigentRuntimeBindingRecord
        from sqlalchemy.exc import IntegrityError

        provider_leases = {
            slot: lease.model_dump(by_alias=True, mode="json")
            for slot, lease in binding.providerLeases.items()
        }
        attestation_refs = {
            key: value
            for key, value in {
                "hostHarnessAttestationRef": binding.hostHarnessAttestationRef,
                "exactHostCapabilityDecisionRef": binding.exactHostCapabilityDecisionRef,
                "workspaceResolutionRef": binding.workspaceResolutionRef,
                "modelOptionAttestationRef": binding.modelOptionAttestationRef,
                "skillDeliveryAttestationRef": binding.skillDeliveryAttestationRef,
            }.items()
            if value is not None
        }
        async with self._session_factory() as session:
            existing = await session.get(
                OmnigentRuntimeBindingRecord, binding.runtimeBindingRef
            )
            if existing is not None:
                loaded = self._from_record(existing)
                if loaded != binding:
                    raise HarnessPlatformError(
                        f"runtime binding conflict for {binding.runtimeBindingRef}",
                        code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                    )
                return loaded
            session.add(
                OmnigentRuntimeBindingRecord(
                    runtime_binding_ref=binding.runtimeBindingRef,
                    execution_plan_ref=binding.executionPlanRef,
                    state=state,
                    provider_leases_json=provider_leases,
                    host_binding_ref=binding.hostBindingRef,
                    host_lease_ref=binding.hostLeaseRef,
                    host_lease_generation=binding.hostLeaseGeneration,
                    omnigent_host_id=binding.omnigentHostId,
                    session_id=binding.omnigentSessionId,
                    credential_runtime_handles_json=dict(credential_handles or {}),
                    attestation_refs_json=attestation_refs,
                    cleanup_authority_refs_json=list(binding.cleanupAuthorityRefs),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                loaded = await self.get(binding.runtimeBindingRef)
                if loaded is not None:
                    return loaded
                raise HarnessPlatformError(
                    f"failed to persist runtime binding: {exc}",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                ) from exc
        return binding

    async def create_initial(
        self,
        *,
        execution_plan_ref: str,
        provider_leases: dict[str, dict[str, Any]],
        credential_handles: dict[str, dict[str, Any]] | None = None,
    ) -> OmnigentRuntimeBinding:
        existing = await self.latest_for_plan(execution_plan_ref)
        if existing is not None:
            expected = {
                slot: lease.model_dump(by_alias=True, mode="json")
                for slot, lease in existing.providerLeases.items()
            }
            if expected != provider_leases:
                raise HarnessPlatformError(
                    "runtime binding provider lease authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            expected_cleanup_refs = tuple(
                sorted(
                    str(handle.get("cleanupRef"))
                    for handle in (credential_handles or {}).values()
                    if handle.get("cleanupRef")
                )
            )
            if existing.cleanupAuthorityRefs != expected_cleanup_refs:
                raise HarnessPlatformError(
                    "runtime binding cleanup authority changed on retry",
                    code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
                )
            return existing
        binding = create_runtime_binding(
            executionPlanRef=execution_plan_ref,
            providerLeases=provider_leases,
            cleanupAuthorityRefs=sorted(
                str(handle.get("cleanupRef"))
                for handle in (credential_handles or {}).values()
                if handle.get("cleanupRef")
            ),
        )
        return await self._persist(
            binding,
            state="credentials_acquired",
            credential_handles=credential_handles,
        )

    async def update_with_host(
        self,
        runtime_binding_ref: str,
        *,
        host_binding_ref: str,
        host_lease_ref: str,
        host_lease_generation: int,
        omnigent_host_id: str,
        host_harness_attestation_ref: str | None = None,
        exact_host_capability_decision_ref: str | None = None,
        workspace_resolution_ref: str | None = None,
        model_option_attestation_ref: str | None = None,
        skill_delivery_attestation_ref: str | None = None,
    ) -> OmnigentRuntimeBinding:
        existing = await self.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        binding = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={
                slot: lease.model_dump(by_alias=True, mode="json")
                for slot, lease in existing.providerLeases.items()
            },
            hostBindingRef=host_binding_ref,
            hostLeaseRef=host_lease_ref,
            hostLeaseGeneration=host_lease_generation,
            omnigentHostId=omnigent_host_id,
            hostHarnessAttestationRef=host_harness_attestation_ref,
            exactHostCapabilityDecisionRef=exact_host_capability_decision_ref,
            workspaceResolutionRef=workspace_resolution_ref,
            modelOptionAttestationRef=model_option_attestation_ref,
            skillDeliveryAttestationRef=skill_delivery_attestation_ref,
            omnigentSessionId=existing.omnigentSessionId,
            cleanupAuthorityRefs=list(existing.cleanupAuthorityRefs),
        )
        return await self._persist(binding, state="host_acquired")

    async def update_with_session(
        self,
        runtime_binding_ref: str,
        *,
        omnigent_session_id: str,
    ) -> OmnigentRuntimeBinding:
        existing = await self.get(runtime_binding_ref)
        if existing is None:
            raise HarnessPlatformError(
                f"runtime binding {runtime_binding_ref} not found",
                code=HarnessPlatformFailure.OMNIGENT_RUNTIME_BINDING_CONFLICT,
            )
        binding = create_runtime_binding(
            executionPlanRef=existing.executionPlanRef,
            providerLeases={
                slot: lease.model_dump(by_alias=True, mode="json")
                for slot, lease in existing.providerLeases.items()
            },
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
        return await self._persist(binding, state="session_bound")


__all__ = [
    "DbExecutionPlanStore",
    "DbRuntimeBindingStore",
    "InMemoryExecutionPlanStore",
    "InMemoryRuntimeBindingStore",
    "OmnigentExecutionPlanStore",
    "OmnigentRuntimeBindingStore",
]
